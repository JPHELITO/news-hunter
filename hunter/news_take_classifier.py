"""
news_take_classifier.py — Classificador determinístico de "take" para notícias de mercado.

Cada notícia recebe:
  include_in_report        bool
  exclusion_reason         str | None
  sector                   str
  region                   str
  normalized_topics        list[str]
  covered_companies_mentioned  list[str]
  take                     "+" | "-" | "=" | "review"
  take_reason              str
  confidence               float [0, 1]
  matched_rules            list[str]

Toda lógica é baseada em regras explícitas, dicionários e score líquido.
Sem modelos de ML, sem chamadas externas — 100% auditável.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any

# ─────────────────────────────────────────────────────────────────────────────
# 1. DICIONÁRIOS CENTRAIS — edite aqui para ajustar cobertura
# ─────────────────────────────────────────────────────────────────────────────

# Empresas cobertas e seus aliases (normalização aplicada depois)
COVERED_COMPANY_ALIASES: dict[str, list[str]] = {
    "GERDAU": [
        "gerdau", "ggbr4", "ggbr", "ggbr3", "gerdau sa", "gerdau s.a",
        "gerdau ameristeel", "gerdau metalurgica",
    ],
    "TERNIUM": [
        "ternium", "tx", "ternium brasil", "ternium mexico",
    ],
    "USIMINAS": [
        "usiminas", "usim5", "usim3", "usinas siderurgicas de minas gerais",
        "usinas siderurgicas", "cosipa",
    ],
    "CSN": [
        "csn", "csna3", "companhia siderurgica nacional",
        "companhia siderurgica nac",
    ],
    "CMIN": [
        "cmin", "cmin3", "csn mineracao", "csn mineração",
        "csn mining", "mineracao siderurgica nacional",
    ],
    "SUZANO": [
        "suzano", "suzb3", "suzano papel", "suzano pulp",
        "suzano papel e celulose",
    ],
    "IRANI": [
        "irani", "rani3", "celulose irani", "irani papel",
    ],
    "CMPC": [
        "cmpc", "empresas cmpc", "cmpc celulosa", "cmpc pulp",
    ],
    "KLABIN": [
        "klabin", "klbn11", "klbn3", "klbn4", "klabin sa",
    ],
    "SOUTHERN COPPER": [
        "southern copper", "scco", "southern peru copper",
    ],
    "COPEC": [
        "copec", "copec.sn", "empresas copec", "arauco",
        "celulosa arauco", "arauco pulp",
    ],
    "GRUPO MEXICO": [
        "grupo mexico", "grupo méxico", "gmexico", "gmexicob",
        "gmexicob.mx", "americas mining", "asarco",
    ],
    "AURA": [
        "aura", "aura minerals", "aura33", "aura mineral",
    ],
    "VALE": [
        "vale", "vale3", "vale sa", "vale s.a", "vale mining",
        "vale do rio doce",
    ],
}

# Empresas relevantes de P&P que NÃO são cobertas (usadas para contexto)
PP_COMPARABLE_COMPANIES = frozenset([
    "upm", "stora enso", "storaenso", "sappi", "resolute", "domtar",
    "international paper", "westrock", "smurfit kappa", "nine dragons",
    "app", "asia pulp", "april", "eldorado", "valmet",
])

# Mapa: termo normalizado → tópico canônico
TOPIC_NORMALIZATION: list[tuple[re.Pattern, str]] = []
_TOPIC_RAW: list[tuple[str, str]] = [
    # Steel products
    (r"\b(hrc|hot.?rolled.?coil|bobina.?a.?quente|hot.?roll)\b",         "hrc"),
    (r"\b(crc|cold.?rolled.?coil|bobina.?a.?frio|cold.?roll)\b",         "crc"),
    (r"\b(rebar|vergalhao|barra.?de.?aco|deformed.?bar)\b",              "rebar"),
    (r"\b(wire.?rod|fio.?maquina)\b",                                     "wire_rod"),
    (r"\b(billet|tarugo|bloom)\b",                                        "billet"),
    (r"\b(slab|placa.?de.?aco|steel.?slab)\b",                           "slab"),
    (r"\b(plate|chapa.?grossa|heavy.?plate|plates)\b",                   "plate"),
    (r"\b(flat.?steel|laminados.?planos|flat.?product|flat.?products)\b", "flat_steel"),
    (r"\b(structural.?steel|aco.?estrutural|beam|beams|merchant.?bar)\b", "structural"),
    (r"\b(pig.?iron|ferro.?gusa)\b",                                      "pig_iron"),
    # Raw materials (mining / inputs)
    (r"\b(iron.?ore|minerio.?de.?ferro|iodex|tsi.?62|iron ore price)\b", "iron_ore"),
    (r"\b(pellet|pellets|pellet.?premium|pelotas?)\b",                   "pellets"),
    (r"\b(sinter.?feed|lump.?ore)\b",                                    "sinter"),
    (r"\b(met.?coal|coking.?coal|carvao.?metalurgico|carvao.?coqueificavel)\b", "met_coal"),
    (r"\b(scrap|sucata|ferrous.?scrap|steel.?scrap|sucata.?ferrosa)\b",  "scrap"),
    (r"\b(dri|direct.?reduced.?iron|hbi)\b",                             "dri"),
    # Pulp & Paper
    (r"\b(pulp|celulose|bhkp|nbsk|bekp|bskp|bctmp|kraft.?pulp|hardwood.?pulp|softwood.?pulp|dissolving.?pulp|fluff.?pulp)\b", "pulp"),
    (r"\b(paper|papel)(?!.?(board|ondulado|embalagem|kraft|tissue))\b",  "paper"),
    (r"\b(tissue|papel.?tissue|papel.?higienico)\b",                     "tissue"),
    (r"\b(containerboard|papelao.?ondulado|corrugated|caixas.?onduladas)\b", "containerboard"),
    (r"\b(occ|old.?corrugated.?container|aparas)\b",                     "occ"),
    (r"\b(newsprint)\b",                                                  "newsprint"),
    # Metals
    (r"\b(copper|cobre|minerio.?de.?cobre|copper.?ore|copper.?price)\b", "copper"),
    (r"\b(gold|ouro)\b",                                                  "gold"),
    (r"\b(silver|prata)\b",                                               "silver"),
    (r"\b(nickel|niquel)\b",                                              "nickel"),
    (r"\b(aluminum|aluminium|aluminio|bauxite|bauxita)\b",               "aluminum"),
    (r"\b(zinc|zinco)\b",                                                 "zinc"),
    # Market concepts
    (r"\b(demand|demanda|consumo|consumption)\b",                         "demand"),
    (r"\b(supply|oferta)\b",                                              "supply"),
    (r"\b(capacity|capacidade)\b",                                        "capacity"),
    (r"\b(production|producao|output)\b",                                 "production"),
    (r"\b(exports?|exportacao|exportacoes|shipments?)\b",                 "exports"),
    (r"\b(imports?|importacao|importacoes)\b",                           "imports"),
    (r"\b(inventories|inventory|estoques?|inventario)\b",                "inventories"),
    (r"\b(prices?|preco|precos)\b",                                       "prices"),
    (r"\b(utilization|utilizacao|capability.?utilization|capacity.?utilization|aisi)\b", "utilization"),
    (r"\b(tariff|tarifa|anti.?dumping|safeguard|section.?232)\b",        "tariffs"),
    (r"\b(blast.?furnace|alto.?forno|eaf|electric.?arc.?furnace|bof|basic.?oxygen)\b", "furnace"),
    (r"\b(mill.?closure|plant.?closure|fechamento.?de.?planta|parada.?de.?planta)\b", "closure"),
]

# Compile topic patterns once at import
for _raw_pattern, _topic in _TOPIC_RAW:
    TOPIC_NORMALIZATION.append((re.compile(_raw_pattern, re.IGNORECASE), _topic))


# Region keywords (checados na versão normalizada)
REGION_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b(china|chinese|chinesa|beijing|shanghai|qingdao|hebei|liaoning|"
                r"japan|japanese|korea|korean|india|indian|asia|asian|asiatica|"
                r"southeast.?asia|vietnam|indonesia|taiwan|asean)\b", re.I), "china_asia"),
    (re.compile(r"\b(us|usa|united.?states|american|america|norte.?americano|"
                r"aisi|nucor|us.?steel|cleveland.?cliffs|north.?america)\b", re.I), "us"),
    (re.compile(r"\b(brazil|brasil|brasileir[ao]|b3|bovespa)\b", re.I), "brazil"),
    (re.compile(r"\b(europe|european|eurozone|eu |germany|germany|france|italy|"
                r"spain|sweden|finland|austria|netherlands|nordic)\b", re.I), "europe"),
    (re.compile(r"\b(turkey|turkish|turca|turco|ukraine|middle.?east|russia|"
                r"mena|africa|latin.?america|mexico|chile|colombia|peru|argentina)\b", re.I), "rest_of_world"),
    (re.compile(r"\b(global|worldwide|international|world|mundial|mundial)\b", re.I), "global"),
]

# Direction words (positive = up / negative = down)
_UP_WORDS = frozenset([
    # EN
    "up", "rise", "rises", "rising", "rose", "risen", "increase", "increases", "increasing",
    "higher", "stronger", "strong", "gains", "gain", "firm", "firmer", "rebound",
    "recovery", "recover", "recovers", "recovered", "recovering",
    "improve", "improves", "improving", "improved",
    "jump", "jumps", "jumped", "surge", "surges", "surged",
    "climb", "climbs", "climbing", "climbed",
    "growth", "grow", "grows", "growing", "grew", "boost", "boosts", "boosted",
    "rally", "rallies", "advance", "advances", "advanced", "accelerate", "accelerates",
    "expand", "expands", "expanding", "expansion", "positive", "record",
    "beat", "beats", "exceed", "exceeds", "above", "strengthen", "strengthens",
    "strengthening", "robust", "resilient", "outperform", "outperforms",
    # PT
    "sobe", "subiu", "alta", "aumento", "maior", "maiores", "melhora", "melhor",
    "recuperacao", "avanco", "forte", "fortalecendo", "crescimento",
    "elevacao", "elevou", "impulsionado", "acelerou", "aquecimento",
])

_DOWN_WORDS = frozenset([
    # EN
    "down", "fall", "falls", "falling", "fell", "fallen", "decrease", "decreases",
    "decreasing", "lower", "weaker", "weak", "decline", "declines", "declining",
    "drop", "drops", "dropping", "dropped", "soften", "softer", "soft",
    "slump", "slumps", "slumped", "slumping", "plunge", "plunges", "plunged",
    "tumble", "tumbles", "tumbled", "retreat", "retreats", "retreated",
    "cut", "cuts", "cutting", "reduce", "reduces", "reducing", "reduced",
    "contraction", "contract", "contracts", "shrink", "shrinks", "shrank", "below",
    "miss", "misses", "loss", "losses", "ease", "eases", "easing", "eased",
    "weaken", "weakens", "weakened", "weakening", "subdued", "sluggish",
    "underperform", "underperforms", "pressured",
    # PT
    "cai", "caiu", "queda", "reducao", "menor", "menores", "piora", "fraco",
    "enfraquecendo", "recuo", "baixa", "retrocesso", "declinio", "colapso",
    "pressao", "pressionar", "despencou", "afundou",
])

# Termos de exclusão automática (tipo de conteúdo)
_EXCLUDE_CONTENT_TYPES = frozenset(["rationale"])

# Cripto "hard" — nomes de moeda/token que NUNCA são da nossa cobertura.
# Excluído SEMPRE, mesmo que um alias ambíguo (ex: 'vale') case de carona.
_HARD_CRYPTO_RE = re.compile(
    r"(?<!\w)("
    r"bitcoin|btc|ethereum|hyperliquid|altcoin|memecoin|dogecoin|shiba|"
    r"solana|stablecoin|criptomoeda|cryptocurrency"
    r")(?!\w)", re.I,
)

# Conteúdo fora de escopo: cripto leve, esporte, entretenimento, política
# local. Excluído quando não há empresa coberta no texto.
_OFF_TOPIC_RE = re.compile(
    r"(?<!\w)("
    r"cripto|crypto|nft|blockchain|web3|defi|"
    r"futebol|futbol|mundial|copa do mundo|world cup|shakira|"
    r"jogador|jogadores|selecao|partida|estadio|"
    r"eleic|election|elecciones|amlo|cnte|tepjf"
    r")(?!\w)", re.I,
)

# Notícia policial / crime: pega carona em palavra de commodity ou nome de
# cidade (ex: "roubo ... em Alumínio" — cidade de SP). Excluído sem empresa coberta.
_CRIME_RE = re.compile(
    r"(?<!\w)("
    r"roubo|roubar|roubad|assalt|quadrilha|furto|furtad|tiroteio|balead|"
    r"latrocinio|homicidio|sequestr|traficant|trafico de|delegacia|"
    r"policia militar|policiais|operacao policial|preso em flagrante|"
    r"chacina|esfaque|estupro|feminicidio"
    r")(?!\w)", re.I,
)

# Tópicos que reduzem prioridade / excluem em Steel/Mining
_LOW_PRIORITY_TOPICS_STEEL = frozenset(["billet", "wire_rod", "plate", "pig_iron"])

# Tópicos que excluem em Steel/Mining quando não há empresa coberta
_EXCLUDE_TOPICS_STEEL = frozenset(["pig_iron"])  # "pig iron brazil" sozinho não entra

# Padrões de exclusão regional para Europa (P&P)
_EU_ONLY_PATTERN = re.compile(
    r"\b(europe|european|germany|france|italy|spain|sweden|finland|nordic|"
    r"storaenso|stora enso|sappi|upm|mondi)\b",
    re.I,
)


# ─────────────────────────────────────────────────────────────────────────────
# 2. FUNÇÕES AUXILIARES PURAS
# ─────────────────────────────────────────────────────────────────────────────

def normalize_text(text: str) -> str:
    """Minúsculas, sem acentos, sem pontuação redundante, espaços normalizados."""
    if not text:
        return ""
    # NFD decompõe caracteres acentuados; filtra combining marks
    nfd = unicodedata.normalize("NFD", text)
    no_accent = "".join(c for c in nfd if unicodedata.category(c) != "Mn")
    lower = no_accent.lower()
    # Substitui pontuação por espaço (preserva hifens em palavras compostas como "hot-rolled")
    cleaned = re.sub(r"[^\w\s\-]", " ", lower)
    return re.sub(r"\s+", " ", cleaned).strip()


# Aliases que coincidem com palavras comuns (PT/EN) e geram falso-positivo:
#   'vale'  → verbo "valer" ("vale a pena", "vale comprar")
#   'tx'    → "TX" = Texas
#   'aura'  → palavra comum ("aura de mercado")
# Só contam como menção à empresa se houver contexto setorial/financeiro no texto.
_AMBIGUOUS_ALIAS_WORDS = frozenset({"vale", "tx", "aura"})

# Contexto que confirma que a menção é realmente à empresa
_COMPANY_CONTEXT_RE = re.compile(
    r"(?<!\w)("
    r"vale3|cmin3|ggbr4|usim5|csna3|suzb3|rani3|klbn11|aura33|"          # tickers BR
    r"minerio|mineradora|mineracao|ferro|pelota|niquel|cobre|ouro|"       # mineração
    r"aco|siderurg|steel|iron|mining|mineral|smelter|"                    # steel/mining
    r"celulose|pulp|paper|papel|"                                         # P&P
    r"acoes|acao|bolsa|b3|dividendo|balanco|resultado|trimestre|"         # finanças BR
    r"shares|stock|equity|earnings|quarter|production|producao|"          # finanças EN
    r"exports?|exportac|demanda|demand|capacidade|capacity"               # mercado
    r")", re.I,
)


def detect_covered_companies(text: str) -> list[str]:
    """Retorna nomes canônicos de empresas cobertas mencionadas no texto.

    Aliases ambíguos (ex: 'vale' = verbo em PT) só contam se houver contexto
    setorial/financeiro — evita falsos positivos com cripto/política/geral.
    """
    norm = normalize_text(text)
    has_context = bool(_COMPANY_CONTEXT_RE.search(norm))
    found: list[str] = []
    for canonical, aliases in COVERED_COMPANY_ALIASES.items():
        strong = False   # alias inequívoco (ticker, nome completo)
        weak = False     # alias ambíguo (palavra comum)
        for alias in aliases:
            # Evita palavra dentro de palavra (ex: "csn" em "csn mineracao" e "csna3")
            pattern = r"(?<!\w)" + re.escape(alias) + r"(?!\w)"
            if re.search(pattern, norm):
                if alias in _AMBIGUOUS_ALIAS_WORDS:
                    weak = True
                else:
                    strong = True
                    break
        if strong or (weak and has_context):
            found.append(canonical)
    return found


def detect_region(text: str) -> str:
    """Detecta região principal. Retorna 'unknown' se não identificado."""
    scores: dict[str, int] = {}
    for pat, region in REGION_PATTERNS:
        hits = len(pat.findall(text))
        if hits:
            scores[region] = scores.get(region, 0) + hits
    if not scores:
        return "unknown"
    return max(scores, key=scores.get)


def detect_topics(text: str) -> list[str]:
    """Retorna tópicos canônicos presentes no texto (sem duplicatas, ordenados por posição)."""
    norm = normalize_text(text)
    seen: set[str] = set()
    result: list[str] = []
    for pat, topic in TOPIC_NORMALIZATION:
        if pat.search(norm) and topic not in seen:
            seen.add(topic)
            result.append(topic)
    return result


def _count_direction(text: str) -> tuple[int, int]:
    """Conta palavras up/down no texto normalizado. Retorna (pos, neg)."""
    norm = normalize_text(text)
    tokens = set(re.findall(r"\b\w+\b", norm))
    pos = len(tokens & _UP_WORDS)
    neg = len(tokens & _DOWN_WORDS)
    return pos, neg


def _main_clause_direction(norm: str) -> tuple[int, int]:
    """
    Conta direção apenas na cláusula principal, ignorando subordinadas
    introduzidas por 'as', 'because', 'driven by', 'amid', etc.
    Evita que 'supply increases' cancele 'scrap prices fall'.
    """
    tokens = set(re.findall(r"\b\w+\b", _primary_clause(norm)))
    pos = len(tokens & _UP_WORDS)
    neg = len(tokens & _DOWN_WORDS)
    return pos, neg


# Conectivos que separam cláusulas. Capturados (grupo) para sabermos qual é.
_CLAUSE_SPLIT_RE = re.compile(
    r"\b(while|but|whereas|although|however|yet|even as|meanwhile|"
    r"as|because|due to|driven by|amid|following|thanks to|boosted by|"
    r"supported by|on the back of|after|"
    r"mas|porem|enquanto|embora|contudo|entretanto|"
    r"porque|devido a|impulsionado|impulsionada|puxado por|puxada por|"
    r"por conta de)\b",
    re.I,
)
# Conectivos CAUSAIS: a cláusula que vem DEPOIS é só o motivo → descartada.
_CAUSAL_CONNECTIVES = frozenset({
    "as", "because", "due to", "driven by", "amid", "following", "thanks to",
    "boosted by", "supported by", "on the back of", "after",
    "porque", "devido a", "impulsionado", "impulsionada", "puxado por",
    "puxada por", "por conta de",
})


def _retained_clauses(norm: str) -> list[str]:
    """Divide em cláusulas pelos conectivos. Descarta a cláusula que vem após
    um conectivo CAUSAL (é o motivo, não sinal). Mantém as contrastivas
    ('while', 'but', 'mas') — os dois lados carregam sinal próprio."""
    parts = _CLAUSE_SPLIT_RE.split(norm)
    clauses = [parts[0]]
    i = 1
    while i < len(parts):
        conn = parts[i].lower().strip()
        clause = parts[i + 1] if i + 1 < len(parts) else ""
        if conn not in _CAUSAL_CONNECTIVES:
            clauses.append(clause)
        i += 2
    return [c for c in clauses if c.strip()]


def _primary_clause(norm: str) -> str:
    """Compat: texto das cláusulas retidas (sem subordinadas causais)."""
    return " ".join(_retained_clauses(norm))


def _direction_positions(text: str) -> tuple[list[int], list[int]]:
    """Posições (char) das palavras de direção up e down."""
    ups, downs = [], []
    for m in re.finditer(r"\w+", text):
        w = m.group(0)
        if w in _UP_WORDS:
            ups.append(m.start())
        elif w in _DOWN_WORDS:
            downs.append(m.start())
    return ups, downs


def _topic_direction_map(norm: str, topics: set[str], window: int = 60) -> dict[str, int]:
    """Direção (+1/-1/0) de cada tópico, calculada DENTRO de cada cláusula —
    a palavra de direção mais próxima do tópico na MESMA cláusula. Isola
    'iron ore fell | met coal rose': cada tópico recebe sua própria direção
    sem vazamento entre cláusulas. Primeira cláusula a definir o tópico vence.
    """
    out: dict[str, int] = {}
    for clause in _retained_clauses(norm):
        ups, downs = _direction_positions(clause)
        if not ups and not downs:
            continue
        for pat, topic in TOPIC_NORMALIZATION:
            if topic not in topics or topic in out:
                continue
            best_dir, best_dist = 0, window + 1
            for m in pat.finditer(clause):
                tpos = m.start()
                for pos in ups:
                    dist = abs(pos - tpos)
                    if dist < best_dist:
                        best_dist, best_dir = dist, +1
                for pos in downs:
                    dist = abs(pos - tpos)
                    if dist < best_dist:
                        best_dist, best_dir = dist, -1
            if best_dist <= window:
                out[topic] = best_dir
    return out


def _is_rationale(text: str, source: str = "") -> bool:
    norm = normalize_text(text)
    if any(t in norm for t in _EXCLUDE_CONTENT_TYPES):
        return True
    if "rationale" in normalize_text(source):
        return True
    return False


def _mentions_turkish_rebar(norm: str) -> bool:
    return bool(re.search(r"\b(turkish|turkey|turk).{0,25}(rebar|vergalhao)\b", norm, re.I) or
                re.search(r"\b(rebar|vergalhao).{0,25}(turkish|turkey|turk)\b", norm, re.I))


def _mentions_capacity_increase(norm: str) -> bool:
    return bool(re.search(
        r"("
        r"new.{0,40}(capacity|mill|plant|facility)|"
        r"capacity.{0,30}(increase|expansion|starts?|launch|come.?online|coming.?online)|"
        r"(expansion|expansao|nova.?usina|ramp.?up|startup|start.?up|commissioning|inaugurac|"
        r"new.?capacity|capacity.?addition)"
        r")",
        norm, re.I,
    ))


def _mentions_capacity_cut(norm: str) -> bool:
    return bool(re.search(
        r"("
        r"closure|closed|closing|closes?|shutdown|curtailment|curtailed|idled?|"
        r"fechamento|parada|corte.?de.?capacidade|"
        r"reduc.{0,15}capacidade|reducao.?de.?capacidade|"
        r"cutting.{0,20}capacity|reducing.{0,20}capacity|"
        r"blast.?furnace.{0,20}(idl|shut|clos|reduc)|"
        r"alto.?forno.{0,20}(parado|fechado|reduzido)"
        r")",
        norm, re.I,
    ))


# ─────────────────────────────────────────────────────────────────────────────
# 3. REGRAS DE EXCLUSÃO
# ─────────────────────────────────────────────────────────────────────────────

def should_exclude_news(text: str, metadata: dict) -> tuple[bool, str]:
    """
    Decide se a notícia deve ser excluída do relatório.

    Retorna (deve_excluir, motivo).
    """
    source = metadata.get("source_name", "") or metadata.get("source", "") or ""
    content_type = metadata.get("content_type", "") or metadata.get("news_type", "") or ""

    # 1. Rationale automático
    if _is_rationale(text, source) or _is_rationale("", content_type):
        return True, "rationale_news"

    norm = normalize_text(text)

    # 1b. Cripto "hard" (nome de moeda) → exclui SEMPRE, mesmo com empresa de
    # carona via alias ambíguo ('vale a pena' num artigo de bitcoin).
    if _HARD_CRYPTO_RE.search(norm):
        return True, "irrelevant_region"

    topics = detect_topics(text)
    covered = detect_covered_companies(text)

    # 2. Conteúdo fora de escopo (cripto leve/esporte/política) sem empresa coberta
    if _OFF_TOPIC_RE.search(norm) and not covered:
        return True, "irrelevant_region"

    # 2b. Notícia policial/crime (commodity ou cidade só de carona) sem empresa coberta
    if _CRIME_RE.search(norm) and not covered:
        return True, "irrelevant_region"

    # 3. Sem tópicos reconhecidos e sem empresa coberta → baixa relevância
    if not topics and not covered:
        return True, "no_market_take_detected"

    # 3. Pig iron sem empresa coberta e sem tópicos relevantes adicionais
    topic_set = set(topics)
    _PIG_IRON_GENERIC = {"prices", "production", "exports", "imports", "demand", "supply"}
    if "pig_iron" in topic_set and topic_set.issubset({"pig_iron"} | _PIG_IRON_GENERIC) and not covered:
        return True, "irrelevant_commodity"

    # 4. Billet ou wire_rod sem contexto adicional relevante
    if topic_set.issubset({"billet", "wire_rod"}) and not covered:
        return True, "low_relevance"

    return False, ""


# ─────────────────────────────────────────────────────────────────────────────
# 4. CLASSIFICAÇÃO DE SETOR
# ─────────────────────────────────────────────────────────────────────────────

_STEEL_TOPICS = frozenset([
    "hrc", "crc", "rebar", "wire_rod", "billet", "slab", "plate",
    "flat_steel", "structural", "pig_iron", "iron_ore", "pellets",
    "sinter", "met_coal", "scrap", "dri", "furnace", "utilization",
])
_MINING_TOPICS = frozenset([
    "iron_ore", "pellets", "copper", "gold", "silver", "nickel", "aluminum",
    "zinc", "sinter",
])
_PP_TOPICS = frozenset([
    "pulp", "paper", "tissue", "containerboard", "occ", "newsprint",
])
_COPPER_GOLD_TOPICS = frozenset(["copper", "gold", "silver", "nickel", "aluminum", "zinc"])

_STEEL_COMPANIES = frozenset(["GERDAU", "TERNIUM", "USIMINAS", "CSN"])
_MINING_COMPANIES = frozenset(["VALE", "CMIN", "SOUTHERN COPPER", "GRUPO MEXICO", "AURA"])
_PP_COMPANIES     = frozenset(["SUZANO", "IRANI", "CMPC", "KLABIN", "COPEC"])


_STEEL_TEXT_RE = re.compile(
    r"\b(steel|aco|siderurgia|siderurgic|iron.?ore|minerio.?de.?ferro|scrap|sucata|"
    r"met.?coal|coking.?coal|rebar|vergalhao|hrc|hot.?rolled|blast.?furnace|alto.?forno|"
    r"utilization|utilizacao|capability.?utilization|aisi)\b",
    re.I,
)
_PP_TEXT_RE = re.compile(
    r"\b(pulp|celulose|paper|papel|tissue|containerboard|occ|old.?corrugated|"
    r"bhkp|nbsk|bekp|pix|foex|eucalyptus|eucalipto)\b",
    re.I,
)


def _classify_sector(topics: list[str], covered: list[str], norm_text: str = "") -> str:
    topic_set = set(topics)
    covered_set = set(covered)

    pp_score    = len(topic_set & _PP_TOPICS) * 2 + len(covered_set & _PP_COMPANIES)
    steel_score = len(topic_set & _STEEL_TOPICS) * 2 + len(covered_set & _STEEL_COMPANIES)
    mining_score = len(topic_set & _MINING_TOPICS) + len(covered_set & _MINING_COMPANIES)

    # Fallback: se nenhum tópico mapeou diretamente para setor, analisa texto bruto
    if pp_score == 0 and steel_score == 0 and mining_score == 0 and norm_text:
        if _STEEL_TEXT_RE.search(norm_text):
            steel_score += 1
        if _PP_TEXT_RE.search(norm_text):
            pp_score += 1

    # Setores dedicados copper/gold: tema puramente de cobre ou ouro, sem
    # aço/minério de ferro/celulose. Mais informativo que "steel_mining".
    _steel_iron = topic_set & (_STEEL_TOPICS | {"iron_ore", "pellets", "sinter"})
    if not _steel_iron and pp_score == 0:
        if "copper" in topic_set and "gold" not in topic_set:
            return "copper"
        if "gold" in topic_set and "copper" not in topic_set:
            return "gold"

    if pp_score > 0 and pp_score >= steel_score and pp_score >= mining_score:
        return "pulp_paper"
    if steel_score > 0 and steel_score >= mining_score:
        return "steel_mining"
    if mining_score > 0:
        return "steel_mining"
    if covered:
        return "company_specific"
    return "unknown"


# ─────────────────────────────────────────────────────────────────────────────
# 5. MOTOR DE TAKE
# ─────────────────────────────────────────────────────────────────────────────

def _compute_take(
    norm: str,
    topics: list[str],
    covered: list[str],
    sector: str,
    region: str,
) -> tuple[str, str, float, list[str]]:
    """
    Calcula take, take_reason, confidence e matched_rules.

    Lógica baseada em score líquido + regras temáticas explícitas.
    Retorna: (take, take_reason, confidence, matched_rules)
    """
    score = 0
    reasons: list[str] = []
    rules: list[str] = []
    confidence_modifiers: list[float] = []

    topic_set = set(topics)
    has_covered = bool(covered)

    # Cláusula primária (descarta subordinada causal "as supply rose").
    primary = _primary_clause(norm)
    # Tópicos presentes nas cláusulas retidas — só esses geram sinal de take.
    # (um tópico só na subordinada causal é contexto/motivo, não sinal próprio.)
    primary_topics = {t for pat, t in TOPIC_NORMALIZATION if pat.search(primary)}
    # Direção ANCORADA ao tópico — passa o norm ORIGINAL (com conectivos) para
    # que _topic_direction_map divida em cláusulas e isole cada direção.
    tdir = _topic_direction_map(norm, primary_topics)
    # Fallback global da cláusula primária.
    pos_p = len(set(re.findall(r"\w+", primary)) & _UP_WORDS)
    neg_p = len(set(re.findall(r"\w+", primary)) & _DOWN_WORDS)

    def d(topic: str) -> int:
        """Direção do tópico. 0 se o tópico não está na cláusula primária
        (está só na subordinada causal → é motivo, não sinal)."""
        if topic not in primary_topics:
            return 0
        v = tdir.get(topic, 0)
        if v != 0:
            return v
        return (pos_p > neg_p) - (pos_p < neg_p)  # +1/-1/0 pelo sinal global

    # ── Helpers ──────────────────────────────────────────────────────────────

    def add(delta: int, reason: str, rule: str, conf: float = 0.85):
        nonlocal score
        score += delta
        reasons.append(reason)
        rules.append(rule)
        confidence_modifiers.append(conf)

    # ── REGRA 0: empresa coberta menciona expansão de capacidade ─────────────
    if has_covered and "capacity" in topic_set and _mentions_capacity_increase(norm):
        add(0, f"Expansão de capacidade por empresa coberta ({', '.join(covered)}); impacto depende do contexto estratégico.",
            "covered_company_capacity_expansion", conf=0.55)
        return ("review",
                f"Expansão de capacidade por empresa coberta ({', '.join(covered)}); impacto depende do contexto estratégico.",
                0.55,
                rules)

    # ── REGRA 1: met coal (custo-insumo) ──────────────────────────────────────
    if "met_coal" in topic_set:
        dd = d("met_coal")
        if dd > 0:
            add(-1, "Alta de met coal pressiona custos das siderúrgicas.", "met_coal_up_neg")
        elif dd < 0:
            add(+1, "Queda de met coal reduz custos das siderúrgicas.", "met_coal_down_pos")
        else:
            add(0, "Movimentação de met coal detectada; direção incerta.", "met_coal_neutral", conf=0.50)

    # ── REGRA 2: scrap (custo-insumo) ─────────────────────────────────────────
    if "scrap" in topic_set:
        dd = d("scrap")
        if dd > 0:
            add(-1, "Alta de scrap pressiona custos.", "scrap_up_neg")
        elif dd < 0:
            add(+1, "Queda de scrap reduz custos.", "scrap_down_pos")

    # ── REGRA 3: OCC (custo-insumo) ──────────────────────────────────────────
    if "occ" in topic_set:
        dd = d("occ")
        if dd > 0:
            add(-1, "Alta de OCC pressiona custos de papel/embalagens.", "occ_up_neg")
        elif dd < 0:
            add(+1, "Queda de OCC reduz custos de papel/embalagens.", "occ_down_pos")

    # ── REGRA 4: demanda ─────────────────────────────────────────────────────
    if "demand" in topic_set:
        dd = d("demand")
        if dd > 0:
            add(+1, "Demanda mais forte indica melhora de mercado.", "demand_up_pos")
        elif dd < 0:
            add(-1, "Demanda mais fraca ou em queda pressiona o mercado.", "demand_down_neg")

    # ── REGRA 5: inventários ─────────────────────────────────────────────────
    if "inventories" in topic_set:
        dd = d("inventories")
        if dd < 0:
            add(+1, "Queda de estoques sugere mercado mais apertado.", "inventories_down_pos")
        elif dd > 0:
            add(-1, "Alta de estoques sugere excesso de oferta ou demanda fraca.", "inventories_up_neg")

    # ── REGRA 6: utilização de capacidade (AISI/US) ───────────────────────────
    if "utilization" in topic_set:
        dd = d("utilization")
        if dd > 0:
            add(+1, "Utilização de capacidade em alta é sinal positivo de mercado.", "utilization_up_pos")
        elif dd < 0:
            add(-1, "Utilização de capacidade em queda é sinal negativo.", "utilization_down_neg")

    # ── REGRA 7: preços de produtos vendidos (HRC, iron ore, pulp, etc.) ─────
    _PRODUCT_TOPICS = {"hrc", "crc", "rebar", "flat_steel", "structural", "slab",
                       "iron_ore", "pellets", "copper", "gold", "pulp", "paper",
                       "tissue", "containerboard"}
    _product_topics = topic_set & _PRODUCT_TOPICS
    if _product_topics and "prices" in topic_set:
        # Direção do preço = direção do produto; se neutro, herda de 'prices'.
        for prod in sorted(_product_topics):
            pd = d(prod) or d("prices")
            if pd > 0:
                add(+1, f"Alta de preços de {prod} beneficia produtores.", f"price_{prod}_up_pos")
            elif pd < 0:
                add(-1, f"Queda de preços de {prod} é negativa para produtores.", f"price_{prod}_down_neg")

    # ── REGRA 8: capacidade de terceiros ─────────────────────────────────────
    if "capacity" in topic_set and not has_covered:
        if _mentions_capacity_increase(norm):
            add(-1, "Aumento de capacidade de empresa não coberta eleva oferta e pode pressionar preços.",
                "third_party_capacity_increase_neg", conf=0.75)
        elif _mentions_capacity_cut(norm):
            add(+1, "Fechamento/redução de capacidade de terceiro reduz oferta.",
                "third_party_capacity_cut_pos", conf=0.80)

    # ── REGRA 9: closure / plant shutdown ────────────────────────────────────
    if "closure" in topic_set and not has_covered:
        add(+1, "Fechamento de planta de terceiro reduz oferta.", "closure_pos", conf=0.75)

    # ── REGRA 10: Turkish rebar exports ──────────────────────────────────────
    if _mentions_turkish_rebar(norm) and "exports" in topic_set:
        dd = d("exports")
        if dd > 0:
            add(-1, "Aumento de exportações de rebar turco eleva competição global.", "turkish_rebar_exports_up_neg")
        elif dd < 0:
            add(+1, "Queda de exportações de rebar turco reduz pressão competitiva.", "turkish_rebar_exports_down_pos")

    # ── REGRA 11: exports genéricos de empresa coberta ────────────────────────
    if "exports" in topic_set and has_covered and not _mentions_turkish_rebar(norm):
        if d("exports") > 0:
            add(+1, f"Aumento de exportações/vendas de empresa coberta ({', '.join(covered)}).",
                "covered_exports_up_pos", conf=0.65)

    # ── REGRA 12: supply/oferta ───────────────────────────────────────────────
    if "supply" in topic_set:
        dd = d("supply")
        if dd > 0:
            add(-1, "Aumento de oferta pode pressionar preços.", "supply_up_neg", conf=0.60)
        elif dd < 0:
            add(+1, "Redução de oferta pode sustentar preços.", "supply_down_pos", conf=0.60)

    # ── REGRA 13: empresa coberta sem regra específica ────────────────────────
    if has_covered and not rules:
        reasons.append(f"Notícia específica de empresa coberta ({', '.join(covered)}); impacto depende do contexto.")
        rules.append("covered_company_generic")
        confidence_modifiers.append(0.50)
        return ("review",
                f"Notícia específica de empresa coberta ({', '.join(covered)}); impacto depende do contexto.",
                0.50,
                rules)

    # ── Score → take ─────────────────────────────────────────────────────────
    if not rules:
        return ("=", "Sem sinal direcional claro detectado.", 0.40, ["no_signal"])

    conf_base = (sum(confidence_modifiers) / len(confidence_modifiers)) if confidence_modifiers else 0.50

    # Penaliza conflito (sinais opostos presentes)
    has_conflict = any(d > 0 for d in [score]) and any(d < 0 for d in [score])
    # score pode ser combinação de múltiplas regras; usa valor absoluto para medir clareza
    abs_score = abs(score)
    if abs_score == 0:
        conf = min(conf_base, 0.50)
        take = "="
        reason = "Sinais conflitantes ou opostos com score líquido zero."
    elif abs_score == 1:
        conf = conf_base * 0.85  # confiança moderada em score 1
        take = "+" if score > 0 else "-"
        reason = "; ".join(reasons)
    else:
        conf = min(conf_base, 0.95)
        take = "+" if score > 0 else "-"
        reason = "; ".join(reasons)

    # Score alto mas confiança baixa → review
    if conf < 0.40:
        take = "review"
        reason = f"Baixa confiança ({conf:.2f}). " + "; ".join(reasons)

    return (take, reason, round(conf, 2), rules)


# ─────────────────────────────────────────────────────────────────────────────
# 6. FUNÇÃO PRINCIPAL
# ─────────────────────────────────────────────────────────────────────────────

def classify_take(text: str, metadata: dict | None = None) -> dict:
    """
    Classifica uma notícia e retorna dicionário com todos os campos do relatório.

    Parâmetros:
        text     — texto consolidado (título + snippet + corpo, o que estiver disponível)
        metadata — dict opcional com chaves como: source_name, content_type, sector, region
    """
    meta = metadata or {}

    # ── Exclusão ──────────────────────────────────────────────────────────────
    exclude, excl_reason = should_exclude_news(text, meta)
    if exclude:
        return {
            "include_in_report":          False,
            "exclusion_reason":           excl_reason,
            "sector":                     "unknown",
            "region":                     "unknown",
            "normalized_topics":          [],
            "covered_companies_mentioned": [],
            "take":                       "=",
            "take_reason":                excl_reason,
            "confidence":                 0.0,
            "matched_rules":              ["excluded"],
        }

    norm = normalize_text(text)
    topics   = detect_topics(text)
    covered  = detect_covered_companies(text)
    region   = detect_region(text)
    sector   = _classify_sector(topics, covered, norm)

    # ── Ajuste de região: empresa coberta brasileira → brazil quando região incerta
    if region == "unknown" and covered:
        _br_covered = _STEEL_COMPANIES | _MINING_COMPANIES | _PP_COMPANIES
        if any(c in _br_covered for c in covered):
            region = "brazil"

    # ── Verificação de relevância pós-setor ──────────────────────────────────
    if sector == "unknown" and not covered:
        return {
            "include_in_report":          False,
            "exclusion_reason":           "no_market_take_detected",
            "sector":                     "unknown",
            "region":                     region,
            "normalized_topics":          topics,
            "covered_companies_mentioned": covered,
            "take":                       "=",
            "take_reason":                "Sem setor ou empresa reconhecida.",
            "confidence":                 0.0,
            "matched_rules":              ["excluded"],
        }

    # ── Exclusão de Europa (P&P) sem empresa coberta ──────────────────────────
    # Exclui se for só demanda local europeia sem benchmark global ou custo relevante.
    if sector == "pulp_paper" and region == "europe" and not covered:
        topic_set_local = set(topics)
        _eu_pp_pass = re.search(
            r"\b(pix|foex|bhkp|nbsk|bekp|pulp.?prices?|pulp.?market|"
            r"celulose.?preco|occ|old.?corrugated|containerboard)\b",
            norm, re.I,
        )
        if not _eu_pp_pass and not (topic_set_local & {"occ", "containerboard", "pulp"}):
            return {
                "include_in_report":          False,
                "exclusion_reason":           "too_specific_europe",
                "sector":                     "pulp_paper",
                "region":                     "europe",
                "normalized_topics":          topics,
                "covered_companies_mentioned": covered,
                "take":                       "=",
                "take_reason":                "Notícia europeia de P&P sem preço de celulose global.",
                "confidence":                 0.0,
                "matched_rules":              ["excluded"],
            }

    # ── Computa take ──────────────────────────────────────────────────────────
    take, take_reason, confidence, matched_rules = _compute_take(
        norm, topics, covered, sector, region
    )

    return {
        "include_in_report":          True,
        "exclusion_reason":           None,
        "sector":                     sector,
        "region":                     region,
        "normalized_topics":          topics,
        "covered_companies_mentioned": covered,
        "take":                       take,
        "take_reason":                take_reason,
        "confidence":                 confidence,
        "matched_rules":              matched_rules,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 7. INTEGRAÇÃO COM PIPELINE (dict e DataFrame)
# ─────────────────────────────────────────────────────────────────────────────

def _build_full_text(art: dict) -> str:
    """Concatena campos disponíveis do artigo para formar texto de classificação."""
    fields = ["title", "headline", "snippet", "summary", "body",
              "commodity", "region", "source_name"]
    parts = [str(art.get(f, "") or "") for f in fields]
    return " ".join(p for p in parts if p)


def classify_article_take(art: dict) -> dict:
    """
    Recebe dict de artigo e retorna o mesmo dict com os campos de take adicionados.
    Preserva todos os campos originais.
    """
    text = _build_full_text(art)
    meta = {
        "source_name":  art.get("source_name", ""),
        "content_type": art.get("content_type", art.get("news_type", "")),
    }
    result = classify_take(text, meta)

    # Serializa listas para string separada por ";" (compatível com Supabase/Excel)
    art["take_topics"]           = "; ".join(result["normalized_topics"])
    art["take_covered_companies"] = "; ".join(result["covered_companies_mentioned"])
    art["take_matched_rules"]    = "; ".join(result["matched_rules"])

    art["include_in_report"]     = result["include_in_report"]
    art["exclusion_reason"]      = result["exclusion_reason"]
    art["take_sector"]           = result["sector"]
    art["take_region"]           = result["region"]
    art["take"]                  = result["take"]
    art["take_reason"]           = result["take_reason"]
    art["take_confidence"]       = result["confidence"]
    return art


def apply_news_classification(df: "pandas.DataFrame") -> "pandas.DataFrame":
    """
    Aplica classify_take a todas as linhas de um DataFrame.
    Adiciona as colunas de classificação sem remover as existentes.
    """
    import pandas as pd

    records = df.to_dict(orient="records")
    classified = [classify_article_take(r) for r in records]
    return pd.DataFrame(classified)
