"""Keyword filtering para o News Hunter."""
from __future__ import annotations

import re
from functools import lru_cache

from .config import ALL_KEYWORDS
from .fetcher import RawArticle

# ── Regras de filtro POR FONTE ────────────────────────────────────────────────
# Cada fonte pode ter comportamento próprio de ingestão. Chave = source_name
# (exatamente como o scraper o define). Campos suportados:
#   pass_through   bool  → aceita TODAS as notícias da fonte (pula keyword,
#                          page-index e blocklist). Use só p/ fontes curadas.
#   title_exclude  list  → descarta se o título contém qualquer um destes termos
#                          (case-insensitive, substring). Aplica-se SEMPRE,
#                          inclusive a fontes pass_through.
#
# Para adicionar regras de outra fonte, basta inserir uma entrada aqui — nenhuma
# outra parte do código precisa mudar.
SOURCE_FILTER_RULES: dict[str, dict] = {
    # Platts (S&P Global): terminal curado de metais/siderurgia. Por decisão de
    # negócio, TODAS as notícias vão para o news hunter — exceto "Rationale"
    # (também barrado por ContentType em platts_scraper._WANTED_TYPES).
    "S&P Platts": {
        "pass_through": True,
        "title_exclude": ["rationale"],
    },
}

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
    "Exame", "InfoMoney", "G1", "G1 Economia", "CNN Brasil",
    "O Globo", "Agência Brasil",
    # Notícia geral (esporte/política/entretenimento misturados) — exigem
    # keyword no TÍTULO para não vazar futebol/política via menção tangencial.
    "Metrópoles", "UOL Economia", "Veja", "Money Times",
    "Google News", "Google Notícias",
])

# ── Page-index detection — Google News retorna URLs de páginas-índice (não
# artigos): "Mineração - Valor Econômico", "Últimas notícias", "Vídeos",
# "B3SA3 - Ações hoje". Reconhecíveis por padrões de título genérico.
_PAGE_INDEX_TITLE_PATTERNS = [
    re.compile(r"^(últimas notícias|ultimas noticias|vídeos|videos|home|notícias|noticias)", re.IGNORECASE),
    re.compile(r"^[A-Z]{2,5}\s*\|\s*[A-Z]{4,5}\d?\b", re.IGNORECASE),  # "B3 | B3SA3"
    re.compile(r"^[A-Z][a-zA-Zçãõá-úñ]+\s+\-\s+(Valor Econômico|Estadão|Folha)", re.IGNORECASE),
    re.compile(r"^(ações hoje|cotações|bolsa hoje)", re.IGNORECASE),
]

def _is_page_index(title: str) -> bool:
    """True se o título parece ser uma página-índice, não um artigo real."""
    if not title or len(title) < 15:
        return True
    if title.count(" ") < 2:  # títulos curtos demais para serem notícia
        return True
    for pat in _PAGE_INDEX_TITLE_PATTERNS:
        if pat.search(title):
            return True
    return False


def _normalize(text: str) -> str:
    """Lowercase apenas. Sem NFD strip — evita falsos positivos com acentos."""
    return text.lower()


@lru_cache(maxsize=1)
def _keyword_pattern() -> re.Pattern:
    """Regex combinada para todos os keywords. Compilada uma vez.

    Usa fronteira de palavra unicode-safe (?<!\\w) ... (?!\\w) para evitar
    casar keyword DENTRO de outra palavra (ex: 'aura' em 'restaurante',
    'rani' em palavras aleatórias, 'app' em 'application'). Keywords
    ordenadas por tamanho desc → as longas casam antes das curtas.
    """
    escaped = [re.escape(kw.lower()) for kw in sorted(ALL_KEYWORDS, key=len, reverse=True)]
    pattern = r"(?<!\w)(?:" + "|".join(escaped) + r")(?!\w)"
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


def _to_dict(art: RawArticle, matched: list[str]) -> dict:
    return {
        "url":              art.url,
        "domain":           art.domain,
        "source_name":      art.source_name,
        "title":            art.title,
        "snippet":          art.snippet,
        "published_at":     art.published_at.isoformat() if art.published_at else None,
        "found_at":         art.found_at.isoformat(),
        "matched_keywords": matched,
    }


def filter_articles(articles: list[RawArticle]) -> list[dict]:
    """
    Filtra os artigos por keyword — com exceções por fonte (SOURCE_FILTER_RULES).

    Fontes comuns: ao menos 1 keyword deve aparecer no título OU snippet; fontes
    genéricas (Folha, Exame…) exigem keyword no TÍTULO.

    Fontes pass_through (ex.: Platts): aceitam TODAS as notícias, pulando
    keyword/page-index/blocklist. O único gate é title_exclude (ex.: "rationale").
    """
    result: list[dict] = []
    for art in articles:
        title_lower = art.title.lower()
        rule = SOURCE_FILTER_RULES.get(art.source_name, {})

        # ── Exclusão por título específica da fonte (aplica-se SEMPRE) ──────────
        excl = rule.get("title_exclude")
        if excl and any(term in title_lower for term in excl):
            continue

        # ── Fonte pass_through: curada → aceita tudo (sem keyword/index/blocklist)
        if rule.get("pass_through"):
            result.append(_to_dict(art, []))
            continue

        # ── Caminho padrão: keyword filtering ──────────────────────────────────
        # 0. Page-index detection: descarta páginas de listagem do Google News
        if _is_page_index(art.title):
            continue

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

        result.append(_to_dict(art, content_matches))

    return result
