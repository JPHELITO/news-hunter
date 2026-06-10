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
import xml.etree.ElementTree as ET
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
# Data embutida no slug (sempre presente em URL arc Reuters, geralmente no fim).
# Lookahead casa em qualquer posição. NÃO removemos "hash final": URLs arc Reuters
# não têm hash e o strip ⩾6 letras comia a última palavra REAL do título
# (ex.: "...ipo plans ft" perdia "reports"; "...weigh tariff" perdia "support").
_DATE_RE = re.compile(r"-\d{4}-\d{2}-\d{2}(?=[-/]|$)")


def _title_from_slug(url: str) -> str:
    slug = url.rstrip("/").split("/")[-1]
    slug = _DATE_RE.sub("", slug)
    words = slug.replace("-", " ").strip()
    return words[:1].upper() + words[1:] if words else ""


def _parse_lastmod(s: str | None):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _localname(tag: str) -> str:
    """Remove o namespace do tag XML (ex: '{http://...}loc' → 'loc')."""
    return tag.rsplit("}", 1)[-1]


def _iter_xml_children(content: bytes, parent_local: str):
    """Itera elementos <parent_local> e devolve dict de filhos por nome local.
    Usa ElementTree (stdlib) — sem depender de lxml."""
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return
    for el in root:
        if _localname(el.tag) != parent_local:
            continue
        fields: dict[str, str] = {}
        for child in el:
            fields[_localname(child.tag)] = (child.text or "").strip()
        yield fields


def _get(url: str) -> tuple[int, bytes]:
    """GET com fallback curl_cffi quando bloqueado.

    A Reuters fica atrás de Cloudflare e devolve 401/403 ao TLS fingerprint do
    `requests` a partir de IP de datacenter (GitHub Actions). curl_cffi imita o
    TLS do Chrome e costuma passar (mesma estratégia do Mining.com em fetcher.py).
    """
    status, content = 0, b""
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
        status, content = resp.status_code, resp.content
    except Exception as e:
        log.debug("Reuters requests falhou [%s]: %s", url, e)
    if status == 200:
        return status, content
    try:
        from curl_cffi import requests as creq
        r2 = creq.get(url, impersonate="chrome", timeout=_TIMEOUT)
        log.info("Reuters curl_cffi fallback [%s]: HTTP %d", url, r2.status_code)
        return r2.status_code, r2.content
    except Exception as e:
        log.debug("Reuters curl_cffi indisponível/falhou: %s", e)
    return status, content


def collect_reuters_headlines() -> list[RawArticle]:
    """Ponto de entrada — retorna RawArticle list (Reuters, via sitemap)."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=WINDOW_HOURS)
    out: list[RawArticle] = []
    seen: set[str] = set()

    try:
        ri_status, ri_content = _get(_SITEMAP_INDEX)
        if ri_status != 200:
            log.warning("Reuters sitemap-index HTTP %d (bloqueio Cloudflare/IP, mesmo c/ curl_cffi)",
                        ri_status)
            return []
        submaps = [f["loc"] for f in _iter_xml_children(ri_content, "sitemap") if "loc" in f][:_SUBMAPS]
    except Exception as e:
        log.warning("Reuters sitemap-index falhou: %s", e)
        return []

    for sm in submaps:
        try:
            st, content = _get(sm)
            if st != 200:
                continue
            for f in _iter_xml_children(content, "url"):
                loc = f.get("loc", "")
                if not loc or loc in seen or ".jpg" in loc or ".png" in loc:
                    continue
                if not _RELEVANT_KW.search(loc):
                    continue
                pub = _parse_lastmod(f.get("lastmod"))
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
