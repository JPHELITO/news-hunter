"""HTML scrapers — fontes sem RSS público.

Usa requests + BeautifulSoup para extrair manchetes diretamente da home
das seções de notícias. Sempre que possível, prefere RSS (em sources.py).

Adicionar uma nova fonte HTML:
  1. Adicione um dict em HTML_SOURCES com selector e label
  2. O fetcher chama collect_html_sources() automaticamente
"""
from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urljoin, urlparse

import requests

try:
    from bs4 import BeautifulSoup
    BS4_OK = True
except ImportError:
    BS4_OK = False

from .fetcher import RawArticle

log = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
}
TIMEOUT = 20  # SteelRadar precisa de tempo extra


# Cada entrada define como extrair manchetes de uma fonte sem RSS.
HTML_SOURCES = [
    {
        "label": "CNN Brasil",
        "page_url": "https://www.cnnbrasil.com.br/economia/",
        "domain": "cnnbrasil.com.br",
        # Seletor combinado — h2 e h3 com link interno
        "selector": "h2 a[href*='cnnbrasil.com.br'], h3 a[href*='cnnbrasil.com.br']",
        "needs_filter": True,
    },
    {
        "label": "Estadão",
        "page_url": "https://www.estadao.com.br/economia/",
        "domain": "estadao.com.br",
        "selector": "a[class*='title'], a[class*='headline']",
        "needs_filter": True,
    },
    {
        "label": "ANM",
        "page_url": "https://www.gov.br/anm/pt-br/assuntos/noticias",
        "domain": "gov.br",
        "selector": "article h2 a",
        "needs_filter": False,  # fonte temática
    },
    {
        "label": "ANTAQ",
        "page_url": "https://www.gov.br/antaq/pt-br/assuntos/noticias",
        "domain": "gov.br",
        "selector": "h2 a[href*='antaq']",
        "needs_filter": True,
    },
    {
        "label": "SMM",
        "page_url": "https://news.metal.com/",
        "domain": "metal.com",
        "selector": "a[href*='/newscontent/']",
        "needs_filter": True,
    },
    {
        "label": "SteelRadar",
        "page_url": "https://www.steelradar.com/",
        "domain": "steelradar.com",
        "selector": "a[href*='/haber/']",
        "needs_filter": False,   # site 100% dedicado a steel; conteúdo em turco
    },
]


def _scrape_source(src: dict) -> list[RawArticle]:
    """Faz scraping de uma fonte HTML."""
    if not BS4_OK:
        log.warning("bs4 não instalado — html_scrapers desativado")
        return []

    label = src["label"]
    url = src["page_url"]
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        if r.status_code != 200:
            log.warning("HTML [%s] %s: HTTP %d", label, url, r.status_code)
            return []
        soup = BeautifulSoup(r.text, "html.parser")
        links = soup.select(src["selector"])
    except Exception as e:
        log.warning("HTML [%s] %s: %s", label, url, e)
        return []

    seen_urls: set[str] = set()
    now_utc = datetime.now(timezone.utc)
    articles: list[RawArticle] = []
    base = url

    for a in links[:60]:  # limite por página
        title = a.get_text(strip=True)
        if not title or len(title) < 12:
            continue
        href = a.get("href", "").strip()
        if not href:
            continue
        # URL absoluta
        if href.startswith("/"):
            href = urljoin(base, href)
        elif not href.startswith("http"):
            continue
        # Dedup local (mesma URL pode aparecer no menu + grid)
        if href in seen_urls:
            continue
        seen_urls.add(href)

        domain = urlparse(href).netloc.replace("www.", "")

        articles.append(RawArticle(
            url=href,
            domain=domain,
            source_name=label,
            title=title,
            snippet="",                   # scrapers HTML não pegam corpo
            published_at=None,            # data não disponível na home
            found_at=now_utc,
            needs_filter=src.get("needs_filter", True),
        ))

    log.info("HTML scraper [%s] -> %d items", label, len(articles))
    return articles


def collect_html_sources() -> list[RawArticle]:
    """Faz scraping de todas as fontes HTML em paralelo."""
    if not BS4_OK:
        return []
    all_articles: list[RawArticle] = []
    with ThreadPoolExecutor(max_workers=6) as ex:
        futures = {ex.submit(_scrape_source, s): s for s in HTML_SOURCES}
        for f in as_completed(futures):
            try:
                all_articles.extend(f.result())
            except Exception as e:
                log.warning("HTML scraper future falhou: %s", e)
    return all_articles
