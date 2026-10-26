# -*- coding: utf-8 -*-
"""Fontes de terras raras (2026-10-26) — os 3 consertos internos e as fontes novas.

Varredura do dia: o robô já recebia ~9 notícias de terras raras por dia (65% da SMM), mas
  1. a Reuters só entrava com "metal"/"mining" no endereço (16 de 19 matérias de terras raras fora);
  2. o filtro de palavras não reconhecia "rare-earth" com hífen, "Pr-Nd", monazita, ítrio, "tierras raras"
     nem pares como Iluka e Arafura;
  3. a regra do "election" (feita para cortar política mexicana) derrubava matéria de terras raras;
e faltavam os comunicados da ASX (Viridis + pares), o site da Aclara e quatro feeds de tema.

Sem rede: tudo o que sairia para a internet é simulado com monkeypatch. Manchetes = as reais do dia.
Rodar: python -m pytest tests/test_terras_raras_fontes.py -v
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from hunter import article_body, asx_announcements as ASX, html_scrapers as HS
from hunter.classify import classify, classify_article_dict
from hunter.digest import _tier1
from hunter.fetcher import RawArticle
from hunter.filter import SOURCE_FILTER_RULES, filter_articles
from hunter.news_take_classifier import (
    _is_curated_source, classify_article_take, detect_covered_companies, detect_topics, should_exclude_news,
)
from hunter.reuters_scraper import _is_relevant_url
from hunter.sources import SOURCES

NOW = datetime(2026, 10, 26, 18, 0, tzinfo=timezone.utc)


def _art(title, source="Global Times", snippet="", needs_filter=True, url="https://x.com/a"):
    return RawArticle(url=url, domain="x.com", source_name=source, title=title, snippet=snippet,
                      published_at=NOW, found_at=NOW, needs_filter=needs_filter)


def _passa(title, **kw):
    return bool(filter_articles([_art(title, **kw)]))


# ── 1. Reuters: o endereço de terras raras passa a contar ─────────────────────────────────────
class TestReutersEndereco:
    BASE = "https://www.reuters.com/markets/commodities/"

    @pytest.mark.parametrize("slug", [
        "lynas-rare-earths-takeover-talks/",
        "mp-materials-stops-china-bound-rare-earth-shipments-over-tariffs-2025-04-17/",
        "solvay-launches-rare-earth-processing-expansion-amid-china-restrictions-2025-04-08/",
        "vulcan-elements-signs-rare-earths-supply-deal-with-reelement-technologies-2025-08-18/",
        "china-bans-export-rare-earths-processing-technologies-2023-12-21/",
    ])
    def test_materias_de_terras_raras_que_ficavam_de_fora(self, slug):
        assert _is_relevant_url(self.BASE + slug)

    def test_minerais_criticos_e_cobertas(self):
        assert _is_relevant_url("https://www.reuters.com/world/us-brazil-critical-minerals-deal-2026-10-20/")
        assert _is_relevant_url("https://www.reuters.com/business/aclara-gets-exim-loan-2026-09-08/")
        assert _is_relevant_url("https://www.reuters.com/business/viridis-raises-120-million-2026-08-20/")

    def test_meteoric_sozinho_e_a_alta_de_qualquer_acao(self):
        assert not _is_relevant_url("https://www.reuters.com/technology/nvidias-meteoric-rise-2026-10-20/")
        assert _is_relevant_url("https://www.reuters.com/markets/lynas-buys-meteoric-resources-2026-09-30/")

    def test_esporte_continua_fora(self):
        assert not _is_relevant_url("https://www.reuters.com/sports/rare-earth-sponsorship-2026-10-20/")


# ── 2. Filtro de palavras ──────────────────────────────────────────────────────────────────────
class TestFiltroPalavras:
    @pytest.mark.parametrize("title", [
        "GT Voice: Rare-earth metal recycling should not be driven by geopolitics",   # Global Times, 26/10
        "China tightens rare-earth export controls",
        "US signs rare-earths deal with Brazil",
        "September Pr-Nd oxide production rebounds about 3% MoM",                      # SMM, perdida em out
        "Lindian Resources on track for December monazite production in Malawi",      # SMM, perdida em out
        "China responds to Japan's yttrium shortage with export controls",            # SMM, perdida em out
        "Gobierno chileno aprueba proyecto de tierras raras en Penco",
        "Iluka refinery cost blowout weighs on shares",
        "Rinehart-backed Arafura expands US rare earths deal by 70%",
        "USA Rare Earth signs magnet supply agreement",
        "Neo Performance begins shipments from Estonia plant",
    ])
    def test_passam_em_fonte_geral(self, title):
        assert _passa(title)

    def test_meteoric_rise_nao_passa(self):
        assert not _passa("Nvidia's meteoric rise continues on AI demand")

    def test_sem_mudanca_no_que_ja_passava(self):
        assert _passa("China rare earth exports fall in September")
        assert _passa("Terras raras ganham destaque com retomada da guerra comercial")


class TestSetorETopico:
    def test_setor_mineracao_com_hifen_e_espanhol(self):
        assert classify("Rare-earth magnet maker Noveon expands")["sector"] == "mining"
        assert classify("Gobierno aprueba proyecto de tierras raras")["sector"] == "mining"
        assert classify("Arafura secures funding for Nolans")["sector"] == "mining"

    def test_meteoric_sozinho_nao_puxa_mineracao(self):
        assert classify("Nvidia's meteoric rise continues")["sector"] is None

    @pytest.mark.parametrize("text", [
        "Pr-Nd prices pull back again",
        "China responds to yttrium shortage",
        "Proyecto de tierras raras en Penco avanza",
        "Rare-earth magnet plant opens in Texas",
    ])
    def test_topico_rare_earths(self, text):
        assert "rare_earths" in detect_topics(text)


# ── 3. Regra do "election" ─────────────────────────────────────────────────────────────────────
class TestEleicao:
    LULA = "Lula's Brazil win puts rare earths in Trump's crosshairs"
    SNIP = ("Brazilian President Luiz Inácio Lula da Silva's election victory is set to sustain the country's "
            "push to process more of its vast rare earth reserves at home")

    def test_terras_raras_com_eleicao_no_resumo_entra(self):
        art = classify_article_take({"title": self.LULA, "snippet": self.SNIP, "source_name": "Northern Miner"})
        assert art["include_in_report"] is True
        assert art["exclusion_reason"] is None

    def test_eleicao_sem_terras_raras_continua_fora(self):
        excl, motivo = should_exclude_news("AMLO rally draws crowds ahead of election", {"source_name": "SCMP China"})
        assert excl and motivo == "irrelevant_region"

    def test_cripto_continua_fora_mesmo_com_minerio(self):
        excl, _ = should_exclude_news("Bitcoin miners buy iron ore assets", {"source_name": "SCMP China"})
        assert excl


# ── 4. Comunicados da ASX ──────────────────────────────────────────────────────────────────────
ITEM = {
    "symbol": "VMM", "isPriceSensitive": True, "documentKey": "2924-03072531-3A685079",
    "headline": "VMM Receives Approval for R$77.5M BNDES Financing Facility",
    "date": "2026-10-26T09:17:00.000Z", "announcementTypes": ["Progress Report"],
}


class TestAsx:
    def test_anuncio_vira_artigo_com_o_nome_na_frente(self):
        a = ASX.announcement_to_article(ITEM, now=NOW)
        assert a.source_name == "ASX" and a.needs_filter is False
        assert a.title == "Viridis: VMM Receives Approval for R$77.5M BNDES Financing Facility"
        assert a.url.endswith("/file/2924-03072531-3A685079")
        assert a.published_at == datetime(2026, 10, 26, 9, 17, tzinfo=timezone.utc)
        assert "(ASX: VMM)" in a.snippet

    def test_titulo_que_ja_cita_a_empresa_fica_como_esta(self):
        a = ASX.announcement_to_article(dict(ITEM, headline="Viridis Places First Major Equipment Order for Colossus"),
                                        now=NOW)
        assert a.title == "Viridis Places First Major Equipment Order for Colossus"

    def test_papelada_outra_empresa_e_velho_ficam_fora(self):
        assert ASX.announcement_to_article(dict(ITEM, isPriceSensitive=False), now=NOW) is None
        assert ASX.announcement_to_article(dict(ITEM, symbol="BHP"), now=NOW) is None
        assert ASX.announcement_to_article(dict(ITEM, documentKey=""), now=NOW) is None
        old = (NOW - timedelta(days=4)).isoformat()
        assert ASX.announcement_to_article(dict(ITEM, date=old), now=NOW) is None

    def test_coberta_marcada_e_entra(self):
        a = ASX.announcement_to_article(ITEM, now=NOW)
        out = filter_articles([a])
        assert len(out) == 1
        classify_article_dict(out[0])
        classify_article_take(out[0])
        assert out[0]["include_in_report"] is True
        assert "VIRIDIS" in out[0]["take_covered_companies"]
        assert out[0]["sector"] == "mining"

    def test_par_ganha_setor_pelo_nome(self):
        a = ASX.announcement_to_article(dict(ITEM, symbol="BRE", headline="BRE Commissions Pilot Plant"), now=NOW)
        assert a.title == "Brazilian Rare Earths: BRE Commissions Pilot Plant"
        assert classify(a.title, a.snippet)["sector"] == "mining"

    def test_fonte_curada_como_a_cvm(self):
        assert SOURCE_FILTER_RULES["ASX"]["pass_through"] is True
        assert _is_curated_source("ASX")

    def test_resumo_da_hora_trata_como_fonte_primaria(self):
        assert _tier1({"source_name": "ASX", "domain": "asx.com.au"})
        assert _tier1({"source_name": "Aclara Resources", "domain": "cdn.prod.website-files.com"})

    def test_coleta_uma_chamada_para_as_sete(self, monkeypatch):
        seen = {}

        class R:
            status_code = 200
            def json(self):
                return {"data": {"items": [ITEM, dict(ITEM, isPriceSensitive=False),
                                           dict(ITEM, symbol="XYZ", documentKey="k2")]}}

        def fake_get(url, params=None, headers=None, timeout=None):
            seen["url"], seen["params"] = url, params
            return R()

        monkeypatch.setattr(ASX.requests, "get", fake_get)
        monkeypatch.setattr(ASX, "datetime", type("D", (datetime,), {"now": staticmethod(lambda tz=None: NOW)}))
        out = ASX.collect_asx_announcements()
        assert len(out) == 1 and out[0].source_name == "ASX"
        assert seen["url"] == ASX.ENDPOINT
        assert len(seen["params"]["xids"].split(",")) == 7

    def test_coleta_com_erro_devolve_vazio(self, monkeypatch):
        def boom(*a, **k):
            raise ConnectionError("fora do ar")
        monkeypatch.setattr(ASX.requests, "get", boom)
        assert ASX.collect_asx_announcements() == []


# ── 5. Site da Aclara (raspador com data no card) ──────────────────────────────────────────────
def _card(date, title, href):
    return (f'<div class="news-list-item"><a class="news-item-box" href="{href}"><div>'
            f'<div class="text-block-66">{date}</div><div class="news-item-title">{title}</div></div></a>'
            f'<a class="news-item-box w-condition-invisible" href="#"><div>'
            f'<div class="text-block-66">{date}</div><div class="news-item-title">{title}</div></div></a></div>')


class TestAclaraSite:
    def test_data_do_card(self):
        assert HS._parse_card_date("19/10/2026") == datetime(2026, 10, 19, tzinfo=timezone.utc)
        assert HS._parse_card_date("7/10/2026") == datetime(2026, 10, 7, tzinfo=timezone.utc)
        assert HS._parse_card_date("31/09/2026") is None
        assert HS._parse_card_date("") is None

    def test_so_entra_o_que_esta_na_janela(self, monkeypatch):
        hoje = datetime.now(timezone.utc)
        html = "<html><body>" + _card(
            f"{hoje.day}/{hoje.month}/{hoje.year}", "Aclara secures key Louisiana air permit for Project Dynamo",
            "https://cdn.prod.website-files.com/x/Aclara_Air_Permit.pdf") + _card(
            "19/10/2025", "Aclara updates Penco Module preliminary economic assessment",
            "https://cdn.prod.website-files.com/x/Aclara_Penco_PEA.pdf") + "</body></html>"

        class R:
            status_code = 200
            headers = {"Content-Type": "text/html; charset=utf-8"}
            content = html.encode()
            text = html

        monkeypatch.setattr(HS.requests, "get", lambda *a, **k: R())
        src = [s for s in HS.HTML_SOURCES if s["label"] == "Aclara Resources"][0]
        arts = HS._scrape_source(src)
        assert [a.title for a in arts] == ["Aclara secures key Louisiana air permit for Project Dynamo"]
        assert arts[0].published_at.date() == hoje.date()
        assert arts[0].needs_filter is False
        assert detect_covered_companies(arts[0].title) == ["ACLARA"]

    def test_fontes_sem_data_continuam_sem_data(self):
        assert all("date_selector" not in s for s in HS.HTML_SOURCES if s["label"] != "Aclara Resources")


# ── 6. Corpo em PDF para a IA ──────────────────────────────────────────────────────────────────
def _pdf_bytes():
    fitz = pytest.importorskip("fitz")
    doc = fitz.open()
    page = doc.new_page()
    lines = ["Aclara updates Penco Module preliminary economic assessment",
             "The updated PEA increases annual production by 31 percent and extends the mine life,",
             "with a higher after-tax net present value than the previous study.",
             "Construction is expected to start once the environmental permit is final.",
             "Heavy rare earths dysprosium and terbium account for most of the basket value."]
    for i, l in enumerate(lines):
        page.insert_text((72, 72 + 18 * i), l)
    return doc.tobytes()


def test_corpo_de_comunicado_em_pdf(monkeypatch):
    pdf = _pdf_bytes()

    class R:
        status_code = 200
        headers = {"Content-Type": "application/pdf"}
        content = pdf
        text = ""

    monkeypatch.setattr(article_body.requests, "get", lambda *a, **k: R())
    body, meta = article_body.fetch_body("https://cdn.prod.website-files.com/x/Aclara_Penco_PEA.pdf")
    assert meta["ok"] and meta["method"] == "pdf"
    assert "31 percent" in body


# ── 7. Fontes cadastradas ──────────────────────────────────────────────────────────────────────
def test_feeds_novos_cadastrados_como_tema():
    por_url = {s["url"]: s for s in SOURCES}
    for url, label in [
        ("https://www.mining.com/commodity/rare-earth/feed/", "Mining.com"),
        ("https://www.fastmarkets.com/insights/category/commodity/rare-earths/feed/", "Fastmarkets Insights"),
        ("https://www.northernminer.com/?s=rare+earth&feed=rss2", "Northern Miner"),
        ("https://www.ft.com/rare-earths?format=rss", "Financial Times"),
        ("https://pocosja.com.br/tag/terras-raras/feed/", "Poços Já"),
    ]:
        assert por_url[url]["label"] == label and por_url[url]["filter"] is False


def test_fastmarkets_de_terras_raras_nao_e_o_terminal_de_celulose():
    labels = {s["label"] for s in SOURCES}
    assert "Fastmarkets Insights" in labels and "Fastmarkets" not in labels
    assert not _is_curated_source("Fastmarkets Insights")
