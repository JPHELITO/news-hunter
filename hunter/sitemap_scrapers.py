"""Coletores por NEWS-SITEMAP — mais robustos que raspar a homepage.

Um news-sitemap (padrão Google News) lista os artigos recentes do site INTEIRO
com `<news:title>` e `<news:publication_date>` — estruturado, cobre todas as
seções e não quebra quando o layout/CSS muda. É a forma preferida de coletar
quando a fonte não tem RSS completo.

Combina-se com html_scrapers: ex.: Estadão = sitemap (site todo) + homepage
(/economia/, pega colunas que às vezes não entram no sitemap). O dedup por URL
em `fetcher.fetch_all` junta os dois sem repetir.

Adicionar uma fonte: incluir um dict em SITEMAP_SOURCES. Nada mais muda.
"""
from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse

from .config import WINDOW_HOURS
from .fetcher import RawArticle, _http_get

log = logging.getLogger(__name__)

# Cada entrada: uma fonte com news-sitemap. `needs_filter` segue o mesmo
# significado do resto do pipeline (True = aplica keyword matching).
SITEMAP_SOURCES = [
    {
        "label": "Estadão",
        "domain": "estadao.com.br",
        "url": "https://www.estadao.com.br/arc/outboundfeeds/news-sitemap/?outputType=xml",
        "needs_filter": True,
    },
]

_DATE_SUFFIX = re.compile(r"-\d{4}-\d{2}-\d{2}/?$")


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _parse_date(s: str | None):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _title_from_slug(url: str) -> str:
    slug = url.rstrip("/").split("/")[-1]
    slug = _DATE_SUFFIX.sub("", slug)
    words = slug.replace("-", " ").replace("_", " ").strip()
    return words[:1].upper() + words[1:] if words else ""


def parse_news_sitemap(content: bytes, label: str, domain: str, needs_filter: bool,
                       now: datetime | None = None) -> list[RawArticle]:
    """Parseia bytes de um news-sitemap → lista de RawArticle. (Testável puro.)"""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=WINDOW_HOURS)
    try:
        root = ET.fromstring(content)
    except ET.ParseError as e:
        log.warning("Sitemap [%s] XML inválido: %s", label, e)
        return []

    out: list[RawArticle] = []
    seen: set[str] = set()
    for url_el in root:
        if _localname(url_el.tag) != "url":
            continue
        loc = title = pub_raw = lastmod = None
        for child in url_el.iter():
            ln = _localname(child.tag)
            txt = (child.text or "").strip()
            if ln == "loc" and not loc:
                loc = txt
            elif ln == "title" and not title:
                title = txt
            elif ln == "publication_date" and not pub_raw:
                pub_raw = txt
            elif ln == "lastmod" and not lastmod:
                lastmod = txt
        if not loc or loc in seen:
            continue
        seen.add(loc)

        pub = _parse_date(pub_raw) or _parse_date(lastmod)
        if pub and pub < cutoff:
            continue
        if not title:
            title = _title_from_slug(loc)
        if not title or len(title) < 12:
            continue

        out.append(RawArticle(
            url=loc,
            domain=urlparse(loc).netloc.replace("www.", "") or domain,
            source_name=label,
            title=title,
            snippet="",
            published_at=pub,
            found_at=now,
            needs_filter=needs_filter,
        ))
    return out


def _scrape_sitemap(src: dict) -> list[RawArticle]:
    label = src["label"]
    url = src["url"]
    try:
        status, content = _http_get(url)
    except Exception as e:
        log.warning("Sitemap [%s] %s: %s", label, url, e)
        return []
    if status != 200 or not content:
        log.warning("Sitemap [%s] HTTP %d: %s", label, status, url)
        return []
    arts = parse_news_sitemap(content, label, src.get("domain", ""),
                              src.get("needs_filter", True))
    log.info("Sitemap [%s] -> %d artigos", label, len(arts))
    return arts


def collect_sitemap_sources() -> list[RawArticle]:
    """Coleta todas as fontes com news-sitemap."""
    out: list[RawArticle] = []
    for src in SITEMAP_SOURCES:
        try:
            out.extend(_scrape_sitemap(src))
        except Exception as e:
            log.warning("Sitemap [%s] falhou: %s", src.get("label"), e)
    return out
