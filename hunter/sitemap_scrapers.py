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
    {
        # Trade BR de mineração (Fiol, Belo Sun, Código de Mineração) — antes sem
        # cobertura. news-sitemap com ~200 artigos + news:title. Fonte setorial estreita.
        "label": "Notícias de Mineração",
        "domain": "noticiasdemineracao.com",
        "url": "https://www.noticiasdemineracao.com/news-sitemap.xml",
        "needs_filter": False,
    },
    {
        # China economia/indústria/policy (EN) — proxy de MADRUGADA p/ minério/aço/
        # celulose, antes do BR acordar. news-sitemap padrão Google (news:title +
        # publication_date), ~100 itens na janela. AMPLO (notícia geral da China) →
        # keyword filtra p/ commodity/economia. Adicionada 2026-06-25.
        "label": "Global Times",
        "domain": "globaltimes.cn",
        "url": "https://www.globaltimes.cn/sitemap.xml",
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
        d = datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    # Alguns sitemaps (ex.: Notícias de Mineração) trazem data SEM offset → naive.
    # Assume UTC para poder comparar com o cutoff (aware) sem TypeError.
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d


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


# ── Sitemap MENSAL sem título + filtro pelo endereço (2026-11-23) ─────────────────────────────────────
# Para site sem RSS cujo sitemap só traz <loc> + <lastmod>: o endereço (slug) diz do que é a matéria, então
# só o que casa `slug_re` é baixado, e o título sai do og:title da página. Cache em memória: o hunt-loop roda
# a cada 5 min e a mesma matéria fica 72 h na janela — cada página é baixada uma vez por processo.
#
# Reporte Minero (Chile): a melhor imprensa para Lundin (Candelaria, Caserones, Vicuña) e Capstone (Mantoverde,
# Mantos Blancos, Santo Domingo) — medido em 23/11: ~18 posts/dia no site, ~1/dia citando as duas pelo endereço
# (28 em 60 dias), contra 0 nos canadenses (Globe and Mail, Financial Post, BNN). Sem RSS (/feed devolve HTML).
# Fora: as seções de responsabilidade social e vídeo (programa de bolsistas, sinalização de estrada…).
MONTHLY_SLUG_SOURCES = [
    {
        "label": "Reporte Minero",
        "domain": "reporteminero.cl",
        "url_fmt": "https://www.reporteminero.cl/sitemap/{y}/{m:02d}/sitemap-pt-post.xml",
        "slug_re": re.compile(
            r"lundin|capstone|hudbay|ero-copper|candelaria|caserones|mantoverde|mantos-blancos|"
            r"santo-domingo|vicuna|josemaria|filo-del-sol|pinto-valley|constancia"),
        "skip_re": re.compile(r"/noticia/(responsabilidad-social|videos)/"),
        "title_suffix_re": re.compile(r"\s+[-|–]\s+Reporte Minero.*$"),
        "max_pages": 12,
    },
]
_PAGE_TITLE_CACHE: dict[str, tuple[str, str]] = {}
_OG_RE = {k: re.compile(r'<meta[^>]+property=["\']og:' + k + r'["\'][^>]+content=["\']([^"\']*)', re.I)
          for k in ("title", "description")}


def _months_to_read(now: datetime) -> list[tuple[int, int]]:
    """O mês corrente e, nos 3 primeiros dias, também o anterior (a janela de 72 h atravessa a virada)."""
    out = [(now.year, now.month)]
    if now.day <= 3:
        out.append((now.year - 1, 12) if now.month == 1 else (now.year, now.month - 1))
    return out


def parse_monthly_sitemap(content: bytes, src: dict, now: datetime | None = None) -> list[tuple[str, datetime]]:
    """(endereço, data) das matérias na janela cujo endereço casa o filtro da fonte. (Testável puro.)"""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=WINDOW_HOURS)
    try:
        root = ET.fromstring(content)
    except ET.ParseError as e:
        log.warning("Sitemap [%s] XML inválido: %s", src["label"], e)
        return []
    out = []
    for url_el in root:
        if _localname(url_el.tag) != "url":
            continue
        loc = lastmod = None
        for child in url_el.iter():
            ln = _localname(child.tag)
            if ln == "loc" and not loc:
                loc = (child.text or "").strip()
            elif ln == "lastmod" and not lastmod:
                lastmod = (child.text or "").strip()
        if not loc:
            continue
        slug = loc.rstrip("/").rsplit("/", 1)[-1].lower()
        if not src["slug_re"].search(slug) or src["skip_re"].search(loc):
            continue
        pub = _parse_date(lastmod)
        if pub is None or pub < cutoff:
            continue
        out.append((loc, pub))
    return out


def _page_title(url: str, src: dict) -> tuple[str, str]:
    if url in _PAGE_TITLE_CACHE:
        return _PAGE_TITLE_CACHE[url]
    status, content = _http_get(url)
    if status != 200 or not content:
        return "", ""
    import html as _html
    text = content.decode("utf-8", errors="replace")
    got = {}
    for k, rx in _OG_RE.items():
        m = rx.search(text)
        got[k] = " ".join(_html.unescape(m.group(1)).split()) if m else ""
    title = src["title_suffix_re"].sub("", got["title"]).strip()
    _PAGE_TITLE_CACHE[url] = (title, got["description"][:400])
    return _PAGE_TITLE_CACHE[url]


def _scrape_monthly(src: dict, now: datetime | None = None) -> list[RawArticle]:
    now = now or datetime.now(timezone.utc)
    hits: list[tuple[str, datetime]] = []
    for y, m in _months_to_read(now):
        url = src["url_fmt"].format(y=y, m=m)
        status, content = _http_get(url)
        if status != 200 or not content:
            log.warning("Sitemap [%s] HTTP %d: %s", src["label"], status, url)
            continue
        hits.extend(parse_monthly_sitemap(content, src, now))
    out: list[RawArticle] = []
    for loc, pub in sorted(set(hits), key=lambda x: x[1], reverse=True)[: src["max_pages"]]:
        title, desc = _page_title(loc, src)
        if len(title) < 12:
            continue
        out.append(RawArticle(url=loc, domain=src["domain"], source_name=src["label"], title=title,
                              snippet=desc, published_at=pub, found_at=now, needs_filter=False))
    log.info("Sitemap [%s] -> %d artigos (endereço casou: %d)", src["label"], len(out), len(hits))
    return out


def collect_sitemap_sources() -> list[RawArticle]:
    """Coleta todas as fontes com news-sitemap (+ as de sitemap mensal filtrado pelo endereço)."""
    out: list[RawArticle] = []
    for src in SITEMAP_SOURCES:
        try:
            out.extend(_scrape_sitemap(src))
        except Exception as e:
            log.warning("Sitemap [%s] falhou: %s", src.get("label"), e)
    for src in MONTHLY_SLUG_SOURCES:
        try:
            out.extend(_scrape_monthly(src))
        except Exception as e:
            log.warning("Sitemap [%s] falhou: %s", src.get("label"), e)
    return out
