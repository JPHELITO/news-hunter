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
from urllib.parse import unquote, urljoin, urlparse

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
        # CNN Brasil migrou para RSS (ver sources.py) — scraper HTML removido.
        # A grade principal do Estadão usa âncoras SEM classe → o seletor por
        # classe (title/headline) só pegava o bloco de colunas (~19 links) e
        # perdia as notícias (ex.: "Venda da CSN Cimentos…"). Selecionamos por
        # PADRÃO DE URL de artigo (slug com 3+ tokens hifenizados) — robusto a
        # mudança de marcação. O filtro de keyword cuida da relevância.
        "label": "Estadão",
        "page_url": "https://www.estadao.com.br/economia/",
        "domain": "estadao.com.br",
        "href_re": r"-[a-z0-9]+-[a-z0-9]+-[a-z0-9]+",
        "needs_filter": True,
    },
    {
        # gov.br (Plone): seleciona por URL de notícia (resiliente a mudança de CSS).
        "label": "ANM",
        "page_url": "https://www.gov.br/anm/pt-br/assuntos/noticias",
        "domain": "gov.br",
        "href_re": r"/anm/.+/noticias/[a-z0-9].*-.*",
        "needs_filter": False,  # fonte temática (agência de mineração)
    },
    {
        "label": "ANTAQ",
        "page_url": "https://www.gov.br/antaq/pt-br/assuntos/noticias",
        "domain": "gov.br",
        "href_re": r"/antaq/.+/noticias/[a-z0-9].*-.*",
        "needs_filter": True,   # portos: amplo → exige keyword (minério/embarques)
    },
    {
        "label": "SMM",
        "page_url": "https://news.metal.com/",
        "domain": "metal.com",
        "selector": "a[href*='/newscontent/']",
        "title_selector": "[class*='__title']",  # o <a> embrulha titulo+resumo+'ha X min'; pega SO o titulo
        "needs_filter": True,
    },
    {
        # Versão em INGLÊS (/en/) — o classificador não fala turco; em /en/haber/
        # os artigos têm título em inglês limpo ("EU safeguard measures…", "U.S.
        # crude steel production rises…"). Antes raspava a home turca (descartada
        # pelo classificador). Site 100% dedicado a steel.
        "label": "SteelRadar",
        "page_url": "https://www.steelradar.com/en/",
        "domain": "steelradar.com",
        "href_re": r"/en/haber/[^/]+-[^/]+",
        "needs_filter": False,
    },
    {
        # RSS oficial está congelado (>170 dias) → scraper da página de notícias.
        "label": "IBRAM",
        "page_url": "https://ibram.org.br/noticias/",
        "domain": "ibram.org.br",
        "selector": "a[href*='/noticia/']",   # singular = artigo (plural = categoria)
        "needs_filter": False,                 # fonte setorial de mineração
        "title_attr": True,                    # a âncora envolve o card → usar title=
    },
    {
        # RSS oficial está morto → scraper. Âncoras sem texto → título via slug.
        "label": "Instituto Aço Brasil",
        "page_url": "https://acobrasil.org.br/site/noticias/",
        "domain": "acobrasil.org.br",
        "selector": "a[href*='/site/noticia/']",
        "needs_filter": False,                 # fonte setorial de siderurgia
        "title_from_slug": True,
    },
]


def _title_from_url(href: str) -> str:
    """Deriva um título legível do slug da URL (fontes com âncora sem texto)."""
    slug = href.rstrip("/").split("/")[-1]
    words = slug.replace("-", " ").replace("_", " ").strip()
    return words[:1].upper() + words[1:] if words else ""


def _canonical_key(href: str) -> str:
    """Chave de dedup robusta. SMM (metal.com) serve a MESMA matéria com URL
    percent-encoded vs literal e slug EN vs PT — todas com o mesmo id em
    /newscontent/<id>; dedup pelo id evita gravar a notícia 2-3×. Para as demais,
    usa o path normalizado (sem barra final / percent-encoding)."""
    m = re.search(r"/newscontent/(\d+)", href)
    if m:
        return "smm:" + m.group(1)
    return urlparse(unquote(href)).path.rstrip("/") or href


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
        # Fonte pode selecionar por CSS (selector) OU por padrão de URL (href_re).
        # href_re é mais robusto p/ sites que trocam classes (ex.: Estadão).
        if src.get("href_re"):
            links = soup.find_all("a", href=re.compile(src["href_re"], re.I))
        else:
            links = soup.select(src["selector"])
    except Exception as e:
        log.warning("HTML [%s] %s: %s", label, url, e)
        return []

    seen_urls: set[str] = set()
    now_utc = datetime.now(timezone.utc)
    articles: list[RawArticle] = []
    base = url

    for a in links[:150]:  # limite por página (href_re traz +âncoras, incl. dups de imagem)
        href = a.get("href", "").strip()
        if not href:
            continue
        # URL absoluta
        if href.startswith("/"):
            href = urljoin(base, href)
        elif not href.startswith("http"):
            continue

        # Título: prefere o atributo title= (IBRAM envolve o card inteiro na âncora →
        # get_text colapsa "categoria + data + LEIA MAIS"); senão o texto da âncora;
        # senão deriva do slug (âncora sem texto, ex.: Aço Brasil).
        title = ""
        if src.get("title_attr"):
            title = (a.get("title") or "").replace("\xa0", " ").strip()
        if not title and src.get("title_selector"):
            # card embrulha titulo + resumo/'ha X min' na mesma ancora → extrai so o elemento do titulo
            el = a.select_one(src["title_selector"])
            if el:
                title = el.get_text(" ", strip=True)
        if not title:
            title = a.get_text(strip=True)
        if (not title or len(title) < 12) and src.get("title_from_slug"):
            title = _title_from_url(href)
        if not title or len(title) < 12:
            continue

        # Dedup local por chave canônica (mesma matéria pode vir 2-3× com encodings
        # diferentes — ex.: SMM por id; ver _canonical_key).
        key = _canonical_key(href)
        if key in seen_urls:
            continue
        seen_urls.add(key)

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
