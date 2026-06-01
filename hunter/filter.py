"""Keyword filtering para o News Hunter."""
from __future__ import annotations

import re
from functools import lru_cache

from .config import ALL_KEYWORDS
from .fetcher import RawArticle

# ── Blocklist — títulos com essas palavras são descartados independente de keywords ──
# Evita agro, cripto e outros setores que jamais são relevantes para S&M / P&P.
_TITLE_BLOCKLIST = frozenset([
    # Agro off-topic
    "milho", "soja", "trigo", "café", "cacau", "açúcar", "sucrose",
    "boi gordo", "frango", "suíno", "porco", "arroz", "laranja",
    "cana-de-açúcar", "algodão", "agronegócio", "grãos",
    "corn", "wheat", "soybean", "sugar cane", "poultry",
    # Cripto / finanças fora do escopo
    "bitcoin", "ethereum", "cripto", "criptomoeda", "blockchain",
    "nft", "token", "defi", "web3",
    # Petróleo (a menos que combinado com keywords nossos — o blocklist é só no título)
    "petróleo", "crude oil", "offshore drilling",
    "refinery oil", "oil spill",
    # Outros claramente fora
    "dark horse", "horse racing", "casino", "lottery",
    "futebol", "football", "basketball", "soccer",
])

# ── Fontes genéricas — exigem keyword no TÍTULO (não apenas no snippet) ────────
# Evita que artigos de agro ou finanças gerais passem por menção tangencial.
_BROAD_SOURCES = frozenset([
    "Folha de S.Paulo", "Folha de São Paulo",
    "Exame", "InfoMoney", "G1", "CNN Brasil",
    "O Globo", "Agência Brasil",
    "Google News", "Google Notícias",
])


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
        title_lower = art.title.lower()

        # 1. Blocklist: descarta imediatamente se o TÍTULO contém palavra off-topic
        if any(w in title_lower for w in _TITLE_BLOCKLIST):
            continue

        content_matches = _matches(art)   # título + snippet

        # 2. Sem nenhuma keyword → descarta
        if not content_matches:
            continue

        # 3. Fontes genéricas (Folha, Exame, InfoMoney…) exigem keyword no TÍTULO
        #    para evitar artigos que só mencionam "cobre" num parágrafo de agro
        if art.source_name in _BROAD_SOURCES:
            if not _matches_field(art.title):
                continue

        matched = content_matches
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
