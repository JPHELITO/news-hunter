"""
Os 4 construtores do hub (hub_export/exporters/) — a forma EXATA do contrato (PLANO, Apêndice B).

Do outro lado, o hub (React) lê estes arquivos com `parse*` que validam campo a campo; qualquer
nome ou forma diferente vira "dado indisponível" na tela do cliente. O que estes testes TRAVAM:
  1) as chaves e a ordem de cada arquivo, a partir do banco falso das fixtures;
  2) o /admin do heatmap aplicado como o `applyHeatmapConfig` do site: oculto sai de TODOS os
     grupos (o B some do Ouro E do Cobre) e vai para `hidden`; a cobertura segue o `sort_order`
     do /admin, só com papéis conhecidos; sem cobertura no /admin → o grupo constante;
  3) `hub_k` manda em `k` nas flags, e nenhuma `hub_*` aparece no arquivo;
  4) nulo nunca vira 0 (e o zero legítimo continua zero), `session` inválida vira null;
  5) papel que faltou na memória do ciclo sai com o último valor do ESTADO — sem ler o banco;
  6) os mapas constantes batem com o QUOTES_LIST (toda bolsa mapeada, todo papel dos grupos existe);
  7) os dois caminhos (memória do ciclo + estado, no loop; banco inteiro, no CLI) dão o MESMO
     arquivo quando o mundo é o mesmo.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import test_hub_export_fixtures as F  # noqa: E402
from hub_export.encode import content_hash  # noqa: E402
from hub_export.exporters import config, intraday, live, quotes_daily, reference  # noqa: E402
from hunter import prices  # noqa: E402

QUOTES_ORDER = [q[0] for q in prices.QUOTES_LIST]
MACRO_ORDER = [m[0] for m in prices.MACRO_YAHOO] + [m[0] for m in prices.MACRO_BCB]


def hunt_sources(**kw):
    kw.setdefault("state", F.previous_state())
    return F.make_sources(**kw)


# ── config.json ───────────────────────────────────────────────────────────────────────────────
def test_config_forma_exata():
    src, _ = hunt_sources()
    c = config.build(src)
    assert list(c) == ["schemaVersion", "generatedAt", "flags", "exchanges", "heatmap",
                       "commodityGroups"]
    assert c["schemaVersion"] == 1 and c["generatedAt"] == F.GENERATED
    assert c["flags"] == {"heatmap": True, "commodities": True, "market": False, "news_feed": False}
    hm = c["heatmap"]
    assert list(hm) == ["max", "groups", "coverage", "hidden", "benchmarkByExchange",
                        "benchmarkLabels"]
    assert hm["max"] == 20
    assert [g["id"] for g in hm["groups"]] == ["coverage", "steel", "iron_ore", "gold", "copper",
                                               "rare_earths", "pp", "index"]
    assert [g["label"] for g in hm["groups"]] == [
        {"pt-br": "Nossa cobertura", "en-us": "Our coverage"}, {"pt-br": "Aço", "en-us": "Steel"},
        {"pt-br": "Minério de ferro", "en-us": "Iron Ore"}, {"pt-br": "Ouro", "en-us": "Gold"},
        {"pt-br": "Cobre", "en-us": "Copper"}, {"pt-br": "Terras raras", "en-us": "Rare Earths"},
        {"pt-br": "Papel e celulose", "en-us": "P&P"}, {"pt-br": "Índices", "en-us": "Index"}]
    assert all(list(g) == ["id", "label", "tickers"] for g in hm["groups"])
    assert hm["benchmarkByExchange"] == {"B3": "IBOV", "NYSE": "SPX", "NASDAQ": "SPX", "US": "SPX",
                                         "TSX": "SPTSX", "ASX": "ASX200", "BMV": "MEXBOL",
                                         "BCS": "IPSA", "LSE": "FTSE100", "HEL": "OMXH25"}
    assert hm["benchmarkLabels"] == {"IBOV": "IBOV", "SPX": "S&P 500", "SPTSX": "S&P/TSX",
                                     "ASX200": "ASX 200", "MEXBOL": "IPC", "IPSA": "IPSA",
                                     "FTSE100": "FTSE 100", "OMXH25": "OMXH25"}
    assert "sectorIndex" not in hm
    assert c["commodityGroups"] == [
        {"id": "2054d74f-5aec-4abf-b749-9370605c889c", "title": "Base & Precious Metals",
         "codes": ["GOLD", "COPPER"]},
        {"id": "77474b40-f8b1-4181-bc24-10a6413d5894", "title": "Iron Ore + Pellet",
         "codes": ["IRON_ORE", "IOPRM00"]}]


def test_config_bolsas_do_quotes_list():
    src, _ = hunt_sources()
    ex = config.build(src)["exchanges"]
    assert set(ex) == {q[3] for q in prices.QUOTES_LIST}
    assert set(ex) == {"B3", "NYSE", "NASDAQ", "US", "TSX", "ASX", "BMV", "BCS", "LSE", "HEL",
                       "AMS", "CME"}
    assert ex["B3"] == {"name": "B3", "tz": "America/Sao_Paulo", "currency": "BRL"}
    assert ex["NYSE"] == {"name": "NYSE", "tz": "America/New_York", "currency": "USD"}
    assert ex["BCS"] == {"name": "Santiago", "tz": "America/Santiago", "currency": "CLP"}
    assert ex["LSE"]["currency"] == "GBp" and ex["US"]["name"] == "US market"
    assert ex["AMS"]["currency"] is None and ex["CME"]["currency"] is None
    assert all(list(v) == ["name", "tz", "currency"] and v["tz"] for v in ex.values())


def test_admin_do_heatmap_aplicado_como_no_site():
    src, _ = hunt_sources()
    hm = config.build(src)["heatmap"]
    groups = {g["id"]: g["tickers"] for g in hm["groups"]}
    assert hm["hidden"] == ["GGB", "B"]                       # na ordem do /admin
    for gid, tickers in groups.items():                       # oculto sai de TODOS os grupos
        assert "GGB" not in tickers and "B" not in tickers, gid
    assert reference.G_GOLD.count("B") == 1 and reference.G_COPPER.count("B") == 1
    assert groups["gold"] == ["AUGO", "ARIS", "BVN", "AEM", "HOC.L"]
    assert groups["steel"] == [t for t in reference.G_STEEL if t != "GGB"]
    # cobertura: sort_order do /admin; desconhecido (ZZZZ.SA) e oculto (B) ficam de fora
    assert hm["coverage"] == ["VALE", "SUZB3.SA"] == groups["coverage"]


def test_sem_cobertura_no_admin_vale_o_grupo_constante():
    http = F.FakeHTTP()
    http.database["rpc"]["get_heatmap_tickers"] = []
    src, _ = hunt_sources(http=http)
    hm = config.build(src)["heatmap"]
    assert hm["coverage"] == reference.G_COVERAGE and hm["hidden"] == []
    assert hm["groups"][0]["tickers"] == reference.G_COVERAGE
    # só desconhecidos/ocultos como cobertura → também cai no constante (sem os ocultos)
    http = F.FakeHTTP()
    http.database["rpc"]["get_heatmap_tickers"] = [
        {"symbol": "NAOEXISTE", "is_hidden": False, "in_coverage": True, "sort_order": 1},
        {"symbol": "AUGO", "is_hidden": True, "in_coverage": True, "sort_order": 2}]
    src, _ = hunt_sources(http=http)
    hm = config.build(src)["heatmap"]
    assert hm["coverage"] == [t for t in reference.G_COVERAGE if t != "AUGO"]


def test_flags_hub_mandam_e_somem():
    src, _ = hunt_sources()
    flags = config.build(src)["flags"]
    assert flags["market"] is False                           # hub_market=false venceu market=true
    assert not [k for k in flags if k.startswith("hub_")] and "orphan" not in flags


# ── live.json ─────────────────────────────────────────────────────────────────────────────────
def test_live_forma_exata():
    src, _ = hunt_sources()
    lv = live.build(src)
    assert list(lv) == ["schemaVersion", "generatedAt", "quotes", "commodities", "macro"]
    assert lv["schemaVersion"] == 1 and lv["generatedAt"] == F.GENERATED
    items = lv["quotes"]["items"]
    assert [i["ticker"] for i in items] == QUOTES_ORDER       # TODOS, na ordem do QUOTES_LIST
    assert all(list(i) == ["ticker", "name", "exchange", "price", "changePct", "changeAbs",
                           "prevClose", "tradedAt", "session", "updatedAt"] for i in items)
    vale = next(i for i in items if i["ticker"] == "VALE3.SA")
    assert vale == {"ticker": "VALE3.SA", "name": "Vale", "exchange": "B3", "price": 61.23,
                    "changePct": 1.234, "changeAbs": 0.75, "prevClose": 60.48,
                    "tradedAt": 1793729041, "session": F.B3_SESS,
                    "updatedAt": "2026-11-03T18:04:01Z"}
    assert lv["quotes"]["updatedAt"] == "2026-11-03T18:04:03Z"
    assert list(lv["commodities"]) == ["updatedAt", "items"]
    assert lv["commodities"]["updatedAt"] == "2026-11-03T18:04:05Z"
    copper = next(c for c in lv["commodities"]["items"] if c["code"] == "COPPER")
    assert list(copper) == ["code", "name", "unit", "price", "changePct", "assessedAt",
                            "updatedAt", "spark"]
    assert copper["assessedAt"] == "2026-11-03" and copper["updatedAt"] == "2026-11-03T18:04:05Z"
    assert list(copper["spark"]) == ["asof", "v", "ph", "d", "w", "m", "ytd", "y"]
    assert copper["spark"]["d"] == {"c": -0.4, "s": [0, 250, 1000, 600], "b": 2}
    assert [c["code"] for c in lv["commodities"]["items"]] == sorted(r["code"] for r in F.COMMODITIES)
    usd = lv["macro"]["items"][0]
    assert usd == {"code": "USD_BRL", "name": "USD/BRL", "unit": "R$", "value": 5.4948,
                   "changePct": 1.071, "updatedAt": "2026-11-03T18:04:11Z"}
    assert [m["code"] for m in lv["macro"]["items"]] == MACRO_ORDER
    assert lv["macro"]["updatedAt"] == "2026-11-03T18:04:11Z"


def test_live_nulos_nunca_viram_zero_e_session_invalida_vira_null():
    src, _ = hunt_sources()
    lv = live.build(src)
    q = {i["ticker"]: i for i in lv["quotes"]["items"]}
    assert q["AUGO"]["session"] is None and q["AUGO"]["tradedAt"] == 1793729000
    assert q["HBM.TO"]["changePct"] is None and q["HBM.TO"]["changeAbs"] is None
    assert q["HBM.TO"]["prevClose"] is None and q["HBM.TO"]["price"] == 25.4
    sem_dado = q["GGB"]                                       # nenhum dado em lugar nenhum
    assert sem_dado["name"] == "Gerdau (NYSE)" and sem_dado["exchange"] == "NYSE"
    assert all(sem_dado[k] is None for k in ("price", "changePct", "changeAbs", "prevClose",
                                             "tradedAt", "session", "updatedAt"))
    c = {i["code"]: i for i in lv["commodities"]["items"]}
    assert c["HRC_CHINA"]["changePct"] is None and c["HRC_CHINA"]["spark"] is None
    assert c["PULP_BHKP_CHINA"]["changePct"] == 0.0           # zero legítimo continua zero
    m = {i["code"]: i for i in lv["macro"]["items"]}
    assert m["US10Y"]["changePct"] is None
    assert m["SELIC"]["changePct"] == 0.0 and m["SELIC"]["value"] == 15.0
    assert m["USD_CAD"]["value"] is None and m["USD_CAD"]["updatedAt"] is None


def test_spark_copiado_campo_a_campo():
    src, _ = hunt_sources()
    c = {i["code"]: i for i in live.build(src)["commodities"]["items"]}
    sp = c["PULP_BHKP_CHINA"]["spark"]
    assert "_debug" not in sp and list(sp) == ["asof", "v", "d", "w", "m", "ytd", "y"]
    assert sp["m"] == {"c": None, "s": [500, 500, 1000], "b": 0}
    assert live._spark({"asof": "2026-11-02", "d": {"c": 1, "s": [1, None], "b": 0}}) == \
        {"asof": "2026-11-02"}                                # janela com ponto ruim fica de fora
    assert live._spark("lixo") is None


def test_papel_que_faltou_no_ciclo_fica_com_o_valor_do_estado_sem_ler_o_banco():
    http = F.FakeHTTP()
    cmpc = next(r for r in http.database["quotes"] if r["ticker"] == F.MISSING_IN_CYCLE)
    cmpc["price"] = 9999.0                                     # o banco mudou… mas não é lido
    cmpc["updated_at"] = "2026-11-03T18:04:59+00:00"
    src, http = hunt_sources(http=http)
    q = {i["ticker"]: i for i in live.build(src)["quotes"]["items"]}
    assert q["CMPC.SN"]["price"] == 1350.0 and q["CMPC.SN"]["updatedAt"] == "2026-11-03T17:49:00Z"
    assert q["CMPC.SN"]["session"] == F.BCS_SESS
    assert not [c for c in http.supa_calls("quotes") if "price" in c.params["select"]
                and "CMPC.SN" in c.params.get("ticker", "")]


# ── intraday.json e quotes-daily.json ─────────────────────────────────────────────────────────
def test_intraday_forma_exata():
    src, _ = hunt_sources()
    it = intraday.build(src)
    assert list(it) == ["schemaVersion", "generatedAt", "updatedAt", "series"]
    assert it["updatedAt"] == "2026-11-03T18:04:03Z"
    com_linha = [t for t in QUOTES_ORDER if t in {r["ticker"] for r in F.QUOTES}]
    assert list(it["series"]) == com_linha                    # papel sem linha nenhuma fica fora
    assert it["series"]["VALE3.SA"] == {"prev": 60.48, "pts": [[1793710800, 60.5],
                                                               [1793711100, 60.7],
                                                               [1793711400, 61.23]]}
    assert it["series"]["VMM.AX"] == {"prev": 0.6, "pts": []}
    assert it["series"]["HBM.TO"]["prev"] is None


def test_quotes_daily_forma_exata():
    src, _ = hunt_sources()
    d = quotes_daily.build(src)
    assert list(d) == ["schemaVersion", "generatedAt", "series"]
    assert d["series"]["VALE3.SA"] == {"updatedAt": "2026-11-02T23:59:02Z",
                                       "pts": [[1793365200, 59.9], [1793451600, 60.1],
                                               [1793624400, 60.48]]}
    assert d["series"]["AUGO"] == {"updatedAt": "2026-11-02T23:50:00Z", "pts": []}
    assert list(d["series"]) == [t for t in QUOTES_ORDER if t in d["series"]]


# ── coerência e equivalência ──────────────────────────────────────────────────────────────────
def test_mapas_constantes_batem_com_o_quotes_list():
    tickers = set(QUOTES_ORDER)
    for gid, _pt, _en, grupo in reference.HEATMAP_GROUPS:
        assert set(grupo) <= tickers, (gid, set(grupo) - tickers)
    assert {q[3] for q in prices.QUOTES_LIST} <= set(reference.EXCHANGES)
    assert set(reference.BENCH_BY_EXCH) <= set(reference.EXCHANGES)
    assert set(reference.BENCH_BY_EXCH.values()) <= tickers
    assert set(reference.BENCH_LABEL) == set(reference.BENCH_BY_EXCH.values())


def test_loop_e_cli_dao_o_mesmo_arquivo():
    """Memória do ciclo + estado (loop) × banco inteiro (CLI, sem memória): mesmo mundo, mesmo hash."""
    loop, _ = hunt_sources()
    cli, http = F.make_sources(mem=None)
    for b in (live.build, intraday.build, quotes_daily.build, config.build):
        assert content_hash(b(loop)) == content_hash(b(cli)), b.__module__
    assert http.supa_calls("quotes")                           # o CLI leu o banco
