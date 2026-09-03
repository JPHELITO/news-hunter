# -*- coding: utf-8 -*-
"""Comunicados da CVM como fonte do hunter (hunter/cvm_filings.py + article_body CVM).

Sem rede: o Supabase e a CVM são simulados com monkeypatch.
"""
import base64
import json
from datetime import datetime, timezone

import pytest

from hunter import article_body, cvm_filings
from hunter.filter import SOURCE_FILTER_RULES, filter_articles
from hunter.news_take_classifier import _is_curated_source


ROW = {
    "id": 3465380, "company": "VALE3", "category": "Outros Comunicados ao Mercado",
    "doc_title": "Vale informa nova composição do Comitê de Auditoria e Riscos",
    "doc_excerpt": "Rio de Janeiro, 27 de agosto de 2026 – A Vale S.A. informa que seu Conselho de Administração aprovou…",
    "cvm_url": "https://www.rad.cvm.gov.br/ENETWEB/frmExibirArquivoIPEExterno.aspx?ID=1562150&flnk",
    "published_at": "2026-08-27T18:58:56-03:00",
}


def test_filing_vira_artigo_curado():
    a = cvm_filings.filing_to_article(ROW, now=datetime(2026, 8, 27, 22, tzinfo=timezone.utc))
    assert a.source_name == "CVM" and a.domain == "rad.cvm.gov.br" and a.needs_filter is False
    assert a.title == "Vale informa nova composição do Comitê de Auditoria e Riscos"
    assert a.snippet.startswith("Outros Comunicados ao Mercado · Vale (VALE3).")
    assert a.published_at.isoformat().startswith("2026-08-27T18:58:56")


def test_titulo_sem_o_nome_da_empresa_ganha_prefixo():
    row = dict(ROW, doc_title="Fato Relevante", company="SUZB3")
    a = cvm_filings.filing_to_article(row)
    assert a.title == "Suzano: Fato Relevante"
    row2 = dict(ROW, doc_title=None, company="KLBN11", category="Aviso aos Acionistas")
    assert cvm_filings.filing_to_article(row2).title == "Klabin — Aviso aos Acionistas"


def test_linha_sem_link_ou_fora_da_cobertura_e_ignorada():
    assert cvm_filings.filing_to_article(dict(ROW, cvm_url=None)) is None
    assert cvm_filings.filing_to_article(dict(ROW, company="PETR4")) is None


def test_fonte_cvm_passa_pelo_filtro_e_e_curada():
    assert SOURCE_FILTER_RULES["CVM"]["pass_through"] is True
    a = cvm_filings.filing_to_article(ROW)
    out = filter_articles([a])
    assert len(out) == 1 and out[0]["source_name"] == "CVM"
    assert _is_curated_source("CVM")


def test_setor_vem_da_empresa():
    arts = [{"source_name": "CVM", "snippet": "Fato Relevante · Klabin (KLBN11). …", "sector": None},
            {"source_name": "CVM", "snippet": "Comunicado · Gerdau (GGBR4). …", "sector": "mining"},
            {"source_name": "Valor Econômico", "snippet": "x (KLBN11)", "sector": None}]
    n = cvm_filings.apply_cvm_sector(arts)
    assert n == 2
    assert arts[0]["sector"] == "pp" and arts[0]["tickers"] == ["KLBN11"]
    assert arts[1]["sector"] == "steel"
    assert arts[2]["sector"] is None          # só mexe na CVM


def test_collect_sem_env_devolve_vazio(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    assert cvm_filings.collect_cvm_filings() == []


def test_collect_le_mw_filings(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://exemplo.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "k")

    class R:
        ok = True
        status_code = 200
        def json(self):
            return [ROW, dict(ROW, id=1, cvm_url=None)]
    seen = {}
    def fake_get(url, headers=None, timeout=None):
        seen["url"] = url
        return R()
    monkeypatch.setattr(cvm_filings.requests, "get", fake_get)
    out = cvm_filings.collect_cvm_filings()
    assert len(out) == 1 and out[0].source_name == "CVM"
    assert "is_newsworthy=eq.true" in seen["url"] and "mw_filings" in seen["url"]


def _fake_pdf_b64():
    fitz = pytest.importorskip("fitz")
    doc = fitz.open()
    page = doc.new_page()
    # insert_text não quebra linha: uma linha por chamada, senão o texto sai da página e some
    lines = ["Vale informa nova composicao do Comite de Auditoria e Riscos",
             "A Vale S.A. informa que seu Conselho de Administracao aprovou, na presente data,",
             "a nova composicao do Comite de Auditoria e Riscos da Vale.",
             "A Sra. Heloisa Belotti Bedicks permanece como Coordenadora e assume a posicao",
             "de Especialista Financeira, nos termos do regimento interno do Comite.",
             "A Sra. Rachel de Oliveira Maia e o Sr. Reinaldo Duarte Castanheira permanecem",
             "como membros do Comite, que segue composto por tres integrantes."]
    for i, l in enumerate(lines):
        page.insert_text((72, 72 + 18 * i), l)
    return base64.b64encode(doc.tobytes()).decode()


def test_corpo_do_comunicado_vem_do_pdf(monkeypatch):
    b64 = _fake_pdf_b64()

    class R:
        ok = True
        def json(self):
            return {"d": b64}
    calls = {}
    def fake_post(url, headers=None, data=None, timeout=None):
        calls["url"] = url; calls["body"] = json.loads(data)
        return R()
    monkeypatch.setattr(article_body.requests, "post", fake_post)
    body, meta = article_body.fetch_body(ROW["cvm_url"], "CVM")
    assert meta["ok"] and meta["method"] == "cvm_pdf"
    assert "Comite de Auditoria" in body
    assert calls["body"]["numeroProtocolo"] == "1562150" and calls["url"].endswith("/ExibirPDF")


def test_corpo_cvm_sem_pdf_nao_quebra(monkeypatch):
    class R:
        ok = True
        def json(self):
            return {"d": "V2"}          # a CVM pediu captcha
    monkeypatch.setattr(article_body.requests, "post", lambda *a, **k: R())
    body, meta = article_body.fetch_body(ROW["cvm_url"], "CVM")
    assert body is None and meta["ok"] is False and "captcha" in meta["reason"]
