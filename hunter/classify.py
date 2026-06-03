"""
Keyword-based classifier para News Hunter.

Para cada artigo recebido, adiciona 4 campos:
  sector       'steel' | 'mining' | 'pp' | None
  news_type    'macro' | 'company' | 'market'
  sentiment    float [-1, +1]  (POS pattern matches - NEG pattern matches normalizado)
  tickers      list[str]       (tickers afetados, ex: ['VALE3.SA', 'CSNA3.SA'])

Custo: zero. Precisão: ~75-80%. Tweakável sem deploy: editar dicionários abaixo.
"""
from __future__ import annotations

import re
from functools import lru_cache

# ───────────────────────────────────────────────────────────────────────────
# Sector dictionaries
# ───────────────────────────────────────────────────────────────────────────
SECTOR_KEYWORDS = {
    "steel": {
        # produtos
        "steel", "aço", "HRC", "hot rolled coil", "bobina a quente",
        "CRC", "cold rolled coil", "bobina a frio", "rebar", "vergalhão",
        "wire rod", "fio máquina", "structural steel", "barra de aço",
        "slab", "billet", "tarugo", "bloom", "heavy plate", "chapa grossa",
        "galvanized", "galvanizado", "galvalume", "tinplate",
        "coated steel",
        # processos
        "blast furnace", "alto-forno", "electric arc furnace", "EAF",
        "basic oxygen furnace", "BOF", "steelmaking",
        # mercado
        "siderurgia", "siderúrgica", "steel price", "preço do aço",
        "steel demand", "steel output", "steel production",
        "steel capacity", "steel imports", "steel exports",
        "steel tariff", "steel anti-dumping", "Section 232",
        # empresas
        "ArcelorMittal", "Gerdau", "CSN", "Companhia Siderúrgica Nacional",
        "Usiminas", "Ternium", "Nucor", "US Steel", "Nippon Steel",
        "POSCO", "Baosteel", "Tata Steel", "SAIL", "Stelco",
        # matérias-primas que afetam steel
        "scrap", "sucata", "ferrous scrap", "pig iron", "ferro gusa",
        "DRI", "direct reduced iron", "HBI",
    },
    "mining": {
        # minérios
        "iron ore", "minério de ferro", "pellet", "pellets",
        "pellet premium", "sinter feed", "lump ore",
        "coking coal", "carvão metalúrgico", "met coal",
        "copper", "cobre", "minério de cobre", "copper ore",
        "nickel", "níquel", "nickel ore",
        "aluminium", "aluminum", "alumínio", "bauxite", "bauxita",
        "zinc", "zinco", "lead", "manganese", "manganês",
        "lithium", "lítio", "cobalt", "cobalto",
        "rare earth", "terras raras", "critical minerals",
        "gold", "ouro", "silver", "prata",
        # mercado mineração
        "mineração", "mining", "minerais", "setor mineral",
        "IODEX", "TSI 62%", "iron ore price",
        # regulatório
        "CFEM", "barragem de rejeitos", "tailings dam",
        "rompimento barragem", "licenciamento ambiental",
        "ANM", "IBRAM",
        # empresas
        "VALE", "Vale S.A.", "BHP", "Rio Tinto", "Fortescue", "FMG",
        "Anglo American", "Simandou", "CMIN", "AURA", "Aura Minerals",
        "Samarco", "MRN", "Kinross", "Sigma Lithium", "Southern Copper",
        "Grupo México", "Antofagasta", "Glencore", "First Quantum",
    },
    "pp": {
        # produtos celulose
        "pulp", "celulose", "BHKP", "celulose de fibra curta",
        "hardwood pulp", "eucalyptus pulp", "NBSK", "softwood pulp",
        "celulose de fibra longa", "BEKP", "BSKP", "BCTMP",
        "dissolving pulp", "celulose solúvel", "fluff pulp",
        "kraft pulp", "celulose kraft", "viscose staple fiber",
        # produtos papel
        "paper", "papel", "papel e celulose", "pulp and paper",
        "paperboard", "cartão", "tissue", "tissue paper",
        "containerboard", "papelão ondulado", "corrugated",
        "coated paper", "papel revestido", "newsprint",
        "packaging paper", "papel embalagem", "papel kraft",
        # mercado P&P
        "pulp price", "preço celulose", "pulp market", "pulp demand",
        "paper price", "preço papel", "paper demand",
        "FOEX", "PIX pulp", "pulp inventory", "estoque celulose",
        "capacity increase", "capacity decrease", "mill closure",
        # florestal
        "eucalipto", "eucalyptus", "pinus", "cavaco",
        "wood chip", "setor florestal", "indústria florestal",
        "Ibá", "ABTCP",
        # empresas
        "Suzano", "Klabin", "Eldorado", "Irani", "CMPC",
        "Stora Enso", "UPM", "Sappi", "APP", "Nine Dragons",
        "Resolute", "International Paper", "WestRock", "Smurfit Kappa",
    },
}

# ───────────────────────────────────────────────────────────────────────────
# Ticker mapping — nome no artigo → ticker B3/NYSE/BMV/Santiago
# ───────────────────────────────────────────────────────────────────────────
TICKER_MAP = {
    # B3
    "VALE": "VALE3.SA", "Vale": "VALE3.SA", "Vale S.A.": "VALE3.SA",
    "CSN": "CSNA3.SA", "Companhia Siderúrgica Nacional": "CSNA3.SA",
    "Gerdau": "GGBR4.SA",
    "Usiminas": "USIM5.SA",
    "Klabin": "KLBN11.SA",
    "Suzano": "SUZB3.SA",
    "CMIN": "CMIN3.SA", "CSN Mineração": "CMIN3.SA",
    "AURA": "AURA33.SA", "Aura Minerals": "AURA33.SA",
    "Irani": "RANI3.SA", "RANI": "RANI3.SA",
    # NYSE
    "Ternium": "TX",
    "Southern Copper": "SCCO",
    "ArcelorMittal": "MT",
    "Nucor": "NUE",
    "US Steel": "X",
    "Cleveland-Cliffs": "CLF",
    "Freeport": "FCX", "Freeport-McMoRan": "FCX",
    "BHP": "BHP",
    "Rio Tinto": "RIO",
    "Vale": "VALE",      # ADR
    "International Paper": "IP",
    # Santiago / México
    "CMPC": "CMPC.SN", "Empresas CMPC": "CMPC.SN",
    "Copec": "COPEC.SN", "Empresas Copec": "COPEC.SN",
    "Grupo México": "GMEXICOB.MX", "Grupo Mexico": "GMEXICOB.MX",
}

# ───────────────────────────────────────────────────────────────────────────
# Macro detection
# ───────────────────────────────────────────────────────────────────────────
MACRO_KEYWORDS = {
    "China stimulus", "estímulo China", "Chinese stimulus",
    "GDP", "PIB", "interest rate", "taxa de juros", "SELIC",
    "tariff", "tarifa", "election", "eleição",
    "inflation", "inflação", "central bank", "banco central",
    "Fed", "Federal Reserve", "BCB",
    "monetary policy", "política monetária",
    "recession", "recessão", "trade war", "guerra comercial",
}

# ───────────────────────────────────────────────────────────────────────────
# Sentiment patterns (regex)
# ───────────────────────────────────────────────────────────────────────────
POS_PATTERNS = [
    re.compile(r"\b(up|sobe|alta|rises?|rose|jump|surge|surges|record|beat|beats|exceed|exceeds)\b", re.I),
    re.compile(r"\b(growth|increase|increases|boost|boosts|rally|strong|strongest|gain|gains)\b", re.I),
    re.compile(r"\b(advance|advances|climbing|climbs|positive|profit|profits|wins|approved)\b", re.I),
]
NEG_PATTERNS = [
    re.compile(r"\b(down|cai|caem|queda|drop|drops|fall|falls|fell|miss|misses|decline|declines|slump)\b", re.I),
    re.compile(r"\b(loss|losses|weak|weakest|cut|cuts|reduce|reduces|decrease|decreases|tumble|tumbles|plunge|plunges)\b", re.I),
    re.compile(r"\b(retreat|retreats|drag|drags|negative|warning|warnings|delay|delays|denied|rejects?)\b", re.I),
]


# ───────────────────────────────────────────────────────────────────────────
# Compiled keyword pattern for fast matching
# ───────────────────────────────────────────────────────────────────────────
@lru_cache(maxsize=1)
def _sector_patterns() -> dict[str, re.Pattern]:
    """Compila um regex por setor com todas as keywords."""
    out = {}
    for sector, keywords in SECTOR_KEYWORDS.items():
        # Ordenar por tamanho desc para que keywords longas casem primeiro
        sorted_kws = sorted(keywords, key=len, reverse=True)
        pattern = "|".join(re.escape(k) for k in sorted_kws)
        out[sector] = re.compile(pattern, re.IGNORECASE)
    return out


@lru_cache(maxsize=1)
def _ticker_pattern() -> re.Pattern:
    """Compila regex de detecção de empresas (case-sensitive para evitar falsos positivos com siglas curtas)."""
    # Ordenar por tamanho desc para casar primeiro a versão longa
    sorted_names = sorted(TICKER_MAP.keys(), key=len, reverse=True)
    pattern = "|".join(re.escape(n) for n in sorted_names)
    # Word boundary para evitar parcial matches
    return re.compile(r"\b(?:" + pattern + r")\b")


@lru_cache(maxsize=1)
def _macro_pattern() -> re.Pattern:
    sorted_kws = sorted(MACRO_KEYWORDS, key=len, reverse=True)
    pattern = "|".join(re.escape(k) for k in sorted_kws)
    return re.compile(pattern, re.IGNORECASE)


# ───────────────────────────────────────────────────────────────────────────
# Public API
# ───────────────────────────────────────────────────────────────────────────
def classify(title: str, snippet: str = "") -> dict:
    """
    Classifica um artigo. Retorna:
      {
        'sector':    'steel' | 'mining' | 'pp' | None,
        'news_type': 'macro' | 'company' | 'market',
        'sentiment': float in [-1.0, 1.0],
        'tickers':   ['VALE3.SA', 'CSNA3.SA', ...],
      }
    """
    text = f"{title or ''} {snippet or ''}"

    # 1) sector — maior score entre os 3
    sector_scores = {}
    for sector, pat in _sector_patterns().items():
        sector_scores[sector] = len(pat.findall(text))
    best_sector = max(sector_scores, key=sector_scores.get)
    sector = best_sector if sector_scores[best_sector] > 0 else None

    # 2) tickers — empresas mencionadas
    raw_matches = _ticker_pattern().findall(text)
    # Dedup preservando ordem
    seen = set()
    tickers = []
    for name in raw_matches:
        t = TICKER_MAP.get(name)
        if t and t not in seen:
            seen.add(t)
            tickers.append(t)

    # 3) news_type
    if tickers:
        news_type = "company"
    elif _macro_pattern().search(text):
        news_type = "macro"
    else:
        news_type = "market"

    # 4) sentiment
    pos = sum(len(p.findall(text)) for p in POS_PATTERNS)
    neg = sum(len(p.findall(text)) for p in NEG_PATTERNS)
    total = pos + neg
    if total == 0:
        sentiment = 0.0
    else:
        sentiment = (pos - neg) / total
        # Limita ao intervalo [-1, 1]
        sentiment = max(-1.0, min(1.0, sentiment))
        # Arredonda para 3 casas
        sentiment = round(sentiment, 3)

    return {
        "sector":    sector,
        "news_type": news_type,
        "sentiment": sentiment,
        "tickers":   tickers,
    }


def classify_article_dict(art: dict) -> dict:
    """Recebe um dict de artigo (com title e snippet), retorna o mesmo dict
    com os 4 campos de classificação adicionados."""
    cls = classify(art.get("title", ""), art.get("snippet", ""))
    art.update(cls)
    return art
