"""Market Watch (Onda 2, WP2.4B — PLANO Apêndice F.6–F.11; pesquisa 2-dados §1.4, §3.4, §4.1–§4.4):
de onde vem cada dado, as sondas, o estado próprio e a família `mw-short-<empresa>.json`.

O QUE O ROBÔ DO SITE GRAVA E COMO (`_shared/market_watch.py`, origin/main f636222):
  • aluguel (`mw_short_interest`): upsert por (papel, dia) dos últimos ~5 dias úteis, a cada 30 min
    nos dias úteis — dia novo = linha nova; a B3 revisa dias recentes (o upsert reescreve SEM mexer
    no `updated_at`); o histórico antigo entra por backfill de PDF (à mão, raro);
  • insiders (`mw_insider_moves`): versão nova de um mês = ids novos + apaga a versão velha;
  • comunicados (`mw_filings`): só insere (ids já conhecidos são pulados) → imutáveis;
  • recompras e capital: upsert diário de TUDO, sem carimbo de mudança (`updated_at` = a hora da
    INSERÇÃO: a sonda não serve).
Toda leitura é pela RPC `get_mw_*` (é ela que esconde a empresa oculta, `is_visible`), com `select`
explícito — e as SONDAS também (contagem + máximo pela RPC, `limit=1`: ~1 KB, quase só cabeçalho), para
contar exatamente o que se publica.

POR FONTE (a política; os tempos em UTC):
  empresas    `get_mw_companies` no máximo 1×/hora (~1,5 KB): o portão de tudo — empresa que some
              (oculta no /admin) sai de TODOS os arquivos na hora seguinte, e as releituras
              (capital, recompras, último aluguel, sondas) acontecem na mesma execução;
  aluguel     sonda (contagem + maior data) 1×/hora. A série INTEIRA de cada empresa visível fica no
              estado: na partida a frio vem das cópias PUBLICADAS das famílias (zero Supabase);
              sem cópia (1º envio, empresa nova) → leitura paginada daquela empresa. A sonda que não
              bate com o cache → releitura da JANELA dos últimos 10 dias de todas as empresas (~7 KB;
              pega dia novo e revisão da B3); a janela também é relida nas marcas de 16:00 e 23:30
              (revisão sem dia novo). Soma que ainda não fecha (backfill antigo, empresa reexibida) →
              contagem por empresa (~1 KB cada) e releitura só de quem não bate;
  último      `get_mw_short_latest` (o "latest" de cada papel): da cópia do mw-companies.json na
              partida a frio quando ela bate com a sonda do aluguel (Σ `days` = contagem, maior data);
              senão, e nas marcas, relido (~1,6 KB);
  capital,    leitura completa nas marcas de 16:00 e 23:30 (o robô da CVM roda às ~12 UTC, com o
  recompras   atraso do cron do site); na partida a frio, da cópia publicada (mw-companies.json e
              mw-buybacks.json) até a marca seguinte;
  insiders    sonda (contagem + maior `updated_at` da janela de 14 meses) a cada 3 h; mudou → a
              janela inteira, paginada (3 páginas, ~85 KB; a soma é conferida com a contagem). A
              janela anda 1×/mês: os meses que saem caem do cache sem ler o banco. Partida a frio: a
              cópia publicada vale se a contagem e o `updatedAt` dela batem com a sonda;
  comunicados sonda (contagem + maior `updated_at`) 1×/hora; mudou → só os inseridos desde o último
              `updatedAt` (− 10 min), juntados por id; a contagem que não fecha → os 600 de novo.
  Fora do ar: se a cópia publicada que faria a semente não vem porque o Mindassets está parado
  (`PublishedCopies.healthy`), as leituras CARAS (aluguel, insiders, comunicados) ESPERAM — nada que
  se monte agora seria publicado antes de ele voltar, e cada runner novo (~4,4/dia) repetiria a leitura
  (~350 KB). No `--out` lê sempre.

ESTADO PRÓPRIO: `<estado>_mw.json` ao lado do principal (a série do aluguel e os insiders pesam ~1 MB;
só é lido nas execuções que montam um arquivo do Market Watch, ~1×/hora).

ENVIO: os 5 arquivos fixos e a família vão no grupo `mw` do registro, com teto de 4 envios por
execução (O28: a carga inicial são 16 envios; num dia de aluguel novo, ~13) — o que passa do teto fica
"na fila" para o ciclo seguinte (5 min depois), sem reler nada (os caches valem 1 h).
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import names
from .encode import iso_date, iso_utc
from .exporters import mw_buybacks as BB
from .exporters import mw_companies as MC
from .exporters import mw_filings as FL
from .exporters import mw_insiders as IN
from .exporters import mw_short_interest as SI
from .schedule import COLD, is_due
from .state import load as _load
from .state import save as _save

log = logging.getLogger(__name__)

PREFIX = "mw-short"
RESERVED = frozenset({"INTEREST"})        # mw-short-interest.json é o arquivo FIXO da visão "Todas"
MW_STATE_VERSION = 1
MARKS_UTC = ((16, 0), (23, 30))           # leituras completas 2×/dia (recompras, capital, janela)
COMPANIES_MAX_AGE_S = 3300                # ~1 h (a cadência COLD, com folga do relógio do loop)
PROBE_MAX_AGE_S = 3300                    # sondas do aluguel e dos comunicados
INSIDERS_PROBE_MAX_AGE_S = 3 * 3600       # a CVM atualiza os insiders ~1×/semana
WINDOW_DAYS = 10                          # a janela relida do aluguel (a B3 revisa dias recentes)
WINDOW_MAX_GAP_DAYS = 30                  # empresa parada há mais que isto: conferida pela contagem
FILINGS_MARGIN = timedelta(minutes=10)
_ISO = "%Y-%m-%dT%H:%M:%SZ"


class MWWaiting(RuntimeError):
    """A cópia publicada que faria a semente não veio e o Mindassets está parado: a leitura cara
    espera (o arquivo tenta de novo na próxima execução)."""


def mw_state_path(main_path) -> Path:
    p = Path(main_path)
    return p.with_name(p.stem + "_mw.json")


def empty_mw_state() -> dict:
    return {"version": MW_STATE_VERSION,
            "companies": {"at": None, "rows": None},
            "capital": {"at": None, "rows": None},
            "latest": {"at": None, "rows": None},
            "buybacks": {"at": None, "items": None},
            "lending": {"probe": None, "probeAt": None, "synced": None, "windowAt": None,
                        "series": {}},
            "insiders": {"probe": None, "probeAt": None, "from": None, "rows": None,
                         "updatedAt": None},
            "filings": {"probe": None, "probeAt": None, "items": None, "updatedAt": None}}


def load_mw_state(path) -> dict:
    return _load(path, empty=empty_mw_state, version=MW_STATE_VERSION)


def save_mw_state(path, st: dict) -> bool:
    return _save(path, st)


def file_name(company) -> str | None:
    """`mw-short-<fileId>.json` de uma empresa (None se o id é recusado pela regra F.0)."""
    if not isinstance(company, str) or company in RESERVED:
        return None
    return names.family_file(PREFIX, company)


def company_from_file(name) -> str | None:
    """A empresa de um nome da família (`mw-short-klbn11.json` → KLBN11); o fixo
    `mw-short-interest.json` não é da família."""
    co = names.family_ident(PREFIX, name)
    return co if co and co not in RESERVED else None


def last_mark(now: datetime) -> str:
    """O instante da última marca (16:00 ou 23:30 UTC) já passada."""
    now = now.astimezone(timezone.utc)
    marks = []
    for h, m in MARKS_UTC:
        t = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if t > now:
            t -= timedelta(days=1)
        marks.append(t)
    return iso_utc(max(marks))


def _rows_total(by: dict) -> tuple[int, str | None]:
    """(linhas, maior data) de um {papel: linhas}."""
    n = sum(len(v) for v in by.values())
    last = max((v[-1][0] for v in by.values() if v), default=None)
    return n, last


class MWStore:
    """Os dados do Market Watch de UMA execução (cada fonte lida no máximo 1× — memo)."""

    def __init__(self, src, state: dict):
        self.src = src
        self.supa = src.supa
        self.pub = src.published
        self.mode = src.mode
        self.now = src.now
        self.rebuild = src.rebuild
        base = empty_mw_state()
        for k, v in base.items():
            if not isinstance(state.get(k), type(v)):
                state[k] = v
            elif isinstance(v, dict):
                for kk, vv in v.items():
                    state[k].setdefault(kk, vv)
        self.st = state
        self._memo: dict = {}
        self.probes: list[str] = []
        self.reads: list[str] = []
        self.seeds: list[str] = []
        self.notes: list[str] = []
        self.planned: list[str] = []

    # ── atalhos ──────────────────────────────────────────────────────────────────────────────
    def _stamp(self) -> str:
        return self.src.generated_at

    def _fresh(self, stamp, max_age: float) -> bool:
        iso = iso_utc(stamp)
        if not iso:
            return False
        age = (self.now - datetime.strptime(iso, _ISO).replace(tzinfo=timezone.utc)).total_seconds()
        return 0 <= age < max_age

    def _before_mark(self, stamp) -> bool:
        iso = iso_utc(stamp)
        return not iso or iso < last_mark(self.now)

    def _copy(self, name: str | None) -> tuple[dict | None, str]:
        """(cópia publicada válida, motivo de não ter): "falta" (404 ou ilegível com o Mindassets
        publicando, sem Mindassets configurado, ou --out) → pode ler do banco; "fora" (o Mindassets
        não responde ou está parado) → a leitura cara espera; "rebuild" → lê do banco."""
        if self.rebuild:
            return None, "rebuild"
        if self.pub is None or not name:
            return None, "falta"
        doc = self.pub.get(name)
        if isinstance(doc, dict) and doc.get("schemaVersion") == 1:
            return doc, ""
        if self.mode == "out" or (self.pub.status(name) in ("ausente", "erro", "ok")
                                  and self.pub.healthy(self.now)):
            return None, "falta"
        return None, "fora"

    def _need_db(self, why: str, what: str) -> None:
        if why == "fora":
            raise MWWaiting(f"{what}: cópia publicada indisponível e o Mindassets está parado — "
                            "a leitura do banco espera")

    # ── empresas (o portão) ──────────────────────────────────────────────────────────────────
    def companies(self) -> list[dict]:
        """As empresas VISÍVEIS, na ordem de `display_order` (relidas no máximo 1×/hora)."""
        if "companies" in self._memo:
            return self._memo["companies"]
        st = self.st["companies"]
        if self.rebuild or st.get("rows") is None or not self._fresh(st.get("at"), COMPANIES_MAX_AGE_S):
            got, seen = [], set()
            for r in self.supa.rpc("get_mw_companies", MC.COMPANY_COLS, tag="mw:empresas",
                                   order=MC.COMPANY_ORDER):
                c = MC.company(r)
                if c and c["ticker"] not in seen:
                    seen.add(c["ticker"])
                    got.append(c)
            prev = st.get("rows")
            if prev is not None and {c["ticker"] for c in prev} != seen:
                self._visibility_changed({c["ticker"] for c in prev}, seen)
            st.update(rows=got, at=self._stamp())
            self.reads.append(f"empresas ({len(got)})")
        self._memo["companies"] = st["rows"]
        return st["rows"]

    def _visible(self) -> set[str]:
        return {c["ticker"] for c in self.companies()}

    def _visibility_changed(self, before: set, after: set) -> None:
        """Empresa oculta (ou reexibida) no /admin: as fontes sem sonda relêem já e as sondas rodam
        já (a da empresa que voltou não bate e puxa o que falta)."""
        gone, new = sorted(before - after), sorted(after - before)
        self.notes.append("empresas visíveis mudaram"
                          + (f" (saíram: {', '.join(gone)})" if gone else "")
                          + (f" (entraram: {', '.join(new)})" if new else ""))
        for k in ("capital", "latest", "buybacks"):
            self.st[k]["at"] = None
        for k in ("lending", "insiders", "filings"):
            self.st[k]["probeAt"] = None

    # ── capital ──────────────────────────────────────────────────────────────────────────────
    def capital(self) -> dict[str, dict]:
        """empresa → capital (só as visíveis)."""
        if "capital" in self._memo:
            return self._memo["capital"]
        vis = self._visible()
        st = self.st["capital"]
        if st.get("rows") is None and not self.rebuild:
            seeded = self._seed_capital()
            if seeded is not None:
                st.update(rows=seeded, at=self._stamp())
                self.seeds.append("capital")
        if self.rebuild or st.get("rows") is None or self._before_mark(st.get("at")):
            caps = {}
            for r in self.supa.rpc("get_mw_share_capital", MC.CAPITAL_COLS, tag="mw:capital"):
                co = MC._text(r.get("company"))
                if co:
                    caps[co] = MC.capital(r)
            st.update(rows=caps, at=self._stamp())
            self.reads.append("capital")
        out = {co: c for co, c in st["rows"].items() if co in vis}
        self._memo["capital"] = out
        return out

    def _seed_capital(self) -> dict | None:
        doc, _why = self._copy("mw-companies.json")
        cos = doc.get("companies") if doc else None
        if not isinstance(cos, list):
            return None
        return {c["ticker"]: c["capital"] for c in cos if isinstance(c, dict)
                and isinstance(c.get("ticker"), str) and isinstance(c.get("capital"), dict)}

    # ── aluguel: a sonda, o último de cada papel e a série inteira ────────────────────────────
    def _lending_probe(self) -> list:
        """[contagem, maior data] do aluguel das empresas visíveis (pela RPC), no máximo 1×/hora."""
        if "lprobe" in self._memo:
            return self._memo["lprobe"]
        st = self.st["lending"]
        if self.rebuild or st.get("probe") is None or not self._fresh(st.get("probeAt"), PROBE_MAX_AGE_S):
            rows, total = self.supa.rpc_count("get_mw_short_interest", "ref_date",
                                              {"p_company": None, "p_from": None},
                                              tag="mw:aluguel:sonda", order="ref_date.desc", limit=1)
            st.update(probe=[total, iso_date(rows[0].get("ref_date")) if rows else None],
                      probeAt=self._stamp())
            self.probes.append("aluguel")
        self._memo["lprobe"] = list(st["probe"])
        return self._memo["lprobe"]

    @staticmethod
    def _latest_matches(rows: list[dict], probe: list) -> bool:
        n = sum(r.get("days") or 0 for r in rows)
        last = max((r.get("refDate") or "" for r in rows), default="") or None
        return [n, last] == list(probe)

    def short_latest(self) -> list[dict]:
        """`get_mw_short_latest` (só as empresas visíveis), na ordem da RPC."""
        if "latest" in self._memo:
            return self._memo["latest"]
        vis = self._visible()
        probe = self._lending_probe()
        st = self.st["latest"]
        if st.get("rows") is None and not self.rebuild:
            doc, _why = self._copy("mw-companies.json")
            rows = doc.get("shortLatest") if doc else None
            if isinstance(rows, list):
                st.update(rows=[r for r in rows if isinstance(r, dict) and r.get("company")],
                          at=self._stamp())
                self.seeds.append("último aluguel")
        rows = st.get("rows")
        if (self.rebuild or rows is None or not self._latest_matches(rows, probe)
                or self._before_mark(st.get("at"))):
            got = [x for x in (MC.latest(r) for r in self.supa.rpc(
                "get_mw_short_latest", MC.LATEST_COLS, tag="mw:aluguel:ultimo",
                order=MC.LATEST_ORDER)) if x]
            st.update(rows=got, at=self._stamp())
            self.reads.append("último aluguel")
        out = [r for r in st["rows"] if r.get("company") in vis]
        self._memo["latest"] = out
        return out

    def lending(self) -> dict[str, dict[str, list]]:
        """empresa → {papel: linhas (FULL_COLUMNS), data crescente} de TODAS as empresas visíveis."""
        if "lending" in self._memo:
            return self._memo["lending"]
        cos = [c["ticker"] for c in self.companies()]
        st = self.st["lending"]
        if self.rebuild:
            st.update(series={}, synced=None, windowAt=None)
        series = st["series"]
        for co in [co for co in series if co not in cos]:
            del series[co]                          # oculta no /admin: sai do cache
        probe = self._lending_probe()
        fresh: set[str] = set()
        seeded: list[str] = []
        for co in cos:
            if co in series:
                continue
            doc, why = self._copy(file_name(co))
            got = self._series_from_copy(co, doc) if doc is not None else None
            if got is not None:
                series[co] = got
                seeded.append(co)
                continue
            self._need_db(why, f"aluguel de {co}")
            series[co] = self._read_company(co)
            fresh.add(co)
        if seeded:
            self.seeds.append(f"aluguel ({len(seeded)} empresa(s))")
        total = self._total(series, cos)
        need = total != probe or (st.get("windowAt") is not None and self._before_mark(st["windowAt"]))
        if need:
            self._window(series, cos)
            total = self._total(series, cos)
            if total != probe:
                self._verify(series, cos, fresh)
                total = self._total(series, cos)
        if need or st.get("windowAt") is None:
            st["windowAt"] = self._stamp()
        st["synced"] = probe if total == probe else None
        if total != probe:
            self.notes.append(f"aluguel: o cache ({total[0]} linhas até {total[1]}) não fecha com a "
                              f"sonda ({probe[0]} até {probe[1]}) — confere de novo na próxima")
        out = {co: series[co] for co in cos if co in series}
        self._memo["lending"] = out
        return out

    def lending_company(self, company: str) -> dict[str, list]:
        if company not in self._visible():
            raise ValueError(f"{company}: empresa desconhecida ou oculta no Market Watch")
        return self.lending().get(company) or {}

    @staticmethod
    def _total(series: dict, cos: list[str]) -> list:
        n, last = 0, None
        for co in cos:
            k, d = _rows_total(series.get(co) or {})
            n += k
            if d and (last is None or d > last):
                last = d
        return [n, last]

    def _series_from_copy(self, co: str, doc: dict) -> dict | None:
        """A série de uma empresa a partir do mw-short-<empresa>.json publicado (None se não serve)."""
        if (doc.get("company") != co or doc.get("columns") != SI.FULL_COLUMNS
                or not isinstance(doc.get("series"), dict)):
            return None
        out = {}
        for t, rows in doc["series"].items():
            if not isinstance(t, str) or not isinstance(rows, list):
                return None
            ok = [list(r) for r in rows if isinstance(r, list) and len(r) == len(SI.FULL_COLUMNS)
                  and isinstance(r[0], str) and iso_date(r[0]) == r[0]]
            if ok:
                out[t] = sorted(ok, key=lambda r: r[0])
        return out

    def _group(self, rows: list[dict], only: str | None = None) -> dict[str, dict[str, list]]:
        by: dict[str, dict[str, list]] = {}
        for r in rows:
            x = SI.row(r)
            if x is None or (only is not None and x[1] != only):
                continue
            t, co, line = x
            by.setdefault(co, {}).setdefault(t, []).append(line)
        for d in by.values():
            for t in d:
                d[t].sort(key=lambda r: r[0])
        return by

    def _read_company(self, co: str) -> dict[str, list]:
        """A série inteira de uma empresa (paginada: a RPC ordena por data crescente e o corte de
        1.000 linhas levaria os dias mais NOVOS)."""
        rows = self.supa.rpc_all("get_mw_short_interest", SI.COLS, {"p_company": co, "p_from": None},
                                 order=SI.ORDER, tag="mw:aluguel:empresa")
        self.reads.append(f"aluguel de {co} ({len(rows)} linhas)")
        return self._group(rows, only=co).get(co, {})

    def _window(self, series: dict, cos: list[str]) -> None:
        """Relê os últimos 10 dias (do menor "último dia" entre as empresas, com teto de 30 dias de
        atraso) de todas as empresas e troca esse trecho no cache — dia novo, revisão e linha que
        sumiu, de uma vez."""
        lasts = [d for d in (_rows_total(series.get(co) or {})[1] for co in cos) if d]
        if not lasts:
            return
        floor = date.fromisoformat(max(lasts)) - timedelta(days=WINDOW_MAX_GAP_DAYS)
        start = (max(date.fromisoformat(min(lasts)), floor) - timedelta(days=WINDOW_DAYS)).isoformat()
        rows = self.supa.rpc_all("get_mw_short_interest", SI.COLS, {"p_company": None, "p_from": start},
                                 order=SI.ORDER, tag="mw:aluguel:janela")
        self.reads.append(f"janela do aluguel desde {start} ({len(rows)} linhas)")
        new = self._group(rows)
        for co in cos:
            cur = series.get(co)
            if cur is None:
                continue
            got = new.get(co, {})
            for t in set(cur) | set(got):
                merged = [r for r in cur.get(t, []) if r[0] < start] + got.get(t, [])
                if merged:
                    cur[t] = merged
                else:
                    cur.pop(t, None)

    def _verify(self, series: dict, cos: list[str], fresh: set) -> None:
        """A soma não fechou depois da janela: contagem por empresa e releitura só de quem não bate
        (backfill de dias antigos, empresa reexibida com cópia velha)."""
        fixed = []
        for co in cos:
            if co in fresh:
                continue
            _rows, n = self.supa.rpc_count("get_mw_short_interest", "ref_date",
                                           {"p_company": co, "p_from": None},
                                           tag="mw:aluguel:contagem", order="ref_date.desc", limit=1)
            if n is not None and n != _rows_total(series.get(co) or {})[0]:
                series[co] = self._read_company(co)
                fresh.add(co)
                fixed.append(co)
        self.notes.append("aluguel conferido por empresa" + (f" (relido: {', '.join(fixed)})"
                                                               if fixed else ""))

    # ── recompras ────────────────────────────────────────────────────────────────────────────
    def buybacks(self) -> dict:
        """{items}: os programas das empresas visíveis, na ordem da RPC (leitura completa nas marcas)."""
        if "buybacks" in self._memo:
            return self._memo["buybacks"]
        vis = self._visible()
        st = self.st["buybacks"]
        if st.get("items") is None and not self.rebuild:
            doc, _why = self._copy("mw-buybacks.json")
            items = doc.get("items") if doc else None
            if isinstance(items, list):
                st.update(items=[it for it in items if isinstance(it, dict) and it.get("company")],
                          at=self._stamp())
                self.seeds.append("recompras")
        if self.rebuild or st.get("items") is None or self._before_mark(st.get("at")):
            rows = self.supa.rpc_all("get_mw_buybacks", BB.COLS, {"p_company": None}, order=BB.ORDER,
                                     tag="mw:recompras")
            st.update(items=[x for x in (BB.item(r) for r in rows) if x], at=self._stamp())
            self.reads.append(f"recompras ({len(rows)})")
        out = {"items": [it for it in st["items"] if it.get("company") in vis]}
        self._memo["buybacks"] = out
        return out

    # ── insiders ─────────────────────────────────────────────────────────────────────────────
    def insiders(self) -> dict:
        """{from, updatedAt, rows}: a janela de 14 meses inteira (linhas nas colunas do arquivo)."""
        if "insiders" in self._memo:
            return self._memo["insiders"]
        vis = self._visible()
        st = self.st["insiders"]
        start = IN.window_from(self.now.date())
        if self.rebuild:
            st.update(rows=None, updatedAt=None, probe=None, probeAt=None)
        if st.get("rows") is not None and st.get("from") != start:
            # a janela andou (virou o mês): os meses que saíram caem sem ler o banco; a sonda confere
            st.update(rows=[r for r in st["rows"] if (r[1] or "") >= start], probeAt=None)
            self.notes.append(f"insiders: janela agora desde {start}")
        st["from"] = start
        if st.get("rows") is None and not self.rebuild:
            doc, why = self._copy("mw-insiders.json")
            got = self._insiders_from_copy(doc, start) if doc is not None else None
            if got is not None:
                st.update(rows=got[0], updatedAt=got[1], probe=None, probeAt=None)
                self.seeds.append("insiders")
            else:
                self._need_db(why, "insiders")
        if st.get("rows") is None or not self._fresh(st.get("probeAt"), INSIDERS_PROBE_MAX_AGE_S):
            probe = None
            if st.get("rows") is not None:
                rows, total = self.supa.rpc_count("get_mw_insiders", "updated_at",
                                                  {"p_company": None, "p_from": start},
                                                  tag="mw:insiders:sonda",
                                                  order="updated_at.desc.nullslast", limit=1)
                probe = [total, iso_utc(rows[0].get("updated_at")) if rows else None]
                self.probes.append("insiders")
            if probe is None or probe != [len(st["rows"]), st.get("updatedAt")]:
                rows = self.supa.rpc_all("get_mw_insiders", IN.COLS, {"p_company": None, "p_from": start},
                                         order=IN.ORDER, tag="mw:insiders")
                got = [x for x in (IN.row(r) for r in rows) if x]
                st.update(rows=got, updatedAt=IN.stamp(rows))
                self.reads.append(f"insiders ({len(rows)} linhas desde {start})")
                probe = [len(rows), st["updatedAt"]]
            st.update(probe=probe, probeAt=self._stamp())
        out = {"from": start, "updatedAt": st.get("updatedAt"),
               "rows": [r for r in st["rows"] if r and r[0] in vis]}
        self._memo["insiders"] = out
        return out

    @staticmethod
    def _insiders_from_copy(doc: dict, start: str) -> tuple[list, str | None] | None:
        if doc.get("columns") != IN.COLUMNS or not isinstance(doc.get("rows"), list):
            return None
        rows = [list(r) for r in doc["rows"] if isinstance(r, list) and len(r) == len(IN.COLUMNS)
                and isinstance(r[0], str) and isinstance(r[1], str)]
        return [r for r in rows if r[1] >= start], iso_utc(doc.get("updatedAt"))

    # ── comunicados ──────────────────────────────────────────────────────────────────────────
    def filings(self) -> dict:
        """{updatedAt, items}: os 600 mais recentes das empresas visíveis."""
        if "filings" in self._memo:
            return self._memo["filings"]
        vis = self._visible()
        st = self.st["filings"]
        if self.rebuild:
            st.update(items=None, updatedAt=None, probe=None, probeAt=None)
        if st.get("items") is None and not self.rebuild:
            doc, why = self._copy("mw-filings.json")
            got = self._filings_from_copy(doc) if doc is not None else None
            if got is not None:
                st.update(items=got[0], updatedAt=got[1], probe=None, probeAt=None)
                self.seeds.append("comunicados")
            else:
                self._need_db(why, "comunicados")
        if st.get("items") is None or not self._fresh(st.get("probeAt"), PROBE_MAX_AGE_S):
            rows, total = self.supa.rpc_count("get_mw_filings", "updated_at",
                                              {"p_company": None, "p_limit": FL.RPC_LIMIT},
                                              tag="mw:comunicados:sonda",
                                              order="updated_at.desc.nullslast", limit=1)
            probe = [total, iso_utc(rows[0].get("updated_at")) if rows else None]
            self.probes.append("comunicados")
            want = [min(total, FL.LIMIT) if total is not None else None, probe[1]]
            if st.get("items") is None:
                self._filings_full(probe)
            elif [len(st["items"]), st.get("updatedAt")] != want:
                if st.get("updatedAt"):
                    self._filings_since(st["updatedAt"], probe)
                if [len(st["items"]), st.get("updatedAt")] != want:
                    self._filings_full(probe)
            st.update(probe=probe, probeAt=self._stamp())
        out = {"updatedAt": st.get("updatedAt"),
               "items": [it for it in st["items"] if it.get("company") in vis]}
        self._memo["filings"] = out
        return out

    def _filings_full(self, probe: list) -> None:
        rows = self.supa.rpc_all("get_mw_filings", FL.COLS, {"p_company": None, "p_limit": FL.LIMIT},
                                 order=FL.ORDER, tag="mw:comunicados")
        items = FL.sort_items([x for x in (FL.item(r) for r in rows) if x])[:FL.LIMIT]
        self.st["filings"].update(items=items, updatedAt=probe[1])
        self.reads.append(f"comunicados ({len(rows)})")

    def _filings_since(self, base: str, probe: list) -> None:
        since = iso_utc(datetime.strptime(base, _ISO).replace(tzinfo=timezone.utc) - FILINGS_MARGIN)
        rows = self.supa.rpc_all("get_mw_filings", FL.COLS, {"p_company": None, "p_limit": FL.RPC_LIMIT},
                                 order=FL.ORDER, tag="mw:comunicados:novos", updated_at=f"gte.{since}")
        by = {it["id"]: it for it in self.st["filings"]["items"]}
        for x in (FL.item(r) for r in rows):
            if x:
                by[x["id"]] = x
        self.st["filings"].update(items=FL.sort_items(list(by.values()))[:FL.LIMIT],
                                  updatedAt=probe[1])
        self.reads.append(f"comunicados novos ({len(rows)})")

    @staticmethod
    def _filings_from_copy(doc: dict) -> tuple[list, str | None] | None:
        items = doc.get("items")
        if not isinstance(items, list):
            return None
        ok = [it for it in items if isinstance(it, dict) and isinstance(it.get("id"), int)
              and isinstance(it.get("company"), str) and isinstance(it.get("publishedAt"), str)]
        return FL.sort_items(ok)[:FL.LIMIT], iso_utc(doc.get("updatedAt"))

    # ── a família mw-short-<empresa>.json ────────────────────────────────────────────────────
    def family_plan(self, explicit=(), auto: bool = True, force: bool = False) -> list[str]:
        """As empresas cujo mw-short-<empresa>.json montar nesta execução: no envio, as visíveis na
        vez (COLD, cada uma pelo seu último build — as que ficaram na fila do teto continuam na vez;
        com --force, todas); no dry/--out, todas; mais as pedidas pelo nome. No envio, o registro das
        que sumiram (ocultas) é esquecido (senão ficariam "na vez" para sempre)."""
        picks: list[str] = []
        if auto:
            files = self.src.state.setdefault("files", {})
            vis = []
            for c in self.companies():
                name = file_name(c["ticker"])
                if name is None:
                    self.notes.append(f"{c['ticker']}: sem nome de arquivo "
                                      f"({names.id_problem(c['ticker'])})")
                    continue
                vis.append(name)
                if (self.mode == "send" and not force
                        and not is_due(COLD, (files.get(name) or {}).get("builtAt"), self.now)):
                    continue
                picks.append(c["ticker"])
            if self.mode == "send":
                for n in [n for n in files if company_from_file(n) and n not in vis]:
                    del files[n]
        for co in explicit:
            if co not in picks:
                picks.append(co)
        self.planned = picks
        return list(picks)

    # ── log ──────────────────────────────────────────────────────────────────────────────────
    def end_note(self) -> str | None:
        """Uma linha para o log — só quando algo foi sondado, lido, semeado ou espera."""
        if not (self.probes or self.reads or self.seeds or self.notes):
            return None
        parts = ["market watch"]
        if self.probes:
            parts.append("sondas: " + ", ".join(self.probes))
        if self.seeds:
            parts.append("da cópia publicada: " + ", ".join(self.seeds))
        if self.reads:
            parts.append("lido do banco: " + ", ".join(self.reads))
        parts += self.notes
        return " · ".join(parts)
