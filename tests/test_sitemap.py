"""Testes do coletor por news-sitemap (hunter/sitemap_scrapers.py).

Foco: parse de loc + news:title + publication_date, janela temporal e campos.
Rodar: python -m pytest tests/test_sitemap.py -v
"""
import sys
import datetime as dt
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from hunter.sitemap_scrapers import parse_news_sitemap

_NOW = dt.datetime(2026, 6, 10, 12, 0, tzinfo=dt.timezone.utc)

_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
        xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">
  <url>
    <loc>https://www.estadao.com.br/economia/vale-aprimora-producao-de-metais/</loc>
    <news:news>
      <news:publication_date>2026-06-10T09:00:00-03:00</news:publication_date>
      <news:title>Vale aprimora producao de metais basicos e disciplina de capital</news:title>
    </news:news>
  </url>
  <url>
    <loc>https://www.estadao.com.br/economia/noticia-muito-antiga-fora-da-janela/</loc>
    <news:news>
      <news:publication_date>2026-06-01T09:00:00-03:00</news:publication_date>
      <news:title>Noticia antiga que deve cair fora da janela de 72h</news:title>
    </news:news>
  </url>
  <url>
    <loc>https://www.estadao.com.br/economia/sem-titulo-derivado-do-slug-aqui/</loc>
    <lastmod>2026-06-10T10:00:00-03:00</lastmod>
  </url>
</urlset>"""


def test_extrai_recente_com_titulo():
    arts = parse_news_sitemap(_XML, "Estadão", "estadao.com.br", True, now=_NOW)
    titles = [a.title for a in arts]
    assert any("Vale aprimora" in t for t in titles)


def test_descarta_fora_da_janela():
    arts = parse_news_sitemap(_XML, "Estadão", "estadao.com.br", True, now=_NOW)
    assert all("antiga" not in a.title.lower() for a in arts)


def test_titulo_derivado_do_slug_quando_ausente():
    arts = parse_news_sitemap(_XML, "Estadão", "estadao.com.br", True, now=_NOW)
    # a 3ª url não tem <news:title> → título vem do slug
    assert any("slug" in a.title.lower() for a in arts)


def test_campos_basicos():
    arts = parse_news_sitemap(_XML, "Estadão", "estadao.com.br", True, now=_NOW)
    a = next(x for x in arts if "Vale aprimora" in x.title)
    assert a.source_name == "Estadão"
    assert a.domain == "estadao.com.br"
    assert a.published_at is not None
    assert a.needs_filter is True
    assert a.snippet == ""
