"""
Onda 2 (WP2.4B) — os arquivos do Market Watch: mw-companies (com os fatos do cabeçalho da
Performance), mw-short-interest + a família mw-short-<empresa>, mw-buybacks, mw-insiders e mw-filings
(PLANO, Apêndice F.6–F.11), e de onde vem cada dado (hub_export/mw.py).

O que estes testes TRAVAM, e por quê:
  1) forma exata de cada arquivo, campo a campo, na ordem do contrato; nada de `cnpj`, `cvm_code`,
     `b3_code`, `is_visible`, `company_name`, `qty_registro/d0/d1`, `id` dos insiders, `updated_at`
     por linha — nem da empresa OCULTA no /admin;
  2) os FATOS da Performance (`_mwFacts` portado): % do float pela classe da EMPRESA (ON, PN, PNA/PNB
     pelo `float_by_class`, UNIT ÷ 5, BDR pelo total), recompra ativa (status manda; sem status, o
     prazo — o defeito M7 corrigido), negócios de insiders dos últimos 12 meses (a regra /compra|venda/
     do site, com o "Exercício opção de venda" contando como venda), `null` quando não dá;
  3) o corte de 1.000 linhas do PostgREST nunca morde: insiders (MAIS de 1.000 na janela, o site
     recebia só 1.000) e a série de uma empresa (ordem CRESCENTE: perderia os dias mais NOVOS) são
     lidos em páginas, com a soma conferida;
  4) Supabase barato: na mesma hora, nada é relido; na hora seguinte, só as sondas (~1 KB); dia novo
     de aluguel = só a janela de 10 dias; revisão da B3 = pela janela nas marcas de 16:00/23:30;
     backfill antigo = contagem por empresa e releitura SÓ da empresa que não fecha; insiders só
     quando a sonda muda (a janela que anda no mês cai do cache sem ler); comunicados só os
     inseridos desde o último `updatedAt`; recompras e capital só nas marcas; partida a frio pelas
     CÓPIAS PUBLICADAS, sem leitura cara; Mindassets parado sem cópia = espera;
  5) empresa ocultada no /admin some de TODOS os arquivos na hora seguinte;
  6) o teto de 4 envios do Market Watch por execução (O28): o resto fica "na fila" (não é erro) e
     vai nos ciclos seguintes sem reler nada; o dry-run simula, o --out grava tudo;
  7) a máscara de e-mail e telefone (U6) passou em TODO texto publicado (varredura dos arquivos).
"""
from __future__ import annotations

import gzip
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import test_hub_export_fixtures as F  # noqa: E402
from hub_export import mw as MW  # noqa: E402
from hub_export.encode import forbidden_keys, gzip_bytes, to_json_bytes  # noqa: E402
from hub_export.exporters import mw_companies, mw_insiders  # noqa: E402
from hub_export.registry import FILES, spec_for  # noqa: E402
from hub_export.run import run_cycle  # noqa: E402
from hub_export.urls import safe_url  # noqa: E402

ONLY = ",".join(F.MW_FIXED) + ",mw-short"
ALL_MW = F.MW_FIXED + F.MW_FAMILY_FILES


def run(http, path, now=F.NOW, only=ONLY, **kw):
    kw.setdefault("memory", F.memory())
    return {r.name: r for r in run_cycle(env=F.env(), http=http, now=now, state_file=path,
                                         only=only, **F.run_kw(**kw))}


def doc(raw: bytes) -> dict:
    return json.loads(gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw)


def mw_calls(http, fn: str | None = None) -> list:
    return [c for c in http.supa_calls() if c.table.startswith("rpc/get_mw_")
            and (fn is None or c.table == f"rpc/{fn}")]


def reads(http, fn: str) -> list:
    """As leituras de verdade de uma RPC (as páginas do rpc_all; não a sonda de `limit=1`)."""
    return [c for c in mw_calls(http, fn) if c.params.get("limit") != "1"]


def build_all(http=None, now=F.NOW, published=None):
    """Os arquivos do Market Watch montados direto pelos construtores (sem o ciclo)."""
    src, http = F.make_sources(http=http, now=now, published=published)
    out = {n: FILES[n].builder(src) for n in F.MW_FIXED}
    for n in F.MW_FAMILY_FILES:
        out[n] = spec_for(n).builder(src)
    return out, src, http


# ── forma ─────────────────────────────────────────────────────────────────────────────────────
def test_formas_exatas_dos_arquivos():
    out, _src, _ = build_all()
    c = out["mw-companies.json"]
    assert list(c) == ["schemaVersion", "generatedAt", "lendingThrough", "companies", "shortLatest"]
    assert [x["ticker"] for x in c["companies"]] == F.MW_VISIBLE          # display_order, sem a oculta
    assert list(c["companies"][0]) == ["ticker", "name", "shareClass", "lendingTickers", "quote",
                                       "sector", "order", "capital", "facts"]
    usim = next(x for x in c["companies"] if x["ticker"] == "USIM5")
    assert usim["lendingTickers"] == ["USIM3", "USIM5", "USIM6"] and usim["quote"] == "USIM5.SA"
    assert list(usim["capital"]) == ["refDate", "docVersion", "sharesOn", "sharesPn", "sharesTotal",
                                     "floatOn", "floatPn", "floatTotal", "pctFloatTotal",
                                     "floatByClass"]
    assert list(usim["capital"]["floatByClass"]) == ["PNA", "PNB"]          # A→Z, como número
    assert next(x for x in c["companies"] if x["ticker"] == "RANI3")["capital"] is None
    assert list(c["companies"][0]["facts"]) == ["shortPctFloat", "shortAsOf", "buybackActive",
                                                "insidersNet12m", "insidersTrades12m"]
    assert c["lendingThrough"] == "2026-11-02" and len(c["shortLatest"]) == 11
    assert list(c["shortLatest"][0]) == ["ticker", "company", "refDate", "qtyTotal", "valueBrl",
                                         "avgPrice", "rateTakerAvg", "qty1m", "rate1m", "days"]
    assert [(r["company"], r["ticker"]) for r in c["shortLatest"]][:3] == \
        [("AURA33", "AURA33"), ("BRAP4", "BRAP3"), ("BRAP4", "BRAP4")]     # a ordem da RPC
    usim6 = next(r for r in c["shortLatest"] if r["ticker"] == "USIM6")
    assert usim6["days"] == 3 and usim6["qty1m"] is None                    # sem pregão 30 dias antes

    s = out["mw-short-interest.json"]
    assert list(s) == ["schemaVersion", "generatedAt", "from", "columns", "series"]
    assert s["from"] == "2026-07-25" and s["columns"] == ["refDate", "qtyTotal", "rateTakerAvg"]
    assert list(s["series"])[:6] == ["VALE3", "BRAP4", "BRAP3", "USIM5", "USIM3", "USIM6"]
    assert s["series"]["BRAP3"]["company"] == "BRAP4"
    rows = s["series"]["VALE3"]["rows"]
    assert len(rows) == 15 and rows[0][0] == "2026-07-30" and rows[-1][0] == "2026-11-02"
    assert all(len(r) == 3 for r in rows) and len(s["series"]["USIM6"]["rows"]) == 3

    k = out["mw-short-klbn11.json"]
    assert list(k) == ["schemaVersion", "generatedAt", "company", "columns", "series"]
    assert k["company"] == "KLBN11" and list(k["series"]) == ["KLBN11", "KLBN3", "KLBN4"]
    assert k["columns"] == ["refDate", "qtyTotal", "valueBrl", "avgPrice", "rateTakerAvg",
                            "rateTakerMin", "rateTakerMax", "rateDonorAvg", "contractsDay",
                            "qtyDay", "valueDay", "source"]
    serie = k["series"]["KLBN11"]
    assert len(serie) == 17 and [r[0] for r in serie] == sorted(r[0] for r in serie)
    assert serie[0][-1] == "bdi_pdf" and serie[-1][-1] == "bdi_api"

    b = out["mw-buybacks.json"]
    assert list(b) == ["schemaVersion", "generatedAt", "items"]
    assert [i["programId"] for i in b["items"]] == [-1, 1973, 1900, 1800, 1500, 1700]   # sem a oculta
    assert list(b["items"][0]) == ["programId", "company", "decidedOn", "expiresOn", "status",
                                   "operation", "reason", "purpose", "qtyOn", "qtyPn", "qtyCircOn",
                                   "qtyCircPn", "authorizedUsd", "brokers"]
    vale = next(i for i in b["items"] if i["programId"] == 1973)
    assert vale["brokers"] == ["BTG PACTUAL CTVM S.A.", "J.P. MORGAN CCVM S.A."]
    assert next(i for i in b["items"] if i["programId"] == -1)["authorizedUsd"] == 200000000

    i = out["mw-insiders.json"]
    assert list(i) == ["schemaVersion", "generatedAt", "updatedAt", "from", "columns", "rows"]
    assert i["columns"] == ["company", "refMonth", "docVersion", "entityType", "entity", "groupType",
                            "moveType", "moveDesc", "operation", "assetType", "assetClass",
                            "broker", "moveDate", "qty", "unitPrice", "volume", "isBalance"]
    assert i["from"] == "2025-10-01" and i["updatedAt"] == "2026-10-19T09:00:00Z"
    assert len(i["rows"]) == 1026 and all(len(r) == 17 for r in i["rows"])

    f = out["mw-filings.json"]
    assert list(f) == ["schemaVersion", "generatedAt", "updatedAt", "items"]
    assert list(f["items"][0]) == ["id", "company", "headline", "category", "docDate", "publishedAt",
                                   "flag", "url", "newsworthy", "docTitle", "docExcerpt"]
    assert f["updatedAt"] == "2026-11-03T01:10:00Z"


def test_nada_de_coluna_interna_nem_da_empresa_oculta():
    out, _src, _ = build_all()
    for name, obj in out.items():
        assert forbidden_keys(obj, extra=spec_for(name).forbidden) == [], name
        texto = json.dumps(obj, ensure_ascii=False)
        for interno in ("00.000.000/0001-91", "009999", "RAZAO SOCIAL INTERNA", "is_visible",
                        "qty_registro", "updated_at", "CSNA3", "CIA SIDERURGICA"):
            assert interno not in texto, (name, interno)
    ids = {m["id"] for m in F.MW_INSIDERS}
    assert not ids & set(re.findall(r"[0-9a-f]{24}", json.dumps(out["mw-insiders.json"])))
    assert "id" not in out["mw-insiders.json"]["columns"]
    assert 1600 not in {i["programId"] for i in out["mw-buybacks.json"]["items"]}       # da oculta
    assert 3545600 not in {i["id"] for i in out["mw-filings.json"]["items"]}
    assert F.MW_HIDDEN not in {c["ticker"] for c in out["mw-companies.json"]["companies"]}
    assert "mw-short-csna3.json" not in out


def test_nomes_da_familia_pela_regra_f0():
    assert MW.file_name("KLBN11") == "mw-short-klbn11.json" and MW.file_name("AURA33") == "mw-short-aura33.json"
    assert MW.company_from_file("mw-short-klbn11.json") == "KLBN11"
    assert MW.company_from_file("mw-short-interest.json") is None          # o fixo, não uma empresa
    assert MW.file_name("INTEREST") is None and MW.file_name("^BVSP") is None and MW.file_name(None) is None
    assert MW.company_from_file("mw-short-KLBN11.json") is None and MW.company_from_file("history-x.json") is None


# ── os fatos do cabeçalho da Performance ──────────────────────────────────────────────────────
def test_fatos_de_cada_empresa():
    out, _src, _ = build_all()
    facts = {c["ticker"]: c["facts"] for c in out["mw-companies.json"]["companies"]}
    lat = {r["ticker"]: r["qtyTotal"] for r in out["mw-companies.json"]["shortLatest"]}
    # ON: ÷ float ON · PN: ÷ float PN · PNA: ÷ float_by_class["PNA"] · UNIT: ÷ (ON+PN)/5 · BDR: total
    assert facts["VALE3"]["shortPctFloat"] == pytest.approx(lat["VALE3"] / 4268000000 * 100)
    assert facts["BRAP4"]["shortPctFloat"] == pytest.approx(lat["BRAP4"] / 230000000 * 100)
    assert facts["USIM5"]["shortPctFloat"] == pytest.approx(lat["USIM5"] / 518248757 * 100)
    assert facts["KLBN11"]["shortPctFloat"] == pytest.approx(lat["KLBN11"] / 980000000 * 100)
    assert facts["AURA33"]["shortPctFloat"] == pytest.approx(lat["AURA33"] / 30000000 * 100)
    assert facts["RANI3"]["shortPctFloat"] is None and facts["RANI3"]["shortAsOf"] is None  # sem capital
    assert facts["VALE3"]["shortAsOf"] == "2026-11-02"
    # recompra: o status manda (encerrado com prazo futuro = não); sem status, o prazo (M7)
    assert {t: f["buybackActive"] for t, f in facts.items()} == {
        "VALE3": True, "BRAP4": False, "USIM5": False, "KLBN11": True, "RANI3": False, "AURA33": True}
    # insiders 12 m (desde 01/11/2025): VALE3 +1.000 −400 (o de out/2025 fica fora); KLBN11 −5.000 e
    # o "Exercício opção de venda" (casa /venda/, como no site) −100; BRAP4 "Compra" +50; USIM5 e
    # RANI3 só saldos (0 negócio); AURA33 sem movimentação nenhuma (null)
    assert {t: (f["insidersNet12m"], f["insidersTrades12m"]) for t, f in facts.items()} == {
        "VALE3": (600, 2), "BRAP4": (50, 1), "USIM5": (0, 0), "KLBN11": (-5100, 2), "RANI3": (0, 0),
        "AURA33": (None, None)}


def test_regras_dos_fatos_casos_de_borda():
    cap = {"floatOn": 100, "floatPn": 400, "floatTotal": 999, "floatByClass": {"PNB": 7}}
    cf = mw_companies.class_float
    assert cf({"shareClass": "UNT"}, cap) == 100                        # (100 + 400) / 5
    assert cf({"shareClass": "UNT"}, {"floatOn": 1, "floatPn": 1}) is None   # arredonda a 0 → null
    assert cf({"shareClass": "PNB"}, cap) == 7 and cf({"shareClass": "PNA"}, cap) == 400
    assert cf({"shareClass": None}, cap) == 100                         # sem classe = ON
    assert cf({"shareClass": "BDR"}, cap) == 999 and cf({"shareClass": "ON"}, None) is None
    co = {"ticker": "X3", "shareClass": "ON"}
    lat = [{"ticker": "X4", "company": "X3", "refDate": "2026-11-02", "qtyTotal": 10}]
    f = mw_companies.facts(co, {"floatOn": 1000}, lat, [], [], "2026-11-03")
    assert f["shortPctFloat"] == 1.0 and f["shortAsOf"] == "2026-11-02"   # sem o principal: o 1º
    f = mw_companies.facts(co, {"floatOn": 0}, lat, [], [], "2026-11-03")
    assert (f["shortPctFloat"], f["shortAsOf"]) == (None, None)            # float 0 → null
    bb = [{"company": "X3", "status": None, "expiresOn": "2026-11-03"}]
    assert mw_companies.facts(co, None, [], bb, [], "2026-11-03")["buybackActive"] is True  # ≥ hoje
    assert mw_companies.facts(co, None, [], bb, [], "2026-11-04")["buybackActive"] is False
    assert mw_companies.months_back("2026-11-03", 12) == "2025-11-01"
    assert mw_companies.months_back("2028-02-29", 12) == "2027-03-01"     # transborda como o JS
    from datetime import date
    assert mw_insiders.window_from(date(2026, 11, 3)) == "2025-10-01"
    assert mw_insiders.window_from(date(2026, 11, 1)) == "2025-09-01"
    assert mw_insiders.window_from(date(2027, 4, 30)) == "2026-04-01"     # 30/04 − 14 m = 02/03


# ── o corte de 1.000 linhas ───────────────────────────────────────────────────────────────────
def test_insiders_mais_de_mil_linhas_paginado_e_conferido():
    out, src, http = build_all()
    rows = out["mw-insiders.json"]["rows"]
    pages = reads(http, "get_mw_insiders")
    assert [(c.params["limit"], c.params["offset"]) for c in pages] == [("1000", "0"), ("1000", "1000")]
    assert all("Range" not in c.headers for c in pages)
    assert pages[0].headers.get("Prefer") == "count=exact" and "id" in pages[0].params["select"]
    # a contagem do MESMO filtro, à parte (o HEAD do brief): bate com o arquivo
    _r, n = src.supa.rpc_count("get_mw_insiders", "company", {"p_company": None,
                                                               "p_from": "2025-10-01"}, limit=1)
    assert n == len(rows) == 1026
    assert {r[1] for r in rows} >= {"2025-10-01", "2026-09-01"} and min(r[1] for r in rows) == "2025-10-01"
    # na ordem da RPC: mês decrescente, data do negócio decrescente com nulos no fim
    assert rows[0][1] == "2026-09-01" and rows[0][12] == "2026-09-12"
    assert [r[1] for r in rows] == sorted((r[1] for r in rows), reverse=True)
    desc = [r[7] for r in rows if r[7]]
    assert "Operação liquidada em 20082026" in desc                       # a data colada fica
    assert any("[e-mail]" in d and "[telefone]" in d for d in desc)


def test_serie_de_uma_empresa_com_mais_de_mil_linhas_traz_os_dias_mais_novos():
    from datetime import date
    http = F.FakeHTTP()
    d0 = date(2022, 1, 3)
    extra = [F.mw_lending_row("VALE3", "VALE3", (d0 + timedelta(days=k)).isoformat(), k, 1000)
             for k in range(1200)]
    http.database["mw_short_interest"] += extra
    src, http = F.make_sources(http=http)
    s = src.mw().lending_company("VALE3")["VALE3"]
    assert len(s) == 1217 and s[-1][0] == "2026-11-02"                     # o mais NOVO veio
    pages = reads(http, "get_mw_short_interest")
    assert len([c for c in pages if '"VALE3"' in (c.data or b"").decode()]) == 2


# ── comunicados ───────────────────────────────────────────────────────────────────────────────
def test_comunicados_link_mascara_e_ordem():
    out, _src, _ = build_all()
    items = {i["id"]: i for i in out["mw-filings.json"]["items"]}
    assert list(items) == [3546251, 3546100, 3546050, 3545900, 3545800, 3545700, 3545500]
    assert items[3546251]["url"] == F.ENET.format(1546251)                 # o link do ENET passa
    assert items[3546100]["url"] is None                                   # sem link
    assert items[3546050]["url"] is None                                   # recusado (credencial)
    assert items[3546251]["publishedAt"] == "2026-11-02T22:32:00Z"         # -03:00 → UTC
    assert items[3545900]["flag"] == "R" and items[3545900]["newsworthy"] is True
    assert items[3545500]["newsworthy"] is False and items[3545500]["docTitle"] is None
    ex = items[3546251]["docExcerpt"]
    assert "[e-mail]" in ex and ex.count("[telefone]") == 2 and "@" not in ex
    assert "2026-2030" in ex and "33.592.510/0001-54" in ex and "33.300.019.766" in ex
    assert items[3545800]["docExcerpt"].endswith("Dúvidas: [e-mail]")      # cortado no fim
    assert "SAC [telefone]" in items[3545700]["docExcerpt"] and "CEP 90010-150" in items[3545700]["docExcerpt"]
    assert safe_url("https://www.rad.cvm.gov.br/ENET/frmExibirArquivoIPEExterno.aspx?ID=1490001&flnk") == \
        "https://www.rad.cvm.gov.br/ENET/frmExibirArquivoIPEExterno.aspx?ID=1490001&flnk"


def test_comunicados_no_maximo_os_600_mais_recentes():
    http = F.FakeHTTP()
    base = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    http.database["mw_filings"] += [
        F._fl(4000000 + k, "VALE3", f"VALE - Outros - {k}", "Outros Comunicados ao Mercado",
              "2026-01-01", (base + timedelta(hours=k)).isoformat()) for k in range(650)]
    out, _src, http = build_all(http=http)
    items = out["mw-filings.json"]["items"]
    assert len(items) == 600 and items[0]["id"] == 3546251
    stamps = [i["publishedAt"] for i in items]
    assert stamps == sorted(stamps, reverse=True)


# ── tráfego: o que é relido e quando ──────────────────────────────────────────────────────────
def test_mesma_hora_nada_e_relido_e_hora_seguinte_so_sondas(tmp_path, monkeypatch):
    F.no_mw_cap(monkeypatch)
    p = tmp_path / "st.json"
    res = run(F.FakeHTTP(), p)
    assert {n: r.action for n, r in res.items()} == {n: "enviado" for n in ALL_MW}
    http = F.FakeHTTP()
    res = run(http, p, now=F.NOW + timedelta(minutes=5), force=True)    # tudo montado de novo
    assert {r.action for r in res.values()} == {"enviado"} and mw_calls(http) == []
    http = F.FakeHTTP()
    res = run(http, p, now=F.NOW + timedelta(minutes=66))               # 1 h depois do último build
    assert {r.action for r in res.values()} == {"igual"}
    feitas = sorted((c.table, c.params.get("limit")) for c in mw_calls(http))
    assert feitas == [("rpc/get_mw_companies", None), ("rpc/get_mw_filings", "1"),
                      ("rpc/get_mw_short_interest", "1")]                 # empresas + 2 sondas
    http = F.FakeHTTP()
    run(http, p, now=F.NOW + timedelta(hours=3, minutes=12))
    assert ("rpc/get_mw_insiders", "1") in [(c.table, c.params.get("limit")) for c in mw_calls(http)]
    assert reads(http, "get_mw_insiders") == []                           # a sonda bateu: não relê


def test_dia_novo_de_aluguel_le_so_a_janela(tmp_path, monkeypatch):
    F.no_mw_cap(monkeypatch)
    p = tmp_path / "st.json"
    run(F.FakeHTTP(), p)
    http = F.FakeHTTP()
    http.database["mw_short_interest"] += [
        F.mw_lending_row(t, co, "2026-11-03", 17, base) for t, co, base in F.MW_LENDING_TICKERS]
    res = run(http, p, now=F.NOW + timedelta(minutes=61))
    janela = reads(http, "get_mw_short_interest")
    assert len(janela) == 1                                               # nenhuma leitura inteira
    assert json.loads(janela[0].data) == {"p_company": None, "p_from": "2026-10-23"}
    assert res["mw-short-interest.json"].action == "enviado"
    assert all(res[n].action == "enviado" for n in F.MW_FAMILY_FILES)
    assert res["mw-companies.json"].action == "enviado"                   # o último aluguel andou
    assert res["mw-buybacks.json"].action == "igual"
    assert len(reads(http, "get_mw_short_latest")) == 1
    vale = doc(http.uploads["mw-short-vale3.json"])["series"]["VALE3"]
    assert vale[-1][0] == "2026-11-03" and len(vale) == 18


def test_revisao_da_b3_sem_dia_novo_entra_pela_janela_na_marca(tmp_path, monkeypatch):
    F.no_mw_cap(monkeypatch)
    p = tmp_path / "st.json"
    run(F.FakeHTTP(), p)                                                  # 18:05 UTC

    def revisado():
        http = F.FakeHTTP()
        row = next(r for r in http.database["mw_short_interest"]
                   if r["ticker"] == "VALE3" and r["ref_date"] == "2026-10-29")
        row["qty_total"] = 123
        return http
    http = revisado()
    res = run(http, p, now=F.NOW + timedelta(minutes=61))                # 19:06: antes da marca
    assert reads(http, "get_mw_short_interest") == [] and res["mw-short-vale3.json"].action == "igual"
    http = revisado()
    res = run(http, p, now=datetime(2026, 11, 3, 23, 31, tzinfo=timezone.utc))   # depois das 23:30
    assert len(reads(http, "get_mw_short_interest")) == 1
    assert res["mw-short-vale3.json"].action == "enviado"
    assert [r for r in doc(http.uploads["mw-short-vale3.json"])["series"]["VALE3"]
            if r[0] == "2026-10-29"][0][1] == 123


def test_backfill_antigo_confere_por_empresa_e_rele_so_a_que_nao_fecha(tmp_path, monkeypatch):
    F.no_mw_cap(monkeypatch)
    p = tmp_path / "st.json"
    run(F.FakeHTTP(), p)
    http = F.FakeHTTP()
    http.database["mw_short_interest"].append(F.mw_lending_row("RANI3", "RANI3", "2026-06-01", 0, 5e6))
    res = run(http, p, now=F.NOW + timedelta(minutes=61))
    feitas = [json.loads(c.data) for c in reads(http, "get_mw_short_interest")]
    contagens = [c for c in mw_calls(http, "get_mw_short_interest")
                 if c.params.get("limit") == "1" and json.loads(c.data)["p_company"]]
    assert len(contagens) == len(F.MW_VISIBLE)                            # ~1 KB cada
    assert feitas[0]["p_company"] is None and feitas[1:] == [{"p_company": "RANI3", "p_from": None}]
    assert res["mw-short-rani3.json"].action == "enviado" and res["mw-short-vale3.json"].action == "igual"
    assert doc(http.uploads["mw-short-rani3.json"])["series"]["RANI3"][0][0] == "2026-06-01"


def test_insiders_janela_anda_no_mes_sem_reler_e_versao_nova_rele(tmp_path, monkeypatch):
    F.no_mw_cap(monkeypatch)
    p = tmp_path / "st.json"
    run(F.FakeHTTP(), p)
    # 02/12: a janela começa em 01/11/2025 — out/2025 cai do cache, a sonda bate, nada é relido
    http = F.FakeHTTP()
    res = run(http, p, now=datetime(2026, 12, 2, 12, tzinfo=timezone.utc), only="mw-insiders")
    assert reads(http, "get_mw_insiders") == []
    ins = doc(http.uploads["mw-insiders.json"])
    assert ins["from"] == "2025-11-01" and min(r[1] for r in ins["rows"]) == "2025-11-01"
    assert res["mw-insiders.json"].action == "enviado"
    # a CVM reapresenta set/2026 da VALE3 (versão 2: ids novos, a velha apagada) → a janela é relida
    http = F.FakeHTTP()
    db = http.database["mw_insider_moves"]
    velhas = [m for m in db if m["company"] == "VALE3" and m["ref_month"] == "2026-09-01"]
    for m in velhas:
        db.remove(m)
    db += [F.mw_insider("VALE3", "2026-09-01", m["group_type"], m["move_type"], qty=m["qty"],
                        asset=m["asset_class"], version=2, seq=k, upd="2026-12-02T15:00:00+00:00")
           for k, m in enumerate(velhas)]
    res = run(http, p, now=datetime(2026, 12, 2, 15, 30, tzinfo=timezone.utc), only="mw-insiders")
    assert len(reads(http, "get_mw_insiders")) == 1                       # 940 linhas: 1 página
    ins = doc(http.uploads["mw-insiders.json"])
    assert ins["updatedAt"] == "2026-12-02T15:00:00Z"
    assert {r[2] for r in ins["rows"] if r[0] == "VALE3" and r[1] == "2026-09-01"} == {2}


def test_comunicados_so_os_inseridos_desde_o_ultimo_updatedat(tmp_path, monkeypatch):
    F.no_mw_cap(monkeypatch)
    p = tmp_path / "st.json"
    run(F.FakeHTTP(), p)
    http = F.FakeHTTP()
    http.database["mw_filings"] += [
        F._fl(3546300, "USIM5", "USIMINAS - Fato Relevante - 03/11/26", "Fato Relevante",
              "2026-11-03", "2026-11-03T14:00:00-03:00", upd="2026-11-03T18:40:00+00:00"),
        # inserido agora, mas publicado há 6 dias (o atraso do robô): também entra
        F._fl(3545000, "RANI3", "IRANI - Aviso aos Acionistas - 28/10/26", "Aviso aos Acionistas",
              "2026-10-28", "2026-10-28T09:00:00-03:00", upd="2026-11-03T18:41:00+00:00")]
    res = run(http, p, now=F.NOW + timedelta(minutes=61))
    novos = reads(http, "get_mw_filings")
    assert len(novos) == 1 and novos[0].params["updated_at"] == "gte.2026-11-03T01:00:00Z"
    items = doc(http.uploads["mw-filings.json"])["items"]
    assert [i["id"] for i in items][:2] == [3546300, 3546251] and 3545000 in {i["id"] for i in items}
    assert res["mw-filings.json"].action == "enviado"


def test_recompras_e_capital_so_nas_marcas(tmp_path, monkeypatch):
    F.no_mw_cap(monkeypatch)
    p = tmp_path / "st.json"
    run(F.FakeHTTP(), p)

    def mudou():
        http = F.FakeHTTP()
        prog = next(b for b in http.database["mw_buyback_programs"] if b["program_id"] == 1973)
        prog["status"] = "Encerrado"
        return http
    http = mudou()
    res = run(http, p, now=F.NOW + timedelta(minutes=61))
    assert reads(http, "get_mw_buybacks") == [] and not mw_calls(http, "get_mw_share_capital")
    assert res["mw-buybacks.json"].action == "igual"
    http = mudou()
    res = run(http, p, now=datetime(2026, 11, 3, 23, 31, tzinfo=timezone.utc))
    assert len(reads(http, "get_mw_buybacks")) == 1 and len(mw_calls(http, "get_mw_share_capital")) == 1
    assert res["mw-buybacks.json"].action == "enviado"
    cos = doc(http.uploads["mw-companies.json"])["companies"]
    assert next(c for c in cos if c["ticker"] == "VALE3")["facts"]["buybackActive"] is False


def test_empresa_oculta_no_admin_some_de_tudo_na_hora_seguinte(tmp_path, monkeypatch):
    F.no_mw_cap(monkeypatch)
    p = tmp_path / "st.json"
    run(F.FakeHTTP(), p)
    http = F.FakeHTTP()
    next(c for c in http.database["mw_companies"] if c["ticker"] == "KLBN11")["is_visible"] = False
    res = run(http, p, now=F.NOW + timedelta(minutes=61))
    assert "mw-short-klbn11.json" not in res                              # a família não a monta mais
    for n in F.MW_FIXED:
        assert res[n].action == "enviado", n
        assert "KLBN" not in json.dumps(doc(http.uploads[n])), n
    st = json.loads((tmp_path / "st.json").read_text(encoding="utf-8"))
    assert "mw-short-klbn11.json" not in st["files"]                      # esquecida no registro
    st_mw = json.loads((tmp_path / "st_mw.json").read_text(encoding="utf-8"))
    assert "KLBN11" not in st_mw["lending"]["series"]                     # e fora do cache
    # reexibida: volta com o dado do banco (as fontes sem sonda relêem já)
    http = F.FakeHTTP()
    res = run(http, p, now=F.NOW + timedelta(minutes=122))
    assert res["mw-short-klbn11.json"].action == "enviado"
    assert "KLBN11" in {c["ticker"] for c in doc(http.uploads["mw-companies.json"])["companies"]}


def test_partida_a_frio_pelas_copias_publicadas_sem_leitura_cara(tmp_path, monkeypatch):
    F.no_mw_cap(monkeypatch)
    primeiro = F.FakeHTTP()
    run(primeiro, tmp_path / "a.json")
    http = F.FakeHTTP(published=dict(primeiro.uploads))
    res = run(http, tmp_path / "b.json", now=F.NOW + timedelta(minutes=30))
    assert {n: r.reason for n, r in res.items()} == {n: "igual à cópia publicada" for n in ALL_MW}
    caras = [c for c in mw_calls(http) if c.params.get("limit") not in ("1", None)]
    assert caras == []                                                     # nenhum rpc_all
    assert sorted({c.table for c in mw_calls(http)}) == [
        "rpc/get_mw_companies", "rpc/get_mw_filings", "rpc/get_mw_insiders", "rpc/get_mw_short_interest"]
    assert http.feeder_calls() == []


def test_semente_publicada_passa_pela_regra_de_hoje(tmp_path, monkeypatch):
    """Cópia publicada por uma versão antiga do exportador (máscara ou filtro de URL mais fracos):
    o que vem dela passa de novo pela máscara e pelo filtro na montagem — e vai corrigido."""
    F.no_mw_cap(monkeypatch)
    primeiro = F.FakeHTTP()
    run(primeiro, tmp_path / "a.json")
    pub = dict(primeiro.uploads)
    ins = doc(pub["mw-insiders.json"])
    ins["rows"][5][7] = "Dúvidas: ri@empresa-exemplo.com ou (21) 3485-0000"
    fil = doc(pub["mw-filings.json"])
    fil["items"][0]["url"] = "https://www.rad.cvm.gov.br/ENET/x.aspx?ID=1&token=SEGREDO"
    pub["mw-insiders.json"] = gzip_bytes(to_json_bytes(ins))
    pub["mw-filings.json"] = gzip_bytes(to_json_bytes(fil))
    http = F.FakeHTTP(published=pub)
    res = run(http, tmp_path / "b.json", now=F.NOW + timedelta(minutes=30))
    assert reads(http, "get_mw_insiders") == [] and reads(http, "get_mw_filings") == []   # semente
    assert res["mw-insiders.json"].action == "enviado" and res["mw-filings.json"].action == "enviado"
    assert doc(http.uploads["mw-insiders.json"])["rows"][5][7] == "Dúvidas: [e-mail] ou [telefone]"
    assert doc(http.uploads["mw-filings.json"])["items"][0]["url"] is None


def test_mindassets_parado_sem_copia_a_leitura_cara_espera(tmp_path):
    velho = {"schemaVersion": 1, "generatedAt": "2026-11-02T09:00:00Z"}   # live.json de 33 h atrás
    http = F.FakeHTTP(published={"live.json": to_json_bytes(velho)})
    res = run(http, tmp_path / "st.json")
    for n in ("mw-short-interest.json", "mw-insiders.json", "mw-filings.json", "mw-companies.json"):
        assert res[n].action == "erro" and "Mindassets" in res[n].reason, n
    for fn in ("get_mw_insiders", "get_mw_filings", "get_mw_short_interest"):
        assert reads(http, fn) == [], fn
    # o --out lê sempre
    res = run(F.FakeHTTP(published={"live.json": to_json_bytes(velho)}), tmp_path / "o.json",
              mode="out", out_dir=tmp_path / "saida")
    assert {r.action for r in res.values()} == {"gravado"}


# ── o teto de envios por execução ─────────────────────────────────────────────────────────────
def test_teto_de_4_envios_do_market_watch_por_execucao(tmp_path):
    p = tmp_path / "st.json"
    acoes = []
    for k in range(4):
        http = F.FakeHTTP()
        res = run(http, p, now=F.NOW + timedelta(minutes=5 * k))
        acoes.append({n: r.action for n, r in res.items()})
        assert len(http.uploads) == [4, 4, 3, 0][k]
        assert not any(r.failed for r in res.values())                    # "na fila" não é erro
        if k:
            assert mw_calls(http) == []                                    # nada relido na fila
    assert list(acoes[0].values()).count("na fila") == 7
    assert acoes[0]["mw-companies.json"] == "enviado" and acoes[0]["mw-filings.json"] == "na fila"
    assert acoes[1]["mw-filings.json"] == "enviado"                        # o fixo antes da família
    assert set(acoes[3].values()) == {"fora da vez"}                       # a fila esvaziou
    assert not [n for n in acoes[3] if n.startswith("mw-short-") and n != "mw-short-interest.json"]
    enviados = [n for a in acoes for n, x in a.items() if x == "enviado"]
    assert sorted(enviados) == sorted(ALL_MW)


def test_teto_no_dry_run_e_no_force_e_o_out_grava_tudo(tmp_path):
    res = run(F.FakeHTTP(), tmp_path / "d.json", mode="dry")
    acoes = [r.action for r in res.values()]
    assert acoes.count("enviaria") == 4 and acoes.count("na fila") == 7
    p = tmp_path / "f.json"
    run(F.FakeHTTP(), p)
    http = F.FakeHTTP()
    res = run(http, p, now=F.NOW + timedelta(minutes=1), force=True)
    assert len(http.uploads) == 4                                          # o --force não fura o teto
    res = run(F.FakeHTTP(), tmp_path / "o.json", mode="out", out_dir=tmp_path / "saida")
    assert {r.action for r in res.values()} == {"gravado"} and len(res) == 11


def test_out_grava_os_mesmos_bytes_que_seriam_enviados(tmp_path, monkeypatch):
    F.no_mw_cap(monkeypatch)
    enviados = F.FakeHTTP()
    run(enviados, tmp_path / "a.json")
    out = tmp_path / "saida"
    run(F.FakeHTTP(), tmp_path / "b.json", mode="out", out_dir=out)
    for n in ALL_MW:
        assert (out / n).read_bytes() == enviados.uploads[n], n
    assert enviados.uploads["mw-companies.json"][:1] == b"{"                 # texto
    for n in ALL_MW[1:]:
        assert enviados.uploads[n][:2] == b"\x1f\x8b", n                     # gzip


def test_empresa_pedida_pelo_nome_e_empresa_oculta_e_erro(tmp_path):
    res = run(F.FakeHTTP(), tmp_path / "a.json", only="mw-short-klbn11", mode="out",
              out_dir=tmp_path / "s")
    assert list(res) == ["mw-short-klbn11.json"] and res["mw-short-klbn11.json"].action == "gravado"
    res = run(F.FakeHTTP(), tmp_path / "b.json", only="mw-short-csna3", mode="out",
              out_dir=tmp_path / "s")
    assert res["mw-short-csna3.json"].action == "erro" and "oculta" in res["mw-short-csna3.json"].reason


def test_familia_fora_da_vez_nem_carrega_o_estado(tmp_path, monkeypatch):
    F.no_mw_cap(monkeypatch)
    p = tmp_path / "st.json"
    run(F.FakeHTTP(), p, only=None)
    (tmp_path / "st_mw.json").write_text("{corrompido", encoding="utf-8")   # se lesse, recomeçaria
    http = F.FakeHTTP()
    res = run(http, p, now=F.NOW + timedelta(minutes=5), only=None)
    assert {res[n].action for n in F.MW_FIXED} == {"fora da vez"}
    assert not [n for n in res if n in F.MW_FAMILY_FILES] and mw_calls(http) == []
    assert (tmp_path / "st_mw.json").read_text(encoding="utf-8") == "{corrompido"


def test_contagem_que_nao_fecha_nao_publica_pela_metade(tmp_path):
    class Mexeu(F.FakeHTTP):
        """Uma linha some entre a 1ª e a 2ª página dos insiders (o robô apagou uma versão)."""
        def _postgrest(self, call, path, name):
            if name == "get_mw_insiders" and call.params.get("offset") == "1000":
                db = self.database["mw_insider_moves"]
                db.remove(next(m for m in db if m["company"] == "VALE3"
                               and m["ref_month"] == "2026-09-01"))
            return super()._postgrest(call, path, name)
    res = run(Mexeu(), tmp_path / "st.json", only="mw-insiders")
    assert res["mw-insiders.json"].action == "erro" and "contagem" in res["mw-insiders.json"].reason


def test_check_conta_o_market_watch_como_o_exportador():
    """`--dry-run --check`: as contagens pelas RPCs (só visíveis) com o mesmo filtro dos arquivos —
    é a conferência "insiders = contagem do mesmo filtro" com dado real."""
    from hub_export.checks import run_checks
    from hub_export.supa import SupaReader
    linhas = run_checks(SupaReader(F.SUPA_URL, F.SUPA_KEY, http=F.FakeHTTP()), now=F.NOW)
    mw = [x for x in linhas if x.startswith("rpc get_mw_")]
    assert mw == ["rpc get_mw_companies: empresas visíveis: 6 linha(s)",
                  "rpc get_mw_short_interest: aluguel: 173 linha(s), topo 2026-11-02",
                  "rpc get_mw_insiders: insiders desde 2025-10-01: 1026 linha(s), "
                  "topo 2026-10-19T09:00:00Z",
                  "rpc get_mw_filings: comunicados (até 2.000): 7 linha(s), topo 2026-11-03T01:10:00Z",
                  "rpc get_mw_buybacks: programas de recompra: 6 linha(s)"]
    assert any("market_watch=" in x for x in linhas)


# ── a máscara em tudo que sai ─────────────────────────────────────────────────────────────────
_EMAIL = re.compile(r"[\w.+-]+\s?@\s?[\w-]+")
_PHONE = re.compile(r"\(\s?\d{2}\s?\)\s?\d{4,5}[-.\s]?\d{4}|\+\s?\d{2}[\s\d().-]{8,}|"
                    r"\b55[ .-]\d{2}[ .-]\d{4}[.-]\d{4}|\b0800[ .-]?\d{3}[ .-]?\d{4}")


def _strings(obj):
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _strings(v)
    elif isinstance(obj, str):
        yield obj


def test_mascara_varre_todos_os_arquivos_gerados(tmp_path):
    run(F.FakeHTTP(), tmp_path / "st.json", mode="out", out_dir=tmp_path / "saida")
    textos = []
    for n in ALL_MW:
        textos += list(_strings(doc((tmp_path / "saida" / n).read_bytes())))
    tudo = "\n".join(textos)
    assert not _EMAIL.search(tudo) and not _PHONE.search(tudo)
    assert tudo.count("[e-mail]") == 4 and tudo.count("[telefone]") == 5   # banco falso: 4 e 5
    for fica in ("Operação liquidada em 20082026", "2026-2030", "33.592.510/0001-54", "90010-150",
                 "1.234.567 toneladas", "06.11.2026"):
        assert fica in tudo, fica
    # a semente da cópia publicada passa pela máscara de hoje (o construtor aplica de novo)
    sujo = {"schemaVersion": 1, "generatedAt": F.GENERATED, "updatedAt": "2026-11-03T01:10:00Z",
            "items": [dict(i, docExcerpt="ri@x-exemplo.com (21) 3485-0000")
                      for i in doc((tmp_path / "saida" / "mw-filings.json").read_bytes())["items"]]}
    from hub_export.exporters import mw_filings
    assert all(mw_filings.publish(i)["docExcerpt"] == "[e-mail] [telefone]" for i in sujo["items"])
