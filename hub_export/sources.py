"""De onde vem cada dado: memória do ciclo → último estado conhecido → Supabase.

Orçamento: ≤ 5 MB/dia de tráfego do Supabase com o loop rodando 24/7 (o sistema inteiro já anda
a ~90 MB/dia num teto de 5 GB/mês, e o Supabase conta ~0,75 KB de cabeçalho por resposta, medido
em 05/11). Cada fonte paga o mínimo:

  cotações + curva do dia   memória do ciclo (`hunter.prices.LAST_QUOTE_ROWS`, gravada pelo
                            `update_quotes` deste MESMO processo) por cima do estado. Lê do banco
                            só o papel que nunca apareceu (partida a frio do runner, ou o Yahoo
                            não devolveu). Sem memória (CLI): sonda `max(updated_at)` e só relê
                            tudo se mudou.
  macro                     idem, com `LAST_MACRO_ROWS`.
  commodities               uma leitura LEVE por montagem (sem séries), que já traz a sonda do
                            risquinho: `asof`, `v` e `ph` de dentro do `spark`. O `spark` inteiro
                            só desce para o código cujo trio mudou (~1–2×/dia por código; o `ph`
                            muda na 2ª passada das de tick vivo, depois do fechamento).
  quotes.daily              sonda `max(daily_updated_at)`; mudou → lista `ticker,daily_updated_at`
                            e relê SÓ os papéis cujo carimbo mudou (cache por papel). Partida a
                            frio: o cache é semeado da cópia PUBLICADA do quotes-daily.json (sai
                            do Mindassets, não do Supabase) e a mesma lista diz quem relê.
  config                    três RPCs pequenas (flags, heatmap do /admin, grupos de commodities),
                            só quando o config.json está na vez (15 min). As flags ficam no estado
                            (o news-digest.json, HOT, lê a `home_digest` dali, com até 15 min).
Onda 1b:
  notícias                  news.py (cache do horizonte, incremental com margem, varreduras,
                            semanas, carga inicial aos poucos, conferência das antigas).
  resumo da hora            tabela `news_digest`, imutável: lida só nas janelas de gravação
                            (E+10…E+45 de Brasília, E = 7…20, ~100 ciclos/dia) e na partida a
                            frio (semeada da cópia publicada + `slot_start > maior`).
  relatórios                RPC `get_recent_reports_v3` (nunca a tabela de origem), no COLD.
  calendário                sonda "contagem + max(updated_at)" dos visíveis (1 chamada) a cada
                            WARM; leitura completa quando muda, na partida a frio e 1×/dia depois
                            das 23:45 UTC; categorias e janelas de dado a cada 60 min.
Onda 2 (PLANO, Apêndice F; pesquisa 2-dados §4):
  cauda mensal              o MESMO cache do diário (zero leitura nova): entrada lida do banco tem a
                            série leve inteira; a semeada da cópia publicada do quotes-daily.json
                            (400 dias) completa a cauda com a cópia PUBLICADA do quotes-monthly.json
                            quando ela é do mesmo mês do corte; senão relê só aquele papel.
  commodities livres        sonda do último ponto das 3 (`last:daily->-1`, ~1 KB); relê só o código
                            cujo último ponto mudou, e cada código ao menos 1×/dia (depois das 06 UTC:
                            o TE reescreve 10 anos a cada ~3 h — a revisão no meio da série só aparece
                            assim); partida a frio semeada da cópia publicada.
  recomendações             sonda "contagem + max(updated_at)" do histórico e das empresas do Stock
                            Guide; RPC `get_rating_history` (paginada) quando muda, na partida a
                            frio e 1×/dia (06 UTC). Flag `market_ratings` desligada: nada é lido.
  histórico longo           history.py (família history-<id>.json + history-index.json).
  Market Watch              mw.py (WP2.4B): as RPCs `get_mw_*`, sondas pela RPC (contagem +
                            máximo, ~1 KB), estado próprio (`<estado>_mw.json`), partida a frio pelas
                            cópias publicadas e a família mw-short-<empresa>.json.

Tudo é normalizado aqui (datas `…Z`, números finitos ou None, `session` válida ou None) e
guardado assim no estado — os construtores só renomeiam campo a campo.
`rebuild=True` (CLI `--rebuild`): ignora as sementes publicadas e os caches de dados e relê o
banco (depois de um conserto manual de título, por exemplo).
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

from hunter import prices

from .encode import epoch_s, iso_date, iso_utc, num, points, session_pair
from .supa import SupaError

log = logging.getLogger(__name__)

MEMORY_MAX_AGE_S = 900     # memória mais velha que isto não é "deste ciclo"
FLAGS_MAX_AGE_S = 900      # as flags valem 15 min (o config.json as relê no WARM)

QUOTE_COLS = "ticker,price,change_pct,change_abs,updated_at,intraday"
MACRO_COLS = "code,value,change_pct,updated_at"
COMM_COLS = ("code,name,unit,price,change_pct,assessed_at,updated_at,"
             "asof:spark->>asof,v:spark->>v,ph:spark->>ph")

BRT = timezone(timedelta(hours=-3))  # o Brasil não tem horário de verão desde 2019
DIGEST_COLS = "slot_start,slot_end,kind,status,summary,items,created_at"
DIGEST_HOURS = range(7, 21)          # E = 7 (madrugada) … 20 (última janela de hora em hora)
DIGEST_MINUTES = (10, 45)            # E+10 … E+45: o robô grava de E+10 a E+40 (digest.py)
REPORTS_COLS = "name,sector,link,date,companies"
REPORTS_LIMIT = 20
CALENDAR_META_EVERY_S = 3600
CALENDAR_DAILY_UTC = (23, 45)        # depois do robô da B3 (update_b3_calendar.yml, 23:20 UTC)
COMM_DAILY_PROBE_COLS = "code,unit,daily_updated_at,last:daily->-1"
COMM_DAILY_COLS = "code,unit,daily,daily_updated_at"
COMM_DAILY_FULL_UTC = (6, 0)         # 1 leitura completa por dia das séries livres
RATINGS_FULL_UTC = (6, 0)            # 1 leitura da RPC por dia, mesmo sem a sonda mudar
_ISO = "%Y-%m-%dT%H:%M:%SZ"


def _age_s(stamp, now: datetime) -> float | None:
    iso = iso_utc(stamp)
    if not iso:
        return None
    return (now - datetime.strptime(iso, _ISO).replace(tzinfo=timezone.utc)).total_seconds()


def digest_window_open(now: datetime) -> bool:
    """Janela de gravação do resumo da hora (horário de Brasília): E+10 ≤ agora < E+45."""
    b = now.astimezone(BRT)
    return b.hour in DIGEST_HOURS and DIGEST_MINUTES[0] <= b.minute < DIGEST_MINUTES[1]


def daily_mark(now: datetime, hour: int, minute: int = 0) -> str:
    """O último HH:MM UTC já passado: leitura "diária" anterior a ele está vencida."""
    mark = now.astimezone(timezone.utc).replace(hour=hour, minute=minute, second=0, microsecond=0)
    if mark > now:
        mark -= timedelta(days=1)
    return iso_utc(mark)


def calendar_daily_mark(now: datetime) -> str:
    """O último 23:45 UTC já passado: leitura completa anterior a ele está vencida."""
    return daily_mark(now, *CALENDAR_DAILY_UTC)


def _text(v) -> str | None:
    return (v.strip() or None) if isinstance(v, str) else None


def _rebuild_light(entry: dict, pub) -> list | None:
    """A série leve de um papel cujo cache veio CORTADO em 400 dias (semente da cópia publicada do
    quotes-daily.json): a cauda da cópia publicada do quotes-monthly.json + o trecho do cache.
    Só vale se a cópia é do MESMO mês do corte (`v_cut`) — o arquivo só é reenviado quando o mês
    vira, então a cópia de outro mês está sem o mês que entrou — e se bate com o cache no trecho em
    comum (um split depois do último envio mudaria os valores). Não valeu → None (relê o papel)."""
    from .exporters.quotes_monthly import cut_month
    if not isinstance(pub, dict):
        return None
    m_now = cut_month(entry.get("at"))
    if m_now is None or cut_month(iso_utc(pub.get("updatedAt"))) != m_now:
        return None
    tail = points(pub.get("pts"))
    pts = [list(p) for p in entry.get("pts") or []]
    if not pts:
        return tail
    first = pts[0][0]
    cur = {p[0]: p[1] for p in pts}
    for ts, v in tail:
        if ts >= first and (ts not in cur or cur[ts] != v):
            return None
    return [p for p in tail if p[0] < first] + pts


# ── universos (a ordem do arquivo é a das listas do robô) ──────────────────────────────────────
def quote_universe() -> list[tuple[str, str, str]]:
    """(ticker, nome, bolsa) na ordem do QUOTES_LIST."""
    return [(t, name, exch) for t, name, _setor, exch, _prov, _sym in prices.QUOTES_LIST]


def macro_universe() -> list[tuple[str, str, str]]:
    """(código, nome, unidade): os do Yahoo e depois os do BCB, como o update_macro grava."""
    return ([(c, n, u) for c, n, u, _s in prices.MACRO_YAHOO]
            + [(c, n, u) for c, n, u, _s in prices.MACRO_BCB])


def cycle_memory(now_epoch: float | None = None) -> dict:
    """As linhas que o update_quotes/update_macro DESTE processo acabaram de montar.
    None em cada chave quando não há (CLI, função que falhou, memória velha)."""
    now_epoch = time.time() if now_epoch is None else now_epoch
    out = {"quotes": None, "macro": None}
    for key, attr in (("quotes", "LAST_QUOTE_ROWS"), ("macro", "LAST_MACRO_ROWS")):
        try:
            at, rows = getattr(prices, attr, None)
        except (TypeError, ValueError):
            continue
        if (isinstance(rows, list) and isinstance(at, (int, float))
                and 0 <= now_epoch - at <= MEMORY_MAX_AGE_S):
            out[key] = rows
    return out


def _in(values) -> str:
    return "in.(" + ",".join('"' + str(v).replace('"', "") + '"' for v in values) + ")"


def _newer(old: dict | None, new: dict) -> bool:
    """`new` substitui `old`? O carimbo mais novo vence; empate → quem chegou depois."""
    if not old:
        return True
    a, b = old.get("updated_at"), new.get("updated_at")
    if b is None:
        return a is None
    return a is None or b >= a


def _quote_row(r: dict) -> dict:
    it = r.get("intraday")
    intraday = None
    if isinstance(it, dict):
        intraday = {"prev": num(it.get("prev")), "pts": points(it.get("pts")),
                    "traded_at": epoch_s(it.get("traded_at")),
                    "session": session_pair(it.get("session"))}
    return {"price": num(r.get("price")), "change_pct": num(r.get("change_pct")),
            "change_abs": num(r.get("change_abs")), "updated_at": iso_utc(r.get("updated_at")),
            "intraday": intraday}


def _macro_row(r: dict) -> dict:
    return {"value": num(r.get("value")), "change_pct": num(r.get("change_pct")),
            "updated_at": iso_utc(r.get("updated_at"))}


def _spark_key(asof, v, ph) -> str:
    """Impressão do risquinho: muda quando muda o dia (`asof`), o formato (`v`) ou a passada
    depois do fechamento (`ph`). `->>` devolve texto, o jsonb devolve número: tudo vira str."""
    return "|".join("" if x is None else str(x) for x in (asof, v, ph))


class Sources:
    """Dados de UMA execução, cada um lido no máximo uma vez (memo)."""

    def __init__(self, supa, state: dict, *, now: datetime | None = None,
                 memory: dict | None = None, published=None, mode: str = "send",
                 rebuild: bool = False, out_dir=None, weeks_arg=None, news_path=None,
                 first_week: str | None = None, history_arg=None, mw_path=None):
        self.supa = supa
        self.state = state
        self.now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        self.generated_at = iso_utc(self.now)
        self.memory = memory or {}
        self.published = published
        self.mode = mode
        self.rebuild = rebuild
        self.out_dir = out_dir
        self.weeks_arg = weeks_arg
        self.news_path = news_path
        self.first_week = first_week
        self.history_arg = history_arg
        self.mw_path = mw_path
        self._mw = None
        self._mw_state = None
        self.quote_universe = quote_universe()
        self.macro_universe = macro_universe()
        self.used_memory = {"quotes": None, "macro": None}
        self._memo: dict = {}
        self._news = None
        self._news_state = None
        self._history = None

    # ── cotações (+ curva do dia) ───────────────────────────────────────────────────────────
    def quotes(self) -> dict[str, dict]:
        if "quotes" not in self._memo:
            self._memo["quotes"] = self._with_memory(
                "quotes", "quotes", "ticker", QUOTE_COLS, _quote_row,
                [t for t, _n, _e in self.quote_universe])
        return self._memo["quotes"]

    def macro(self) -> dict[str, dict]:
        if "macro" not in self._memo:
            self._memo["macro"] = self._with_memory(
                "macro", "macro_indicators", "code", MACRO_COLS, _macro_row,
                [c for c, _n, _u in self.macro_universe])
        return self._memo["macro"]

    def _with_memory(self, key, table, id_col, cols, norm, universe) -> dict[str, dict]:
        st = self.state[key]
        uni = set(universe)
        rows = {} if self.rebuild else {k: v for k, v in (st.get("rows") or {}).items()
                                         if k in uni and isinstance(v, dict)}

        def merge(raw_rows):
            for r in raw_rows:
                k = r.get(id_col)
                if k in uni:
                    nr = norm(r)
                    if _newer(rows.get(k), nr):
                        rows[k] = nr

        mem = self.memory.get(key)
        if mem is not None:
            self.used_memory[key] = len(mem)
            merge(r for r in mem if isinstance(r, dict))
            # Papel que nem a memória nem o estado conhecem: o banco é consultado UMA vez por
            # estado (partida a frio do runner) — senão um papel sem preço nenhum custaria uma
            # leitura em todo ciclo.
            checked = set() if self.rebuild else set(st.get("checked") or [])
            missing = [k for k in universe if k not in rows and k not in checked]
            if missing:
                merge(self.supa.select(table, cols, tag=key, **{id_col: _in(missing)}))
                st["checked"] = sorted(checked | set(missing))
        else:
            probe = self.supa.select(table, "updated_at", tag=key, order="updated_at.desc.nullslast",
                                     limit=1)
            top = iso_utc(probe[0].get("updated_at")) if probe else None
            if not rows or top is None or top != st.get("probe"):
                merge(self.supa.select(table, cols, tag=key))
                st["probe"] = top
        st["rows"] = rows
        return rows

    # ── commodities (+ cache do risquinho) ──────────────────────────────────────────────────
    def commodities(self) -> list[dict]:
        if "commodities" in self._memo:
            return self._memo["commodities"]
        st = self.state["commodities"]
        cache = self.state["spark"]
        if self.rebuild:
            cache.clear()
        try:
            raw = self.supa.select("commodities", COMM_COLS, tag="commodities", order="code")
        except SupaError as e:
            if not st.get("rows"):
                raise
            # o banco falhou neste ciclo: as cotações (memória) seguem frescas, então o arquivo
            # sai com as commodities da última leitura boa em vez de não sair
            log.warning("commodities: leitura falhou (%s) — usando a última leitura boa", e)
            raw = None
        if raw is not None:
            light = []
            for r in raw:
                code = r.get("code")
                if not isinstance(code, str) or not code:
                    continue
                light.append({"code": code, "name": r.get("name") if isinstance(r.get("name"), str) else None,
                              "unit": r.get("unit") if isinstance(r.get("unit"), str) else None,
                              "price": num(r.get("price")), "change_pct": num(r.get("change_pct")),
                              "assessed_at": iso_date(r.get("assessed_at")),
                              "updated_at": iso_utc(r.get("updated_at")),
                              "spark_key": (_spark_key(r.get("asof"), r.get("v"), r.get("ph"))
                                            if r.get("asof") is not None else None)})
            light.sort(key=lambda x: x["code"])
            st["rows"] = light
            need = [x["code"] for x in light
                    if x["spark_key"] and (cache.get(x["code"]) or {}).get("key") != x["spark_key"]]
            if need:
                try:
                    for g in self.supa.select("commodities", "code,spark", tag="commodities:spark",
                                              code=_in(need)):
                        sp = g.get("spark")
                        if isinstance(g.get("code"), str) and isinstance(sp, dict):
                            cache[g["code"]] = {"key": _spark_key(sp.get("asof"), sp.get("v"),
                                                                  sp.get("ph")), "spark": sp}
                except SupaError as e:
                    log.warning("spark: leitura falhou (%s) — fica o desenho anterior", e)
            com_spark = {x["code"] for x in light if x["spark_key"]}
            for code in [c for c in cache if c not in com_spark]:
                del cache[code]
        out = []
        for x in st.get("rows") or []:
            row = {k: v for k, v in x.items() if k != "spark_key"}
            row["spark"] = (cache.get(x["code"]) or {}).get("spark") if x.get("spark_key") else None
            out.append(row)
        self._memo["commodities"] = out
        return out

    # ── série diária leve (quotes.daily) ────────────────────────────────────────────────────
    def daily(self) -> dict[str, dict]:
        if "daily" in self._memo:
            return self._memo["daily"]
        st = self.state["daily"]
        universe = [t for t, _n, _e in self.quote_universe]
        uni = set(universe)
        cache = {} if self.rebuild else {t: e for t, e in (st.get("rows") or {}).items()
                                         if t in uni and isinstance(e, dict)}
        probe = self.supa.select("quotes", "daily_updated_at", tag="quotes:daily",
                                 order="daily_updated_at.desc.nullslast", limit=1)
        top = iso_utc(probe[0].get("daily_updated_at")) if probe else None
        if not (cache and top is not None and top == st.get("probe")):
            if not cache and not self.rebuild:
                cache.update(self._seed_daily())
            want = {}
            for r in self.supa.select("quotes", "ticker,daily_updated_at", tag="quotes:daily"):
                if r.get("ticker") in uni:
                    want[r["ticker"]] = iso_utc(r.get("daily_updated_at"))
            need = [t for t in universe if t in want and (t not in cache or cache[t].get("at") != want[t])]
            if need:
                params = {} if len(need) == len(want) else {"ticker": _in(need)}
                for r in self.supa.select("quotes", "ticker,daily,daily_updated_at",
                                          tag="quotes:daily", **params):
                    if r.get("ticker") in want:
                        # `full`: a série leve INTEIRA (com a cauda mensal) — a semente da cópia
                        # publicada vem cortada em 400 dias (ver monthly())
                        cache[r["ticker"]] = {"at": iso_utc(r.get("daily_updated_at")),
                                              "pts": points(r.get("daily")), "full": True}
            for t in [t for t in cache if t not in want]:
                del cache[t]
            st["probe"] = top
        st["rows"] = cache
        self._memo["daily"] = cache
        return cache

    def _seed_daily(self) -> dict[str, dict]:
        """Partida a frio do cache diário a partir do quotes-daily.json PUBLICADO (é o nosso
        próprio arquivo: `pts` = quotes.daily dos últimos 400 dias, `updatedAt` = daily_updated_at
        já normalizado). A cópia vem cortada, mas basta: o arquivo também sai cortado, e a janela
        só anda para a frente. Quem estiver com carimbo diferente no banco é relido em seguida."""
        if self.published is None:
            return {}
        doc = self.published.get("quotes-daily.json")
        if not isinstance(doc, dict) or doc.get("schemaVersion") != 1:
            return {}
        series = doc.get("series")
        uni = {t for t, _n, _e in self.quote_universe}
        out = {}
        if isinstance(series, dict):
            for t, s in series.items():
                if t in uni and isinstance(s, dict):
                    at = iso_utc(s.get("updatedAt"))
                    if at:
                        out[t] = {"at": at, "pts": points(s.get("pts"))}
        if out:
            log.info("diário: %d séries semeadas da cópia publicada", len(out))
        return out

    def daily_full(self, tickers) -> None:
        """Relê do banco a série leve INTEIRA (com a cauda mensal) destes papéis, no cache."""
        d = self.daily()
        want = [t for t in tickers if t in d]
        if not want:
            return
        for r in self.supa.select("quotes", "ticker,daily,daily_updated_at", tag="quotes:mensal",
                                  ticker=_in(want)):
            if r.get("ticker") in d:
                d[r["ticker"]] = {"at": iso_utc(r.get("daily_updated_at")),
                                  "pts": points(r.get("daily")), "full": True}

    # ── cauda mensal da série leve (quotes-monthly.json, Onda 2) ───────────────────────────────
    def monthly(self) -> dict[str, dict]:
        """papel → {updatedAt, pts}: a cauda mensal (quotes_monthly.monthly_part) de cada papel do
        cache do diário. Zero leitura nova quando a cópia publicada vale (ver `_rebuild_light`)."""
        if "monthly" in self._memo:
            return self._memo["monthly"]
        from .exporters.quotes_monthly import monthly_part
        d = self.daily()
        pub = {}
        if not self.rebuild and self.published is not None:
            doc = self.published.get("quotes-monthly.json")
            if (isinstance(doc, dict) and doc.get("schemaVersion") == 1
                    and isinstance(doc.get("series"), dict)):
                pub = doc["series"]
        out, need = {}, []
        for t, _n, _e in self.quote_universe:
            e = d.get(t)
            if not e:
                continue
            light = e.get("pts") if e.get("full") else _rebuild_light(e, pub.get(t))
            if light is None:
                need.append(t)
                continue
            out[t] = {"updatedAt": e.get("at"), "pts": monthly_part(light, e.get("at"))}
        if need:
            self.daily_full(need)
            for t in need:
                e = d.get(t)
                if e and e.get("full"):
                    out[t] = {"updatedAt": e.get("at"), "pts": monthly_part(e.get("pts"), e.get("at"))}
            log.info("cauda mensal: %d papel(éis) relido(s) do banco (sem cópia publicada que valha)",
                     len(need))
        self._memo["monthly"] = out
        return out

    # ── commodities de número livre (commodities-daily.json, Onda 2) ───────────────────────────
    def commodities_daily(self) -> dict[str, dict]:
        """código → {at, unit, pts} só dos códigos da lista branca (FREE_SERIES). Nenhum outro
        código é pedido ao banco nem guardado (a série paga em números nunca sai)."""
        if "commDaily" in self._memo:
            return self._memo["commDaily"]
        from .exporters.commodities_daily import FREE_SERIES
        st = self.state["commDaily"]
        codes = list(FREE_SERIES)
        cache = {} if self.rebuild else {c: dict(e) for c, e in (st.get("rows") or {}).items()
                                         if c in FREE_SERIES and isinstance(e, dict)}
        if not cache and not st.get("seeded") and not self.rebuild and self.published is not None:
            cache.update(self._seed_comm_daily(FREE_SERIES))
        seen = {}
        for r in self.supa.select("commodities", COMM_DAILY_PROBE_COLS,
                                  tag="commodities:livres:sonda", code=_in(codes)):
            c = r.get("code")
            if c in FREE_SERIES:
                last = points([r.get("last")])
                seen[c] = {"at": iso_utc(r.get("daily_updated_at")), "unit": _text(r.get("unit")),
                           "last": last[0] if last else None}
        mark = daily_mark(self.now, *COMM_DAILY_FULL_UTC)

        def stale(c) -> bool:
            """Relê: código novo, último ponto diferente ou leitura inteira de antes da marca do dia."""
            e = cache.get(c)
            if e is None or (e.get("readAt") or "") < mark:
                return True
            pts = e.get("pts") or []
            return (pts[-1] if pts else None) != seen[c]["last"]
        need = [c for c in codes if c in seen and stale(c)]
        if need:
            for r in self.supa.select("commodities", COMM_DAILY_COLS, tag="commodities:livres",
                                      code=_in(need)):
                c = r.get("code")
                if c in FREE_SERIES:
                    cache[c] = {"at": iso_utc(r.get("daily_updated_at")), "unit": _text(r.get("unit")),
                                "pts": points(r.get("daily")), "readAt": self.generated_at}
        for c in list(cache):
            if c not in seen:
                del cache[c]                          # sumiu do banco: não sai
            elif c not in need:
                cache[c].update(at=seen[c]["at"], unit=seen[c]["unit"])   # carimbo e unidade da sonda
        # só depois de todas as leituras: uma que falhe não deixa o estado meio novo
        st.update(rows=cache, seeded=True)
        self._memo["commDaily"] = cache
        return cache

    def _seed_comm_daily(self, free) -> dict:
        """Partida a frio: as séries da cópia PUBLICADA (zero Supabase), lidas "quando foi gerada";
        a sonda diz quem relê."""
        doc = self.published.get("commodities-daily.json")
        if (not isinstance(doc, dict) or doc.get("schemaVersion") != 1
                or not isinstance(doc.get("series"), dict)):
            return {}
        out = {}
        for c, s in doc["series"].items():
            if c in free and isinstance(s, dict):
                out[c] = {"at": iso_utc(s.get("updatedAt")), "unit": _text(s.get("unit")),
                          "pts": points(s.get("pts")), "readAt": iso_utc(doc.get("generatedAt"))}
        return out

    # ── histórico de recomendações (ratings.json, Onda 2) ──────────────────────────────────────
    def ratings(self) -> dict:
        """{updatedAt, companies}. Flag `market_ratings` desligada (fail-closed, regra `hub_`) →
        nada é lido e `companies: []`. Ligada: 2 sondas baratas; a RPC (paginada, contagem
        conferida) só quando a sonda muda, na partida a frio e 1×/dia."""
        if "ratings" in self._memo:
            return self._memo["ratings"]
        from .exporters import ratings as rt
        from .exporters.config import _flags
        st = self.state["ratings"]
        if _flags(self._flag_rows()).get("market_ratings") is not True:
            st.update(probe=None, fullAt=None, data=None)          # religada: relê tudo
            out = {"updatedAt": None, "companies": []}
        else:
            hist, n_hist = self.supa.select_count("stock_guide_rating_history", "updated_at",
                                                  tag="ratings:sonda",
                                                  order="updated_at.desc.nullslast", limit=1)
            cos, n_cos = self.supa.select_count("stock_guide_companies", "updated_at",
                                                tag="ratings:sonda",
                                                order="updated_at.desc.nullslast", limit=1)
            top = iso_utc(hist[0].get("updated_at")) if hist else None
            probe = [n_hist, top, n_cos, iso_utc(cos[0].get("updated_at")) if cos else None]
            data = None if self.rebuild else st.get("data")
            if (data is None or probe != st.get("probe") or not st.get("fullAt")
                    or st["fullAt"] < daily_mark(self.now, *RATINGS_FULL_UTC)):
                data = rt.companies(self.supa.rpc_all("get_rating_history", rt.COLS,
                                                      order=rt.ORDER, tag="ratings"))
                st.update(data=data, fullAt=self.generated_at)
            st["probe"] = probe
            out = {"updatedAt": top, "companies": data}
        self._memo["ratings"] = out
        return out

    # ── histórico longo (history.py) ────────────────────────────────────────────────────────────
    def history(self):
        """O HistoryStore desta execução (registro, sonda, fila e orçamento da carga)."""
        if self._history is None:
            from .history import HistoryStore
            self._history = HistoryStore(self, explicit=self.history_arg)
        return self._history

    def history_if_loaded(self):
        return self._history

    # ── Market Watch (mw.py) ────────────────────────────────────────────────────────────────────
    def mw(self):
        """O MWStore desta execução (o estado sai do arquivo próprio, uma vez)."""
        if self._mw is None:
            from .mw import MWStore, empty_mw_state, load_mw_state
            st = load_mw_state(self.mw_path) if self.mw_path else empty_mw_state()
            self._mw_state = st
            self._mw = MWStore(self, st)
        return self._mw

    def mw_if_loaded(self):
        return self._mw

    def save_mw(self) -> None:
        if self._mw is not None and self.mw_path:
            from .mw import save_mw_state
            save_mw_state(self.mw_path, self._mw_state)

    def mw_family_plan(self, explicit=(), auto: bool = True, force: bool = False) -> list[str]:
        """As empresas da família mw-short nesta execução. No envio, se nenhum arquivo da família
        conhecido está na vez (o caso de 11 em cada 12 ciclos), nem carrega o estado do Market Watch."""
        from .mw import company_from_file
        from .schedule import COLD, is_due
        if self._mw is None and self.mode == "send" and auto and not explicit and not force:
            files = self.state.get("files") or {}
            known = [n for n in files if company_from_file(n)]
            if known and not any(is_due(COLD, (files[n] or {}).get("builtAt"), self.now) for n in known):
                return []
        return self.mw().family_plan(explicit=explicit, auto=auto, force=force)

    # ── configuração do /admin ──────────────────────────────────────────────────────────────
    def flags(self) -> list[dict]:
        rows = self._rpc("get_dashboard_flags", "key,enabled")
        self.state["flags"] = {"at": self.generated_at, "rows": rows}
        return rows

    def heatmap_admin(self) -> list[dict]:
        return self._rpc("get_heatmap_tickers", "symbol,is_hidden,in_coverage,sort_order")

    def commodity_groups(self) -> list[dict]:
        return self._rpc("get_commodity_groups", "id,title,codes,sort_order,is_visible")

    def _rpc(self, fn: str, cols: str) -> list[dict]:
        if fn not in self._memo:
            self._memo[fn] = self.supa.rpc(fn, cols, tag="config")
        return self._memo[fn]

    def _flag_rows(self) -> list[dict]:
        """As flags com no máximo 15 min (do estado), ou lidas agora (e guardadas)."""
        cached = self.state["flags"]
        age = _age_s(cached.get("at"), self.now)
        if (not self.rebuild and cached.get("rows") and age is not None and 0 <= age < FLAGS_MAX_AGE_S):
            return cached["rows"]
        return self.flags()

    def digest_enabled(self) -> bool:
        """`home_digest` só vale ligada com `true` (fail-closed, decisão O3); `hub_home_digest`
        manda nela, como nas outras flags (config._flags)."""
        from .exporters.config import _flags
        on = _flags(self._flag_rows()).get("home_digest") is True
        if not on:
            self.state["digest"]["seeded"] = False     # religada: relê, não confia no cache velho
        return on

    # ── notícias (news.py) ──────────────────────────────────────────────────────────────────
    def news(self):
        """O NewsStore desta execução (o cache sai do arquivo próprio, uma vez)."""
        if self._news is None:
            from . import news as news_mod
            from .weeks import FIRST_WEEK
            st = news_mod.load_news(self.news_path) if self.news_path else news_mod.empty_news_state()
            self._news_state = st
            self._news = news_mod.NewsStore(self.supa, self.published, st, now=self.now,
                                            mode=self.mode, rebuild=self.rebuild,
                                            out_dir=self.out_dir, weeks_arg=self.weeks_arg,
                                            first_week=self.first_week or FIRST_WEEK)
        return self._news

    def news_if_loaded(self):
        return self._news

    def save_news(self) -> None:
        if self._news is not None and self.news_path:
            from . import news as news_mod
            news_mod.save_news(self.news_path, self._news_state)

    # ── resumo da hora ──────────────────────────────────────────────────────────────────────
    def digest_windows(self) -> list[dict]:
        if "digest" in self._memo:
            return self._memo["digest"]
        from .exporters import news_digest as nd
        st = self.state["digest"]
        rows = {} if self.rebuild else {w["slotStart"]: w for w in st["rows"]
                                         if isinstance(w, dict) and w.get("slotStart")}
        read = digest_window_open(self.now)
        if not st["seeded"] or self.rebuild:
            if not self.rebuild and self.published is not None:
                doc = self.published.get("news-digest.json")
                if isinstance(doc, dict) and doc.get("schemaVersion") == 1:
                    for obj in doc.get("windows") or []:
                        w = nd.from_published(obj)
                        if w:
                            rows.setdefault(w["slotStart"], w)
            read = True
        if read:
            if rows:
                got = self.supa.select("news_digest", DIGEST_COLS, tag="digest",
                                       slot_start=f"gt.{max(rows)}", order="slot_start.asc")
            else:
                got = self.supa.select("news_digest", DIGEST_COLS, tag="digest",
                                       order="slot_start.desc", limit=nd.KEEP)
            for r in got:
                w = nd.from_row(r)
                if w:
                    rows[w["slotStart"]] = w
            st["readAt"] = self.generated_at
            st["seeded"] = True
        keep = sorted(rows.values(), key=lambda w: w["slotStart"], reverse=True)[:nd.KEEP]
        st["rows"] = keep
        self._memo["digest"] = keep
        return keep

    # ── relatórios ──────────────────────────────────────────────────────────────────────────
    def recent_reports(self) -> list[dict]:
        if "reports" not in self._memo:
            self._memo["reports"] = self.supa.rpc("get_recent_reports_v3", REPORTS_COLS,
                                                  {"p_limit": REPORTS_LIMIT}, tag="reports")
        return self._memo["reports"]

    # ── calendário ──────────────────────────────────────────────────────────────────────────
    def calendar(self) -> dict:
        if "calendar" in self._memo:
            return self._memo["calendar"]
        from .exporters import calendar as cal
        st = self.state["calendar"]
        first, last = cal.date_range(self.now)
        rows, total = self.supa.select_count("exec_calendar_events", "updated_at",
                                             tag="calendar:sonda", is_visible="eq.true",
                                             order="updated_at.desc.nullslast", limit=1)
        probe = {"count": total, "max": iso_utc(rows[0].get("updated_at")) if rows else None}
        full = (self.rebuild or st["events"] is None or probe != st["probe"]
                or st["range"] != [first, last] or not st["fullAt"]
                or st["fullAt"] < calendar_daily_mark(self.now))
        age = _age_s(st["metaAt"], self.now)
        meta = full or age is None or age >= CALENDAR_META_EVERY_S - 60
        if meta:
            cats = [c for c in (cal.category(r) for r in self.supa.rpc(
                "get_exec_calendar_categories", cal.CATEGORY_COLS, tag="calendar")) if c]
            wins = [w for w in (cal.data_window(r) for r in self.supa.rpc(
                "get_exec_calendar_data_windows", cal.WINDOW_COLS, tag="calendar")) if w]
            categories, windows = cal.sort_categories(cats), cal.sort_windows(wins)
        else:
            categories, windows = st["categories"], st["windows"]
        if full:
            evs = self.supa.rpc("get_exec_calendar_events", cal.EVENT_COLS,
                                {"p_from": first, "p_to": last}, tag="calendar")
            events = cal.sort_events([e for e in (cal.event(r) for r in evs)
                                      if e and cal.in_range(e, first, last)])
        else:
            events = st["events"]
        # só depois de TODAS as leituras: uma que falhe não deixa o estado meio novo
        if meta:
            st.update(categories=categories, windows=windows, metaAt=self.generated_at)
        if full:
            st.update(events=events, fullAt=self.generated_at, range=[first, last])
        st["probe"] = probe
        out = {"from": first, "to": last, "updatedAt": probe["max"], "categories": categories,
               "windows": windows, "events": events}
        self._memo["calendar"] = out
        return out
