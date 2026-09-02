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
        "title_exclude": ["rationale", "pricing rational"],
    },
    # Comunicados oficiais das cobertas (mw_filings → hunter/cvm_filings.py): fonte primária,
    # já filtrada por categoria lá na origem (só o newsworthy). Aceita tudo.
    "CVM": {
        "pass_through": True,
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

# Match por FRONTEIRA DE PALAVRA (não substring): senão "defi" casaria em
# "define/definir", "wheat" em "Wheaton", "corn" em "Corning", "token" em
# "tokenização" — derrubando manchete real de empresa coberta. Frases
# multi-palavra ("boi gordo", "crude oil") usam a mesma fronteira.
_BLOCK_RE = re.compile(
    r"(?<!\w)(?:" + "|".join(re.escape(w) for w in sorted(_TITLE_BLOCKLIST, key=len, reverse=True)) + r")(?!\w)",
    re.IGNORECASE,
)

# ── Page-index detection — descarta títulos que são páginas de listagem/índice
# (não artigos): "Últimas notícias", "Vídeos", "Seção - Jornal", "Ações hoje".
# Genérico — útil p/ qualquer scraper de homepage que capte links de navegação.
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


# ── Keywords AMBÍGUAS — palavras comuns quando minúsculas, empresa quando
# capitalizadas. Casam SÓ na forma capitalizada (case-sensitive), evitando
# falsos positivos como "vale a pena" (≠ Vale) ou "baixe o app" (≠ APP).
# chave = token minúsculo · valor = formas aceitas (Title/UPPER).
_AMBIGUOUS_CASED: dict[str, tuple[str, ...]] = {
    "vale":  ("Vale", "VALE"),
    "app":   ("APP",),
    "aura":  ("Aura", "AURA"),
    "sail":  ("SAIL",),
    "sigma": ("Sigma", "SIGMA"),
    "cba":   ("CBA",),
    "rani":  ("RANI",),
}


@lru_cache(maxsize=1)
def _strong_pattern() -> re.Pattern:
    """Regex case-INSENSITIVE das keywords inequívocas (todas menos as ambíguas).

    Fronteira de palavra unicode-safe (?<!\\w) ... (?!\\w) evita casar dentro de
    outra palavra. Keywords longas antes das curtas. Compostos como 'Vale S.A.' e
    'Sigma Lithium' permanecem aqui (só o token isolado 'vale'/'sigma' é ambíguo).
    """
    kws = [kw for kw in ALL_KEYWORDS if kw.lower() not in _AMBIGUOUS_CASED]
    escaped = [re.escape(kw.lower()) for kw in sorted(kws, key=len, reverse=True)]
    return re.compile(r"(?<!\w)(?:" + "|".join(escaped) + r")(?!\w)", re.IGNORECASE)


@lru_cache(maxsize=1)
def _ambiguous_pattern() -> re.Pattern:
    """Regex case-SENSITIVE das formas capitalizadas aceitas (Vale|VALE|APP|…)."""
    forms = [f for variants in _AMBIGUOUS_CASED.values() for f in variants]
    escaped = [re.escape(f) for f in sorted(forms, key=len, reverse=True)]
    return re.compile(r"(?<!\w)(?:" + "|".join(escaped) + r")(?!\w)")  # SEM IGNORECASE


# "Vale" + expressão idiomática ("Vale lembrar/destacar/a pena…" / "Vale Tudo"), NÃO a
# empresa. Notícia real da Vale usa 3ª pessoa ("Vale registra/anuncia/eleva"), então
# excluir os INFINITIVOS + "tudo" é seguro — não bloqueia manchete verdadeira.
# "tudo" mata a novela "Vale Tudo" e a expressão "Vale tudo para …" (a empresa nunca
# aparece como "Vale tudo"; se vier "Vale: tudo sobre a mineradora", o ':' quebra o \s+).
# "do sil[íi]cio" = "Vale do Silício" (Silicon Valley — tecnologia, não a mineradora).
# SEGURO: "Vale do Rio Doce" (nome histórico da empresa) e "Vale do Aço" (região
# siderúrgica de MG) NÃO casam "do silício" → seguem passando.
_VALE_IDIOM = re.compile(
    r"\s+(?:tudo|do\s+sil[íi]cio|a\s+pena|lembrar|destacar|ressaltar|citar|notar|"
    r"mencionar|frisar|salientar|dizer|conferir|registrar|comentar|observar|pontuar)\b",
    re.IGNORECASE,
)


def _match_text(text: str) -> list[str]:
    """Keywords que batem no texto: fortes (case-insensitive) + ambíguas (capitalizadas)."""
    if not text:
        return []
    found = set(m.group(0).lower() for m in _strong_pattern().finditer(text.lower()))
    for m in _ambiguous_pattern().finditer(text):           # case original
        # 'Vale a pena / Vale lembrar …' → expressão, não a empresa → ignora
        if m.group(0).lower() == "vale" and _VALE_IDIOM.match(text, m.end()):
            continue
        found.add(m.group(0).lower())
    return sorted(found)


def _matches(article: RawArticle) -> list[str]:
    """Keywords que bateram no título + snippet."""
    return _match_text(f"{article.title} {article.snippet}")


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

    Fontes comuns (filter=True): >=1 keyword no título OU snippet (two-tier — keywords
    ambíguas exigem forma capitalizada; ver _AMBIGUOUS_CASED).
    Fontes setoriais (needs_filter=False): aceitam tudo, só blocklist de título.
    Fontes pass_through (Platts): aceitam TUDO, só title_exclude (ex.: "rationale").
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

        # ── Fonte temática/setorial (filter=False → needs_filter=False): curada e
        #    100% no escopo (Portal Celulose, Ibá, Siderurgia Brasil, ABTCP, ANM,
        #    SteelRadar, IBRAM, Aço Brasil, Reuters). Aceita TUDO, sem exigir
        #    keyword — só barra a blocklist de título (agro/cripto/esporte) por
        #    segurança. (Antes, por bug, essas passavam por keyword e perdiam
        #    matérias setoriais sem termo exato no título/resumo.)
        if not getattr(art, "needs_filter", True):
            if _BLOCK_RE.search(title_lower):
                continue
            result.append(_to_dict(art, []))
            continue

        # ── Caminho padrão: keyword filtering ──────────────────────────────────
        # 0. Page-index: descarta páginas de listagem/índice (não artigos)
        if _is_page_index(art.title):
            continue

        # 1. Blocklist: descarta se o TÍTULO contém palavra off-topic (fronteira de palavra)
        if _BLOCK_RE.search(title_lower):
            continue

        content_matches = _matches(art)   # título + snippet (com casing de ambíguas)

        # 2. Sem nenhuma keyword → descarta
        if not content_matches:
            continue

        # 3. Fontes genéricas (Folha, Exame, InfoMoney…): ANTES exigiam keyword no
        #    TÍTULO (anti-ruído). Agora aceitam título OU resumo — o ruído clássico
        #    ("vale a pena", "baixe o app") é cortado pelo casing case-sensitive das
        #    ambíguas (_AMBIGUOUS_CASED). Keyword forte no corpo (Suzano/Gerdau/
        #    "minério de ferro") já é sinal confiável; o classificador decide o resto.
        #    → sem gate extra: _matches já cobre título + resumo.

        result.append(_to_dict(art, content_matches))

    return result
