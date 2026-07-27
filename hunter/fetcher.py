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

# UA de navegador — WAFs bloqueiam bot-UA óbvio. Accept padrão de navegador
# (não RSS-específico) — alguns WAFs devolvem 415 a Accept estranho.
# Sem Accept-Encoding/Cache-Control manuais (requests cuida; evitam 415).
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
}
TIMEOUT = 15  # segundos por request


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


def _http_get(url: str) -> tuple[int, bytes]:
    """GET com fallback curl_cffi quando bloqueado (401/403/429).

    Cloudflare bloqueia o TLS fingerprint do `requests` a partir de IP de
    datacenter (ex: Mining.com 403, Estadão sitemap 401 no GitHub Actions) e
    rate-limit devolve 429. curl_cffi imita o TLS do Chrome e costuma passar.
    Se não estiver instalado, retorna o código original.
    """
    resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    if resp.status_code in (401, 403, 429):
        try:
            from curl_cffi import requests as creq
            r2 = creq.get(url, impersonate="chrome", timeout=TIMEOUT)
            log.info("curl_cffi fallback [%s]: HTTP %d", url, r2.status_code)
            return r2.status_code, r2.content
        except Exception as e:
            log.debug("curl_cffi indisponível/falhou: %s", e)
    return resp.status_code, resp.content


def _fetch_one(source: dict) -> list[RawArticle]:
    """Busca e parseia um único feed RSS. Retorna lista de RawArticle."""
    label = source["label"]
    url = source["url"]
    needs_filter = source.get("filter", True)

    try:
        status, content = _http_get(url)
        if status != 200:
            # Bloqueio (403/401/429) aparece aqui — diagnóstico no log do GitHub.
            log.warning("Feed BLOQUEADO/ERRO [%s] HTTP %d: %s", label, status, url)
            return []
        feed = feedparser.parse(content)
    except Exception as e:
        log.warning("Feed EXCEÇÃO [%s] %s: %s", label, url, e)
        return []

    n_entries = len(feed.entries)
    if n_entries == 0:
        # 200 com 0 entradas costuma ser CHALLENGE do Cloudflare servido como HTML (não RSS) ao
        # IP de datacenter. O fallback de _http_get só dispara em 401/403/429 → esse caso passava
        # batido e sumia a fonte em silêncio. Tenta curl_cffi (TLS Chrome), que costuma receber o
        # feed real. (Foi o que gutava o Mining.com no CI sem erro visível.)
        try:
            from curl_cffi import requests as creq
            r2 = creq.get(url, impersonate="chrome", timeout=TIMEOUT)
            feed2 = feedparser.parse(r2.content)
            if len(feed2.entries) > 0:
                log.info("Feed [%s] recuperado via curl_cffi (0 -> %d entradas)", label, len(feed2.entries))
                feed = feed2
                n_entries = len(feed.entries)
        except Exception as e:
            log.debug("curl_cffi retry (0 entradas) falhou [%s]: %s", label, e)
    if n_entries == 0:
        log.warning("Feed VAZIO [%s] HTTP 200 mas 0 entradas (possível bloqueio de conteúdo): %s",
                    label, url)
        return []

    from datetime import timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(hours=WINDOW_HOURS)

    # Itera TODAS as entradas e corta DEPOIS do filtro de janela (filter-then-cap):
    # cortar em MAX_PER_SOURCE antes perderia itens recentes em feeds grandes (Valor/
    # Folha/G1 devolvem ~100, todos dentro de 72h → metade era descartada).
    articles: list[RawArticle] = []
    for entry in feed.entries:
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

    articles = articles[:MAX_PER_SOURCE]   # cap após o filtro de janela

    # Se o feed tinha entradas mas TODAS caíram fora da janela, sinaliza —
    # ajuda a distinguir "bloqueio" de "feed sem novidades recentes".
    if not articles and n_entries:
        log.info("Feed [%s] %d entradas, 0 dentro da janela de %dh", label, n_entries, WINDOW_HOURS)
    else:
        log.info("Feed OK [%s] -> %d items (de %d entradas)", label, len(articles), n_entries)
    return articles


# Domínios de NOTÍCIA GERAL (política/esporte/economia ampla) que poluem o feed
# com conteúdo irrelevante a S&M/P&P. Bloqueados na ingestão — independe do
# caminho (RSS, scraper agregador que segue link externo, etc.). Extensível.
_BLOCKED_DOMAINS = frozenset({
    "elfinanciero.com.mx",   # jornal geral mexicano: política/esporte/op-ed
})


def _is_blocked_domain(domain: str) -> bool:
    d = (domain or "").lower().lstrip(".")
    return any(d == b or d.endswith("." + b) for b in _BLOCKED_DOMAINS)


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

    # 1c) News-sitemaps (cobertura do site inteiro; robusto a mudança de layout).
    #     Combina com os scrapers de homepage via dedup por URL (ex.: Estadão).
    try:
        from .sitemap_scrapers import collect_sitemap_sources
        sitemap_items = collect_sitemap_sources()
        all_articles.extend(sitemap_items)
        log.info("Sitemaps: %d artigos", len(sitemap_items))
    except Exception as e:
        log.warning("Sitemap scrapers falharam: %s", e)

    # 2) Feeds RSS
    with ThreadPoolExecutor(max_workers=6) as ex:
        futures = {ex.submit(_fetch_one, src): src for src in SOURCES}
        for fut in as_completed(futures):
            try:
                all_articles.extend(fut.result())
            except Exception as e:
                log.warning("Feed future error: %s", e)

    # Deduplica por URL. Na COLISÃO (ex.: Estadão homepage ∩ sitemap), preferir a
    # melhor versão: a que tem published_at (sitemap traz data real → recência
    # correta) e, em empate, o título mais longo (sitemap usa <news:title> bem
    # formatado; homepage às vezes deriva título do slug, truncado).
    def _better(new: RawArticle, cur: RawArticle) -> bool:
        if (new.published_at is not None) != (cur.published_at is not None):
            return new.published_at is not None
        return len(new.title or "") > len(cur.title or "")

    seen: dict[str, RawArticle] = {}
    for art in all_articles:
        cur = seen.get(art.url)
        if cur is None or _better(art, cur):
            seen[art.url] = art

    deduped = list(seen.values())

    # Blocklist de domínios de ruído (notícia geral) — corta na ingestão.
    blocked = [a for a in deduped if _is_blocked_domain(a.domain)]
    if blocked:
        doms = ", ".join(sorted({a.domain for a in blocked}))
        log.info("Blocklist: %d artigos descartados (%s)", len(blocked), doms)
        deduped = [a for a in deduped if not _is_blocked_domain(a.domain)]

    log.info("Total após dedup: %d artigos", len(deduped))
    return deduped
