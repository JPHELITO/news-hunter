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
