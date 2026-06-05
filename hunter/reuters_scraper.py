"""Scraper Reuters via sitemap.

Reuters descontinuou os RSS públicos e o site responde 401 (Cloudflare).
O endpoint de sindicação Arc (`outboundfeeds/sitemap`) ainda retorna os
artigos mais recentes com `lastmod`. Sem título no sitemap → derivado do slug.

Filtra por termo de commodity/empresa na URL (alta precisão) → needs_filter
desnecessário. Respeita WINDOW_HOURS via lastmod.

Pode ser bloqueado a partir de IP de datacenter (GitHub Actions); nesse caso
retorna [] silenciosamente, sem quebrar o pipeline.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone, timedelta

import requests

from .config import WINDOW_HOURS
from .fetcher import RawArticle

log = logging.getLogger(__name__)

_SITEMAP_INDEX = "https://www.reuters.com/arc/outboundfeeds/sitemap-index/?outputType=xml"
_SUBMAPS = 6  # nº de sub-sitemaps recentes (~100 artigos cada)
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/xml, text/xml, */*",
}
_TIMEOUT = 15

# Termos de S&M / P&P na URL — com fronteira de palavra (slug é hifenizado).
# Evita "steelers"→steel, "scraps"→scrap, "papers"→paper, "value"→vale.
_RELEVANT_KW = re.compile(
    r"\b("
    r"iron-ore|copper|steel|mining|metals|aluminium|aluminum|nickel|"
    r"coking-coal|met-coal|coal|zinc|manganese|pellet|scrap|pulp|"
    r"vale-sa|bhp|rio-tinto|anglo-american|fortescue|glencore|"
    r"gerdau|usiminas|csn|suzano|klabin|ternium|arcelormittal|nucor"
    r")\b",
    re.I,
)
_DATE_SUFFIX = re.compile(r"-\d{4}-\d{2}-\d{2}/?$")


def _title_from_slug(url: str) -> str:
    slug = url.rstrip("/").split("/")[-1]
    slug = _DATE_SUFFIX.sub("", slug)            # remove sufixo -2026-06-05
    slug = re.sub(r"-[a-z0-9]{6,}$", "", slug)   # remove hash final ocasional
    words = slug.replace("-", " ").strip()
    return words[:1].upper() + words[1:] if words else ""


def _parse_lastmod(s: str | None):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def collect_reuters_headlines() -> list[RawArticle]:
    """Ponto de entrada — retorna RawArticle list (Reuters, via sitemap)."""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return []

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=WINDOW_HOURS)
    out: list[RawArticle] = []
    seen: set[str] = set()

    try:
        ri = requests.get(_SITEMAP_INDEX, headers=_HEADERS, timeout=_TIMEOUT)
        if ri.status_code != 200:
            log.warning("Reuters sitemap-index HTTP %d (provável bloqueio Cloudflare/IP)",
                        ri.status_code)
            return []
        submaps = [l.get_text() for l in BeautifulSoup(ri.content, "xml").find_all("loc")][:_SUBMAPS]
    except Exception as e:
        log.warning("Reuters sitemap-index falhou: %s", e)
        return []

    for sm in submaps:
        try:
            r = requests.get(sm, headers=_HEADERS, timeout=_TIMEOUT)
            if r.status_code != 200:
                continue
            for u in BeautifulSoup(r.content, "xml").find_all("url"):
                loc_el = u.find("loc")
                if not loc_el:
                    continue
                loc = loc_el.get_text().strip()
                if not loc or loc in seen or ".jpg" in loc or ".png" in loc:
                    continue
                if not _RELEVANT_KW.search(loc):
                    continue
                pub = _parse_lastmod(u.find("lastmod").get_text() if u.find("lastmod") else None)
                if pub and pub < cutoff:
                    continue
                title = _title_from_slug(loc)
                if not title or len(title) < 12:
                    continue
                seen.add(loc)
                out.append(RawArticle(
                    url=loc,
                    domain="reuters.com",
                    source_name="Reuters",
                    title=title,
                    snippet="",
                    published_at=pub,
                    found_at=now,
                    needs_filter=False,   # _RELEVANT_KW (com \b) já garante precisão
                ))
        except Exception as e:
            log.debug("Reuters sub-sitemap falhou: %s", e)

    log.info("Reuters sitemap: %d artigos", len(out))
    return out
