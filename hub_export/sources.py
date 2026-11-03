"""De onde vem cada dado: memória do ciclo → último estado conhecido → Supabase.

Orçamento: ≤ 5 MB/dia de tráfego do Supabase com o loop rodando 24/7 (o sistema inteiro já anda
a ~90 MB/dia num teto de 5 GB/mês, e o Supabase conta ~1 KB de cabeçalho por resposta). Cada
fonte paga o mínimo:

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
                            só quando o config.json está na vez (15 min).

Tudo é normalizado aqui (datas `…Z`, números finitos ou None, `session` válida ou None) e
guardado assim no estado — os construtores só renomeiam campo a campo.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from hunter import prices

from .encode import epoch_s, iso_date, iso_utc, num, points, session_pair
from .supa import SupaError

log = logging.getLogger(__name__)

MEMORY_MAX_AGE_S = 900     # memória mais velha que isto não é "deste ciclo"

QUOTE_COLS = "ticker,price,change_pct,change_abs,updated_at,intraday"
MACRO_COLS = "code,value,change_pct,updated_at"
COMM_COLS = ("code,name,unit,price,change_pct,assessed_at,updated_at,"
             "asof:spark->>asof,v:spark->>v,ph:spark->>ph")


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
                 memory: dict | None = None, published=None):
        self.supa = supa
        self.state = state
        self.now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        self.generated_at = iso_utc(self.now)
        self.memory = memory or {}
        self.published = published
        self.quote_universe = quote_universe()
        self.macro_universe = macro_universe()
        self.used_memory = {"quotes": None, "macro": None}
        self._memo: dict = {}

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
        rows = {k: v for k, v in (st.get("rows") or {}).items() if k in uni and isinstance(v, dict)}

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
            checked = set(st.get("checked") or [])
            missing = [k for k in universe if k not in rows and k not in checked]
            if missing:
                merge(self.supa.select(table, cols, **{id_col: _in(missing)}))
                st["checked"] = sorted(checked | set(missing))
        else:
            probe = self.supa.select(table, "updated_at", order="updated_at.desc.nullslast",
                                     limit=1)
            top = iso_utc(probe[0].get("updated_at")) if probe else None
            if not rows or top is None or top != st.get("probe"):
                merge(self.supa.select(table, cols))
                st["probe"] = top
        st["rows"] = rows
        return rows

    # ── commodities (+ cache do risquinho) ──────────────────────────────────────────────────
    def commodities(self) -> list[dict]:
        if "commodities" in self._memo:
            return self._memo["commodities"]
        st = self.state["commodities"]
        cache = self.state["spark"]
        try:
            raw = self.supa.select("commodities", COMM_COLS, order="code")
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
                    for g in self.supa.select("commodities", "code,spark", code=_in(need)):
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
        cache = {t: e for t, e in (st.get("rows") or {}).items() if t in uni and isinstance(e, dict)}
        probe = self.supa.select("quotes", "daily_updated_at",
                                 order="daily_updated_at.desc.nullslast", limit=1)
        top = iso_utc(probe[0].get("daily_updated_at")) if probe else None
        if not (cache and top is not None and top == st.get("probe")):
            if not cache:
                cache.update(self._seed_daily())
            want = {}
            for r in self.supa.select("quotes", "ticker,daily_updated_at"):
                if r.get("ticker") in uni:
                    want[r["ticker"]] = iso_utc(r.get("daily_updated_at"))
            need = [t for t in universe if t in want and (t not in cache or cache[t].get("at") != want[t])]
            if need:
                params = {} if len(need) == len(want) else {"ticker": _in(need)}
                for r in self.supa.select("quotes", "ticker,daily,daily_updated_at", **params):
                    if r.get("ticker") in want:
                        cache[r["ticker"]] = {"at": iso_utc(r.get("daily_updated_at")),
                                              "pts": points(r.get("daily"))}
            for t in [t for t in cache if t not in want]:
                del cache[t]
            st["probe"] = top
        st["rows"] = cache
        self._memo["daily"] = cache
        return cache

    def _seed_daily(self) -> dict[str, dict]:
        """Partida a frio do cache diário a partir do quotes-daily.json PUBLICADO (é o nosso
        próprio arquivo: `pts` = quotes.daily tal qual, `updatedAt` = daily_updated_at já
        normalizado). Quem estiver com carimbo diferente no banco é relido logo em seguida."""
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

    # ── configuração do /admin ──────────────────────────────────────────────────────────────
    def flags(self) -> list[dict]:
        return self._rpc("get_dashboard_flags", "key,enabled")

    def heatmap_admin(self) -> list[dict]:
        return self._rpc("get_heatmap_tickers", "symbol,is_hidden,in_coverage,sort_order")

    def commodity_groups(self) -> list[dict]:
        return self._rpc("get_commodity_groups", "id,title,codes,sort_order,is_visible")

    def _rpc(self, fn: str, cols: str) -> list[dict]:
        if fn not in self._memo:
            self._memo[fn] = self.supa.rpc(fn, cols)
        return self._memo[fn]
