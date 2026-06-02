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

# Fontes reconhecidas no Google News — usam o nome original do artigo.
# Qualquer outra publica­ção usa o label configurado em sources.py.
_KNOWN_SOURCES = frozenset([
    # Agências e jornais internacionais
    "Reuters", "Bloomberg", "Bloomberg Línea", "Financial Times",
    "The Wall Street Journal", "WSJ", "Associated Press", "AP",
    # Commodity news
    "S&P Global", "S&P Global Commodity Insights", "Platts",
    "Fastmarkets", "Argus", "Argus Media",
    "Kallanish", "Kallanish Commodities", "Kallanish Steel",
    "MEPS International", "MEPS",
    "AMM", "American Metal Market",
    "Steel Times International",
    # Mineração / siderurgia
    "Mining.com", "Mining Weekly", "Mining Technology",
    "SteelOrbis", "Steel Orbis",
    "Mysteel", "Shanghai Metals Market", "SMM",
    "GMK Center", "EUROMETAL",
    "Metal Bulletin",
    # Celulose / papel
    "ICIS", "Risi", "Fisher International",
    "Paper Advance", "Tissue Online", "Tissue World",
    # Brasileiros
    "Valor Econômico", "Estadão", "O Estado de S. Paulo",
    "Folha de S.Paulo", "Folha de São Paulo",
    "InfoMoney", "Exame", "Money Times",
    "CNN Brasil", "G1", "O Globo",
    "Agência Brasil", "Reuters Brasil",
    # Associações
    "World Steel Association",
    "IBRAM", "Ibá", "ANM", "Instituto Aço Brasil",
    "AISI", "CISA", "CEPI",
    # Outros relevantes
    "Business Wire", "PR Newswire", "GlobeNewswire",
    "Macroaxis", "Seeking Alpha",
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


def _resolve_google_url(gnews_url: str) -> str:
    """Segue redirect do Google News para obter URL canônica.
    Timeout curto — se falhar, retorna a URL original."""
    try:
        r = requests.head(gnews_url, allow_redirects=True, timeout=5, headers=HEADERS)
        return r.url
    except Exception:
        return gnews_url


def _fetch_one(source: dict) -> list[RawArticle]:
    """Busca e parseia um único feed RSS. Retorna lista de RawArticle."""
    label = source["label"]
    url = source["url"]
    needs_filter = source.get("filter", True)
    is_gnews = "news.google.com" in url

    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
    except Exception as e:
        log.warning("Feed error [%s] %s: %s", label, url, e)
        return []

    cutoff = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0
    )
    cutoff = cutoff.replace(hour=0)  # início do dia atual como fallback
    # Janela real: WINDOW_HOURS atrás
    from datetime import timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(hours=WINDOW_HOURS)

    articles: list[RawArticle] = []
    gnews_urls_to_resolve: list[tuple[int, str]] = []

    for entry in feed.entries[:MAX_PER_SOURCE]:
        title = _strip_html(entry.get("title", "")).strip()
        if not title:
            continue

        raw_url = entry.get("link", "").strip()
        if not raw_url:
            continue

        published_at = _parse_date(entry)

        # Janela rigorosa: ARTIGOS SEM published_at SÃO REJEITADOS.
        # Sem data não temos como saber se a notícia é fresca — descartamos.
        # Isso evita que notícias velhas (publicadas há horas/dias) apareçam
        # como recém-descobertas e poluam o feed.
        if not published_at:
            continue
        if published_at < cutoff:
            continue

        snippet = _strip_html(
            entry.get("summary", "") or entry.get("description", "")
        )[:400]

        # Source name: para Google News, usar o campo source do item
        # mas só se for uma fonte reconhecida — senão usa o label configurado
        if is_gnews:
            raw_src = getattr(getattr(entry, "source", None), "title", None) or ""
            src_name = raw_src if raw_src in _KNOWN_SOURCES else label
        else:
            src_name = label

        domain = urlparse(raw_url).netloc.replace("www.", "")

        art = RawArticle(
            url=raw_url,
            domain=domain,
            source_name=src_name,
            title=title,
            snippet=snippet,
            published_at=published_at,
            found_at=datetime.now(timezone.utc),
            needs_filter=needs_filter,
        )
        articles.append(art)

        if is_gnews:
            gnews_urls_to_resolve.append((len(articles) - 1, raw_url))

    # Resolve URLs do Google News em paralelo
    if gnews_urls_to_resolve:
        def resolve(idx_url):
            idx, u = idx_url
            return idx, _resolve_google_url(u)

        with ThreadPoolExecutor(max_workers=8) as ex:
            for idx, resolved in ex.map(resolve, gnews_urls_to_resolve):
                articles[idx].url = resolved
                articles[idx].domain = urlparse(resolved).netloc.replace("www.", "")

    log.info("Feed OK [%s] -> %d items", label, len(articles))
    return articles


def fetch_all() -> list[RawArticle]:
    """Busca todos os feeds em paralelo. Retorna todos os artigos brutos."""
    all_articles: list[RawArticle] = []

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
