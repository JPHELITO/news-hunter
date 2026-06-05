"""Fetch e parse de feeds RSS."""
from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

import feedparser
import requests

from .config import MAX_PER_SOURCE, WINDOW_HOURS
from .sources import SOURCES

log = logging.getLogger(__name__)

# Whitelist de fontes — usadas para validar source_name vindo do feed
# Cada nome aqui corresponde a uma fonte declarada em sources.py ou html_scrapers.py
_KNOWN_SOURCES = frozenset([
    "Mining.com",
    "Valor Econômico", "Folha de S.Paulo", "G1 Economia",
    "InfoMoney", "Exame", "Money Times",
    "Metrópoles", "UOL Economia", "Veja",
    "Portal Celulose", "Siderurgia Brasil",
    "Instituto Aço Brasil", "IBRAM", "Ibá", "ABTCP",
    "CNN Brasil", "Estadão", "ANM", "ANTAQ",
    "SMM", "SteelRadar",
    "S&P Platts", "Fastmarkets",
])

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; NewsHunter/1.0; +https://github.com/JPHELITO)",
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
}
TIMEOUT = 12  # segundos por request


@dataclass
class RawArticle:
    url: str
    domain: str
    source_name: str
    title: str
    snippet: str
    published_at: Optional[datetime]
    found_at: datetime
    needs_filter: bool  # True = aplicar keyword matching


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", " ", text or "").strip()


def _parse_date(entry) -> Optional[datetime]:
    for attr in ("published_parsed", "updated_parsed"):
        val = getattr(entry, attr, None)
        if val:
            try:
                return datetime(*val[:6], tzinfo=timezone.utc)
            except Exception:
                pass
    return None


def _fetch_one(source: dict) -> list[RawArticle]:
    """Busca e parseia um único feed RSS. Retorna lista de RawArticle."""
    label = source["label"]
    url = source["url"]
    needs_filter = source.get("filter", True)

    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
    except Exception as e:
        log.warning("Feed error [%s] %s: %s", label, url, e)
        return []

    from datetime import timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(hours=WINDOW_HOURS)

    articles: list[RawArticle] = []
    for entry in feed.entries[:MAX_PER_SOURCE]:
        title = _strip_html(entry.get("title", "")).strip()
        if not title:
            continue
        raw_url = entry.get("link", "").strip()
        if not raw_url:
            continue

        published_at = _parse_date(entry)
        if published_at and published_at < cutoff:
            continue

        snippet = _strip_html(
            entry.get("summary", "") or entry.get("description", "")
        )[:400]

        articles.append(RawArticle(
            url=raw_url,
            domain=urlparse(raw_url).netloc.replace("www.", ""),
            source_name=label,
            title=title,
            snippet=snippet,
            published_at=published_at,
            found_at=datetime.now(timezone.utc),
            needs_filter=needs_filter,
        ))

    log.info("Feed OK [%s] -> %d items", label, len(articles))
    return articles


def fetch_all() -> list[RawArticle]:
    """Busca todos os feeds RSS + scrapers HTML em paralelo."""
    all_articles: list[RawArticle] = []

    # 1) Scrapers HTML (CNN, Estadão, ANM, ANTAQ, SMM, SteelRadar, IBRAM, Aço Brasil)
    try:
        from .html_scrapers import collect_html_sources
        html_items = collect_html_sources()
        all_articles.extend(html_items)
        log.info("HTML scrapers: %d artigos", len(html_items))
    except Exception as e:
        log.warning("HTML scrapers falharam: %s", e)

    # 1b) Reuters via sitemap (sem RSS; site é 401)
    try:
        from .reuters_scraper import collect_reuters_headlines
        reuters_items = collect_reuters_headlines()
        all_articles.extend(reuters_items)
        log.info("Reuters: %d artigos", len(reuters_items))
    except Exception as e:
        log.warning("Reuters scraper falhou: %s", e)

    # 2) Feeds RSS
    with ThreadPoolExecutor(max_workers=6) as ex:
        futures = {ex.submit(_fetch_one, src): src for src in SOURCES}
        for fut in as_completed(futures):
            try:
                all_articles.extend(fut.result())
            except Exception as e:
                log.warning("Feed future error: %s", e)

    # Deduplica por URL
    seen: dict[str, RawArticle] = {}
    for art in all_articles:
        if art.url not in seen:
            seen[art.url] = art

    deduped = list(seen.values())
    log.info("Total após dedup: %d artigos", len(deduped))
    return deduped
