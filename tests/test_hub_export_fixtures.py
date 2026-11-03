"""
O "banco falso" do exportador do hub (hub_export/) — e os testes que travam que ele continua
cobrindo os casos que o brief do WP1.2 exige.

Os outros `test_hub_export_*.py` importam daqui (sem conftest.py, convenção do repositório).
O mundo falso é PEQUENO e REALISTA, com os formatos que o PostgREST devolve de verdade (carimbos
com microssegundos e `+00:00`, `->>` devolvendo texto) e os formatos que o `update_quotes` monta
na memória do ciclo:

  • papéis de 7 bolsas (B3, NYSE, Nasdaq, Santiago, ASX, Toronto, Londres) + um índice;
  • CMPC.SN SEM preço no ciclo (o Yahoo não devolveu) — tem de sair o último valor do estado;
  • AUGO com `session` inválida (fecha antes de abrir) — tem de virar `null`;
  • VMM.AX sem velas no dia (`pts: []`); HBM.TO sem variação (`change_pct` nulo, nunca 0);
  • commodities com e sem `spark`, e um `spark` com chave de depuração que NÃO pode vazar;
  • /admin do heatmap com papel oculto (GGB e B), cobertura em ordem própria, símbolo
    desconhecido (ignorado) e um oculto marcado como cobertura (fica oculto);
  • flags com `hub_market` mandando no `market` e um `hub_` órfão;
  • grupos de commodities visíveis fora de ordem e um invisível.

Nada aqui fala com a rede: o `FakeHTTP` faz o papel do `requests.Session` para o Supabase (um
mini-PostgREST: select com alias e `->>`, filtros `in.`/`eq.`, `order` e `limit`), para o Feeder
(roteiro de respostas por etapa, com falhas sob encomenda) e para a cópia publicada no Mindassets.
"""
from __future__ import annotations

import copy
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hub_export import state as hub_state  # noqa: E402

# ── endereços e segredos FALSOS (os testes conferem que nunca aparecem em log) ─────────────────
SUPA_URL = "https://projeto-secreto.supabase.co"
SUPA_KEY = "service-key-SECRETA-0123456789"
FEEDER_URL = "https://feeder.example.test"
PROJECT_ID = "proj-123"
FEEDER_KEY = "rdf_pub.CHAVE-SECRETA-NAO-PODE-VAZAR"
PUT_HOST = "https://oci.example.test"
PUBLIC_BASE = "https://public.example.test/base"

NOW = datetime(2026, 11, 3, 18, 5, 12, 345678, tzinfo=timezone.utc)
GENERATED = "2026-11-03T18:05:12Z"

# janelas de pregão de 03/11/2026 (epoch, s)
B3_SESS = [1793710800, 1793736000]         # 13:00–20:00 UTC
NY_SESS = [1793716200, 1793739600]         # 14:30–21:00 UTC
LSE_SESS = [1793692800, 1793723400]        # 08:00–16:30 UTC
ASX_SESS = [1793746800, 1793768400]        # 23:00–05:00 UTC (o pregão de 04/11 em Sydney)
BCS_SESS = [1793709000, 1793732400]        # 12:30–19:00 UTC


def env(**extra) -> dict:
    e = {"SUPABASE_URL": SUPA_URL, "SUPABASE_SERVICE_KEY": SUPA_KEY,
         "DASHBOARD_FEEDER_URL": FEEDER_URL, "DASHBOARD_FEEDER_PROJECT_ID": PROJECT_ID,
         "DASHBOARD_FEEDER_API_KEY": FEEDER_KEY, "HUB_EXPORT_PUBLIC_BASE": PUBLIC_BASE}
    e.update(extra)
    return e


# ── linhas de `quotes` como o PostgREST devolve ────────────────────────────────────────────────
def _q(ticker, name, sector, exchange, price, chg, chg_abs, upd, intraday, daily, daily_upd):
    return {"ticker": ticker, "name": name, "sector": sector, "exchange": exchange,
            "price": price, "change_pct": chg, "change_abs": chg_abs, "updated_at": upd,
            "intraday": intraday, "daily": daily, "daily_updated_at": daily_upd}


QUOTES = [
    _q("IBOV", "Ibovespa", "index", "B3", 128450.12, 0.52, 664.3,
       "2026-11-03T18:04:01.123456+00:00",
       {"prev": 127785.82, "pts": [[1793710800, 127800.5], [1793711100, 128100.25]],
        "traded_at": 1793729041, "session": B3_SESS},
       [[1793365200, 127100.0], [1793624400, 127785.82]], "2026-11-03T00:29:08.879604+00:00"),
    _q("VALE3.SA", "Vale", "iron_ore", "B3", 61.23, 1.234, 0.75,
       "2026-11-03T18:04:01.123457+00:00",
       {"prev": 60.48, "pts": [[1793710800, 60.5], [1793711100, 60.7], [1793711400, 61.23]],
        "traded_at": 1793729041, "session": B3_SESS},
       [[1793365200, 59.9], [1793451600, 60.1], [1793624400, 60.48]],
       "2026-11-02T23:59:02.685735+00:00"),
    _q("SCCO", "Southern Copper", "copper", "NYSE", 112.4, -0.85, -0.96,
       "2026-11-03T18:04:01.5+00:00",
       {"prev": 113.36, "pts": [[1793716200, 113.1], [1793716500, 112.4]],
        "traded_at": 1793729100, "session": NY_SESS},
       [[1793367000, 110.0], [1793629800, 113.36]], "2026-11-02T23:55:46.537336+00:00"),
    # sessão inválida (fecha antes de abrir) → session null
    _q("AUGO", "Aura Minerals (Nasdaq)", "gold", "NASDAQ", 24.1, 2.1, 0.5,
       "2026-11-03T18:04:02+00:00",
       {"prev": 23.6, "pts": [[1793716200, 23.9]], "traded_at": 1793729000,
        "session": [NY_SESS[1], NY_SESS[0]]},
       [], "2026-11-02T23:50:00+00:00"),
    # sem preço no ciclo de agora: este é o valor "do banco" (= o do estado anterior)
    _q("CMPC.SN", "CMPC", "pp", "BCS", 1350.0, -0.3, -4.0,
       "2026-11-03T17:49:00.000001+00:00",
       {"prev": 1354.0, "pts": [[1793709000, 1352.0]], "traded_at": 1793725000,
        "session": BCS_SESS},
       [[1793365200, 1340.0], [1793624400, 1354.0]], "2026-11-03T00:10:00+00:00"),
    # Sydney fechada, sem vela do dia (pts []), último negócio de ontem
    _q("VMM.AX", "Viridis Mining", "rare_earths", "ASX", 0.62, 3.33, 0.02,
       "2026-11-03T18:04:03+00:00",
       {"prev": 0.6, "pts": [], "traded_at": 1793682000, "session": ASX_SESS},
       [[1793365200, 0.58]], "2026-11-03T00:05:00+00:00"),
    # variação ausente (Yahoo sem fechamento anterior) → null, nunca 0
    _q("HBM.TO", "Hudbay Minerals", "copper", "TSX", 25.4, None, None,
       "2026-11-03T18:04:03.25+00:00",
       {"prev": None, "pts": [[1793716200, 25.4]], "traded_at": 1793729050, "session": NY_SESS},
       [[1793367000, 24.9]], "2026-11-02T23:40:00+00:00"),
    _q("AAL.L", "Anglo American", "iron_ore", "LSE", 2380.5, 0.4, 9.5,
       "2026-11-03T18:04:03.75+00:00",
       {"prev": 2371.0, "pts": [[1793692800, 2372.0], [1793710800, 2380.5]],
        "traded_at": 1793723400, "session": LSE_SESS},
       [[1793365200, 2300.0]], "2026-11-02T23:30:00+00:00"),
]
MISSING_IN_CYCLE = "CMPC.SN"

MACRO = [
    {"code": "USD_BRL", "name": "USD/BRL", "unit": "R$", "value": 5.4948, "change_pct": 1.071,
     "updated_at": "2026-11-03T18:04:11.95015+00:00"},
    {"code": "US10Y", "name": "US 10Y Yield", "unit": "%", "value": 4.123, "change_pct": None,
     "updated_at": "2026-11-03T18:04:11.950183+00:00"},
    # variação ZERO legítima (fica 0.0) — e o BCB não respondeu no ciclo de agora
    {"code": "SELIC", "name": "SELIC Target", "unit": "%", "value": 15.0, "change_pct": 0.0,
     "updated_at": "2026-11-03T09:00:00+00:00"},
]
MACRO_MISSING_IN_CYCLE = "SELIC"


def _spark(asof, v=2, ph=None, extra=None):
    sp = {"asof": asof, "v": v,
          "d": {"c": -0.4, "s": [0, 250, 1000, 600], "b": 2},
          "w": {"c": 1.2, "s": [100, 0, 400, 1000, 600], "b": 0},
          "m": {"c": None, "s": [500, 500, 1000], "b": 0},
          "ytd": {"c": 12.5, "s": [0, 1000], "b": 0},
          "y": {"c": -3.0, "s": [1000, 0, 300], "b": 0}}
    if ph is not None:
        sp["ph"] = ph
    sp.update(extra or {})
    return sp


COMMODITIES = [
    {"code": "IRON_ORE", "name": "IO Fines 61%", "unit": "USD/dmt", "price": 99.05,
     "change_pct": -0.4, "assessed_at": "2026-11-02", "updated_at": "2026-11-03T17:32:39.736573+00:00",
     "spark": _spark("2026-11-02")},
    {"code": "COPPER", "name": "Copper", "unit": "USD/lb", "price": 5.1234, "change_pct": 0.8,
     "assessed_at": "2026-11-03", "updated_at": "2026-11-03T18:04:05.1+00:00",
     "spark": _spark("2026-11-03", ph=0)},
    {"code": "HRC_CHINA", "name": "HRC China", "unit": "USD/mt", "price": 455.0,
     "change_pct": None, "assessed_at": "2026-10-31", "updated_at": "2026-11-03T17:32:39.7+00:00",
     "spark": None},
    # chave de depuração dentro do spark: a lista branca tem de barrar
    {"code": "PULP_BHKP_CHINA", "name": "BHKP China Net", "unit": "USD/t", "price": 560.0,
     "change_pct": 0.0, "assessed_at": "2026-10-30", "updated_at": "2026-11-03T11:00:00+00:00",
     "spark": _spark("2026-10-30", extra={"_debug": {"token": "x"}})},
]

FLAGS = [
    {"key": "heatmap", "enabled": True},
    {"key": "commodities", "enabled": True},
    {"key": "market", "enabled": True},
    {"key": "hub_market", "enabled": False},     # o hub manda: market → false
    {"key": "news_feed", "enabled": False},
    {"key": "hub_orphan", "enabled": True},      # sem "orphan" no /admin: não aparece
]

HEATMAP_ADMIN = [
    {"symbol": "GGB", "is_hidden": True, "in_coverage": False, "sort_order": 0},
    {"symbol": "ZZZZ.SA", "is_hidden": False, "in_coverage": True, "sort_order": 0},  # desconhecido
    {"symbol": "VALE", "is_hidden": False, "in_coverage": True, "sort_order": 1},
    {"symbol": "SUZB3.SA", "is_hidden": False, "in_coverage": True, "sort_order": 2},
    {"symbol": "B", "is_hidden": True, "in_coverage": True, "sort_order": 3},          # oculto vence
]

COMMODITY_GROUPS = [
    {"id": "77474b40-f8b1-4181-bc24-10a6413d5894", "title": "Iron Ore + Pellet",
     "codes": ["IRON_ORE", "IOPRM00"], "sort_order": 10, "is_visible": True},
    {"id": "2054d74f-5aec-4abf-b749-9370605c889c", "title": "Base & Precious Metals",
     "codes": ["GOLD", "COPPER"], "sort_order": 5, "is_visible": True},
    {"id": "15fea25d-e224-4a60-b5d9-4a44b5deb005", "title": "Escondido",
     "codes": ["PULP_BHKP_CHINA"], "sort_order": 1, "is_visible": False},
]


def db() -> dict:
    return copy.deepcopy({"quotes": QUOTES, "macro_indicators": MACRO, "commodities": COMMODITIES,
                          "rpc": {"get_dashboard_flags": FLAGS,
                                  "get_heatmap_tickers": HEATMAP_ADMIN,
                                  "get_commodity_groups": COMMODITY_GROUPS}})


# ── a memória do ciclo, como o update_quotes/update_macro monta ───────────────────────────────
def memory_quotes() -> list[dict]:
    rows = []
    for r in QUOTES:
        if r["ticker"] == MISSING_IN_CYCLE:
            continue
        rows.append({k: copy.deepcopy(r[k]) for k in
                     ("ticker", "name", "sector", "exchange", "price", "change_pct", "change_abs",
                      "intraday", "updated_at")})
    return rows


def memory_macro() -> list[dict]:
    return [copy.deepcopy(r) for r in MACRO if r["code"] != MACRO_MISSING_IN_CYCLE]


def memory() -> dict:
    return {"quotes": memory_quotes(), "macro": memory_macro()}


def previous_state() -> dict:
    """O estado deixado pelo ciclo anterior: já conhece CMPC.SN e SELIC (que faltam agora) e já
    consultou o banco pelos papéis que ninguém trouxe (`checked`) — como fica depois do 1º ciclo."""
    from hub_export.sources import _macro_row, _quote_row, macro_universe, quote_universe
    st = hub_state.empty_state()
    cmpc = next(r for r in QUOTES if r["ticker"] == MISSING_IN_CYCLE)
    st["quotes"]["rows"][MISSING_IN_CYCLE] = _quote_row(cmpc)
    st["quotes"]["checked"] = sorted(t for t, _n, _e in quote_universe())
    selic = next(r for r in MACRO if r["code"] == MACRO_MISSING_IN_CYCLE)
    st["macro"]["rows"][MACRO_MISSING_IN_CYCLE] = _macro_row(selic)
    st["macro"]["checked"] = sorted(c for c, _n, _u in macro_universe())
    return st


# ── o HTTP falso ──────────────────────────────────────────────────────────────────────────────
class FakeResp:
    def __init__(self, status: int = 200, body=b"", headers: dict | None = None):
        self.status_code = status
        self.content = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        self.headers = dict(headers or {})

    def json(self):
        return json.loads(self.content)


@dataclass
class Call:
    method: str
    url: str
    params: dict
    headers: dict
    data: bytes | None
    timeout: float | None

    @property
    def table(self) -> str | None:
        if self.url.startswith(SUPA_URL + "/rest/v1/"):
            return self.url[len(SUPA_URL + "/rest/v1/"):]
        return None


def _arrow(row: dict, expr: str):
    col, key = expr.split("->>", 1)
    obj = row.get(col)
    val = obj.get(key) if isinstance(obj, dict) else None
    if val is None:
        return None
    if isinstance(val, bool):
        return "true" if val else "false"
    return val if isinstance(val, str) else json.dumps(val)


def _project(row: dict, select: str) -> dict:
    out = {}
    for item in select.split(","):
        item = item.strip()
        alias, expr = item.split(":", 1) if ":" in item else (item, item)
        out[alias] = _arrow(row, expr) if "->>" in expr else copy.deepcopy(row.get(expr))
    return out


def _matches(row: dict, params: dict) -> bool:
    for k, v in params.items():
        if k in ("select", "order", "limit"):
            continue
        if v.startswith("in.(") and v.endswith(")"):
            values = [x.strip().strip('"') for x in v[4:-1].split(",")]
            if str(row.get(k)) not in values:
                return False
        elif v.startswith("eq."):
            if str(row.get(k)) != v[3:]:
                return False
        else:
            raise AssertionError(f"filtro não suportado pelo falso: {k}={v}")
    return True


def _order(rows: list[dict], spec: str) -> list[dict]:
    parts = spec.split(".")
    col, desc, nullslast = parts[0], "desc" in parts, "nullslast" in parts
    present = [r for r in rows if r.get(col) is not None]
    absent = [r for r in rows if r.get(col) is None]
    present.sort(key=lambda r: r[col], reverse=desc)
    nulls_first = desc and not nullslast
    return absent + present if nulls_first else present + absent


@dataclass
class FakeHTTP:
    """`requests.Session` falso. `feeder_script[etapa]` = lista de respostas (status ou exceção)
    consumidas na ordem; vazia = sucesso. Etapas: initialize, PUT, complete, failed."""
    database: dict = field(default_factory=db)
    published: dict = field(default_factory=dict)          # nome → bytes (cópia no Mindassets)
    feeder_script: dict = field(default_factory=dict)
    supa_error: dict = field(default_factory=dict)          # tabela/rpc → status ou exceção
    calls: list = field(default_factory=list)
    uploads: dict = field(default_factory=dict)             # nome → bytes recebidos no PUT
    revision: int = 0

    def request(self, method, url, headers=None, params=None, data=None, json=None,
                timeout=None, stream=False):
        call = Call(method, url, dict(params or {}), dict(headers or {}), data, timeout)
        self.calls.append(call)
        if url.startswith(SUPA_URL):
            return self._supa(call)
        if url.startswith(FEEDER_URL) or url.startswith(PUT_HOST):
            return self._feeder(call)
        if url.startswith(PUBLIC_BASE):
            name = url[len(PUBLIC_BASE) + 1:]
            if name in self.published:
                return FakeResp(200, self.published[name])
            return FakeResp(404, b"<Error>NoSuchKey</Error>")
        raise AssertionError(f"URL inesperada no teste: {url}")

    # Supabase ─────────────────────────────────────────────────────────────────────────────
    def _supa(self, call: Call):
        path = call.url[len(SUPA_URL):]
        name = path.rsplit("/", 1)[1]
        err = self.supa_error.get(name)
        if isinstance(err, BaseException):
            raise err
        if isinstance(err, int):
            return FakeResp(err, {"message": "erro falso"})
        rows = (self.database["rpc"][name] if path.startswith("/rest/v1/rpc/")
                else self.database[name])
        rows = [r for r in rows if _matches(r, call.params)]
        if "order" in call.params:
            rows = _order(rows, call.params["order"])
        if "limit" in call.params:
            rows = rows[:int(call.params["limit"])]
        return FakeResp(200, [_project(r, call.params["select"]) for r in rows])

    # Feeder ───────────────────────────────────────────────────────────────────────────────
    def _next(self, step):
        queue = self.feeder_script.get(step) or []
        return queue.pop(0) if queue else 200

    def _feeder(self, call: Call):
        if call.method == "PUT":
            step = "PUT"
        elif call.url.endswith("/complete"):
            step = "complete"
        elif call.url.endswith("/failed"):
            step = "failed"
        else:
            step = "initialize"
        outcome = self._next(step)
        if isinstance(outcome, BaseException):
            raise outcome
        if outcome != 200:
            return FakeResp(outcome, {"error": f"{step} falhou"})
        if step == "initialize":
            body = json.loads(call.data)
            self.revision += 1
            upd = f"upd-{self.revision}"
            ctype = body["contentType"]
            return FakeResp(200, {"update": {"id": upd, "status": "AWAITING_UPLOAD"},
                                  "upload": {"url": f"{PUT_HOST}/bucket/{body['name']}"
                                                    f"?X-Amz-Signature=ASSINATURA-SECRETA-{upd}",
                                             "requiredHeaders": {"content-type": ctype}}})
        if step == "PUT":
            name = call.url.split("/bucket/", 1)[1].split("?", 1)[0]
            self.uploads[name] = call.data
            return FakeResp(200, b"")
        if step == "complete":
            return FakeResp(200, {"update": {"id": call.url.split("/")[-2],
                                             "revision": self.revision}})
        return FakeResp(200, {"update": {"status": "UPLOAD_FAILED"}})

    # leitura dos registros ───────────────────────────────────────────────────────────────
    def supa_calls(self, table: str | None = None) -> list[Call]:
        return [c for c in self.calls if c.table is not None
                and (table is None or c.table == table)]

    def feeder_calls(self) -> list[Call]:
        return [c for c in self.calls if c.url.startswith((FEEDER_URL, PUT_HOST))]


def connection_error():
    return requests.ConnectionError(f"HTTPSConnectionPool: falhou {PUT_HOST}/x?X-Amz-Signature=SIG")


def make_sources(http=None, state=None, mem="default", published=None, now=NOW):
    """Sources ligado ao banco falso. `mem="default"` = a memória do ciclo das fixtures."""
    from hub_export.sources import Sources
    from hub_export.supa import SupaReader
    http = http or FakeHTTP()
    supa = SupaReader(SUPA_URL, SUPA_KEY, http=http)
    st = hub_state.empty_state() if state is None else state
    return Sources(supa, st, now=now, memory=memory() if mem == "default" else mem,
                   published=published), http


# ── os testes do próprio banco falso ─────────────────────────────────────────────────────────
def test_fixtures_cobrem_os_casos_do_brief():
    bolsas = {r["exchange"] for r in QUOTES}
    assert len(bolsas) >= 6
    assert MISSING_IN_CYCLE not in {r["ticker"] for r in memory_quotes()}
    sess = next(r for r in QUOTES if r["ticker"] == "AUGO")["intraday"]["session"]
    assert sess[1] < sess[0]                                    # sessão inválida
    assert any(r["spark"] is None for r in COMMODITIES)
    assert any(r["spark"] for r in COMMODITIES)
    assert any(r["is_hidden"] for r in HEATMAP_ADMIN)
    assert any(r["in_coverage"] and not r["is_hidden"] for r in HEATMAP_ADMIN)


def test_mini_postgrest_entende_alias_seta_filtro_e_ordem():
    http = FakeHTTP()
    r = http.request("GET", SUPA_URL + "/rest/v1/commodities",
                     params={"select": "code,asof:spark->>asof,v:spark->>v",
                             "code": 'in.("COPPER","HRC_CHINA")', "order": "code"})
    assert r.json() == [{"code": "COPPER", "asof": "2026-11-03", "v": "2"},
                        {"code": "HRC_CHINA", "asof": None, "v": None}]
    r = http.request("GET", SUPA_URL + "/rest/v1/quotes",
                     params={"select": "updated_at", "order": "updated_at.desc.nullslast",
                             "limit": "1"})
    assert r.json() == [{"updated_at": "2026-11-03T18:04:03.75+00:00"}]
