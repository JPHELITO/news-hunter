"""Notícias da Onda 1b: o cache do horizonte, a leitura incremental, as varreduras, as semanas e o
índice (PLANO, Apêndice E.1–E.4; estratégia da pesquisa 1b-dados §3.4–3.7).

POR QUE TÃO ELABORADO: `news_articles` não tem `updated_at` nem `created_at` (conferido em 05/11
com `--check`: `published_at`, `found_at` e `take_llm_at` são timestamptz; não há `id`). O único
carimbo de mudança é `take_llm_at` (a IA classificou); a reclassificação do robô e a retirada do
relatório são PATCH sem carimbo; `found_at` é o relógio do RASPADOR (o hunt-playwright insere
minutos depois; lote manual grava `found_at` retroativo). E o orçamento é ≤ 5 MB/dia para o
exportador inteiro, com ~0,75 KB de cabeçalho por chamada.

O CACHE (arquivo próprio `<estado>_news.json`, ao lado do estado principal):
  rows         url → item do contrato (com `reason`), para as semanas COMPLETAS (de `completeFrom`,
               = a semana anterior quando o cache nasceu, até a corrente), mais as linhas
               "atrasadas" (semana antiga, mas achadas no horizonte) e as da janela do news-latest;
  wmFound/wmTake  maior `found_at` e maior `take_llm_at` vistos (marcas d'água);
  sweep        última varredura de 80 h, último dia da varredura do horizonte, hash das regras;
  pending      semana antiga → {url: item | null}: correções a aplicar SOBRE A CÓPIA PUBLICADA;
  weeks        o REGISTRO das semanas publicadas {count, first, last, updatedAt} — semeado do
               news-index.json publicado; só o envio de verdade o altera;
  archive      carga inicial: instantes das cargas (≤ 2 por hora) e bytes gastos (teto por estado);
  audit        o dia da última conferência das semanas antigas.

POR EXECUÇÃO (sync, 1×):
  • partida a frio: semeia das CÓPIAS PUBLICADAS (news-latest + semana anterior + corrente, do
    Mindassets — zero Supabase); sem cópia (1º envio, --rebuild) lê o horizonte do banco UMA vez;
  • incremental (1 chamada): `found_at ≥ wmFound − 30 min` OU `take_llm_at ≥ wmTake − 2 min`;
  • varredura de 80 h (sem carimbo, 1–2 chamadas): na partida a frio, a cada 3 h e +10/+40 min
    depois que as regras do classificador mudam (deploy). Linha que não voltou = saiu do relatório;
    URL nova = lida por inteiro (lotes de 30); campo diferente = atualiza;
  • varredura do horizonte inteiro (semana anterior + corrente): 1×/dia (06 UTC) e na partida a
    frio com semente velha (> 3 h).
SEMANAS (COLD):
  • completas (anterior e corrente): montadas do cache; vão só quando mudaram;
  • antigas: correções do incremental/varredura e da conferência diária aplicadas sobre a cópia
    publicada; a conferência (05 UTC) compara, por semana, a contagem do banco pela data efetiva
    (HEAD, ~0,75 KB) com a das cópias publicadas e relê só a fatia que divergiu;
  • carga inicial desde 2026-w23 aos poucos: no máximo 2 por hora (da mais nova para a mais antiga)
    e até 300 KB de Supabase por estado — medido em 05/11: ~167 KB por semana, então 2 semanas por
    runner, ≈ 1,4 MB/dia com ~4 runners por dia (as 21 semanas antigas em ~2,5 dias, sem passar
    dos 5 MB/dia do exportador inteiro) —, retomando pelo índice publicado.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import logging
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import weeks as W
from .encode import iso_utc
from .exporters.news_common import (INCREMENTAL_COLS, NEWS_COLS, SWEEP_COLS, apply_mutable,
                                    bucket_iso, from_published, item_from_row, plain_iso,
                                    sort_latest, sort_week, week_item)
from .exporters.news_reference import pulse_window_start
from .state import load as _load
from .state import save as _save
from .urls import url_problem

log = logging.getLogger(__name__)

NEWS_STATE_VERSION = 1
LATEST_HOURS = 48
MARGIN_FOUND = timedelta(minutes=30)      # found_at chega fora de ordem (relógio do raspador)
MARGIN_TAKE = timedelta(minutes=2)
SWEEP_EVERY = timedelta(hours=3)
RULES_SWEEPS = (timedelta(minutes=10), timedelta(minutes=40))   # loop ≤ 5 min, playwright ≤ 30
STALE_SEED = timedelta(hours=3)
HORIZON_SWEEP_HOUR = 6                    # [06:00, 07:00) UTC, 1×/dia
AUDIT_HOUR = 5                            # [05:00, 06:00) UTC, 1×/dia
AUDIT_FIX_PER_RUN = 2                     # fatias relidas por execução (~150 KB cada)
ARCHIVE_PER_HOUR = 2
ARCHIVE_STATE_BYTES = 300 * 1024          # teto de Supabase da carga inicial por estado (runner)
URL_BATCH = 30                            # `url=in.(…)` cabe na URL do PostgREST
RULE_FILES = ("news_take_classifier.py", "filter.py", "classify.py", "config.py", "cvm_filings.py")
HUNTER_DIR = Path(__file__).resolve().parents[1] / "hunter"
ISO = "%Y-%m-%dT%H:%M:%SZ"


def news_state_path(main_path) -> Path:
    p = Path(main_path)
    return p.with_name(p.stem + "_news.json")


def empty_news_state() -> dict:
    return {"version": NEWS_STATE_VERSION, "seeded": False, "seededFrom": None, "completeFrom": None,
            "rows": {}, "wmFound": None, "wmTake": None,
            "sweep": {"last": None, "horizonDay": None, "rulesHash": None, "due": [],
                      "staleSeed": False},
            "pending": {}, "dropped": {}, "weeks": {}, "weeksSeeded": False, "indexSeed": {},
            "archive": {"loads": [], "spent": 0}, "audit": {"day": None}}


def load_news(path) -> dict:
    return _load(path, empty=empty_news_state, version=NEWS_STATE_VERSION)


def save_news(path, st: dict) -> bool:
    return _save(path, st)


def _iso(d: datetime) -> str:
    return d.astimezone(timezone.utc).strftime(ISO)


def _dt(s: str) -> datetime:
    return datetime.strptime(s, ISO).replace(tzinfo=timezone.utc)


def _floor_hour(d: datetime) -> datetime:
    return d.replace(minute=0, second=0, microsecond=0)


def _quote(v: str) -> str:
    """Valor entre aspas para `in.(…)` do PostgREST (vírgula, parêntese e aspas no meio da URL)."""
    return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _bucket_label(item: dict) -> str:
    return W.label_of(_dt(bucket_iso(item)))


def _plain_label(item: dict) -> str:
    return W.label_of(_dt(plain_iso(item)))


def rules_hash(hunter_dir: Path = HUNTER_DIR) -> str | None:
    """sha1 das regras que reclassificam as notícias (lidas do disco do runner: o hunt-loop faz
    `git reset --hard` no código novo a cada iteração). None se nenhum arquivo foi lido."""
    h = hashlib.sha1()
    seen = False
    for name in RULE_FILES:
        try:
            h.update((hunter_dir / name).read_bytes())
            seen = True
        except OSError:
            continue
    return h.hexdigest() if seen else None


class NewsStore:
    """As notícias de UMA execução. `mode`: "send" | "dry" | "out" (como o run.py)."""

    def __init__(self, supa, published, state: dict, *, now: datetime, mode: str = "send",
                 rebuild: bool = False, out_dir=None, weeks_arg=None,
                 first_week: str = W.FIRST_WEEK, hunter_dir: Path = HUNTER_DIR):
        self.supa = supa
        self.pub = published
        self.st = state
        self.now = now.astimezone(timezone.utc)
        self.mode = mode
        self.rebuild = rebuild
        self.out_dir = Path(out_dir) if out_dir else None
        self.weeks_arg = weeks_arg
        self.first_week = first_week
        self.hunter_dir = hunter_dir
        self.current = W.label_of(self.now)
        self.previous = W.shift(self.current, -1)
        self.horizon_start = _iso(W.start(self.previous))
        self.latest_cut = _iso(_floor_hour(self.now - timedelta(hours=LATEST_HOURS)))
        self.window_start = _iso(_floor_hour(min(self.now - timedelta(hours=LATEST_HOURS),
                                                 pulse_window_start(self.now))))
        self.dropped: Counter = Counter()
        self.notes: list[str] = []
        self.plan: dict[str, str] = {}
        self.fresh: dict[str, list] = {}
        self.run_reg: dict[str, dict] = {}
        self.registry_changed = False          # esta execução publicou/corrigiu uma semana
        self._synced = False
        self._error: Exception | None = None

    # ── atalhos de estado ───────────────────────────────────────────────────────────────────
    @property
    def rows(self) -> dict:
        return self.st["rows"]

    @property
    def registry(self) -> dict:
        return self.st["weeks"]

    @property
    def complete_from(self) -> str:
        return self.st["completeFrom"] or self.previous

    def _spent(self) -> int:
        return self.supa.wire_bytes + self.supa.header_bytes

    def in_latest(self, item: dict) -> bool:
        return bucket_iso(item) >= self.window_start or item["foundAt"] >= self.latest_cut

    def _pending_op(self, label: str, url: str, value) -> None:
        if label >= self.first_week and label < self.complete_from:
            self.st["pending"].setdefault(label, {})[url] = value

    # ── sincronização (1× por execução) ─────────────────────────────────────────────────────
    def sync(self) -> None:
        """Uma vez por execução. Se falhar (banco fora), TODO arquivo de notícias desta execução
        falha junto — nenhum sai montado de um cache pela metade — e o próximo ciclo tenta de
        novo (as marcas d'água só andam com o que foi de fato lido)."""
        if self._error is not None:
            raise self._error
        if self._synced:
            return
        try:
            self._sync()
        except Exception as e:
            self._error = e
            raise
        self._synced = True

    def _sync(self) -> None:
        st = self.st
        if self.rebuild:
            st.update(rows={}, wmFound=None, wmTake=None, seeded=False, completeFrom=None)
            st["sweep"].update(last=None, horizonDay=None, staleSeed=False)
        self._check_rules()
        if not st["weeksSeeded"] and self.mode != "out":
            self._seed_registry()
        if not st["seeded"]:
            self._seed()
        else:
            self._incremental()
        sw = st["sweep"]
        due = [d for d in sw["due"] if d <= _iso(self.now)]
        if sw["last"] is None or _dt(sw["last"]) <= self.now - SWEEP_EVERY or due:
            self._sweep(self.window_start, "sweep")
            sw["last"] = _iso(self.now)
            sw["due"] = [d for d in sw["due"] if d > _iso(self.now)]
        today = self.now.date().isoformat()
        if sw["staleSeed"] or (self.now.hour == HORIZON_SWEEP_HOUR and sw["horizonDay"] != today):
            self._sweep(self.horizon_start, "horizonte")
            sw["horizonDay"] = today
            sw["staleSeed"] = False
        self._evict()
        drop = ", ".join(f"{k} {n}" for k, n in sorted(self.dropped.items()))
        log.info("notícias: %d no cache (semente %s), %s%s", len(self.rows), st["seededFrom"] or "—",
                 "; ".join(self.notes) or "sem leitura", f" · descartadas: {drop}" if drop else "")
        self._drops_logged = Counter(self.dropped)

    def end_note(self) -> str | None:
        """O que as leituras de semana (carga, conferência, --weeks) descartaram depois da linha
        da sincronização — para o log da execução."""
        extra = self.dropped - getattr(self, "_drops_logged", Counter())
        if not extra:
            return None
        return "notícias (semanas): descartadas: " + ", ".join(f"{k} {n}" for k, n in sorted(extra.items()))

    def _check_rules(self) -> None:
        sw = self.st["sweep"]
        h = rules_hash(self.hunter_dir)
        if h and sw["rulesHash"] and h != sw["rulesHash"]:
            sw["due"] = sorted(set(sw["due"]) | {_iso(self.now + d) for d in RULES_SWEEPS})
            self.notes.append("regras mudaram: varreduras em +10 e +40 min")
        if h:
            sw["rulesHash"] = h

    def _doc(self, name: str) -> dict | None:
        if self.pub is None:
            return None
        doc = self.pub.get(name)
        return doc if isinstance(doc, dict) and doc.get("schemaVersion") == 1 else None

    def _seed_registry(self) -> None:
        """O registro das semanas publicadas nasce do news-index.json PUBLICADO. 404 = nada
        publicado ainda; fora do ar = tenta de novo na próxima execução (sem carga inicial)."""
        if self.pub is None:
            return
        doc = self._doc("news-index.json")
        status = self.pub.status("news-index.json")
        if doc is None and status != "ausente":
            return
        reg = {}
        for w in (doc or {}).get("weeks") or []:
            lab = w.get("week") if isinstance(w, dict) else None
            n = w.get("count") if isinstance(w, dict) else None
            if W.parse(lab or "") and isinstance(n, int) and not isinstance(n, bool):
                reg[lab] = {"count": n, "first": None, "last": None, "updatedAt": None}
        self.st["weeks"] = reg
        self.st["indexSeed"] = {k: (doc or {}).get(k) for k in ("firstDate", "lastDate", "updatedAt")}
        self.st["weeksSeeded"] = True

    def _seed(self) -> None:
        st = self.st
        st["completeFrom"] = self.previous
        latest = cur = prev = None
        if not self.rebuild:
            latest = self._doc("news-latest.json")
            prev = self._doc(W.file_name(self.previous))
            cur = self._doc(W.file_name(self.current))
        # a semana corrente ainda não publicada (começou há pouco) não falta se o news-latest
        # publicado já cobria o começo dela
        cur_ok = cur is not None or (latest is not None and isinstance(latest.get("windowStart"), str)
                                     and latest["windowStart"] <= _iso(W.start(self.current)))
        if latest is not None and prev is not None and cur_ok:
            rows = {}
            for doc in (prev, cur):
                for obj in (doc or {}).get("items") or []:
                    it = from_published(obj)
                    if it:
                        rows[it["url"]] = it
            for obj in latest.get("items") or []:
                it = from_published(obj)
                if it:
                    rows[it["url"]] = it
            st["rows"] = rows
            st["wmFound"] = max((i["foundAt"] for i in rows.values()), default=None) \
                or _iso(self.now - timedelta(hours=LATEST_HOURS))
            gen = iso_utc(latest.get("generatedAt")) or _iso(self.now)
            st["wmTake"] = _iso(_dt(gen) - timedelta(minutes=10))
            st["sweep"]["last"] = None                      # partida a frio: varre 80 h
            st["sweep"]["staleSeed"] = _dt(gen) < self.now - STALE_SEED
            st["seededFrom"] = "publicada"
            st["seeded"] = True
            self.notes.append(f"semente publicada {len(rows)}")
            self._incremental()
            return
        self._read_horizon()

    def _read_horizon(self) -> None:
        """Sem cópia publicada utilizável (1º envio, --rebuild): o horizonte inteiro do banco."""
        st = self.st
        hs = self.horizon_start
        t0 = self._spent()
        got = self.supa.select_all("news_articles", INCREMENTAL_COLS, order="found_at.asc,url.asc",
                                   tag="news:horizonte", include_in_report="eq.true",
                                   **{"or": f"(published_at.gte.{hs},found_at.gte.{hs})"})
        st["rows"] = {}
        for r in got:
            self._upsert(r)
        self._advance(got)
        st["wmFound"] = st["wmFound"] or _iso(self.now)
        st["wmTake"] = st["wmTake"] or st["wmFound"]
        st["sweep"]["last"] = _iso(self.now)
        st["sweep"]["horizonDay"] = self.now.date().isoformat()
        st["sweep"]["staleSeed"] = False
        st["seededFrom"] = "banco"
        st["seeded"] = True
        self.notes.append(f"horizonte do banco {len(got)} ({(self._spent() - t0) / 1024:.1f} KB)")

    def _advance(self, got: list[dict]) -> None:
        """Avança as marcas d'água — nunca além de agora (carimbo do futuro esconderia linhas)."""
        now = _iso(self.now)
        st = self.st
        for col, key in (("found_at", "wmFound"), ("take_llm_at", "wmTake")):
            vals = [v for v in (iso_utc(r.get(col)) for r in got) if v and v <= now]
            if vals:
                st[key] = max([v for v in (st[key], max(vals)) if v])

    def _upsert(self, row: dict) -> bool:
        item, prob = item_from_row(row)
        url = row.get("url").strip() if isinstance(row.get("url"), str) else None
        if item is None:
            self.dropped[prob] += 1
            if url:
                if not prob.startswith("url "):    # (link recusado sai de graça: nem é guardado)
                    self.st["dropped"][url] = prob     # a varredura não paga por ela de novo
                if url in self.rows:
                    self._remove(url)
            return False
        self.st["dropped"].pop(url, None)
        if self.rows.get(url) == item:
            return False
        self.rows[url] = item
        self._pending_op(_bucket_label(item), url, week_item(item))
        return True

    def _remove(self, url: str) -> None:
        item = self.rows.pop(url, None)
        if item:
            self._pending_op(_bucket_label(item), url, None)

    def _incremental(self) -> None:
        st = self.st
        wf = st["wmFound"]
        if not wf:
            return
        wt = st["wmTake"] or wf
        terms = f"(found_at.gte.{_iso(_dt(wf) - MARGIN_FOUND)},take_llm_at.gte.{_iso(_dt(wt) - MARGIN_TAKE)})"
        got = self.supa.select_all("news_articles", INCREMENTAL_COLS, order="found_at.asc,url.asc",
                                   tag="news:incremental", include_in_report="eq.true",
                                   **{"or": terms})
        changed = sum(1 for r in got if self._upsert(r))
        self._advance(got)
        self.notes.append(f"incremental {len(got)} ({changed} novas/mudadas)")

    def _sweep(self, cut: str, kind: str) -> None:
        """Varredura sem carimbo: tudo o que está incluído com data efetiva ou achada ≥ `cut`."""
        got = self.supa.select_all("news_articles", SWEEP_COLS, order="url.asc", tag=f"news:{kind}",
                                   include_in_report="eq.true",
                                   **{"or": f"(published_at.gte.{cut},found_at.gte.{cut})"})
        seen = {}
        for r in got:
            u = r.get("url")
            if isinstance(u, str) and u.strip():
                seen[u.strip()] = r
        removed = changed = 0
        for url, item in list(self.rows.items()):
            if ((item["publishedAt"] or "") >= cut or item["foundAt"] >= cut) and url not in seen:
                self._remove(url)
                removed += 1
        for url, r in seen.items():
            item = self.rows.get(url)
            if item is not None and apply_mutable(item, r):
                changed += 1
                self._pending_op(_bucket_label(item), url, week_item(item))
        # linha incluída mas que não sai (link recusado, sem título): anotada uma vez por estado,
        # para a varredura seguinte não a reler por inteiro
        dropped = self.st["dropped"]
        for u in [u for u in dropped if u not in seen]:
            del dropped[u]
        missing = []
        for u in (u for u in seen if u not in self.rows):
            prob = dropped.get(u) or (f"url {url_problem(u)}" if url_problem(u) else None)
            if prob:
                self.dropped[prob] += 1
            else:
                missing.append(u)
        added = 0
        for i in range(0, len(missing), URL_BATCH):
            batch = missing[i:i + URL_BATCH]
            full = self.supa.select("news_articles", INCREMENTAL_COLS, tag=f"news:{kind}",
                                    include_in_report="eq.true",
                                    url="in.(" + ",".join(_quote(u) for u in batch) + ")")
            added += sum(1 for r in full if self._upsert(r))
            self._advance(full)
        self.notes.append(f"varredura {kind} {len(got)} (−{removed} +{added} ~{changed})")

    def _evict(self) -> None:
        """Sai do cache o que não é de semana completa, nem atrasada no horizonte, nem da janela."""
        cf = _iso(W.start(self.complete_from))
        for url, item in list(self.rows.items()):
            if bucket_iso(item) >= cf or item["foundAt"] >= self.horizon_start or self.in_latest(item):
                continue
            del self.rows[url]

    # ── news-latest ─────────────────────────────────────────────────────────────────────────
    def latest_items(self) -> list[dict]:
        self.sync()
        return sort_latest([dict(i) for i in self.rows.values() if self.in_latest(i)])

    # ── semanas ─────────────────────────────────────────────────────────────────────────────
    def week_labels(self, explicit=(), auto: bool = True) -> list[str]:
        """As semanas a montar nesta execução, em ordem crescente. `auto` = a seleção do
        exportador (família "news-week"); `explicit` = as pedidas pelo nome."""
        self.sync()
        cf = self.complete_from
        plan: dict[str, str] = {}
        if not auto:
            pass
        elif self.mode == "out" or (self.mode == "dry" and self.weeks_arg is not None):
            n = self.weeks_arg if self.weeks_arg is not None else 2
            first = self.first_week if n == "all" else max(self.first_week, W.shift(self.current, 1 - int(n)))
            for lab in W.span(first, self.current):
                plan[lab] = "cache" if lab >= cf else "banco"
        else:
            for lab in W.span(max(cf, self.first_week), self.current):
                plan[lab] = "cache"
            for lab in list(self.st["pending"]):
                if lab >= cf or lab not in self.registry:
                    del self.st["pending"][lab]          # completa: o cache já tem; não publicada:
                    continue                             # a carga inicial lê do banco, já certa
                if self.st["pending"][lab]:
                    plan.setdefault(lab, "correção")
            self._audit(plan)
            for lab in self._archive_pick():
                plan.setdefault(lab, "carga")
        for lab in explicit:
            if self.first_week <= lab <= self.current:
                plan[lab] = "cache" if lab >= cf else "banco"
            else:
                self.notes.append(f"{lab} fora do arquivo ({self.first_week}…{self.current}): ignorada")
        self.plan = plan
        if any(v != "cache" for v in plan.values()):
            self.notes.append("semanas: " + ", ".join(f"{k} {v}" for k, v in sorted(plan.items())
                                                       if v != "cache"))
        return sorted(plan)

    def week_items(self, label: str) -> list[dict]:
        """Os itens (forma semanal, ordenados) da semana `label` nesta execução."""
        self.sync()
        if label in self.fresh:
            return self.fresh[label]
        kind = self.plan.get(label) or ("cache" if label >= self.complete_from else "banco")
        if kind == "cache":
            items = [week_item(i) for i in self.rows.values() if _bucket_label(i) == label]
        elif kind == "correção":
            items = self._patched(label)
        else:
            items = self._read_week(label, "carga" if kind == "carga" else "semana")
        items = sort_week(items)
        self.fresh[label] = items
        return items

    def _patched(self, label: str) -> list[dict]:
        name = W.file_name(label)
        doc = self._doc(name)
        if doc is None:
            if self.pub is not None and self.pub.status(name) == "ausente":
                self.registry.pop(label, None)           # sumiu do Mindassets: recarrega do banco
                self.st["pending"].pop(label, None)
            raise RuntimeError(f"cópia publicada de {name} indisponível ({self.pub.status(name) if self.pub else '—'})")
        base = {}
        for obj in doc.get("items") or []:
            it = from_published(obj)
            if it:
                base[it["url"]] = week_item(it)
        for url, value in (self.st["pending"].get(label) or {}).items():
            if value is None:
                base.pop(url, None)
            else:
                base[url] = value
        return list(base.values())

    def _read_week(self, label: str, kind: str) -> list[dict]:
        """A semana inteira do banco: data efetiva OU achada na semana (cobre a data esquisita)."""
        s, e = _iso(W.start(label)), _iso(W.end(label))
        t0 = self._spent()
        got = self.supa.select_all(
            "news_articles", NEWS_COLS, order="url.asc", tag=f"news:{kind}",
            include_in_report="eq.true",
            **{"or": f"(and(published_at.gte.{s},published_at.lt.{e}),and(found_at.gte.{s},found_at.lt.{e}))"})
        out = []
        for r in got:
            item, prob = item_from_row(r)
            if item is None:
                self.dropped[prob] += 1
            elif _bucket_label(item) == label:
                out.append(week_item(item))
        if kind == "carga" and self.mode != "out":
            self.st["archive"]["spent"] += self._spent() - t0
            self.st["archive"]["loads"].append(_iso(self.now))
        return out

    def _archive_pick(self) -> list[str]:
        if not self.st["weeksSeeded"] or self.mode == "out":
            return []
        missing = [lab for lab in W.span(self.first_week, W.shift(self.complete_from, -1))
                   if lab not in self.registry]
        if not missing:
            return []
        ar = self.st["archive"]
        hour_ago = _iso(self.now - timedelta(hours=1))
        ar["loads"] = [t for t in ar["loads"] if t > hour_ago]
        room = ARCHIVE_PER_HOUR - len(ar["loads"])
        if room <= 0 or ar["spent"] >= ARCHIVE_STATE_BYTES:
            return []
        return list(reversed(missing))[:room]

    def after_week(self, label: str, items: list[dict], updated_at, action: str) -> None:
        """Chamado pelo run.py depois de cada semana: o que foi de fato publicado entra no registro."""
        meta = {"count": len(items),
                "first": min((bucket_iso(i)[:10] for i in items), default=None),
                "last": max((bucket_iso(i)[:10] for i in items), default=None),
                "updatedAt": updated_at}
        kind = self.plan.get(label)
        old = self.registry.get(label)
        if self.mode == "dry" and action in ("enviaria", "igual"):
            self.run_reg[label] = meta
            self.registry_changed |= old is None or old.get("count") != meta["count"]
        if self.mode != "send" or action not in ("enviado", "igual"):
            return
        self.registry_changed |= old is None or old.get("count") != meta["count"]
        self.registry[label] = meta
        if kind == "correção":
            self.st["pending"].pop(label, None)
        if label == self.complete_from and label < self.previous:
            # a última montagem da semana que saiu do horizonte foi publicada: ela vira antiga
            self.st["completeFrom"] = W.shift(label, 1)

    # ── conferência diária das semanas antigas ──────────────────────────────────────────────
    def _audit(self, plan: dict) -> None:
        if self.mode not in ("send", "dry") or self.pub is None or self.now.hour != AUDIT_HOUR:
            return
        today = self.now.date().isoformat()
        if self.st["audit"].get("day") == today:
            return
        cf = self.complete_from
        old = [lab for lab in W.span(self.first_week, W.shift(cf, -1))]
        if not old or any(lab not in self.registry for lab in old):
            return                                       # carga inicial incompleta: amanhã
        docs = {}
        for lab in old:
            doc = self._doc(W.file_name(lab))
            if doc is None:
                return                                   # Mindassets fora: tenta no próximo ciclo
            docs[lab] = [it for it in (from_published(o) for o in doc.get("items") or []) if it]
        ref = Counter()
        for items in docs.values():
            for it in items:
                ref[_plain_label(it)] += 1
        cfs = _iso(W.start(cf))
        for it in self.rows.values():
            if bucket_iso(it) >= cfs:
                ref[_plain_label(it)] += 1
        wrong = []
        for lab in old:
            s, e = _iso(W.start(lab)), _iso(W.end(lab))
            n = self.supa.count(
                "news_articles", "url", tag="news:conferência", include_in_report="eq.true",
                **{"or": f"(and(published_at.gte.{s},published_at.lt.{e}),"
                         f"and(published_at.is.null,found_at.gte.{s},found_at.lt.{e}))"})
            if n is not None and n != ref[lab]:
                wrong.append(lab)
        for lab in wrong[:AUDIT_FIX_PER_RUN]:
            self._audit_fix(lab, docs, plan)
        if len(wrong) <= AUDIT_FIX_PER_RUN:
            self.st["audit"]["day"] = today
        self.notes.append(f"conferência: {len(old)} semanas, divergentes {', '.join(wrong) or 'nenhuma'}")

    def _audit_fix(self, label: str, docs: dict, plan: dict) -> None:
        """Relê do banco a fatia de `label` (data efetiva na semana) e corrige onde ela mora."""
        s, e = _iso(W.start(label)), _iso(W.end(label))
        got = self.supa.select_all(
            "news_articles", NEWS_COLS, order="url.asc", tag="news:conferência",
            include_in_report="eq.true",
            **{"or": f"(and(published_at.gte.{s},published_at.lt.{e}),"
                     f"and(published_at.is.null,found_at.gte.{s},found_at.lt.{e}))"})
        fresh = {}
        for r in got:
            item, prob = item_from_row(r)
            if item is not None and _plain_label(item) == label:
                fresh[item["url"]] = item
        cfs = _iso(W.start(self.complete_from))
        for lab, items in docs.items():
            for it in items:
                if _plain_label(it) == label and it["url"] not in fresh:
                    self._pending_op(lab, it["url"], None)
        for url, it in list(self.rows.items()):
            if _plain_label(it) == label and bucket_iso(it) >= cfs and url not in fresh:
                del self.rows[url]
        for url, it in fresh.items():
            if bucket_iso(it) >= cfs:
                self.rows[url] = it
            else:
                self._pending_op(_bucket_label(it), url, week_item(it))
        for lab in self.st["pending"]:
            if lab in self.registry and self.st["pending"][lab]:
                plan.setdefault(lab, "correção")

    # ── índice ──────────────────────────────────────────────────────────────────────────────
    def published_weeks(self) -> dict[str, dict]:
        """O que o news-index.json lista: no envio, o registro (semente publicada + o que esta
        execução publicou); no dry, também o que ela ENVIARIA; no --out, os arquivos da pasta."""
        self.sync()
        if self.mode == "out":
            return self._out_registry()
        return {**self.registry, **self.run_reg}

    def index_seed(self) -> dict:
        return self.st.get("indexSeed") or {}

    def _out_registry(self) -> dict[str, dict]:
        reg = {}
        if not self.out_dir or not self.out_dir.is_dir():
            return reg
        for p in sorted(self.out_dir.glob(f"{W.PREFIX}-*.json")):
            lab = W.label_from_file(p.name)
            if not lab:
                continue
            try:
                raw = p.read_bytes()
                doc = json.loads(gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw)
            except (OSError, ValueError, EOFError) as e:
                log.warning("--out: %s ilegível (%s) — fora do índice", p.name, type(e).__name__)
                continue
            items = [i for i in doc.get("items") or [] if isinstance(i, dict)]
            dates = [bucket_iso(i)[:10] for i in (from_published(o) for o in items) if i]
            reg[lab] = {"count": len(items), "first": min(dates, default=None),
                        "last": max(dates, default=None), "updatedAt": iso_utc(doc.get("updatedAt"))}
        return reg
