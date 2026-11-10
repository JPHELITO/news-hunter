"""Família `history-<id>.json` + `history-index.json` (PLANO, Apêndice F.1–F.2; pesquisa 2-dados
§4.3–4.6): o histórico diário COMPLETO de cada papel da Market, para os ranges de mais de 1 ano.

A SÉRIE é `quote_history.daily` tal qual (1 ponto por dia UTC; epoch = abertura do pregão;
fechamento ajustado por split): 6 a 280 KB crus por papel, os 55 ≈ 7,3 MB / 2,43 MB gzip. Reler a
longa de todos todo dia gastaria metade do orçamento do Supabase. Por isso:

MANUTENÇÃO (pesquisa §4.3)
  • A série leve (`quotes.daily`) é REDUÇÃO EXATA da longa: as duas são gravadas na MESMA transação
    (`append_quote_history`), e o trecho ≥ `v_cut` (= `daily_updated_at` − 372 d) da leve É o da
    longa. O dia a dia = a cópia PUBLICADA (Mindassets, zero Supabase) com `ts < v_cut` + o trecho
    `ts ≥ v_cut` da série leve, que o cache do diário da 1a já tem (`junction`). Por `ts`, não por
    dia: o pregão da ASX que abre à 00:00 UTC do mesmo dia do `v_cut` (mas antes dele) é da longa e
    não do trecho diário — cortar por dia o perderia. O resultado é conferido com a sonda (pontos,
    primeiro e último carimbo, ordem estrita); não fechou → leitura inteira daquele papel.
  • Split (`lastSplitTs` novo), `firstTs` diferente ou papel ainda não publicado (carga) → leitura
    inteira (`quote_history?ticker=eq.X`, 2–98 KB gzip). A de papel JÁ publicado (split, conserto)
    sai na hora — é rara (~1 por mês) e conserta um degrau visível —; a da carga espera o orçamento.
  • QUAIS papéis: a sonda de metadados (`select=ticker,points,first_ts,last_ts,last_split_ts,
    updated_at`, ~1,75 KB) contra o REGISTRO do que está publicado (semeado do history-index.json
    publicado). A sonda só roda quando a série leve mudou (o cache do diário anda ~1×/noite por
    papel) ou a cada 6 h. Fim de semana: o append roda sem ponto novo, os metadados não mudam → nada
    a montar, nada a enviar.
  • O `updated_at` da longa e o `daily_updated_at` da leve são o MESMO `now()` da transação: papel
    cujo cache diário é de outro append que o da sonda espera a sonda seguinte (nunca costura duas
    versões diferentes).

ENVIO: no máximo 2 por execução (o Feeder pega 10 revisões por ~60 s de TODOS os times: uma rajada
nossa atrasaria os outros), com a família por último no COLD (o orçamento de 90 s corta ela antes).
Em regime: ~55 envios por dia útil, depois do append noturno (00–03 UTC), 2 a cada ciclo de 5 min.

CARGA INICIAL (O28): os papéis ainda não publicados, na ordem do universo (os 20 da cobertura, os
índices, os pares), a no máximo 2 por execução E no ritmo de ~1 MB de Supabase por dia. O estado
morre com o runner (~5h30, ~4,4 por dia), então um contador "por dia" num estado não veria os outros
runners. Por isso o orçamento ACUMULA em tempo de runner: 1 MB a cada 24 h (≈ 12 B/s, ~240 KB por
runner), começa vazio em cada runner, e só a leitura inteira (carga, split, conserto) gasta dele.
Como os runners do loop não se sobrepõem, a soma num dia não passa de ~1 MB. Os 55 (2,43 MB gz)
levam ~2,5 dias. O gasto real é contado (corpo + cabeçalhos) e o do dia UTC aparece no log.

MINDASSETS PARADO: sem o history-index.json publicado E com o Mindassets parado (live.json velho ou
fora do ar), a família espera — tratar o 404 como "nada publicado" refaria a carga inteira, e um
índice parcial publicado depois faria o mesmo na partida a frio seguinte. Pelo mesmo motivo, a cópia
de um papel que não vem porque o Mindassets não responde (ou está parado) faz o papel ESPERAR; só a
cópia que falta de fato (404 com ele publicando) ou que não fecha vai pela leitura inteira.
Um papel cuja leitura inteira falhou volta à fila depois de 1 h (erro persistente não vira leitura a
cada 5 min).

O ÍNDICE: só o que já está publicado (registro + o que esta execução enviou), na ordem do universo.
Vai fora da cadência quando esta execução publicou um papel NOVO; a atualização diária de um papel
já publicado espera o COLD (o `lastTs` do índice é informativo — o arquivo do papel é a fonte).
No --out: o que está na pasta.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from . import names
from .encode import epoch_s, iso_utc, points
from .exporters.quotes_monthly import CUT_S, epoch_of
from .exporters.reference import MARKET_UNIVERSE

log = logging.getLogger(__name__)

PREFIX = "history"
INDEX_NAME = "history-index.json"
UNIVERSE = tuple(MARKET_UNIVERSE)
_UNI = frozenset(UNIVERSE)

PER_RUN = 2                         # envios da família por execução (O28)
LOAD_BYTES_PER_DAY = 1024 * 1024    # leitura inteira: ~1 MB por 24 h de runner
EST_BYTES_PER_POINT = 7             # medido em 09/11: 6,0 (VALE3) a 7,1 (AUGO) B/ponto gzip
EST_HEADER_BYTES = 800
EST_MIN_BYTES = 2048
PROBE_MAX_AGE_S = 6 * 3600          # sonda de segurança, mesmo sem a série leve mudar
RETRY_FAILED_S = 3600               # papel cuja leitura inteira falhou volta à fila depois disto
PROBE_COLS = "ticker,points,first_ts,last_ts,last_split_ts,updated_at"
LONG_COLS = "ticker,daily,points,first_ts,last_ts,last_split_ts,updated_at"
_ISO = "%Y-%m-%dT%H:%M:%SZ"


def file_name(ticker) -> str | None:
    """`history-<fileId>.json` de um papel do universo (None fora dele ou id recusado)."""
    return names.family_file(PREFIX, ticker) if ticker in _UNI else None


def ticker_from_file(name) -> str | None:
    """O papel de um nome da família — só papéis do universo (o `history-index.json` não é um)."""
    t = names.family_ident(PREFIX, name)
    return t if t in _UNI else None


def probe_meta(row: dict) -> dict | None:
    """Uma linha da sonda (ou da leitura inteira) → {points, firstTs, lastTs, lastSplitTs,
    updatedAt}; None se não dá para usar. `last_split_ts` 0 (o banco grava 0 = nenhum) → None."""
    n = row.get("points")
    if isinstance(n, bool) or not isinstance(n, int) or n < 1:
        return None
    first, last = epoch_s(row.get("first_ts")), epoch_s(row.get("last_ts"))
    if first is None or last is None:
        return None
    return {"points": n, "firstTs": first, "lastTs": last,
            "lastSplitTs": epoch_s(row.get("last_split_ts")), "updatedAt": iso_utc(row.get("updated_at"))}


def _key(m: dict) -> tuple:
    return (m.get("points"), m.get("firstTs"), m.get("lastTs"), m.get("lastSplitTs"))


def junction(published_pts, light_pts, at) -> list[list] | None:
    """A série longa de hoje = cópia publicada com `ts < v_cut` + série leve com `ts ≥ v_cut`
    (`v_cut` = `at` − 372 d, `at` = `daily_updated_at` da série leve). None sem carimbo."""
    cut = epoch_of(at)
    if cut is None:
        return None
    cut -= CUT_S
    return ([list(p) for p in published_pts or [] if p[0] < cut]
            + [list(p) for p in light_pts or [] if p[0] >= cut])


def fits(pts, meta: dict) -> bool:
    """A série bate com os metadados da sonda (contagem, pontas) e é estritamente crescente?"""
    if not pts or len(pts) != meta.get("points"):
        return False
    if pts[0][0] != meta.get("firstTs") or pts[-1][0] != meta.get("lastTs"):
        return False
    return all(a[0] < b[0] for a, b in zip(pts, pts[1:]))


def estimate(meta: dict) -> int:
    """Bytes de Supabase (gzip + cabeçalhos) de uma leitura inteira, pelo nº de pontos."""
    return max(EST_MIN_BYTES, meta.get("points", 0) * EST_BYTES_PER_POINT + EST_HEADER_BYTES)


def _doc_meta(doc: dict) -> dict | None:
    """Metadados de um history-<id>.json (cópia publicada ou arquivo do --out)."""
    pts = doc.get("points")
    if isinstance(pts, bool) or not isinstance(pts, int) or pts < 1:
        return None
    first, last = epoch_s(doc.get("firstTs")), epoch_s(doc.get("lastTs"))
    if first is None or last is None:
        return None
    return {"points": pts, "firstTs": first, "lastTs": last,
            "lastSplitTs": epoch_s(doc.get("lastSplitTs")), "updatedAt": iso_utc(doc.get("updatedAt"))}


class HistoryStore:
    """A família de UMA execução. `explicit` = os papéis do `--history` (lista ou "all")."""

    def __init__(self, src, explicit=None):
        self.src = src
        self.supa = src.supa
        self.pub = src.published
        self.mode = src.mode
        self.now = src.now
        st = src.state["history"]
        st.setdefault("seeded", False)
        st.setdefault("published", {})
        st.setdefault("probe", None)
        st.setdefault("needLong", [])
        st.setdefault("bucket", {"since": None, "spent": 0, "day": None, "daySpent": 0})
        self.st = st
        self.copies = not src.rebuild           # --rebuild: nada de junção com a cópia publicada
        if explicit == "all":
            self.explicit_arg = list(UNIVERSE)
        else:
            self.explicit_arg = [t for t in (explicit or []) if t in _UNI]
        self.prepared: dict[str, dict] = {}
        self.run_reg: dict[str, dict] = {}
        self.registry_new = False               # esta execução publicou um papel novo (índice urgente)
        self.planned: list[str] = []
        self.pending = (0, 0)                   # (atualizações, carga) na última fila
        self.waiting: tuple[str, int] | None = None
        self.notes: list[str] = []
        self.probed = False                     # a sonda rodou nesta execução

    # ── atalhos ──────────────────────────────────────────────────────────────────────────────
    def _spent(self) -> int:
        return self.supa.wire_bytes + self.supa.header_bytes

    def _age_s(self, stamp) -> float | None:
        iso = iso_utc(stamp)
        if not iso:
            return None
        return (self.now - datetime.strptime(iso, _ISO).replace(tzinfo=timezone.utc)).total_seconds()

    def _light(self, t: str) -> dict | None:
        """A entrada do cache do diário da 1a (série leve) — do estado, sem ler o banco."""
        e = (self.src.state["daily"].get("rows") or {}).get(t)
        return e if isinstance(e, dict) else None

    # ── registro (o que está publicado) ──────────────────────────────────────────────────────
    def _seed_registry(self) -> bool:
        """O registro nasce do history-index.json PUBLICADO. 404 com o Mindassets publicando =
        nada publicado ainda (carga). 404 com ele parado, fora do ar ou ilegível = espera (tenta
        de novo na próxima execução). Sem Mindassets configurado (testes, banco local) = vazio."""
        st = self.st
        if st["seeded"]:
            return True
        if self.mode == "out":
            return False
        # o que ESTA execução já registrou (pedido pelo nome antes da semente) prevalece
        mine = dict(st.get("published") or {})
        if self.pub is None:
            st.update(seeded=True, published=mine, indexUpdatedAt=None)
            return True
        doc = self.pub.get(INDEX_NAME)
        if isinstance(doc, dict) and doc.get("schemaVersion") == 1 and isinstance(doc.get("items"), list):
            reg = {}
            for it in doc["items"]:
                t = it.get("ticker") if isinstance(it, dict) else None
                m = _doc_meta(it) if t in _UNI and it.get("file") == file_name(t) else None
                if m:
                    m["updatedAt"] = None
                    reg[t] = {"file": file_name(t), **m}
            st.update(seeded=True, published={**reg, **mine},
                      indexUpdatedAt=iso_utc(doc.get("updatedAt")))
            return True
        if self.pub.status(INDEX_NAME) == "ausente" and self.pub.healthy(self.now):
            st.update(seeded=True, published=mine, indexUpdatedAt=None)
            return True
        return False

    # ── a fila desta execução ────────────────────────────────────────────────────────────────
    def plan(self, explicit=(), auto: bool = True) -> list[str]:
        """Os papéis a montar nesta execução: os da fila do loop (só em send/dry, no máximo
        PER_RUN, atualizações antes da carga) + os pedidos pelo nome / --history (sempre)."""
        ex = []
        for t in list(explicit) + self.explicit_arg:
            if t in _UNI and t not in ex:
                ex.append(t)
        picks: list[str] = []
        if self.mode in ("send", "dry"):
            seeded = self._seed_registry()          # antes de qualquer envio desta execução
            if auto and seeded:
                self._maybe_probe()
                picks = self._pick(set(ex))
            elif auto:
                self.notes.append("aguardando o history-index.json publicado (Mindassets parado)")
        self.planned = picks + [t for t in ex if t not in picks]
        return list(self.planned)

    def _fingerprint(self) -> str:
        rows = self.src.state["daily"].get("rows") or {}
        pairs = sorted((t, (rows.get(t) or {}).get("at")) for t in UNIVERSE if t in rows)
        return hashlib.sha1(json.dumps(pairs).encode("utf-8")).hexdigest()[:16]

    def _probe(self) -> dict:
        """A sonda de metadados da quote_history (uma chamada, as ~60 linhas)."""
        self.src.daily()          # a série leve em dia ANTES da sonda (o mesmo append dos dois lados)
        meta = {}
        for r in self.supa.select("quote_history", PROBE_COLS, tag="history:sonda"):
            t, m = r.get("ticker"), probe_meta(r)
            if isinstance(t, str) and m:
                meta[t] = m
        self.st["probe"] = {"at": self.src.generated_at, "fp": self._fingerprint(), "rows": meta}
        self.probed = True
        return meta

    def _maybe_probe(self) -> None:
        pr = self.st.get("probe") if isinstance(self.st.get("probe"), dict) else None
        if pr is not None and not self.src.rebuild and pr.get("fp") == self._fingerprint():
            age = self._age_s(pr.get("at"))
            if age is not None and 0 <= age < PROBE_MAX_AGE_S:
                return                              # a série leve não andou: a longa também não
        self._probe()

    def _rows(self) -> dict:
        pr = self.st.get("probe")
        return (pr.get("rows") or {}) if isinstance(pr, dict) else {}

    def _available(self) -> float:
        """O orçamento da leitura inteira que já acumulou neste runner (bytes)."""
        b = self.st["bucket"]
        if not b.get("since"):
            b.update(since=self.src.generated_at, spent=0)
        age = self._age_s(b["since"]) or 0.0
        return max(0.0, age) * LOAD_BYTES_PER_DAY / 86400 - (b.get("spent") or 0)

    def _debit(self, n: int) -> None:
        b = self.st["bucket"]
        if not b.get("since"):
            b.update(since=self.src.generated_at, spent=0)
        b["spent"] = (b.get("spent") or 0) + n
        day = self.now.date().isoformat()
        if b.get("day") != day:
            b.update(day=day, daySpent=0)
        b["daySpent"] = (b.get("daySpent") or 0) + n

    def _pick(self, exclude: set) -> list[str]:
        rows = self._rows()
        reg = self.st["published"]
        updates, loads = [], []
        for t in UNIVERSE:
            m = rows.get(t)
            if m is None or t in exclude or file_name(t) is None:
                continue
            r = reg.get(t)
            if r is None:
                loads.append(t)
            elif _key(r) != _key(m):
                updates.append(t)
        self.pending = (len(updates), len(loads))
        avail = self._available()
        need_long = set(self.st.get("needLong") or [])
        failed = self.st.setdefault("failedAt", {})
        picks: list[str] = []
        for t in updates + loads:
            if len(picks) >= PER_RUN:
                break
            age = self._age_s(failed.get(t))
            if age is not None and 0 <= age < RETRY_FAILED_S:
                continue                # a leitura inteira falhou há pouco: tenta de hora em hora
            m, r = rows[t], reg.get(t)
            if (r is not None and t not in need_long and self.copies
                    and r.get("firstTs") == m["firstTs"] and r.get("lastSplitTs") == m["lastSplitTs"]):
                e = self._light(t)
                if e is not None and e.get("at") != m.get("updatedAt"):
                    continue            # a série leve é de outro append: espera a próxima sonda
                doc, why = self._copy(t)
                if why == "fora":
                    self.notes.append(f"{t}: cópia publicada indisponível (Mindassets), espera")
                    continue            # nunca troca a junção pela longa por causa do Mindassets
                got = self._junction(t, m, e, doc) if e is not None and doc is not None else None
                if got is not None:
                    self.prepared[t] = got
                    picks.append(t)
                    continue
                need_long.add(t)        # sem junção (cópia que falta de fato, não fecha, sem série leve)
                self.st["needLong"] = sorted(need_long)
                self.notes.append(f"{t}: sem junção, leitura inteira")
            cost = estimate(m)
            if r is None and cost > avail:
                self.waiting = (t, cost)
                break                   # a carga anda na ordem do universo, no ritmo do orçamento
            # papel já publicado (split, conserto, junção que não fechou): relê já — é raro e
            # conserta um degrau visível; o gasto entra no mesmo orçamento (a carga desacelera)
            avail -= cost
            self.prepared[t] = {"long": True}
            picks.append(t)
        return picks

    # ── a série de um papel ──────────────────────────────────────────────────────────────────
    def _copy(self, t: str) -> tuple[dict | None, str | None]:
        """(cópia publicada, motivo de não ter): "falta" = 404 com o Mindassets publicando (ou
        --rebuild, sem Mindassets) → leitura inteira; "fora" = o Mindassets não respondeu ou está
        parado → espera (relê-la por isso custaria a longa de todos de uma vez)."""
        if not self.copies or self.pub is None:
            return None, "falta"
        name = file_name(t)
        doc = self.pub.get(name)
        if doc is not None:
            return doc, None
        if self.pub.status(name) == "ausente" and self.pub.healthy(self.now):
            return None, "falta"
        return None, "fora"

    def _junction(self, t: str, m: dict, e: dict, doc: dict) -> dict | None:
        """Cópia publicada + série leve, conferida com a sonda; None se não deu (vai pela longa)."""
        if not isinstance(doc, dict) or doc.get("schemaVersion") != 1 or doc.get("ticker") != t:
            return None
        if epoch_s(doc.get("firstTs")) != m["firstTs"] or epoch_s(doc.get("lastSplitTs")) != m["lastSplitTs"]:
            return None
        merged = junction(points(doc.get("pts")), e.get("pts"), e.get("at"))
        if merged is None or not fits(merged, m):
            return None
        return {"pts": merged, "lastSplitTs": m["lastSplitTs"], "updatedAt": e.get("at")}

    def _explicit_junction(self, t: str) -> dict | None:
        """Pedido pelo nome / --history: a junção se houver cópia publicada (sonda só então)."""
        doc, _why = self._copy(t)
        if doc is None:
            return None
        e = (self.src.daily() or {}).get(t)
        pr = self.st.get("probe")
        fresh = isinstance(pr, dict) and pr.get("fp") == self._fingerprint()
        rows = self._rows() if fresh else self._probe()
        m = rows.get(t)
        if m is None or not isinstance(e, dict) or e.get("at") != m.get("updatedAt"):
            return None
        return self._junction(t, m, e, doc)

    def _long(self, t: str) -> dict:
        """A série inteira de um papel, lida da quote_history (gasta do orçamento da carga). Falhou
        → o papel só volta à fila do loop depois de 1 h (um erro que persiste não vira uma leitura
        a cada 5 min)."""
        before = self._spent()
        try:
            rows = self.supa.select("quote_history", LONG_COLS, tag="history:longa", ticker=f"eq.{t}")
            r = next((x for x in rows if x.get("ticker") == t), None)
            pts = points((r or {}).get("daily"))
            if not pts:
                raise RuntimeError(f"{t}: sem histórico na quote_history")
        except Exception:
            self.st.setdefault("failedAt", {})[t] = self.src.generated_at
            raise
        finally:
            if self.mode in ("send", "dry"):
                self._debit(self._spent() - before)
        m = probe_meta(r)
        if m is None or not fits(pts, m):
            log.warning("histórico %s: metadados da quote_history não batem com a série (vai a série)", t)
        self.st["needLong"] = [x for x in self.st.get("needLong") or [] if x != t]
        self.st.setdefault("failedAt", {}).pop(t, None)
        return {"pts": pts, "lastSplitTs": epoch_s(r.get("last_split_ts")),
                "updatedAt": iso_utc(r.get("updated_at"))}

    def series(self, t: str) -> dict:
        """{pts, lastSplitTs, updatedAt} do papel: o que a fila preparou (junção) ou a longa."""
        p = self.prepared.get(t)
        if p is not None and "pts" in p:
            return p
        if p is None:
            got = self._explicit_junction(t)
            if got is not None:
                self.prepared[t] = got
                return got
        got = self._long(t)
        self.prepared[t] = got
        return got

    # ── depois do envio ──────────────────────────────────────────────────────────────────────
    def after(self, t: str, obj: dict, action: str) -> None:
        """Chamado pelo run.py depois de cada arquivo: o que foi de fato publicado entra no registro."""
        meta = {"file": file_name(t), "points": obj["points"], "firstTs": obj["firstTs"],
                "lastTs": obj["lastTs"], "lastSplitTs": obj["lastSplitTs"],
                "updatedAt": obj.get("updatedAt")}
        if self.mode == "send" and action in ("enviado", "igual"):
            reg = self.st["published"]
            self.registry_new |= t not in reg
            reg[t] = meta
        elif self.mode == "dry" and action in ("enviaria", "igual"):
            self.registry_new |= t not in self.st["published"]
            self.run_reg[t] = meta

    # ── índice ───────────────────────────────────────────────────────────────────────────────
    def index(self) -> dict:
        """{updatedAt, items} do history-index.json: o registro (+ o que o dry enviaria; no
        --out, a pasta). Registro desconhecido (Mindassets parado) → erro: índice parcial nunca."""
        if self.mode == "out":
            reg = self._out_registry()
            stamps = []
        else:
            if not self._seed_registry():
                raise RuntimeError("history-index: o índice publicado não pôde ser lido e o "
                                   "Mindassets está parado — não publica índice parcial")
            reg = {**self.st["published"], **self.run_reg}
            stamps = [self.st.get("indexUpdatedAt")]
        items = []
        for t in UNIVERSE:
            m = reg.get(t)
            if m:
                items.append({"ticker": t, "file": file_name(t), "firstTs": m["firstTs"],
                              "lastTs": m["lastTs"], "points": m["points"],
                              "lastSplitTs": m["lastSplitTs"]})
        stamps += [m.get("updatedAt") for m in reg.values()]
        stamps = [s for s in stamps if s]
        return {"updatedAt": max(stamps, default=None), "items": items}

    def _out_registry(self) -> dict:
        reg = {}
        out = Path(self.src.out_dir) if self.src.out_dir else None
        if out is None or not out.is_dir():
            return reg
        for p in sorted(out.glob(f"{PREFIX}-*.json")):
            t = ticker_from_file(p.name)
            if t is None:
                continue
            try:
                raw = p.read_bytes()
                doc = json.loads(gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw)
            except (OSError, ValueError, EOFError) as e:
                log.warning("--out: %s ilegível (%s) — fora do índice", p.name, type(e).__name__)
                continue
            m = _doc_meta(doc) if isinstance(doc, dict) and doc.get("ticker") == t else None
            if m:
                reg[t] = m
        return reg

    # ── log ──────────────────────────────────────────────────────────────────────────────────
    def end_note(self) -> str | None:
        """Uma linha para o log — só quando a sonda rodou, algo foi montado ou algo espera (o ciclo
        ocioso de 5 em 5 min não repete a mesma linha)."""
        if not (self.probed or self.planned or self.notes):
            return None
        b = self.st.get("bucket") or {}
        upd, loads = self.pending
        parts = [f"histórico: {len(self.st.get('published') or {})} publicado(s)"]
        if self.st.get("seeded") or self.mode == "out":
            parts.append(f"fila {upd} atualização(ões) + {loads} de carga")
        parts.append("nesta execução: " + (", ".join(self.planned) or "nenhum"))
        if self.waiting:
            parts.append(f"esperando orçamento: {self.waiting[0]} (~{self.waiting[1] / 1024:.0f} KB)")
        if self.mode in ("send", "dry") and b.get("since"):
            parts.append(f"carga: {max(0.0, self._available()) / 1024:.0f} KB disponíveis, "
                         f"{(b.get('daySpent') or 0) / 1024:.0f} KB gastos hoje (UTC)")
        parts += self.notes
        return " · ".join(parts)
