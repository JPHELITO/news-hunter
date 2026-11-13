"""
A Onda 4 no exportador do hub (PLANO, Apêndice H; WP4.1B): `stock-guide.json`, `sg-sensitivity.json`,
`data-sources.json` (pages.py + exporters/), `glossary.json` (sitefiles.py + exporters/glossary.py) e o
`calendar.json` com local, descrição e links (exporters/calendar.py + freetext.py).

O que estes testes TRAVAM, e por quê (o arquivo é PÚBLICO e o envio aparece em produção na hora):
  1) campo a campo: a forma exata de cada arquivo; a empresa oculta só com os 4 campos; `modelUrl` da
     constante pelo filtro de URL; dividend yield em pp; chaves proibidas nunca (`how_pulled`, `updated_by`,
     `external_id`, `ics_seq`, `locked`, `note`, `key`, `where`, as notas internas da sensibilidade, contatos);
  2) a sensibilidade: só `sens2d` e só as grades que a tela DESENHA (porte de `_s2Sheets` e das análises);
  3) as flags no dado (O50): cada uma desligada → arquivo vazio e NADA lido; religada → cheio de novo;
     `hub_<chave>` manda;
  4) a cadência: sonda igual → nenhuma leitura cara; sonda mudou → lê; leitura de segurança no prazo (Comp
     Table diária, sensibilidade semanal); a sonda guardada não se repete num erro;
  5) o marcador `source` DENTRO do hash: nova subida idêntica da planilha → 1 envio e depois estável; a
     partida a frio com a cópia publicada igual à sonda não lê o banco; Mindassets parado → a leitura espera
     (1×/dia às 07 UTC);
  6) o calendário: contatos mascarados, rastreadores de e-mail recusados (as 3 formas reais, com endereços
     FICTÍCIOS), `[link]` no texto, `from` sem limite inferior;
  7) o glossário: `where` → rota pela tabela fechada (cobertura dos 15 endereços e das exceções por termo),
     desconhecido → `null` + uma linha de aviso; sonda condicional (304); GitHub fora do ar → nada muda;
     download conferido; partida a frio pelo `source.blob` publicado;
  8) nenhuma exceção derruba o ciclo; nada de segredo no log.
"""
from __future__ import annotations

import gzip
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import test_hub_export_fixtures as F  # noqa: E402
from hub_export import pages as P  # noqa: E402
from hub_export import state as S  # noqa: E402
from hub_export.encode import content_hash, forbidden_keys, maybe_gunzip, to_json_bytes  # noqa: E402
from hub_export.exporters import calendar as CAL  # noqa: E402
from hub_export.exporters import data_sources as DS  # noqa: E402
from hub_export.exporters import glossary as GL  # noqa: E402
from hub_export.exporters import sg_sensitivity as SS  # noqa: E402
from hub_export.exporters import stock_guide as SG  # noqa: E402
from hub_export.freetext import clean_text, public_url  # noqa: E402
from hub_export.registry import FILES, GITHUB_FILES, ORDER, resolve, spec_for  # noqa: E402
from hub_export.run import run_cycle  # noqa: E402

WAVE4 = ["stock-guide.json", "sg-sensitivity.json", "data-sources.json"]
PAGES = ",".join(n[:-5] for n in WAVE4)
ENV_GH = F.env(GH_TOKEN=F.GH_TOKEN)
SG_READS = ("rpc/get_stock_guide_comps", "rpc/get_stock_guide_config", "rpc/get_stock_guide_global_peers")
SENS_READ = "rpc/get_stock_guide_sensitivity_tables"
DS_READ = "rpc/get_data_source_status"
# as 3 formas de rastreio de e-mail medidas nos links reais do calendário (4-dados §0.6), com endereços
# FICTÍCIOS: Salesforce Marketing Cloud do Itaú com CampaignID, MZ/SendGrid `/ls/click?upn=`, Closir `/c/…`
TRACKERS = ("https://cloud.sfmc.itau.com.br/confirmacao_externa_prod?CampaignID=99999&subscriberkey=FICTICIO",
            "https://mailerurl.mziq.com/ls/click?upn=FICTICIO-0000-AAAA",
            "https://mail.closir.com/c/eJyFICTICIOabc123")


@pytest.fixture(autouse=True)
def _sem_teto(monkeypatch):
    F.no_mw_cap(monkeypatch)


def run(http, path, *, now=F.NOW, only=PAGES, env=None, **kw):
    kw.setdefault("memory", F.memory())
    return {r.name: r for r in run_cycle(env=env or F.env(), http=http, now=now, state_file=path,
                                         only=only, **F.run_kw(**kw))}


def doc(payload: bytes) -> dict:
    return json.loads(maybe_gunzip(payload))


def reads(http, *tables) -> int:
    return sum(len(http.supa_calls(t)) for t in tables)


def build(name, http=None, state=None, **kw):
    src, http = F.make_sources(http=http, state=state, **kw)
    return FILES[name].builder(src), http


def flags_http(**over):
    """O banco falso com as flags trocadas (`None` = a flag some da tabela)."""
    http = F.FakeHTTP()
    rows = [f for f in http.database["rpc"]["get_dashboard_flags"] if f["key"] not in over]
    rows += [{"key": k, "enabled": v} for k, v in over.items() if v is not None]
    http.database["rpc"]["get_dashboard_flags"] = rows
    return http


# ── 1) forma e lista branca ───────────────────────────────────────────────────────────────────────
def test_stock_guide_forma_exata_oculta_so_com_4_campos_e_link_do_modelo():
    sg, http = build("stock-guide.json")
    assert list(sg) == ["schemaVersion", "generatedAt", "updatedAt", "source", "years", "footnote",
                        "companies", "peers"]
    assert sg["source"] == {"stamp": "2026-11-06T15:39:10Z", "companies": 4, "peers": 3}
    assert sg["updatedAt"] == sg["source"]["stamp"]
    assert sg["years"] == {"y1": "2026E", "y2": "2027E"} and sg["footnote"] is None
    assert [c["code"] for c in sg["companies"]] == ["VALE", "AUGO", "CSNA3", "ARA"]      # display_order
    vale, augo, csn, ara = sg["companies"]
    assert list(vale) == list(SG.COMPANY_KEYS) and list(vale["y1"]) == list(SG.YEAR_KEYS)
    assert csn == {"code": "CSNA3", "name": "CSN", "order": 6, "restricted": True}       # só os 4
    assert vale["restricted"] is False and vale["quote"] == "VALE" and vale["rating"] == "MP"
    assert vale["shares"] == 4255.762795000001 and vale["targetPrice"] == 14            # como o banco guarda
    assert vale["modelUrl"] == "https://www.itau.com.br/itaubba-pt/portal/equity/company/VALE"
    assert augo["modelUrl"].endswith("/company/AURA33")                                # slug do portal
    assert augo["rating"] is None                                                       # "Buy" não é OP/MP/UP
    assert (ara["tradeCcy"], ara["baseCcy"], ara["fxToBase"]) == ("CAD", "USD", 0.708)
    assert ara["y1"]["ebitda"] == -12.0 and augo["y2"]["evAdjustment"] is None
    assert [p["name"] for p in sg["peers"]] == ["CAP", "BHP", "RIO"]
    cap = sg["peers"][0]
    assert list(cap) == list(SG.PEER_KEYS) and list(cap["y1"]) == list(SG.PEER_YEAR_KEYS)
    assert cap["y1"]["divYieldPct"] == 1.817737541199 and cap["y2"]["divYieldPct"] is None   # fração × 100
    assert cap["y1"]["pe"] is None and cap["y1"]["pce"] == 27.951849852000375
    texto = json.dumps(sg)
    for interno in ("sharepoint", "uuid-admin", "capex", "net_revenues", "volume", "Mt", "nota interna",
                    "12345.6", "is_live", "last_update", "2026-11-05"):
        assert interno not in texto, interno
    # a leitura: as 3 RPCs (nunca as tabelas, salvo a sonda), com select explícito sem as colunas internas
    rpc = {c.table: c.params["select"] for c in http.supa_calls() if c.table.startswith("rpc/get_stock")}
    assert set(rpc) == set(SG_READS)
    assert "model_url" not in rpc["rpc/get_stock_guide_comps"] and "capex" not in rpc["rpc/get_stock_guide_comps"]
    sonda = [c for c in http.supa_calls() if c.table in ("stock_guide_companies", "stock_guide_global_peers")]
    assert [c.params["select"] for c in sonda] == ["updated_at", "updated_at"]
    assert all(c.params["limit"] == "1" and c.headers.get("Prefer") == "count=exact" for c in sonda)


def test_model_url_e_a_constante_do_site_pelo_filtro(monkeypatch):
    assert len(SG.MODEL_URL) == 18 and SG.MODEL_URL["AUGO"] == SG.MODEL_URL["AURA33"]
    assert SG.MODEL_URL["VALE3"] == SG.MODEL_URL["VALE"] and set(SG.MODEL_URL) >= {"ARA", "VMM", "COPEC"}
    assert SG.model_url("CMIN3.SA") == SG.PORTAL + "CMIN3"                   # sem o sufixo, como `_short`
    assert SG.model_url("ZZZZ") is None and SG.model_url(None) is None
    monkeypatch.setitem(SG.MODEL_URL, "VALE", "https://mailerurl.mziq.com/ls/click?upn=FICTICIO")
    assert SG.model_url("VALE") is None                                      # o filtro vale para a constante


def test_sensibilidade_so_sens2d_e_so_as_grades_desenhadas():
    sens, http = build("sg-sensitivity.json")
    assert list(sens) == ["schemaVersion", "generatedAt", "updatedAt", "source", "companies"]
    assert sens["source"] == {"stamp": "2026-11-12T09:00:00Z", "rows": 4}     # a sonda conta a oculta
    assert [c["code"] for c in sens["companies"]] == ["VALE", "GGBR4", "ARA"]  # exemplo e oculta fora
    grades = {c["code"]: sum(len(sh["tables"]) for sh in c["sheets"]) for c in sens["companies"]}
    assert grades == F.SENS_DRAWN and SS.tables_count(sens["companies"]) == 12
    vale, gg, ara = sens["companies"]
    assert list(vale) == list(SS.COMPANY_KEYS)
    assert [sh["analysis"]["id"] for sh in vale["sheets"]] == ["fx", "freight", "copper"]
    assert [t["metric"] for sh in vale["sheets"] for t in sh["tables"]] == ["ebitda", "ev_ebitda"] * 3
    fx = vale["sheets"][0]
    assert list(fx["analysis"]) == list(SS.ANALYSIS_KEYS)
    assert vale["sheets"][1]["analysis"]["modelPrice"] == 11.90             # cada análise com o seu preço
    assert fx["tables"][1]["pairId"] == "fx-t1" and fx["tables"][0]["id"] == "fx-t1"
    assert list(fx["tables"][0]) == list(SS.TABLE_KEYS)
    # Gerdau: a de 2026 sai (há 2027), a margem nunca, a SEM ano fica; ano em texto vira número
    assert [(t["id"], t["year"]) for t in gg["sheets"][0]["tables"]] == [("t1", 2027), ("t2", 2027),
                                                                         ("t3", 2027), ("t4", None)]
    assert gg["sheets"][0]["name"] == "Sensitivity " and gg["sheets"][0]["analysis"] is None   # como escrito
    assert gg["modelPrice"] == 24.0 and gg["assumptions"] == [{"label": "Avg. FX 2027", "value": 5.4}]
    assert gg["sheets"][0]["tables"][2]["values"][0] == [0.051, None, 0.07]   # célula vazia = null
    # a desenvolvedora: DCF upside e P/NAV; P/NAV em grade 1-D
    assert [t["metric"] for t in ara["sheets"][0]["tables"]] == ["upside", "p_nav"]
    pnav = ara["sheets"][0]["tables"][1]
    assert pnav["rowLevels"] is None and pnav["rowLabel"] is None and len(pnav["values"]) == 1
    assert ara["mktcapModel"] is None and ara["assumptions"] == []
    texto = json.dumps(sens, ensure_ascii=False)
    for interno in ("corrigido à mão", "título original", "VALE3", "premissa conferida", "FCF ÷ yield",
                    "Exemplo", "Margin", "Dividend", "4255.76", '"corner"', '"kind"', '"panel"'):
        assert interno not in texto, interno
    (ler,) = http.supa_calls(SENS_READ)
    assert ler.params["definition->>kind"] == "eq.sens2d" and ler.params["select"] == "id,display_order,definition"
    (sonda,) = http.supa_calls("stock_guide_sensitivities")
    assert sonda.params["definition->>kind"] == "eq.sens2d" and sonda.params["select"] == "updated_at"


def test_porte_do_corte_da_tela_casos_de_borda():
    def t(metric, year=2027, tid="x"):
        return {"id": tid, "metric": metric, "year": year, "title": "T", "colLevels": [1], "values": [[1]]}
    # sem nenhuma de 2027: o ano do analista fica (melhor que aba vazia)
    out = SS.drawn([{"name": "a", "tables": [t("ebitda", 2026), t("fcf", 2026)]}])
    assert [x["year"] for _, ts in out for x in ts] == [2026, 2026]
    # "2027E" não é 2027 (Number("2027E") é NaN): sai quando existe grade de 2027
    out = SS.drawn([{"name": "a", "tables": [t("ebitda", "2027E"), t("fcf", 2027), t("fcf", " 2027 ")]}])
    assert [x["year"] for _, ts in out for x in ts] == [2027, " 2027 "]
    # aba sem nenhuma grade mostrável sai; métrica que não é texto não conta
    out = SS.drawn([{"name": "a", "tables": [t("ebitda_margin")]}, {"name": "b", "tables": [t("fcf")]},
                    {"name": "c", "tables": [{"metric": None, "year": 2027}]}])
    assert [sh["name"] for sh, _ in out] == ["b"]
    # análises: a aba SEM análise numa empresa que tem análises não é desenhada; o corte é por análise
    d = {"sheets": [{"name": "s0", "tables": [t("ebitda")]},
                    {"name": "s1", "analysis": {"key": 1, "label": None}, "tables": [t("ebitda", 2026)]},
                    {"name": "s2", "analysis": {"key": "1"}, "tables": [t("div_yield")]},
                    {"name": "s3", "analysis": {"key": "b"}, "tables": [t("ebitda", 2027), t("fcf", 2026)]}]}
    out = SS.drawn_sheets(d)
    assert [(s["name"], s["analysis"]["id"], [x["year"] for x in s["tables"]]) for s in out] == \
        [("s1", "1", [2026]), ("s3", "b", [2027])]                 # a "1" não tem 2027: fica a de 2026
    assert out[0]["analysis"]["label"] is None and out[0]["analysis"]["assumptions"] is None
    assert SS.company({"definition": {"kind": "grid6", "ticker": "X"}}) is None
    assert SS.company({"definition": {"kind": "sens2d"}}) is None             # sem empresa: fora
    assert SS.company({"definition": "lixo"}) is None


def test_data_sources_forma_e_sem_bastidor():
    ds, http = build("data-sources.json")
    assert list(ds) == ["schemaVersion", "generatedAt", "updatedAt", "checkedAt", "sources"]
    assert ds["checkedAt"] == ds["updatedAt"] == "2026-11-11T19:46:12Z"
    assert [s["id"] for s in ds["sources"]] == ["secex_steel", "korea", "gacc", "quotes"]   # ordem da RPC
    secex, korea, gacc, quotes = ds["sources"]
    assert list(secex) == list(DS.SOURCE_KEYS)
    assert (secex["kind"], secex["state"], secex["manual"], secex["lastPeriod"]) == \
        ("monthly", "green", False, "2026-10")
    assert gacc["manual"] is True and gacc["state"] == "amber"
    assert (quotes["statusText"], quotes["nextExpected"], quotes["lastPeriod"]) == \
        ("live ( 4 min ago)", "contínuo", "11/11 19:42 UTC")                  # foto, como gravada
    assert korea["state"] == "grey" and korea["lastPeriod"] is None and korea["checkedAt"] == "2026-11-11T19:40:00Z"
    assert "how_pulled" not in json.dumps(ds) and "_inbox" not in json.dumps(ds)
    (ler,) = http.supa_calls(DS_READ)
    assert "how_pulled" not in ler.params["select"] and "updated_at" not in ler.params["select"]
    (sonda,) = http.supa_calls("data_source_status")
    assert sonda.params["order"] == "checked_at.desc.nullslast" and sonda.params["select"] == "checked_at"


def test_chaves_proibidas_nunca_saem_nem_com_o_banco_sujo():
    import test_hub_export_whitelist as WL
    http, mem = WL.mundo_sujo()                     # as tabelas da Onda 4 e o glossário, sujos também
    src, _ = F.make_sources(http=http, mem=mem, state=F.previous_state())
    proibidas = {"how_pulled", "updated_by", "external_id", "ics_seq", "locked", "note", "key", "email",
                 "phone", "whatsapp", "token", "where"}
    for name in WAVE4 + ["glossary.json", "calendar.json"]:
        obj = FILES[name].builder(src)
        assert forbidden_keys(obj, extra=spec_for(name).forbidden) == [], name
        chaves = set()

        def walk(o):
            if isinstance(o, dict):
                chaves.update(k.lower() for k in o)
                for v in o.values():
                    walk(v)
            elif isinstance(o, list):
                for v in o:
                    walk(v)
        walk(obj)
        livres = proibidas - ({"note"} if name == "calendar.json" else set())   # `note` é das janelas de dado
        assert not (chaves & livres), (name, chaves & livres)
        texto = json.dumps(obj, ensure_ascii=False)
        assert "a@b.c" not in texto and "+55" not in texto and "uuid-admin" not in texto, name


# ── 2) flags no dado ──────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("over,vazios", [
    ({"stock_guide": False}, {"stock-guide.json": ("companies", "peers"), "sg-sensitivity.json": ("companies",)}),
    ({"stock_guide": None}, {"stock-guide.json": ("companies", "peers"), "sg-sensitivity.json": ("companies",)}),
    ({"stock_guide_sensitivity": False}, {"sg-sensitivity.json": ("companies",)}),
    ({"stock_guide_peers": False}, {"stock-guide.json": ("peers",)}),
    ({"data_page": False}, {"data-sources.json": ("sources",)}),
    ({"data_page": True, "hub_data_page": False}, {"data-sources.json": ("sources",)}),
])
def test_flag_desligada_esvazia_o_arquivo_sem_ler_e_religada_enche(tmp_path, over, vazios):
    p = tmp_path / "st.json"
    run(F.FakeHTTP(), p)                                                     # ligadas: cheios
    http = flags_http(**over)
    res = run(http, p, now=F.NOW + timedelta(hours=1))
    for name, blocos in vazios.items():
        obj = doc(http.uploads[name])
        assert res[name].action == "enviado" and all(obj[b] == [] for b in blocos), (name, over)
    if "stock-guide.json" in vazios and "companies" in vazios["stock-guide.json"]:
        assert not reads(http, *SG_READS, "stock_guide_companies", "stock_guide_global_peers")
        assert doc(http.uploads["stock-guide.json"])["source"] == {"stamp": None, "companies": 0, "peers": 0}
    if "sg-sensitivity.json" in vazios:
        assert not reads(http, SENS_READ, "stock_guide_sensitivities")
    if "data-sources.json" in vazios:
        assert not reads(http, DS_READ, "data_source_status")
    if vazios == {"stock-guide.json": ("peers",)}:                          # só os pares: nem lidos
        assert not reads(http, "rpc/get_stock_guide_global_peers", "stock_guide_global_peers")
        assert doc(http.uploads["stock-guide.json"])["companies"]
    # religada: cheio de novo (o estado da flag desligada não guarda nada)
    http = F.FakeHTTP()
    res = run(http, p, now=F.NOW + timedelta(hours=2))
    for name in vazios:
        obj = doc(http.uploads[name])
        assert res[name].action == "enviado" and (obj.get("companies") or obj.get("sources")), name
        if name == "stock-guide.json":
            assert obj["peers"] and obj["source"]["peers"] == 3


def test_flags_da_onda_4_continuam_na_lista_branca_do_config():
    from hub_export.exporters.config import CLIENT_FLAGS
    assert {"stock_guide", "stock_guide_sensitivity", "stock_guide_peers", "data_page", "exec_calendar",
            "quarterly"} <= set(CLIENT_FLAGS)


# ── 3) cadência: sonda, leitura cheia, segurança ──────────────────────────────────────────────────
def test_sonda_igual_nao_le_e_sonda_mudou_le(tmp_path):
    p = tmp_path / "st.json"
    http = F.FakeHTTP()
    run(http, p)
    assert reads(http, *SG_READS) == 3 and reads(http, SENS_READ) == 1 and reads(http, DS_READ) == 1
    http = F.FakeHTTP()                                                       # 60 min depois, nada mudou
    res = run(http, p, now=F.NOW + timedelta(minutes=60))
    assert {res[n].action for n in WAVE4} == {"igual"}
    assert reads(http, *SG_READS, SENS_READ, DS_READ) == 0
    assert len(http.supa_calls("stock_guide_companies")) == 1                 # só a sonda
    http = F.FakeHTTP()                                                       # 5 min depois: nem na vez
    res = run(http, p, now=F.NOW + timedelta(minutes=65))
    assert {res[n].action for n in WAVE4} == {"fora da vez"} and not http.supa_calls()
    # o analista subiu um alvo novo (o lote regrava updated_at): a Comp Table relê e vai
    http = F.FakeHTTP()
    http.database["stock_guide_companies"][0].update(target_price=15.5,
                                                     updated_at="2026-11-07T10:00:00+00:00")
    res = run(http, p, now=F.NOW + timedelta(minutes=120))
    assert reads(http, *SG_READS) == 3 and res["stock-guide.json"].action == "enviado"
    assert doc(http.uploads["stock-guide.json"])["companies"][0]["targetPrice"] == 15.5
    assert reads(http, SENS_READ, DS_READ) == 0 and res["data-sources.json"].action == "igual"
    # o robô do frescor rodou (checked_at novo): só a Data relê
    http = F.FakeHTTP()
    http.database["stock_guide_companies"][0].update(target_price=15.5,
                                                     updated_at="2026-11-07T10:00:00+00:00")
    for r in http.database["data_source_status"]:
        r.update(checked_at="2026-11-12T19:00:00+00:00", status_text="on schedule (11d)")
    res = run(http, p, now=F.NOW + timedelta(minutes=180))
    assert reads(http, DS_READ) == 1 and reads(http, *SG_READS, SENS_READ) == 0
    assert res["data-sources.json"].action == "enviado"
    assert doc(http.uploads["data-sources.json"])["checkedAt"] == "2026-11-12T19:00:00Z"


def test_leitura_de_seguranca_comp_table_diaria_e_sensibilidade_semanal(tmp_path):
    p = tmp_path / "st.json"
    run(F.FakeHTTP(), p)                                                      # terça 03/11 18:05 UTC
    def ciclo(when):
        http = F.FakeHTTP()
        # PATCH sem `updated_at`: a sonda não vê
        http.database["stock_guide_companies"][0]["target_price"] = 99.0
        http.database["stock_guide_sensitivities"][1]["definition"]["model_price"] = 12.5
        run(http, p, now=when)
        return http
    http = ciclo(datetime(2026, 11, 3, 23, 30, tzinfo=timezone.utc))
    assert reads(http, *SG_READS, SENS_READ) == 0                             # a sonda não vê o PATCH
    http = ciclo(datetime(2026, 11, 4, 6, 20, tzinfo=timezone.utc))           # depois das 06:00 UTC
    assert reads(http, *SG_READS) == 3 and reads(http, SENS_READ) == 0
    assert doc(http.uploads["stock-guide.json"])["companies"][0]["targetPrice"] == 99.0
    http = ciclo(datetime(2026, 11, 4, 7, 30, tzinfo=timezone.utc))
    assert reads(http, *SG_READS) == 0                                        # 1×/dia
    http = ciclo(datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc))           # domingo: a semanal ainda não
    assert reads(http, SENS_READ) == 0
    http = ciclo(datetime(2026, 11, 9, 6, 10, tzinfo=timezone.utc))           # segunda 06:10
    assert reads(http, SENS_READ) == 1
    assert doc(http.uploads["sg-sensitivity.json"])["companies"][0]["modelPrice"] == 12.5
    http = ciclo(datetime(2026, 11, 10, 6, 10, tzinfo=timezone.utc))
    assert reads(http, SENS_READ) == 0 and reads(http, *SG_READS) == 3       # a diária, sim
    assert P.weekly_mark(datetime(2026, 11, 9, 5, 0, tzinfo=timezone.utc), 0, 6) == "2026-11-02T06:00:00Z"
    assert P.daily_mark(datetime(2026, 11, 4, 5, 59, tzinfo=timezone.utc), 6) == "2026-11-03T06:00:00Z"


def test_releitura_que_falha_tenta_de_novo_sem_sondar_outra_vez(tmp_path):
    p = tmp_path / "st.json"
    http = F.FakeHTTP(supa_error={"get_stock_guide_comps": 500})
    res = run(http, p)
    assert res["stock-guide.json"].action == "erro" and "HTTP 500" in res["stock-guide.json"].reason
    assert res["sg-sensitivity.json"].action == "enviado"                     # o resto segue
    assert len(http.supa_calls("stock_guide_companies")) == 1
    http = F.FakeHTTP()
    res = run(http, p, now=F.NOW + timedelta(minutes=5))                      # o ciclo seguinte
    assert res["stock-guide.json"].action == "enviado"
    assert not http.supa_calls("stock_guide_companies") and reads(http, *SG_READS) == 3   # sonda guardada


# ── 4) marcador `source`, partida a frio, Mindassets parado ────────────────────────────────────────
def test_nova_subida_identica_da_planilha_um_envio_e_depois_estavel(tmp_path):
    p = tmp_path / "st.json"
    run(F.FakeHTTP(), p)

    def subida():
        http = F.FakeHTTP()
        for r in http.database["stock_guide_companies"]:
            r["updated_at"] = "2026-11-07T10:00:00+00:00"                     # o lote regrava TODOS
        return http
    http = subida()
    res = run(http, p, now=F.NOW + timedelta(hours=2))
    assert res["stock-guide.json"].action == "enviado" and res["stock-guide.json"].reason == "mudou"
    novo = doc(http.uploads["stock-guide.json"])
    assert novo["source"]["stamp"] == "2026-11-07T10:00:00Z" and novo["updatedAt"] == "2026-11-07T10:00:00Z"
    http = subida()
    res = run(http, p, now=F.NOW + timedelta(hours=3))
    assert res["stock-guide.json"].action == "igual" and reads(http, *SG_READS) == 0
    # runner novo: a cópia publicada já tem o marcador da subida → nada lido
    http = subida()
    http.published = {"stock-guide.json": to_json_bytes(novo)}
    res = run(http, tmp_path / "novo.json", now=F.NOW + timedelta(hours=4), only="stock-guide")
    assert res["stock-guide.json"].action == "igual" and reads(http, *SG_READS) == 0
    assert "igual à cópia publicada" in res["stock-guide.json"].reason


def test_partida_a_frio_com_a_copia_publicada_igual_so_a_sonda(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    primeiro = F.FakeHTTP()
    run(primeiro, tmp_path / "a.json")
    http = F.FakeHTTP(published=dict(primeiro.uploads))
    res = run(http, tmp_path / "b.json", now=F.NOW + timedelta(hours=7))       # runner novo, fora da janela
    assert {res[n].action for n in WAVE4} == {"igual"} and http.uploads == {}
    assert reads(http, *SG_READS, SENS_READ, DS_READ) == 0
    assert any("nada lido" in r.getMessage() for r in caplog.records)
    # o marcador publicado é de OUTRA subida: lê, compara pelo hash e só manda se mudou
    velho = {**doc(primeiro.uploads["stock-guide.json"]),
             "source": {"stamp": "2026-10-01T00:00:00Z", "companies": 4, "peers": 3}}
    http = F.FakeHTTP(published={**primeiro.uploads, "stock-guide.json": to_json_bytes(velho)})
    res = run(http, tmp_path / "c.json", now=F.NOW + timedelta(hours=7))
    assert reads(http, *SG_READS) == 3 and res["stock-guide.json"].action == "enviado"
    # partida a frio nas 2 h depois da marca da segurança diária: lê (o runner que morreu pode não ter lido)
    http = F.FakeHTTP(published=dict(primeiro.uploads))
    run(http, tmp_path / "d.json", now=datetime(2026, 11, 4, 6, 30, tzinfo=timezone.utc))
    assert reads(http, *SG_READS) == 3 and reads(http, SENS_READ, DS_READ) == 0


def test_mindassets_parado_e_sem_semente_a_leitura_espera_e_le_1x_por_dia(tmp_path):
    velho = {"schemaVersion": 1, "generatedAt": "2026-11-01T00:00:00Z"}       # live.json parado há dias
    pub = {"live.json": to_json_bytes(velho)}
    p = tmp_path / "st.json"
    http = F.FakeHTTP(published=dict(pub))
    res = run(http, p)
    for n in WAVE4:
        assert res[n].action == "erro" and "espera" in res[n].reason, n
    assert reads(http, *SG_READS, SENS_READ, DS_READ) == 0
    http = F.FakeHTTP(published=dict(pub))
    run(http, p, now=F.NOW + timedelta(minutes=5))                            # tenta de novo: sonda guardada
    assert not http.supa_calls("stock_guide_companies") and reads(http, *SG_READS) == 0
    sete = datetime(2026, 11, 4, 7, 10, tzinfo=timezone.utc)
    http = F.FakeHTTP(published=dict(pub))
    res = run(http, p, now=sete)                                              # a hora do dia: lê
    assert {res[n].action for n in WAVE4} == {"enviado"} and reads(http, *SG_READS) == 3
    http = F.FakeHTTP(published=dict(pub))
    res = run(http, tmp_path / "outro.json", now=sete + timedelta(hours=2))   # runner novo, fora da hora
    assert {res[n].action for n in WAVE4} == {"erro"}
    res = run(F.FakeHTTP(published=dict(pub)), tmp_path / "out.json", mode="out",
              out_dir=tmp_path / "o")                                         # o --out nunca espera
    assert {res[n].action for n in WAVE4} == {"gravado"}


# ── 5) calendário completo (H.3) ───────────────────────────────────────────────────────────────────
def test_calendario_com_local_descricao_e_links_pelo_filtro_e_pela_mascara():
    http = F.FakeHTTP()
    ev = http.database["exec_calendar_events"][0]
    ev.update(location="Sala 3 — tel. (11) 3708-8000",
              description=("Inscrição em " + TRACKERS[0] + " ou https://zoom.us/webinar/register/WN_ficticio. "
                           "Dúvidas: analista.ficticio@example.com, +55 (11) 99999-0000."),
              links=[{"label": "confirmar", "url": TRACKERS[0]}, {"label": "webinar", "url": TRACKERS[1]},
                     {"label": "", "url": TRACKERS[2]},
                     {"label": "  Zoom  ", "url": "https://zoom.us/webinar/register/WN_ficticio"},
                     {"label": None, "url": "https://www.b3.com.br/cronograma"},
                     {"label": "e-mail no link", "url": "https://x.example.com/?email=fulano@example.com"},
                     {"label": "x", "url": "javascript:alert(1)"}, "lixo"])
    c, _ = build("calendar.json", http=http)
    e1 = next(e for e in c["events"] if e["id"] == "e-1")
    assert list(e1) == list(CAL.EVENT_KEYS)
    assert e1["location"] == "Sala 3 — tel. [telefone]"
    assert e1["description"] == ("Inscrição em [link] ou https://zoom.us/webinar/register/WN_ficticio. "
                                 "Dúvidas: [e-mail], [telefone].")
    assert e1["links"] == [{"label": "Zoom", "url": "https://zoom.us/webinar/register/WN_ficticio"},
                           {"label": None, "url": "https://www.b3.com.br/cronograma"}]
    texto = json.dumps(c, ensure_ascii=False)
    for pedaco in ("sfmc", "mziq", "closir", "upn=", "CampaignID", "example.com", "99999-0000", "3708"):
        assert pedaco not in texto.replace("WN_ficticio", ""), pedaco
    for u in TRACKERS:
        assert public_url(u) is None and clean_text(u) == "[link]"
    e5 = next(e for e in c["events"] if e["id"] == "e-5")
    assert e5["location"] == "São Paulo" and e5["description"] == "Dúvidas: [e-mail] ou [telefone]."
    assert e5["links"] == [{"label": "Inscrição", "url": "https://events.itaubba.com/mining"},
                           {"label": None, "url": "https://www.example.org/agenda-ficticia"}]
    e2 = next(e for e in c["events"] if e["id"] == "e-2")
    assert (e2["location"], e2["description"], e2["links"]) == (None, None, [])


def test_calendario_sem_limite_inferior_e_from_do_evento_unico_mais_antigo():
    c, http = build("calendar.json")
    assert [e["id"] for e in c["events"]] == ["e-4", "e-2", "e-1", "e-5"]     # o de 2023 volta
    assert (c["from"], c["to"]) == ("2023-05-01", "2028-05-31")               # não a recorrente de 2025
    ev = next(x for x in http.supa_calls() if x.table == "rpc/get_exec_calendar_events")
    assert b'"p_from": "1900-01-01"' in ev.data and b'"p_to": "2028-05-31"' in ev.data
    sel = ev.params["select"].split(",")
    assert {"location", "description", "links"} <= set(sel)
    assert not {"external_id", "ics_seq", "locked", "updated_by", "updated_at"} & set(sel)
    assert CAL.first_day([]) is None
    assert CAL.first_day([{"startDate": "2025-03-05", "recurrence": {"freq": "monthly"}}]) == "2025-03-05"


def test_texto_livre_idempotente_e_url_colada_na_pontuacao():
    t = "Veja https://ri.vale.com/3q26. E " + TRACKERS[1] + ", ok."
    assert clean_text(t) == "Veja https://ri.vale.com/3q26. E [link], ok."
    assert clean_text(clean_text(t)) == clean_text(t)
    assert clean_text("   ") is None and clean_text(None) is None and clean_text(12) is None
    reuniao = "Link https://zoom.us/j/81234567890 (ID 812 3456 7890)"
    assert clean_text(reuniao) == reuniao                                     # o número da reunião não é telefone
    assert clean_text("sem link  nem marcador") == "sem link  nem marcador"


# ── 6) glossário (H.5) ─────────────────────────────────────────────────────────────────────────────
def test_glossario_forma_rotas_e_sem_nota_nem_caminho(caplog):
    caplog.set_level(logging.WARNING)
    g, http = build("glossary.json")
    assert list(g) == ["schemaVersion", "generatedAt", "updatedAt", "source", "sections"]
    assert g["source"] == {"blob": F.glossary_blob(), "commit": F.GLOSSARY_COMMIT[0],
                           "committedAt": F.GLOSSARY_COMMIT[1]}
    assert g["updatedAt"] == F.GLOSSARY_COMMIT[1]
    rotas = {i["term"]: i["route"] for s in g["sections"] for i in s["items"]}
    assert rotas == {"Minério fictício 61%": "", "Minério fictício 62%": "market",
                     "Import volume (fictício)": "steel-imports", "Import model — orange line": "steel-prediction",
                     "Termo sem endereço": None, "Termo de página que sumiu": None,
                     "China woodchip imports (GACC)": "woodchips", "LTM shipments": "corrugated",
                     "Paper production (fictício)": "paper-fundamentals", "Buyback programs": "buybacks",
                     "Sensitivity tables": "stock-guide-sensitivity"}
    assert [s["id"] for s in g["sections"]] == ["commodities", "steel", "pulp", "market_watch"]
    assert g["sections"][2]["intro"] == "Contato: [e-mail]" and g["sections"][3]["intro"] is None
    m62 = g["sections"][0]["items"][1]
    assert (m62["unit"], m62["freq"]) == (None, None) and list(m62) == list(GL.ITEM_KEYS)
    avisos = [r.getMessage() for r in caplog.records if "sem seção no hub" in r.getMessage()]
    assert len(avisos) == 1 and "/pagina-antiga.html" in avisos[0]
    texto = json.dumps(g, ensure_ascii=False)
    for interno in ("Nota interna", "2026-09-02", "/index.html", "steel_sm_dashboard", '"where"', '"note"'):
        assert interno not in texto, interno


def test_tabela_where_rota_cobre_os_15_enderecos_e_as_excecoes():
    assert len(GL.WHERE_ROUTE) == 15
    assert set(GL.WHERE_ROUTE.values()) | set(GL.TERM_ROUTE.values()) == set(GL.ROUTES)   # toda rota alcançável
    assert set(GL.TERM_ROUTE.values()) == {"steel-prediction", "woodchips", "corrugated"}
    # os 5 termos de exceção do site (origin/main cffdadc), com o endereço da página-mãe
    sm, pp = "/Steel and Mining/steel_sm_dashboard.html", "/Pulp and Paper/pp_dashboard.html"
    casos = {("Import model — orange line", sm + "#imports"): "steel-prediction",
             ("Customs-of-origin exports — black line (Korea, China)", sm + "#imports"): "steel-prediction",
             ("China woodchip imports (GACC)", pp + "#pulp"): "woodchips",
             ("Corrugated shipments (Empapel / IBPO)", pp + "#paper"): "corrugated",
             ("  ltm   SHIPMENTS ", pp + "#paper"): "corrugated",
             ("Iron ore exports", sm + "#exports"): "steel-exports",
             ("Import model — orange line", None): None,                     # sem endereço, sem link
             ("Import model — orange line", "/outra.html"): None}
    for (term, where), rota in casos.items():
        assert GL.route(term, where) == rota, term
    for where, rota in GL.WHERE_ROUTE.items():
        assert GL.route("Qualquer termo", where) == rota


def _gl_run(http, p, *, now=F.NOW, env=ENV_GH, **kw):
    return run(http, p, now=now, only="config,glossary", env=env, **kw)


def test_glossario_evento_sonda_condicional_e_blob_novo(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    p = tmp_path / "st.json"
    http = F.FakeHTTP()
    res = _gl_run(http, p)
    assert res["glossary.json"].action == "enviado" and http.uploads["glossary.json"][:2] == b"\x1f\x8b"
    assert [len(http.github_calls(k)) for k in ("sonda", "download", "commit")] == [1, 1, 1]
    assert http.github_calls("download")[0].url.endswith("/git/blobs/" + F.glossary_blob())
    for minutos, sondas, n304 in ((5, 0, 0), (10, 0, 0), (15, 1, 1), (20, 0, 0)):
        http = F.FakeHTTP()
        res = _gl_run(http, p, now=F.NOW + timedelta(minutes=minutos))
        assert res["glossary.json"].action == "fora da vez" and "glossary.json" not in http.uploads
        assert len(http.github_calls("sonda")) == sondas and http.github.not_modified == n304, minutos
        assert not http.github_calls("download")
    # o editor mudou um termo: blob novo → baixa, confere e manda
    novo = json.loads(json.dumps(F.GLOSSARY_DOC))
    novo["sections"][0]["items"][0]["desc"] = "Descrição revista."
    http = F.FakeHTTP()
    http.github.files[F.GLOSSARY_PATH] = F.glossary_bytes(novo)
    http.github.commits[F.GLOSSARY_PATH] = ("c" * 40, "2026-11-03T19:00:00Z")
    res = _gl_run(http, p, now=F.NOW + timedelta(minutes=30))
    assert res["glossary.json"].action == "enviado" and len(http.github_calls("download")) == 1
    g = doc(http.uploads["glossary.json"])
    assert g["source"] == {"blob": F.glossary_blob(novo), "commit": "c" * 40, "committedAt": "2026-11-03T19:00:00Z"}
    assert g["sections"][0]["items"][0]["desc"] == "Descrição revista."
    assert any(r.getMessage().startswith("glossário:") for r in caplog.records)


def test_glossario_partida_a_frio_pelo_blob_publicado(tmp_path):
    primeiro = F.FakeHTTP()
    _gl_run(primeiro, tmp_path / "a.json")
    http = F.FakeHTTP(published=dict(primeiro.uploads))
    res = _gl_run(http, tmp_path / "b.json")
    assert res["glossary.json"].action == "fora da vez" and http.uploads == {}          # config igual
    assert len(http.github_calls()) == 1 and not http.github_calls("download")   # só a sonda


@pytest.mark.parametrize("falha", [503, F.FakeResp(403, {"message": "rate limit"},
                                                      headers={"X-RateLimit-Remaining": "0"})])
def test_glossario_com_o_github_fora_nada_muda_e_o_ciclo_segue(tmp_path, caplog, falha):
    caplog.set_level(logging.INFO)
    http = F.FakeHTTP()
    http.github.fail["sonda"] = falha
    res = _gl_run(http, tmp_path / "st.json")
    assert res["config.json"].action == "enviado" and res["glossary.json"].action == "fora da vez"
    assert "glossary.json" not in http.uploads
    linhas = [r.getMessage() for r in caplog.records if r.getMessage().startswith("glossário:")]
    assert len(linhas) == 1 and "não muda neste ciclo" in linhas[0]
    assert F.GH_TOKEN not in caplog.text and "api.github.com" not in caplog.text
    # o download falhou (SHA adulterado): erro só do glossário, o resto segue, nada publicado
    http = F.FakeHTTP()
    http.github.corrupt = True
    res = _gl_run(http, tmp_path / "b.json")
    assert res["glossary.json"].action == "erro" and "SHA" in res["glossary.json"].reason
    assert list(http.uploads) == ["config.json"]


def test_glossario_sem_token_no_envio_fica_fora_e_no_out_vai(tmp_path):
    http = F.FakeHTTP()
    res = _gl_run(http, tmp_path / "st.json", env=F.env())
    assert list(res) == ["config.json"] and http.github_calls() == []
    res = _gl_run(F.FakeHTTP(), tmp_path / "o.json", env=F.env(), mode="out", out_dir=tmp_path / "out")
    assert res["glossary.json"].action == "gravado"
    assert doc((tmp_path / "out" / "glossary.json").read_bytes())["source"]["blob"] == F.glossary_blob()


def test_glossario_que_nao_e_json_nao_publica():
    http = F.FakeHTTP()
    http.github.files[F.GLOSSARY_PATH] = b"isto nao e json"
    with pytest.raises(ValueError, match="não é JSON"):
        build("glossary.json", http=http)
    http = F.FakeHTTP()
    http.github.files[F.GLOSSARY_PATH] = F.glossary_bytes({"generated": "x"})
    with pytest.raises(ValueError, match="seções"):
        build("glossary.json", http=http)


# ── 7) registro, CLI, nenhuma exceção derruba o ciclo ──────────────────────────────────────────────
def test_registro_ordem_e_nomes_da_onda_4():
    for n in WAVE4:
        assert FILES[n].cadence == 3600 and FILES[n].group is None and FILES[n].pulse is None
    assert (FILES["stock-guide.json"].gzip, FILES["sg-sensitivity.json"].gzip,
            FILES["data-sources.json"].gzip, FILES["glossary.json"].gzip) == (True, True, False, True)
    g = FILES["glossary.json"]
    assert g.cadence == -1 and g.urgent is not None and g.after is not None and "glossary.json" in GITHUB_FILES
    assert ORDER.index("mw-short") < ORDER.index("stock-guide.json") < ORDER.index("history")
    assert ORDER.index("history-index.json") < ORDER.index("glossary.json") < ORDER.index("mm-trade.json")
    assert resolve("stock-guide,sg-sensitivity.json,data-sources,glossary") == WAVE4 + ["glossary.json"]
    assert not [n for n in FILES if n.startswith("quarterly")]                # U11: nada do Quarterly


def test_workflow_lista_os_nomes_novos():
    import re
    wf = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "hub-export.yml"
    desc = re.search(r'description: "Arquivos: (.*)"', wf.read_text(encoding="utf-8")).group(1)
    for nome in ("stock-guide", "sg-sensitivity", "data-sources", "glossary", "calendar"):
        assert nome in desc, nome


def test_nenhuma_excecao_derruba_o_ciclo(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    http = F.FakeHTTP(supa_error={"get_stock_guide_sensitivity_tables": 500, "data_source_status": 503})
    http.database["stock_guide_global_peers"].append({"company": None, "display_order": 99})   # lixo
    http.database["stock_guide_companies"].append({"ticker": "  ", "is_visible": True})
    res = run(http, tmp_path / "st.json", only="config," + PAGES)
    assert res["config.json"].action == "enviado" and res["stock-guide.json"].action == "enviado"
    assert res["sg-sensitivity.json"].action == "erro" and res["data-sources.json"].action == "erro"
    assert len(doc(http.uploads["stock-guide.json"])["companies"]) == 4        # o lixo ficou de fora
    for segredo in (F.SUPA_KEY, F.FEEDER_KEY, "projeto-secreto"):
        assert segredo not in caplog.text


def test_out_grava_os_mesmos_bytes_do_envio(tmp_path):
    enviados = F.FakeHTTP()
    run(enviados, tmp_path / "a.json", env=ENV_GH, only=PAGES + ",glossary,calendar")
    out = tmp_path / "saida"
    res = run(F.FakeHTTP(), tmp_path / "b.json", mode="out", out_dir=out, only=PAGES + ",glossary,calendar")
    assert {r.action for r in res.values()} == {"gravado"}
    for n in WAVE4 + ["glossary.json", "calendar.json"]:
        assert (out / n).read_bytes() == enviados.uploads[n], n
    assert (out / "data-sources.json").read_bytes()[:1] == b"{"                # texto
    assert gzip.decompress((out / "stock-guide.json").read_bytes()).startswith(b'{"schemaVersion":1')
    assert content_hash(doc(enviados.uploads["stock-guide.json"])) == res["stock-guide.json"].hash


def test_estado_antigo_sem_as_secoes_da_onda_4_continua_valendo(tmp_path):
    p = tmp_path / "st.json"
    velho = S.empty_state()
    for k in ("stockGuide", "sgSensitivity", "dataSources", "glossary"):
        velho.pop(k)
    S.save(p, velho)
    res = run(F.FakeHTTP(), p)
    assert {res[n].action for n in WAVE4} == {"enviado"}


def test_check_confere_as_sondas_e_as_rpcs_da_onda_4_sem_valores():
    from hub_export.checks import run_checks
    from hub_export.supa import SupaReader
    http = F.FakeHTTP()
    linhas = run_checks(SupaReader(F.SUPA_URL, F.SUPA_KEY, http=http), now=F.NOW)
    texto = "\n".join(linhas)
    assert "sonda stock_guide_sensitivities (sens2d): 4 linha(s), max(updated_at) 2026-11-12T09:00:00Z" in texto
    assert "sonda data_source_status: 4 linha(s), max(checked_at) 2026-11-11T19:46:12Z" in texto
    assert "rpc get_stock_guide_comps (service key): 4 linha(s)" in texto
    assert "rpc get_stock_guide_sensitivity_tables (service key): 3 linha(s)" in texto   # sem a oculta
    assert "stock_guide=True" in texto and "data_page=True" in texto
    for valor in ("Vale", "CAP.SN", "how_pulled", "_inbox", F.SUPA_URL, F.SUPA_KEY):
        assert valor not in texto


def test_a_sonda_das_empresas_serve_ao_ratings_e_ao_stock_guide_no_mesmo_ciclo(tmp_path):
    http = F.FakeHTTP()
    res = run(http, tmp_path / "st.json", only="ratings,stock-guide")
    assert {r.action for r in res.values()} == {"enviado"}
    assert len(http.supa_calls("stock_guide_companies")) == 1               # uma chamada, dois arquivos
    assert len(http.supa_calls("stock_guide_global_peers")) == 1
