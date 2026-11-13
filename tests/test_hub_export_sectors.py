"""
Os setores da Onda 3 — Steel & Mining (`mm-*`) e Pulp & Paper (`pp-*`) — no exportador do hub
(hub_export/github.py, sectors.py, exporters/mm_*.py, pp_*.py, sector_index.py, raw_table.py;
PLANO, Apêndice G; pesquisa 3-dados §5 e §9.1).

O que estes testes TRAVAM, e por quê:
  1) o cliente do GitHub: sonda (SHA do blob + tamanho), download CONFERIDO pelo sha1 do git (SHA
     errado → erro e nada usado), commit; o token só no cabeçalho, nunca em URL/mensagem/log;
  2) a codificação em corridas, ida e volta: `0` × `null`, 3 meses vazios ficam no trecho, 4 abrem um
     novo; e a prova com o banco de fixture: os 8 arquivos de país devolvem as linhas do `SELECT` da
     página, a celulose também, e a base bruta devolve o `SELECT *` do site (ordem, valores e tipos);
  3) campo a campo: a forma de cada um dos 17 (chaves exatas), o que NUNCA sai (G.0: `value_usd`,
     `Wire Rod`, subcategorias, `exprev_*`, `updated_at` por linha…) e a base bruta como a exceção
     declarada (G.11) — com a rede embaixo: coluna de nome proibido ou BLOB fazem ela falhar;
  4) o plano EVENT: blob igual → nenhum download (e nem sonda dentro de 15 min); blob novo sem dado
     novo → só o índice; teto de 4 por ciclo, o resto na fila SEM ser montado, o índice esperando e
     um download só; partida a frio com o índice publicado igual → nada a montar; sem índice →
     compara cada arquivo com a cópia publicada; Mindassets parado → montagem só na hora diária;
  5) falhas: SHA do download errado → erro, nada publicado; coluna sumida → aquele arquivo falha, os
     outros seguem e o índice espera; GitHub fora do ar ou no limite → nenhum arquivo do setor e o
     ciclo segue; sem GH_TOKEN no envio → o setor nem entra no ciclo (uma linha de log);
  6) os modos: dry-run simula sem mexer no registro e sem baixar à toa; --out grava os mesmos bytes
     do envio e roda sem rede com `HUB_EXPORT_SECTORS_DB_DIR` (o blob = sha1 do arquivo);
  7) o registro (cadência EVENT, gzip, grupo, ordem com o índice por último) e os atalhos do --only;
  8) os bancos de fixture guardados em `hub_export/samples/sectors/` batem com o gerador.
"""
from __future__ import annotations

import gzip
import json
import logging
import os
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import test_hub_export_fixtures as F  # noqa: E402
from hub_export import schedule as SCH  # noqa: E402
from hub_export import sectors as SE  # noqa: E402
from hub_export import state as S  # noqa: E402
from hub_export.encode import forbidden_keys, maybe_gunzip, to_json_bytes  # noqa: E402
from hub_export.exporters.raw_table import ORDERS, rows_of  # noqa: E402
from hub_export.exporters.sector_common import (SchemaError, encode_runs, expand_runs,  # noqa: E402
                                                month_index, month_label)
from hub_export.github import GitHubClient, GitHubError, git_blob_sha  # noqa: E402
from hub_export.registry import FILES, GROUP_CAPS, ORDER, resolve, spec_for  # noqa: E402
from hub_export.run import run_cycle  # noqa: E402

ENV = F.env(GH_TOKEN=F.GH_TOKEN)
MM_PATH, PP_PATH = SE.DBS[SE.MM].path, SE.DBS[SE.PP].path
DATA_17 = [n for n in SE.ALL_FILES if n not in SE.RAW_TABLE_OF]


@pytest.fixture(autouse=True)
def _sem_teto(monkeypatch):
    F.no_mw_cap(monkeypatch)                    # o teto tem os testes próprios (GROUP_CAPS de volta)


def run(http, path, now=F.NOW, only="sectors", env=None, **kw):
    return {r.name: r for r in run_cycle(env=ENV if env is None else env, http=http, now=now,
                                         state_file=path, only=only, **F.run_kw(**kw))}


def acts(res) -> dict:
    return {n: r.action for n, r in res.items()}


def doc(payload: bytes) -> dict:
    return json.loads(maybe_gunzip(payload))


def fixture_con(sector: str) -> sqlite3.Connection:
    """O banco de fixture descomprimido, só leitura (direto dos bytes guardados)."""
    data = F.sector_files()[SE.DBS[sector].path]
    raw = gzip.decompress(data) if SE.DBS[sector].gz else data
    con = sqlite3.connect(":memory:")
    con.deserialize(raw)
    return con


@pytest.fixture(scope="module")
def built():
    src, http = F.make_sources()
    return F.sector_objects(src)


# ── 1) o cliente do GitHub ─────────────────────────────────────────────────────────────────────
def test_github_sonda_download_e_commit_com_o_token_so_no_cabecalho():
    http = F.FakeHTTP()
    gh = GitHubClient(token=F.GH_TOKEN, http=http)
    sha, size = gh.probe(MM_PATH)
    assert sha == F.sector_blob(SE.MM) and size == len(F.sector_files()[MM_PATH])
    assert git_blob_sha(gh.download(sha, size)) == sha
    assert gh.last_commit(MM_PATH) == F.SECTOR_COMMITS[SE.MM]
    assert gh.calls == 3 and gh.bytes > size
    sonda = http.github_calls("sonda")[0]
    assert "/contents/Steel%20and%20Mining/steel_sm_web.db.gz" in sonda.url        # espaço codificado
    for c in http.github_calls():
        assert c.headers["Authorization"] == f"Bearer {F.GH_TOKEN}"
        assert c.headers["User-Agent"] == "hub-export"
        assert F.GH_TOKEN not in c.url and all(F.GH_TOKEN not in str(v) for v in c.params.values())
    http = F.FakeHTTP()
    GitHubClient(http=http).probe(PP_PATH)                                        # sem token
    assert "Authorization" not in http.github_calls()[0].headers


def test_github_sha_errado_tamanho_errado_limite_e_rede_sem_vazar_nada():
    http = F.FakeHTTP()
    http.github.corrupt = True
    gh = GitHubClient(token=F.GH_TOKEN, http=http)
    sha, size = gh.probe(MM_PATH)
    with pytest.raises(GitHubError, match="SHA do conteúdo"):
        gh.download(sha, size)
    with pytest.raises(GitHubError, match="a sonda disse"):
        gh.download(sha, size + 1)
    falhas = {"limite de taxa": F.FakeResp(403, {"message": "API rate limit exceeded"},
                                          headers={"X-RateLimit-Remaining": "0"}),
              "HTTP 500": 500,
              "sem resposta": requests.ConnectionError(
                  f"https://api.github.com/repos/x?access_token={F.GH_TOKEN}")}
    for texto, falha in falhas.items():
        http = F.FakeHTTP()
        http.github.fail["sonda"] = falha
        with pytest.raises(GitHubError, match=texto) as e:
            GitHubClient(token=F.GH_TOKEN, http=http).probe(MM_PATH)
        assert F.GH_TOKEN not in str(e.value) and "api.github.com" not in str(e.value)
        assert e.value.__cause__ is None


# ── 2) corridas e meses ────────────────────────────────────────────────────────────────────────
def test_corridas_ida_e_volta_zero_null_e_trecho_novo():
    pts = {0: (1.5,), 1: (0.0,), 5: (2.0,), 10: (3.0,), 11: (None,)}
    runs = encode_runs(pts, 1)        # 2→4: 3 vazios = mesmo trecho; 6→9: 4 vazios = trecho novo
    assert runs == [[0, [1.5, 0.0, None, None, None, 2.0]], [10, [3.0]]]
    assert expand_runs(runs) == [(0, (1.5,)), (1, (0.0,)), (5, (2.0,)), (10, (3.0,))]  # 11: sem linha
    dois = {3: (1.0, None), 4: (None, 2.0), 9: (0.0, 0.0)}
    assert encode_runs(dois, 2) == [[3, [1.0, None], [None, 2.0]], [9, [0.0], [0.0]]]
    assert expand_runs(encode_runs(dois, 2)) == sorted(dois.items())
    assert encode_runs({}, 1) == [] and expand_runs([]) == []
    assert month_index("1997-01") == 1997 * 12 and month_label(month_index("2026-10")) == "2026-10"
    for ruim in ("2026-13", "2026-1", "26-10", None, "2026-10-01"):
        with pytest.raises(SchemaError):
            month_index(ruim)


# ── 3) os arquivos, campo a campo ──────────────────────────────────────────────────────────────
def _months(obj) -> int:
    return month_index(obj["through"]) - month_index(obj["from"]) + 1


def test_forma_exata_dos_17(built):
    env_ = ["schemaVersion", "generatedAt"]
    assert list(built["mm-index.json"]) == env_ + ["source", "dataThrough", "raw"]
    assert list(built["pp-index.json"]) == env_ + ["source", "dataThrough", "raw"]
    assert list(built["mm-index.json"]["dataThrough"]) == ["secex", "iabr", "inda", "customs"]
    assert list(built["pp-index.json"]["dataThrough"]) == ["secex", "iba", "empapel", "gacc"]
    tr = built["mm-trade.json"]
    assert list(tr) == env_ + ["updatedAt", "from", "through", "series"]
    assert list(tr["series"]) == ["imp", "exp"]
    for d in tr["series"].values():
        assert list(d) == ["total", "flat", "long", "semi"]                # só as 4 de `secat()`
        for m in d.values():
            assert list(m) == ["volume", "revenue", "price", "yoyVolumePct"]
            assert all(len(a) == _months(tr) for a in m.values())
    dm = built["mm-domestic.json"]
    assert list(dm) == env_ + ["iabr", "inda"]
    assert list(dm["iabr"]) == ["updatedAt", "from", "through", "production", "consumption",
                                "domesticSales"]
    assert list(dm["iabr"]["production"]) == ["crudeSteel", "flat", "long", "semi"]
    assert list(dm["iabr"]["consumption"]) == ["total", "flat", "long"]       # sem `semi` (G.3)
    assert list(dm["iabr"]["domesticSales"]) == ["total", "flat", "long", "semi", "slabs", "billets"]
    assert list(dm["inda"]) == ["updatedAt", "from", "through", "inventories", "invMonths",
                                "purchases", "sales", "salesLtm", "salesMa3"]
    pr = built["mm-prediction.json"]
    assert list(pr) == env_ + ["imports", "exports"]
    for blk in (pr["imports"], pr["exports"]):
        assert list(blk) == ["updatedAt", "from", "through", "series"]
        assert list(blk["series"]) == ["HRC", "CRC", "Coated"]
        for cs in blk["series"].values():
            assert list(cs) == ["Korea", "China"] and all(len(a) == _months(blk) for a in cs.values())
    for d in SE.DIRECTIONS:
        for t, cats in SE.COUNTRY_TABS.items():
            o = built[SE.country_file(d, t)]
            assert list(o) == env_ + ["updatedAt", "direction", "tab", "from", "through", "countries",
                                      "categories"]
            assert (o["direction"], o["tab"]) == (d, t) and list(o["categories"]) == list(cats)
    pe = built["pp-pulp-exports.json"]
    assert list(pe) == env_ + ["from", "through", "workingDays", "ports", "destinations"]
    assert list(pe["ports"]) == ["names", "series"] and len(pe["workingDays"]) == _months(pe)
    assert list(pe["destinations"]) == ["countries", "products"]
    assert list(pe["destinations"]["products"]) == ["hardwood", "softwood", "ukp", "dissolving", "other"]
    wc = built["pp-woodchips.json"]
    assert list(wc) == env_ + ["from", "through", "suppliers", "fibres"]
    assert list(wc["fibres"]) == ["HW", "SW"]
    for sup in wc["fibres"].values():
        assert list(sup) == wc["suppliers"]                                   # todos em toda fibra
        assert all(list(m) == ["volume", "revenue"] and len(m["volume"]) == _months(wc)
                   for m in sup.values())
    pa = built["pp-paper.json"]
    assert list(pa) == env_ + ["from", "through", "production", "domesticSales", "exports", "imports",
                               "apparentConsumption"]
    for k in ("production", "domesticSales", "exports", "imports"):
        assert list(pa[k]) == ["total", "packaging", "pw", "newsprint", "cardboard", "other"]
    assert list(built["pp-corrugated.json"]) == env_ + ["from", "through", "shipments", "workingDays",
                                                        "perWorkingDay", "ltm"]
    for name in SE.RAW_TABLE_OF:
        assert list(built[name]) == env_ + ["table", "columns", "data", "dict"]


def test_o_que_nunca_sai_nos_17_e_a_base_bruta_como_excecao(built):
    proibidos = ('"value_usd"', '"valueUsd"', "Wire Rod", '"updated_at"', '"yoy_revenue"',
                 '"working_days"', '"inv_yoy"', '"hist_avg"', '"pig_iron"', '"pigIron"', "exprev",
                 "iba_pulp", "company_q", '"revenue_usd_mn"', "github", "http", "overdueDays")
    for name in DATA_17:
        obj = {k: v for k, v in built[name].items() if k != "raw"}   # o índice lista as tabelas brutas
        texto = json.dumps(obj, ensure_ascii=False)
        for p in proibidos:
            assert p not in texto, (name, p)
        assert forbidden_keys(built[name], extra=spec_for(name).forbidden) == [], name
    subcats = {"hrc", "crc", "coated", "heavy_plate", "flat_others", "rebar", "bar", "wire_rod",
               "shapes", "long_others", "ingot_billet", "placa"}
    assert not subcats & set(built["mm-trade.json"]["series"]["imp"])        # só as 4 categorias
    for d in SE.DIRECTIONS:                                                   # país: só volume
        for t in SE.COUNTRY_TABS:
            assert "revenue" not in json.dumps(built[SE.country_file(d, t)])
    # a base bruta leva TUDO (a exceção declarada, G.11), com a rede dos nomes proibidos embaixo
    assert "value_usd" in built["mm-raw-import-prediction.json"]["columns"]
    assert "Wire Rod" in built["mm-raw-import-prediction.json"]["dict"]["product"]
    assert "exprev_total" in built["pp-raw-iba-paper.json"]["columns"]
    for name in SE.RAW_TABLE_OF:
        assert forbidden_keys(built[name], extra=spec_for(name).forbidden) == [], name


def test_preditivo_so_com_as_duas_linhas_recusa_wire_rod_e_value_usd(built):
    con = fixture_con(SE.MM)
    assert con.execute("SELECT count(*) FROM import_prediction WHERE product='Wire Rod'").fetchone()[0]
    pr = built["mm-prediction.json"]
    assert "Wire Rod" not in pr["imports"]["series"] and "value_usd" not in json.dumps(pr)
    korea = pr["imports"]["series"]["HRC"]["Korea"]                    # 2022-12 sem linha → null
    assert korea[month_index("2022-12") - month_index(pr["imports"]["from"])] is None
    assert pr["exports"]["series"]["Coated"]["Korea"][0] is None      # a Coreia começa um mês depois
    assert (pr["imports"]["from"], pr["imports"]["through"]) == ("2022-10", "2023-06")
    assert (pr["exports"]["from"], pr["exports"]["through"]) == ("2022-11", "2023-05")
    # par (produto, país) sem a ponta de exportação não sai; produto sem par nenhum também não
    http = F.FakeHTTP()
    http.github.files[MM_PATH] = F.sector_db_bytes(SE.MM, lambda c: c.execute(
        "DELETE FROM pred_exports WHERE product='Coated' AND country='Korea'"))
    src, _ = F.make_sources(http=http)
    pr = FILES["mm-prediction.json"].builder(src)
    assert list(pr["imports"]["series"]["Coated"]) == ["China"] == list(pr["exports"]["series"]["Coated"])
    # o Wire Rod com as duas pontas continua fora (G.0), e o arquivo sai (não falha pela chave)
    http = F.FakeHTTP()
    http.github.files[MM_PATH] = F.sector_db_bytes(SE.MM, lambda c: c.execute(
        "INSERT INTO pred_exports SELECT period, country, product, value_usd, volume_kg, updated_at "
        "FROM import_prediction WHERE product='Wire Rod' AND period BETWEEN '2022-11' AND '2023-05'"))
    src, _ = F.make_sources(http=http)
    pr = FILES["mm-prediction.json"].builder(src)
    assert list(pr["exports"]["series"]) == ["HRC", "CRC", "Coated"]
    assert forbidden_keys(pr, extra=FILES["mm-prediction.json"].forbidden) == []


def test_dicionarios_em_ordem_de_codigo_unicode(built):
    for d in SE.DIRECTIONS:
        for t in SE.COUNTRY_TABS:
            names = built[SE.country_file(d, t)]["countries"]
            assert names == sorted(names) and len(set(names)) == len(names)
    assert built["mm-countries-imp-flat.json"]["countries"] == ["Alemanha", "China", "Coreia do Sul",
                                                                "Japão"]
    assert built["mm-countries-exp-total.json"]["countries"][-1] == "Áustria"   # depois do ASCII
    assert built["mm-countries-imp-long.json"]["countries"] == ["China", "Índia"]
    pe = built["pp-pulp-exports.json"]
    assert pe["ports"]["names"] == ["Barcarena", "Paranaguá", "Rio Grande", "Santos", "Vitória"]
    assert pe["destinations"]["countries"] == ["Belgium", "China", "Italy", "Netherlands", "Türkiye",
                                               "United States"]
    # G.0: fornecedores também em ordem de código Unicode (é a do banco); o padrão do hub é constante
    assert built["pp-woodchips.json"]["suppliers"] == ["Australia", "Chile", "Others", "Total", "Vietnam"]


def test_paises_ida_e_volta_com_o_banco_de_fixture(built):
    con = fixture_con(SE.MM)
    vistos = set()
    for d in SE.DIRECTIONS:
        for t, cats in SE.COUNTRY_TABS.items():
            o = built[SE.country_file(d, t)]
            base = month_index(o["from"])
            assert (o["from"], o["through"]) == ("2022-01", "2023-06")
            for c in cats:
                got = sorted((month_label(base + mi), o["countries"][ni], v)
                             for ni, runs in o["categories"][c] for mi, (v,) in expand_runs(runs))
                exp = [tuple(r) for r in con.execute(
                    "SELECT period,country,volume_ktons FROM secex_country WHERE direction=? AND "
                    "category=? ORDER BY period,country", (d, c))]
                assert got == exp, (d, t, c)
                vistos.add((d, c))
    assert len(vistos) == 30 and sum(con.execute(
        "SELECT count(*) FROM secex_country").fetchone()) == sum(
        con.execute("SELECT count(*) FROM secex_country WHERE direction=? AND category=?",
                    dc).fetchone()[0] for dc in vistos)
    o = built["mm-countries-imp-flat.json"]
    idx = o["countries"].index
    hrc = dict(o["categories"]["hrc"])
    assert [r[0] for r in hrc[idx("Coreia do Sul")]] == [0, 14]             # 4 vazios: trecho novo
    trecho = hrc[idx("Coreia do Sul")][0][1]
    assert len(trecho) == 10 and trecho[4:7] == [None, None, None]          # 3 vazios: no trecho
    crc = dict(o["categories"]["crc"])[idx("Alemanha")]
    assert crc == [[0, crc[0][1]]] and crc[0][1][2] == 0.0 and crc[0][1][3] is None   # 0 × null
    tiny = dict(built["mm-countries-exp-flat.json"]["categories"]["coated"])
    assert [v for ni, r in tiny.items() for _i, (v,) in expand_runs(r)] == [1e-06]


def test_celulose_cavacos_papel_e_ondulado_ida_e_volta(built):
    con = fixture_con(SE.PP)
    pe = built["pp-pulp-exports.json"]
    base = month_index(pe["from"])
    got = sorted((month_label(base + mi), pe["ports"]["names"][ni], v, r)
                 for ni, runs in pe["ports"]["series"] for mi, (v, r) in expand_runs(runs))
    assert got == [tuple(x) for x in con.execute(
        "SELECT period,port,volume_ktons,revenue_usd_mn FROM secex_pulp_port ORDER BY period,port")]
    got = sorted((month_label(base + mi), pe["destinations"]["countries"][ni], prd, v, r)
                 for prd, lst in pe["destinations"]["products"].items()
                 for ni, runs in lst for mi, (v, r) in expand_runs(runs))
    assert got == [tuple(x) for x in con.execute(
        "SELECT period,country,product,volume_ktons,revenue_usd_mn FROM secex_pulp_country "
        "ORDER BY period,country,product")]
    ports = dict(pe["ports"]["series"])
    names = pe["ports"]["names"]
    assert [r[0] for r in ports[names.index("Paranaguá")]] == [0, 6]          # 4 vazios: 2 trechos
    assert [r[0] for r in ports[names.index("Vitória")]] == [0]               # 3 vazios: 1 trecho
    assert ports[names.index("Barcarena")] == [[5, [0.0], [0.0]]]
    assert pe["workingDays"] == [19 + (i % 4) for i in range(18)]              # 2021-12 fora do eixo
    wc = built["pp-woodchips.json"]
    assert (wc["from"], wc["through"]) == ("2022-01", "2022-06")               # o corte do volume 0
    assert built["pp-index.json"]["dataThrough"]["gacc"] == "2022-05"         # o último com volume
    got = sorted((month_label(month_index(wc["from"]) + i), f, n, v, r)
                 for f, sup in wc["fibres"].items() for n, m in sup.items()
                 for i, (v, r) in enumerate(zip(m["volume"], m["revenue"])))
    assert got == sorted(tuple(x) for x in con.execute(
        "SELECT period,fibre,country,volume_bdmt,revenue_usd_mn FROM gacc_woodchips WHERE period>=?",
        (wc["from"],)))
    pa = built["pp-paper.json"]
    assert (pa["from"], pa["through"]) == ("2022-08", "2023-01")
    assert pa["production"]["total"] == [x for (x,) in con.execute(
        "SELECT prod_total FROM iba_paper ORDER BY period")]
    co = built["pp-corrugated.json"]
    assert co["perWorkingDay"] == [x for (x,) in con.execute(
        "SELECT exp_per_day FROM empapel ORDER BY period")]


def test_eixo_comum_null_no_mes_sem_linha_e_yoy_em_pontos(built):
    tr = built["mm-trade.json"]
    imp = tr["series"]["imp"]
    assert imp["semi"]["volume"][-1] is None and imp["total"]["volume"][-1] is not None
    con = fixture_con(SE.MM)
    yoy = con.execute("SELECT yoy_volume FROM secex_imports WHERE category='total' AND "
                      "period='2023-01'").fetchone()[0]
    assert imp["total"]["yoyVolumePct"][12] == round(yoy * 100, 2)
    assert imp["total"]["yoyVolumePct"][:12] == [None] * 12
    price = con.execute("SELECT price_usd_ton FROM secex_exports WHERE category='flat' AND "
                        "period='2022-05'").fetchone()[0]
    assert tr["series"]["exp"]["flat"]["price"][4] == price                    # o guardado, cru
    dm = built["mm-domestic.json"]
    assert (dm["iabr"]["from"], dm["iabr"]["through"]) == ("2023-01", "2023-06")
    assert dm["iabr"]["consumption"]["total"][-1] is None                     # o consumo para antes
    assert dm["inda"]["salesLtm"][:2] == [None, None] and dm["inda"]["salesLtm"][2] is not None
    assert dm["iabr"]["updatedAt"] == "2026-10-15T20:39:52Z"                   # o maior carimbo
    assert tr["updatedAt"] == "2026-11-06T18:49:41Z"


def test_base_bruta_ida_e_volta_das_19_tabelas(built):
    for name, (s, t) in SE.RAW_TABLE_OF.items():
        con = fixture_con(s)
        cur = con.execute(f'SELECT * FROM "{t}"' + ORDERS.get(t, ""))
        exp = [tuple(r) for r in cur]
        o = built[name]
        assert o["table"] == t and o["columns"] == [d[0] for d in cur.description], name
        got = rows_of(o)
        assert got == exp, name
        assert all(type(a) is type(b) for g, e in zip(got, exp) for a, b in zip(g, e)), name
        for col, values in o["dict"].items():                    # dicionário: distintos, ordenados
            assert values == sorted(set(values)) and col in o["columns"]
    sc = built["mm-raw-secex-country.json"]
    assert set(sc["dict"]) == {"period", "direction", "category", "country", "updated_at"}
    assert built["pp-raw-company-q.json"]["dict"] == {}                       # sem repetição
    assert "inv_yoy" not in built["mm-raw-inda-distribution.json"]["dict"]   # número: cru


def test_base_bruta_recusa_coluna_proibida_e_blob():
    http = F.FakeHTTP()
    http.github.files[PP_PATH] = F.sector_db_bytes(SE.PP, lambda c: (
        c.execute("ALTER TABLE empapel ADD COLUMN email TEXT"),
        c.execute("ALTER TABLE company_q ADD COLUMN foto BLOB"),
        c.execute("UPDATE company_q SET foto = x'0001'")))
    src, _ = F.make_sources(http=http)
    with pytest.raises(SchemaError, match="nome proibido"):
        FILES["pp-raw-empapel.json"].builder(src)
    with pytest.raises(SchemaError, match="não vira JSON"):
        FILES["pp-raw-company-q.json"].builder(src)
    assert FILES["pp-corrugated.json"].builder(src)["shipments"]          # a tela não vê a coluna


def test_indice_do_setor(built):
    con = fixture_con(SE.MM)
    mi = built["mm-index.json"]
    assert mi["source"] == {"blob": F.sector_blob(SE.MM), "commit": F.SECTOR_COMMITS[SE.MM][0],
                            "committedAt": F.SECTOR_COMMITS[SE.MM][1]}
    assert mi["dataThrough"] == {"secex": "2023-06", "iabr": "2023-06", "inda": "2023-06",
                                 "customs": "2023-05"}
    assert [r["table"] for r in mi["raw"]] == list(SE.RAW_TABLES[SE.MM])
    assert all(r["rows"] == con.execute(f'SELECT count(*) FROM "{r["table"]}"').fetchone()[0]
               for r in mi["raw"])
    pi = built["pp-index.json"]
    assert pi["dataThrough"] == {"secex": "2023-06", "iba": "2023-01", "empapel": "2023-02",
                                 "gacc": "2022-05"}
    assert pi["source"]["blob"] == F.sector_blob(SE.PP) and len(pi["raw"]) == 8


# ── 4) o plano EVENT, a fila e a partida a frio ───────────────────────────────────────────────
def test_primeiro_ciclo_manda_tudo_e_o_indice_de_cada_setor_por_ultimo(tmp_path):
    http = F.FakeHTTP()
    res = run(http, tmp_path / "st.json")
    assert acts(res) == {n: "enviado" for n in SE.ALL_FILES}
    assert all(res[n].bytes == len(http.uploads[n]) for n in SE.ALL_FILES)
    assert http.uploads["mm-trade.json"][:2] == b"\x1f\x8b" and http.uploads["mm-index.json"][:1] == b"{"
    up = list(http.uploads)
    for s in SE.SECTORS:
        assert max(up.index(n) for n in SE.queue_of(s)) < up.index(SE.INDEX[s])
    assert len(http.github_calls("sonda")) == 2 and len(http.github_calls("download")) == 2
    assert len(http.github_calls("commit")) == 2
    reg = S.load(tmp_path / "st.json")["sectors"]
    for s in SE.SECTORS:
        assert reg[s]["blob"] == reg[s]["indexBlob"] == F.sector_blob(s) and reg[s]["pending"] == []
    assert not list(tmp_path.glob("hub_export_*.sqlite"))                 # fila vazia: cache apagado
    assert doc(http.uploads["mm-index.json"])["source"]["commit"] == F.SECTOR_COMMITS[SE.MM][0]


def test_blob_igual_nenhum_download_e_sonda_so_a_cada_15_min(tmp_path):
    p = tmp_path / "st.json"
    run(F.FakeHTTP(), p)
    for minutos, sondas in ((5, 0), (10, 0), (15, 2), (20, 0)):
        http = F.FakeHTTP()
        res = run(http, p, now=F.NOW + timedelta(minutes=minutos))
        assert set(acts(res).values()) == {"fora da vez"} and http.uploads == {}, minutos
        assert len(http.github_calls("sonda")) == sondas, minutos
        assert not http.github_calls("download") and not http.github_calls("commit")


def _regzip(data: bytes, mtime: int = 1234) -> bytes:
    """O mesmo banco, outro .gz (o build_web_db.py grava o relógio no cabeçalho): blob novo, dado igual."""
    return gzip.compress(gzip.decompress(data), compresslevel=6, mtime=mtime)


def test_blob_novo_sem_dado_novo_so_o_indice(tmp_path):
    p = tmp_path / "st.json"
    run(F.FakeHTTP(), p)
    http = F.FakeHTTP()
    http.github.files[MM_PATH] = _regzip(http.github.files[MM_PATH])
    http.github.commits[SE.MM] = ("d" * 40, "2026-11-10T12:00:00Z")
    res = run(http, p, now=F.NOW + timedelta(minutes=15))
    assert {n: a for n, a in acts(res).items() if a != "fora da vez"} == {
        **{n: "igual" for n in SE.queue_of(SE.MM)}, "mm-index.json": "enviado"}
    assert list(http.uploads) == ["mm-index.json"]
    # no envio, quem sai "igual" nem é comprimido (o log mostra o tamanho cru); quem vai, sim
    assert res["mm-raw-secex-country.json"].bytes is None and res["mm-raw-secex-country.json"].raw_bytes
    assert res["mm-index.json"].bytes == len(http.uploads["mm-index.json"])
    idx = doc(http.uploads["mm-index.json"])
    assert idx["source"] == {"blob": git_blob_sha(http.github.files[MM_PATH]), "commit": "d" * 40,
                             "committedAt": "2026-11-10T12:00:00Z"}
    assert len(http.github_calls("download")) == 1                          # só o do aço


def test_dado_novo_manda_so_o_que_mudou_e_o_indice(tmp_path):
    p = tmp_path / "st.json"
    run(F.FakeHTTP(), p)
    http = F.FakeHTTP()
    http.github.files[PP_PATH] = F.sector_db_bytes(SE.PP, lambda c: c.execute(
        "UPDATE empapel SET shipments_kton = shipments_kton + 1 WHERE period = '2023-02'"))
    res = run(http, p, now=F.NOW + timedelta(minutes=15))
    assert list(http.uploads) == ["pp-corrugated.json", "pp-raw-empapel.json", "pp-index.json"]
    assert res["pp-paper.json"].action == "igual" and res["mm-trade.json"].action == "fora da vez"


def test_teto_de_4_por_ciclo_o_resto_na_fila_sem_montar_e_o_indice_espera(tmp_path, monkeypatch):
    monkeypatch.setattr("hub_export.run.GROUP_CAPS", {"sectors": 4})
    p = tmp_path / "st.json"
    enviados, downloads = [], 0
    for ciclo in range(12):
        http = F.FakeHTTP()
        res = run(http, p, now=F.NOW + timedelta(minutes=5 * ciclo))
        assert not any(r.failed for r in res.values())                       # "na fila" não é erro
        enviados += list(http.uploads)
        downloads += len(http.github_calls("download"))
        fila = [n for n, r in res.items() if r.action == "na fila"]
        assert all(res[n].bytes is None for n in fila)                       # nem montado
        if ciclo == 0:
            assert len(http.uploads) == 4 and len(fila) == 30                 # 34 na fila − 4
            assert res["mm-index.json"].action == res["pp-index.json"].action == "fora da vez"
            assert list(tmp_path.glob("hub_export_*.sqlite"))                # o cache fica p/ a fila
    assert sorted(enviados) == sorted(SE.ALL_FILES) and len(enviados) == 36  # cada um uma vez
    for s in SE.SECTORS:
        assert max(enviados.index(n) for n in SE.queue_of(s)) < enviados.index(SE.INDEX[s])
    assert downloads == 2                                                     # um por banco
    assert not list(tmp_path.glob("hub_export_*.sqlite"))


def test_force_nao_fura_o_teto_e_o_indice_forcado_espera_a_fila(tmp_path, monkeypatch):
    monkeypatch.setattr("hub_export.run.GROUP_CAPS", {"sectors": 4})
    http = F.FakeHTTP()
    res = run(http, tmp_path / "a.json", force=True)
    assert len(http.uploads) == 4
    http = F.FakeHTTP()
    res = run(http, tmp_path / "b.json", only="mm-index", force=True)
    assert res["mm-index.json"].action == "erro" and "espera" in res["mm-index.json"].reason
    assert http.uploads == {}


def test_partida_a_frio_com_o_indice_publicado_igual_nada_a_montar(tmp_path):
    primeiro = F.FakeHTTP()
    run(primeiro, tmp_path / "a.json")
    http = F.FakeHTTP(published=dict(primeiro.uploads))
    res = run(http, tmp_path / "b.json")                                      # runner novo
    assert set(acts(res).values()) == {"fora da vez"} and http.uploads == {}
    assert len(http.github_calls()) == 2                                      # só as 2 sondas
    reg = S.load(tmp_path / "b.json")["sectors"]
    assert reg[SE.MM]["indexBlob"] == reg[SE.MM]["blob"] == F.sector_blob(SE.MM)
    assert reg[SE.MM]["commit"] == F.SECTOR_COMMITS[SE.MM][0]                # do índice publicado


def test_partida_a_frio_sem_indice_compara_cada_arquivo_com_a_copia_publicada(tmp_path):
    primeiro = F.FakeHTTP()
    run(primeiro, tmp_path / "a.json")
    pub = {n: b for n, b in primeiro.uploads.items() if n not in SE.INDEX.values()}
    http = F.FakeHTTP(published=pub)
    res = run(http, tmp_path / "b.json")
    assert acts(res) == {**{n: "igual" for n in pub}, "mm-index.json": "enviado",
                         "pp-index.json": "enviado"}
    assert list(http.uploads) == ["mm-index.json", "pp-index.json"]
    assert len(http.github_calls("download")) == 2
    # índice publicado de OUTRO blob: a mesma conta (baixa, compara, só o índice vai)
    velho = {**doc(primeiro.uploads["mm-index.json"]),
             "source": {"blob": "0" * 40, "commit": None, "committedAt": None}}
    http = F.FakeHTTP(published={**primeiro.uploads, "mm-index.json": to_json_bytes(velho)})
    res = run(http, tmp_path / "c.json")
    assert list(http.uploads) == ["mm-index.json"] and res["mm-trade.json"].action == "igual"
    assert res["pp-index.json"].action == "fora da vez"                    # o P&P já estava publicado


def test_mindassets_parado_sem_indice_monta_so_na_hora_diaria(tmp_path):
    velho = {"schemaVersion": 1, "generatedAt": "2026-11-01T00:00:00Z"}
    pub = {"live.json": to_json_bytes(velho)}                                 # parado há dias
    p = tmp_path / "st.json"
    http = F.FakeHTTP(published=dict(pub))
    res = run(http, p)                                                        # 18:05 UTC
    assert set(acts(res).values()) == {"fora da vez"} and not http.github_calls("download")
    reg = S.load(p)["sectors"][SE.MM]
    assert not reg.get("seeded") and reg.get("probeBlob") == F.sector_blob(SE.MM)
    sete = datetime(2026, 11, 4, 7, 10, tzinfo=timezone.utc)
    http = F.FakeHTTP(published=dict(pub))
    res = run(http, p, now=sete)                                              # a hora do dia
    assert {r.action for r in res.values()} == {"enviado"}
    assert S.load(p)["sectors"][SE.MM]["outageDay"] == "2026-11-04"
    http = F.FakeHTTP(published=dict(pub))
    res = run(http, tmp_path / "outro.json", now=sete + timedelta(hours=3))  # runner novo, fora da hora
    assert set(acts(res).values()) == {"fora da vez"} and not http.github_calls("download")


# ── 5) falhas ──────────────────────────────────────────────────────────────────────────────────
def test_sha_do_download_errado_erro_e_nada_publicado(tmp_path):
    http = F.FakeHTTP()
    http.github.corrupt = True
    res = run(http, tmp_path / "st.json")
    for n in SE.queue_of(SE.MM) + SE.queue_of(SE.PP):
        assert res[n].action == "erro" and "SHA" in res[n].reason, n
    assert res["mm-index.json"].action == "fora da vez" and http.uploads == {}
    assert len(http.github_calls("download")) == 2                            # 1 tentativa por banco
    assert not list(tmp_path.glob("hub_export_*.sqlite"))
    reg = S.load(tmp_path / "st.json")["sectors"]
    assert len(reg[SE.MM]["pending"]) == 22                                    # tenta de novo depois
    res = run(F.FakeHTTP(), tmp_path / "st.json", now=F.NOW + timedelta(minutes=5))
    assert {r.action for r in res.values()} == {"enviado"}


def test_coluna_sumida_falha_so_aquele_arquivo_e_o_indice_espera(tmp_path):
    http = F.FakeHTTP()
    http.github.files[MM_PATH] = F.sector_db_bytes(SE.MM, lambda c: c.execute(
        "ALTER TABLE secex_imports DROP COLUMN price_usd_ton"))
    p = tmp_path / "st.json"
    res = run(http, p)
    assert res["mm-trade.json"].action == "erro" and "price_usd_ton" in res["mm-trade.json"].reason
    assert res["mm-raw-secex-imports.json"].action == "enviado"               # a bruta leva o que há
    assert {res[n].action for n in SE.queue_of(SE.MM) if n != "mm-trade.json"} == {"enviado"}
    assert res["mm-index.json"].action == "fora da vez"                       # espera o mm-trade
    assert res["pp-index.json"].action == "enviado"                           # o outro setor segue
    assert S.load(p)["sectors"][SE.MM]["pending"] == ["mm-trade.json"]
    http2 = F.FakeHTTP()
    http2.github.files[MM_PATH] = http.github.files[MM_PATH]
    res = run(http2, p, now=F.NOW + timedelta(minutes=5))
    assert res["mm-trade.json"].action == "erro" and res["mm-index.json"].action == "fora da vez"
    assert not http2.github_calls("download")                                 # o cache serve


def test_tabela_fora_do_banco_sai_da_fila_da_base_bruta(tmp_path):
    http = F.FakeHTTP()
    http.github.files[PP_PATH] = F.sector_db_bytes(SE.PP, lambda c: c.execute("DROP TABLE company_q"))
    res = run(http, tmp_path / "st.json", only="pp")
    assert res["pp-raw-company-q.json"].action == "fora da vez"
    assert res["pp-index.json"].action == "enviado"
    assert "company_q" not in [r["table"] for r in doc(http.uploads["pp-index.json"])["raw"]]


@pytest.mark.parametrize("falha", [requests.ConnectionError("https://api.github.com/x"), 503,
                                   F.FakeResp(403, {"message": "rate limit"},
                                              headers={"X-RateLimit-Remaining": "0"})])
def test_github_fora_do_ar_nenhum_arquivo_do_setor_e_o_ciclo_segue(tmp_path, caplog, falha):
    caplog.set_level(logging.INFO)
    http = F.FakeHTTP()
    http.github.fail["sonda"] = falha
    res = run(http, tmp_path / "st.json", only="config,sectors", memory=F.memory())
    assert res["config.json"].action == "enviado"
    assert {res[n].action for n in SE.ALL_FILES} == {"fora da vez"}
    assert list(http.uploads) == ["config.json"] and not http.github_calls("download")
    linhas = [r.getMessage() for r in caplog.records if r.getMessage().startswith("setores:")]
    assert len(linhas) == 1 and "nenhum arquivo do setor neste ciclo" in linhas[0]
    res = run(F.FakeHTTP(), tmp_path / "st.json", now=F.NOW + timedelta(minutes=5))
    assert {res[n].action for n in SE.ALL_FILES} == {"enviado"}              # voltou: segue


def test_sem_token_no_envio_o_setor_nem_entra_no_ciclo(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    http = F.FakeHTTP()
    res = run(http, tmp_path / "st.json", env=F.env(), only="config,sectors", memory=F.memory())
    assert list(res) == ["config.json"] and http.github_calls() == []
    assert any("sem GH_TOKEN" in r.getMessage() for r in caplog.records)


# ── 6) os modos ────────────────────────────────────────────────────────────────────────────────
def test_dry_run_simula_sem_mexer_no_registro_e_sem_baixar_a_toa(tmp_path):
    p = tmp_path / "st.json"
    http = F.FakeHTTP()
    res = run(http, p, mode="dry")
    assert {r.action for r in res.values()} == {"enviaria"} and http.feeder_calls() == []
    assert S.load(p)["sectors"] == {}                                         # registro intocado
    run(F.FakeHTTP(), p)                                                      # o envio de verdade
    antes = S.load(p)["sectors"]
    http = F.FakeHTTP()
    res = run(http, p, mode="dry", now=F.NOW + timedelta(minutes=20))
    assert set(acts(res).values()) == {"fora da vez"} and not http.github_calls("download")
    assert all(r.bytes is None for r in res.values())                        # nem montado
    depois = S.load(p)["sectors"]
    assert {s: {k: v for k, v in r.items() if k not in ("probeAt",)} for s, r in depois.items()} == \
        {s: {k: v for k, v in r.items() if k not in ("probeAt",)} for s, r in antes.items()}


def test_dry_run_com_teto_simula_a_fila(tmp_path, monkeypatch):
    monkeypatch.setattr("hub_export.run.GROUP_CAPS", {"sectors": 4})
    res = run(F.FakeHTTP(), tmp_path / "st.json", mode="dry")
    acoes = list(acts(res).values())
    assert acoes.count("enviaria") == 4 and acoes.count("na fila") == 30
    assert res["mm-index.json"].action == "fora da vez"


def test_out_grava_os_mesmos_bytes_do_envio_e_roda_sem_rede_com_a_pasta(tmp_path, monkeypatch):
    enviados = F.FakeHTTP()
    run(enviados, tmp_path / "a.json")
    out = tmp_path / "saida"
    http = F.FakeHTTP()
    res = run(http, tmp_path / "b.json", mode="out", out_dir=out, env=F.env())   # sem token: a API
    assert {r.action for r in res.values()} == {"gravado"}
    assert all("Authorization" not in c.headers for c in http.github_calls())
    for n in SE.ALL_FILES:
        assert (out / n).read_bytes() == enviados.uploads[n], n
    assert S.load(tmp_path / "b.json")["sectors"] == {}
    # sem rede: a pasta com os dois bancos (os nomes do repositório)
    pasta = tmp_path / "bancos"
    pasta.mkdir()
    for s in SE.SECTORS:
        (pasta / F.SECTOR_FILE[s]).write_bytes(F.sector_files()[SE.DBS[s].path])
    http = F.FakeHTTP()
    out2 = tmp_path / "saida2"
    res = run(http, tmp_path / "c.json", mode="out", out_dir=out2,
              env=F.env(HUB_EXPORT_SECTORS_DB_DIR=str(pasta)))
    assert {r.action for r in res.values()} == {"gravado"} and http.github_calls() == []
    for n in SE.ALL_FILES:
        if n in SE.INDEX.values():
            idx = doc((out2 / n).read_bytes())
            assert idx["source"]["commit"] is None and idx["source"]["committedAt"] is None
            assert idx["source"]["blob"] == doc(enviados.uploads[n])["source"]["blob"]
        else:
            assert (out2 / n).read_bytes() == enviados.uploads[n], n
    assert not list(tmp_path.glob("hub_export_*.sqlite"))
    # no ENVIO a pasta é ignorada (só vale no --out/--dry-run): vai pelo GitHub
    http = F.FakeHTTP()
    run(http, tmp_path / "d.json", env=F.env(GH_TOKEN=F.GH_TOKEN,
                                             HUB_EXPORT_SECTORS_DB_DIR=str(pasta)), only="pp")
    assert len(http.github_calls("download")) == 1


def test_cache_estragado_baixa_de_novo(tmp_path, monkeypatch):
    monkeypatch.setattr("hub_export.run.GROUP_CAPS", {"sectors": 4})
    p = tmp_path / "st.json"
    run(F.FakeHTTP(), p, only="mm")
    cache = list(tmp_path.glob("hub_export_mm_*.sqlite"))
    assert len(cache) == 1
    cache[0].write_bytes(b"isto nao e um banco sqlite" * 100)
    http = F.FakeHTTP()
    res = run(http, p, now=F.NOW + timedelta(minutes=5), only="mm")
    assert len(http.github_calls("download")) == 1 and len(http.uploads) == 4
    assert not any(r.failed for r in res.values())


def test_log_uma_linha_por_arquivo_ocioso_calado_e_nenhum_segredo(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    p = tmp_path / "st.json"
    run(F.FakeHTTP(), p)
    for n in SE.ALL_FILES:
        assert any(r.getMessage().startswith(n + " ") for r in caplog.records), n
    linhas = [r.getMessage() for r in caplog.records if r.getMessage().startswith("setores:")]
    assert len(linhas) == 1 and "banco baixado" in linhas[0] and "GitHub: 6 chamada(s)" in linhas[0]
    caplog.clear()
    run(F.FakeHTTP(), p, now=F.NOW + timedelta(minutes=5))                    # ocioso
    assert not [r for r in caplog.records if SE.sector_of(r.getMessage().split(" ")[0])]
    assert not [r for r in caplog.records if r.getMessage().startswith("setores:")]
    texto = caplog.text + (tmp_path / "st.json").read_text(encoding="utf-8")
    assert F.GH_TOKEN not in texto and "api.github.com" not in texto


# ── 7) registro e CLI ──────────────────────────────────────────────────────────────────────────
def test_registro_dos_36(tmp_path):
    texto = {"mm-index.json", "mm-prediction.json", "pp-index.json", "pp-corrugated.json"}
    for n in SE.ALL_FILES:
        s = FILES[n]
        assert (s.cadence, s.group, s.pulse) == (SCH.EVENT, "sectors", None), n
        assert s.gzip is (n not in texto), n
        assert s.urgent is not None and s.after is not None, n
    assert GROUP_CAPS["sectors"] == 4
    assert ORDER[-36:] == SE.ALL_FILES and ORDER.index("glossary.json") == len(ORDER) - 37
    assert ORDER.index("history-index.json") == len(ORDER) - 38
    for s in SE.SECTORS:
        assert SE.FILES_OF[s][-1] == SE.INDEX[s]                              # o índice por último
    assert len(SE.ALL_FILES) == len(set(SE.ALL_FILES)) == 36
    assert SE.raw_file(SE.MM, "secex_country") == "mm-raw-secex-country.json"
    assert not SCH.is_due(SCH.EVENT, None, F.NOW)                              # nunca pelo relógio
    assert not SCH.is_due(SCH.EVENT, "2020-01-01T00:00:00Z", F.NOW)


def test_only_aceita_os_nomes_e_os_atalhos():
    assert resolve("sectors") == SE.ALL_FILES
    assert resolve("mm") == SE.FILES_OF[SE.MM] and resolve("pp") == SE.FILES_OF[SE.PP]
    assert resolve("mm-countries") == SE.COUNTRY_FILES and len(resolve("mm-raw")) == 11
    assert len(resolve("pp-raw")) == 8
    assert resolve("mm-trade,pp-index.json,mm-countries-exp-semi") == \
        ["mm-trade.json", "pp-index.json", "mm-countries-exp-semi.json"]
    assert resolve("config,pp-raw,pp-raw-empapel")[:2] == ["config.json", "pp-raw-secex-pulp-port.json"]
    for ruim in ("mm-countries-imp-steel", "mm-raw-secex_country", "mm-index2", "pp-raw-x"):
        with pytest.raises(ValueError, match="desconhecido"):
            resolve(ruim)


def test_workflows_passam_o_gh_token_e_listam_os_setores():
    wf = Path(__file__).resolve().parents[1] / ".github" / "workflows"
    hub = (wf / "hub-export.yml").read_text(encoding="utf-8")
    assert re.search(r"^\s+GH_TOKEN:\s+\$\{\{ github\.token \}\}\s*$", hub, re.M)
    desc = re.search(r'description: "Arquivos: (.*)"', hub).group(1)
    for nome in [*SE.ALIASES, "mm-trade", "mm-index", "pp-index", "mm-raw-secex-country"]:
        assert nome in desc, nome
    loop = (wf / "hunt-loop.yml").read_text(encoding="utf-8")                 # já passava (Pulse)
    assert re.search(r"^\s+GH_TOKEN:\s+\$\{\{ secrets\.GITHUB_TOKEN \}\}", loop, re.M)


# ── 8) os bancos de fixture guardados ──────────────────────────────────────────────────────────
def _content(con) -> dict:
    out = {}
    for name, sql in con.execute("SELECT name, sql FROM sqlite_master ORDER BY name"):
        rows = [tuple(r) for r in con.execute(f'SELECT * FROM "{name}"')] if sql and \
            sql.upper().startswith("CREATE TABLE") else None
        out[name] = (sql, rows)
    return out


def test_bancos_de_fixture_guardados_batem_com_o_gerador():
    for s in SE.SECTORS:
        novo = F.sector_db_bytes(s)
        if os.environ.get("UPDATE_GOLDEN") == "1":
            (F.SECTOR_SAMPLES / F.SECTOR_FILE[s]).write_bytes(novo)
            F._SECTOR_BYTES.clear()
        guardado = F.sector_files()[SE.DBS[s].path]
        a, b = sqlite3.connect(":memory:"), sqlite3.connect(":memory:")
        a.deserialize(gzip.decompress(novo) if SE.DBS[s].gz else novo)
        b.deserialize(gzip.decompress(guardado) if SE.DBS[s].gz else guardado)
        assert _content(a) == _content(b), f"{F.SECTOR_FILE[s]} mudou — se foi de propósito: UPDATE_GOLDEN=1"


@pytest.mark.skipif(not os.environ.get("HUB_EXPORT_SECTORS_REAL_DB_DIR"),
                    reason="rodada manual: HUB_EXPORT_SECTORS_REAL_DB_DIR = pasta com os 2 bancos reais")
def test_ida_e_volta_com_os_bancos_reais(tmp_path):
    """A prova com o dado real (os bancos tirados do git do site): países, celulose e base bruta."""
    pasta = Path(os.environ["HUB_EXPORT_SECTORS_REAL_DB_DIR"])
    res = run(F.FakeHTTP(), tmp_path / "st.json", mode="out", out_dir=tmp_path / "o",
              env=F.env(HUB_EXPORT_SECTORS_DB_DIR=str(pasta)))
    assert {r.action for r in res.values()} == {"gravado"}
    cons = {}
    for s in SE.SECTORS:
        data = (pasta / F.SECTOR_FILE[s]).read_bytes()
        cons[s] = sqlite3.connect(":memory:")
        cons[s].deserialize(gzip.decompress(data) if SE.DBS[s].gz else data)
    total = 0
    for d in SE.DIRECTIONS:
        for t, cats in SE.COUNTRY_TABS.items():
            o = doc((tmp_path / "o" / SE.country_file(d, t)).read_bytes())
            base = month_index(o["from"])
            for c in cats:
                got = sorted((month_label(base + mi), o["countries"][ni], v)
                             for ni, runs in o["categories"][c] for mi, (v,) in expand_runs(runs))
                exp = [tuple(r) for r in cons[SE.MM].execute(
                    "SELECT period,country,volume_ktons FROM secex_country WHERE direction=? AND "
                    "category=? ORDER BY period,country", (d, c))]
                assert got == exp, (d, t, c)
                if t == "total" or c not in ("flat", "long", "semi"):
                    total += len(exp)
    assert total == cons[SE.MM].execute("SELECT count(*) FROM secex_country").fetchone()[0]
    for name, (s, t) in SE.RAW_TABLE_OF.items():
        o = doc((tmp_path / "o" / name).read_bytes())
        exp = [tuple(r) for r in cons[s].execute(f'SELECT * FROM "{t}"' + ORDERS.get(t, ""))]
        assert rows_of(o) == exp, name
