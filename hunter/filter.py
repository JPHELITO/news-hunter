"""Keyword filtering — aplica apenas em feeds marcados com filter=True."""
from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

from .config import ALL_KEYWORDS
from .fetcher import RawArticle


def _normalize(text: str) -> str:
    """Lowercase apenas. Sem NFD strip — evita falsos positivos com acentos."""
    return text.lower()


@lru_cache(maxsize=1)
def _keyword_pattern() -> re.Pattern:
    """Regex combinada para todos os keywords. Compilada uma vez."""
    escaped = [re.escape(kw.lower()) for kw in ALL_KEYWORDS]
    # Sem \b para keywords com acentos (ex: "aço", "minério") — word boundary
    # do Python não funciona bem com unicode. Usamos busca simples.
    pattern = "|".join(escaped)
    return re.compile(pattern, re.IGNORECASE)


def _matches_field(text: str) -> list[str]:
    """Retorna keywords que batem em um campo específico (ex: só o título)."""
    haystack = _normalize(text)
    pat = _keyword_pattern()
    found = set(m.group(0).lower() for m in pat.finditer(haystack))
    return sorted(found)


def _matches(article: RawArticle) -> list[str]:
    """Retorna lista de keywords que bateram no título + snippet."""
    haystack = _normalize(f"{article.title} {article.snippet}")
    pat = _keyword_pattern()
    found = set(m.group(0).lower() for m in pat.finditer(haystack))
    return sorted(found)


def filter_articles(articles: list[RawArticle]) -> list[dict]:
    """
    Filtra todos os artigos por keyword — sem exceções.

    Mesmo fontes Google News (pre-filtradas por query) precisam bater
    ao menos um keyword no título ou snippet. Isso elimina artigos
    tangencialmente relacionados que as queries trazem por coincidência.

    Regra:
      - Ao menos 1 keyword deve aparecer no TÍTULO   (match forte)
      - OU ao menos 2 keywords devem aparecer no título+snippet (match composto)
    """
    result: list[dict] = []
    for art in articles:
        title_matches   = _matches_field(art.title)
        content_matches = _matches(art)           # título + snippet

        # Aceita se: 1 match no título, OU 2+ matches no conteúdo todo
        if not title_matches and len(content_matches) < 2:
            continue

        matched = content_matches or title_matches
        result.append({
            "url":              art.url,
            "domain":           art.domain,
            "source_name":      art.source_name,
            "title":            art.title,
            "snippet":          art.snippet,
            "published_at":     art.published_at.isoformat() if art.published_at else None,
            "found_at":         art.found_at.isoformat(),
            "matched_keywords": matched,
        })

    return result
