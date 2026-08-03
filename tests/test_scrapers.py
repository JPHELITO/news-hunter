"""Testes dos coletores HTML/sitemap: título por slug (Reuters), chave canônica
de dedup (SMM), e título via atributo title= (IBRAM).

Rodar: python -m pytest tests/test_scrapers.py -v
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from hunter.reuters_scraper import _title_from_slug
import hunter.html_scrapers as HS
from hunter.html_scrapers import _canonical_key
from hunter.platts_scraper import _type_allowed, _is_headline_search_url


class TestReutersSlugTitle:
    def test_preserva_ultima_palavra_real(self):
        # o strip de "hash" antigo comia palavras >=6 letras ("support", "reports")
        t = _title_from_slug(
            "https://www.reuters.com/markets/uk-considers-easing-steel-tariff-support-2026-06-09")
        assert "support" in t.lower()
        assert "2026" not in t

    def test_remove_data_no_fim(self):
        t = _title_from_slug("https://www.reuters.com/x/vietnam-coal-output-rises-2026-06-08/")
        assert t.lower().endswith("rises")
        assert "2026" not in t


class TestCanonicalKey:
    def test_smm_mesma_id_varios_encodings(self):
        a = _canonical_key("https://news.metal.com/newscontent/103456/Steel-Prices-Up")
        b = _canonical_key("https://news.metal.com/newscontent/103456/A%C3%A7o-precos-sobem")
        assert a == b == "smm:103456"

    def test_nao_smm_path_normalizado(self):
        a = _canonical_key("https://x.com/economia/vale-define-plano/")
        b = _canonical_key("https://x.com/economia/vale-define-plano")
        assert a == b


class TestPlattsContentType:
    """Regra de negócio: TODA notícia da Platts entra, EXCETO 'Rationale'.
    O scraper filtra por ContentType via BLOCKLIST (não whitelist)."""

    def test_analysis_passa(self):
        # o bug: 'Analysis' (com tabelas/imagens) era descartado pelo whitelist antigo
        assert _type_allowed("Analysis")

    def test_tipos_conhecidos_passam(self):
        for ct in ("News", "Top News", "Flash", "Market Commentary",
                   "Blog", "Headline Analysis", "Feature", "Podcast"):
            assert _type_allowed(ct), ct

    def test_tipo_novo_desconhecido_passa(self):
        # tipo que a Platts venha a criar entra sozinho (sem mexer no código)
        assert _type_allowed("Something Brand New")

    def test_rationale_barrado(self):
        assert not _type_allowed("Rationale")

    def test_pricing_rationale_barrado_substring(self):
        assert not _type_allowed("Pricing Rationale")

    def test_case_insensitive(self):
        assert not _type_allowed("RATIONALE")

    def test_vazio_ou_none_passa(self):
        # ContentType ausente cai no default 'News' no chamador; a função é tolerante
        assert _type_allowed("")
        assert _type_allowed(None)


class TestPlattsHeadlineUrl:
    """A view 'Enhanced' (2026-08) migrou o feed de content-bff/v1/search →
    content-bff/v4/search/blendedsearch. O interceptor precisa casar as duas
    (nova + legada) e IGNORAR os endpoints de facetas/config e de imagem."""

    _BASE = "https://api.platts.com/platts-platform"

    def test_v4_blendedsearch_enhanced(self):
        assert _is_headline_search_url(f"{self._BASE}/content-bff/v4/search/blendedsearch")

    def test_versao_futura_agnostica(self):
        # v5+ que a Platts venha a lançar deve entrar sozinha (casa por 'search/blendedsearch')
        assert _is_headline_search_url(f"{self._BASE}/content-bff/v9/search/blendedsearch")

    def test_v1_search_classic_legado(self):
        assert _is_headline_search_url(f"{self._BASE}/content-bff/v1/search?q=steel")

    def test_facetas_nao_casam(self):
        # blendedcascadingfacets e blendedtypes NÃO são a lista de artigos
        assert not _is_headline_search_url(f"{self._BASE}/content-bff/v4/search/blendedcascadingfacets")
        assert not _is_headline_search_url(f"{self._BASE}/content-bff/v3/search/blendedtypes")

    def test_article_endpoint_nao_casa(self):
        # o corpo do artigo (v2/search/article/<id>) não é o feed de headlines
        assert not _is_headline_search_url(f"{self._BASE}/content-bff/v2/search/article/abc-123")

    def test_variante_imagem_ignorada(self):
        assert not _is_headline_search_url(f"{self._BASE}/content-bff/v4/search/blendedsearch/image")

    def test_url_vazia_ou_none(self):
        assert not _is_headline_search_url("")
        assert not _is_headline_search_url(None)


class _FakeResp:
    status_code = 200
    text = (
        '<html><body>'
        '<a class="mb-3" href="https://ibram.org.br/noticia/mineracao-essencial-transicao/"'
        ' title="Mineração será essencial para a transição energética">'
        'NotíciasMineração será essencial para a transição energética09/06/2026LEIA MAIS</a>'
        '</body></html>'
    )


def test_ibram_usa_title_attr(monkeypatch):
    monkeypatch.setattr(HS.requests, "get", lambda *a, **k: _FakeResp())
    src = {
        "label": "IBRAM", "page_url": "https://ibram.org.br/noticias/",
        "domain": "ibram.org.br", "selector": "a[href*='/noticia/']",
        "needs_filter": False, "title_attr": True,
    }
    arts = HS._scrape_source(src)
    assert len(arts) == 1
    # usa o atributo title= (limpo), não o get_text run-on com data/LEIA MAIS
    assert arts[0].title == "Mineração será essencial para a transição energética"
