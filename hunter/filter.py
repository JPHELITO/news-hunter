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


def _matches(article: RawArticle) -> list[str]:
    """Retorna lista de keywords que bateram no título + snippet."""
    haystack = _normalize(f"{article.title} {article.snippet}")
    pat = _keyword_pattern()
    found = set(m.group(0).lower() for m in pat.finditer(haystack))
    return sorted(found)


def filter_articles(articles: list[RawArticle]) -> list[dict]:
    """
    Aplica filtro de keywords onde necessário e converte para dict
    pronto para o Supabase.

    - needs_filter=False → aceita sempre (Google News já filtrou pela query)
    - needs_filter=True  → só aceita se tiver keyword match
    """
    result: list[dict] = []
    for art in articles:
        if art.needs_filter:
            matched = _matches(art)
            if not matched:
                continue
        else:
            # Ainda coleta quais keywords batem (informativo)
            matched = _matches(art)

        result.append({
            "url": art.url,
            "domain": art.domain,
            "source_name": art.source_name,
            "title": art.title,
            "snippet": art.snippet,
            "published_at": art.published_at.isoformat() if art.published_at else None,
            "found_at": art.found_at.isoformat(),
            "matched_keywords": matched,
        })

    return result
