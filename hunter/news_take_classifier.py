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
        # NÃO incluir o ticker "tx" (NYSE): colide com "TX" = Texas, onipresente
        # em manchetes de aço dos EUA (ex.: "...in Houston, TX"), e o contexto
        # setorial sempre presente nesses textos não desambigua. Notícia real de
        # Ternium sempre soletra "Ternium".
        "ternium", "ternium brasil", "ternium mexico",
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

# Players da indústria que NÃO são da cobertura, mas cujas notícias o analista
# inclui no relatório (peers/comparáveis). Mapeiam para um setor → quando
# mencionados, a notícia ENTRA (mesmo sem tópico) e herda o setor. NÃO acionam a
# lógica de take "company-specific" (essa fica só para empresas cobertas).
# Nomes ambíguos (ex.: "april" mês, "resolute" adjetivo) deliberadamente fora.
INDUSTRY_PLAYERS: dict[str, str] = {
    # Steel & Mining (não cobertos)
    "bhp": "steel_mining", "rio tinto": "steel_mining", "fortescue": "steel_mining",
    "anglo american": "steel_mining", "glencore": "steel_mining",
    "first quantum": "steel_mining", "freeport": "steel_mining",
    "nucor": "steel_mining", "us steel": "steel_mining", "u s steel": "steel_mining",
    "cleveland-cliffs": "steel_mining", "cleveland cliffs": "steel_mining",
    "arcelormittal": "steel_mining", "arcelor mittal": "steel_mining",
    "baowu": "steel_mining", "nippon steel": "steel_mining", "posco": "steel_mining",
    "tata steel": "steel_mining", "hbis": "steel_mining", "simandou": "steel_mining",
    # Pulp & Paper (não cobertos)
    "kimberly-clark": "pulp_paper", "kimberly clark": "pulp_paper",
    "bracell": "pulp_paper", "celulosa argentina": "pulp_paper",
    "solenis": "pulp_paper", "global cellulose fibers": "pulp_paper",
    "asia pulp": "pulp_paper", "indah kiat": "pulp_paper",
    "upm": "pulp_paper", "stora enso": "pulp_paper", "storaenso": "pulp_paper",
    "sappi": "pulp_paper", "domtar": "pulp_paper", "international paper": "pulp_paper",
    "westrock": "pulp_paper", "smurfit": "pulp_paper", "mondi": "pulp_paper",
    "metsa": "pulp_paper", "nine dragons": "pulp_paper", "eldorado": "pulp_paper",
    "valmet": "pulp_paper", "georgia-pacific": "pulp_paper", "essity": "pulp_paper",
}

# Mapa: termo normalizado → tópico canônico
TOPIC_NORMALIZATION: list[tuple[re.Pattern, str]] = []
_TOPIC_RAW: list[tuple[str, str]] = [
    # Steel products
    (r"\b(hrc|hot.?rolled.?coil|bobina.?a.?quente|hot.?roll)\b",         "hrc"),
    (r"\b(crc|cold.?rolled.?coil|bobina.?a.?frio|cold.?roll)\b",         "crc"),
    (r"\b(rebar|vergalhao|barra.?de.?aco|deformed.?bar)\b",              "rebar"),
    (r"\b(wire.?rod|fio.?maquina)\b",                                     "wire_rod"),
    (r"\b(long.?products|longs.?market)\b",                               "structural"),
    (r"\b(billet|tarugo|bloom)\b",                                        "billet"),
    (r"\b(slab|placa.?de.?aco|steel.?slab)\b",                           "slab"),
    (r"\b(plate|chapa.?grossa|heavy.?plate|plates)\b",                   "plate"),
    (r"\b(flat.?steel|laminados.?planos|flat.?product|flat.?products)\b", "flat_steel"),
    (r"\b(structural.?steel|aco.?estrutural|beam|beams|merchant.?bar)\b", "structural"),
    (r"\b(pig.?iron|ferro.?gusa)\b",                                      "pig_iron"),
    # Raw materials (mining / inputs)
    (r"\b(iron.?ore|minerio.?de.?ferro|iodex|tsi.?62|iron ore price)\b", "iron_ore"),
    (r"\b(mining|miner|minerals?|mineradora|mineracao|mineral.?demand)\b",  "mining"),
    (r"\b(pellet|pellets|pellet.?premium|pelotas?)\b",                   "pellets"),
    (r"\b(sinter.?feed|lump.?ore)\b",                                    "sinter"),
    (r"\b(met.?coal|coking.?coal|carvao.?metalurgico|carvao.?coqueificavel|coke)\b", "met_coal"),
    (r"\b(scrap|sucata|ferrous.?scrap|steel.?scrap|sucata.?ferrosa)\b",  "scrap"),
    (r"\b(dri|direct.?reduced.?iron|hbi)\b",                             "dri"),
    # Pulp & Paper
    (r"\b(pulp|celulose|bhkp|nbsk|bekp|bek|bskp|bctmp|woodpulp|pulpwood|kraft.?pulp|hardwood.?pulp|softwood.?pulp|dissolving.?pulp|fluff.?pulp)\b", "pulp"),
    (r"\b(papers?|papel)(?!.?(board|ondulado|embalagem|kraft|tissue))\b", "paper"),
    (r"\b(tissue|papel.?tissue|papel.?higienico|jumbo.?roll)\b",          "tissue"),
    (r"\b(containerboard|papelao.?ondulado|corrugated|caixas.?onduladas|kraftliner|linerboard|white.?top|white.?board|testliner|box.?shipments?|boxboard|cartonboard|fluting)\b", "containerboard"),
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
    (r"\b(inventories|inventory|stocks|estoques?|inventario)\b",         "inventories"),
    (r"\b(prices?|preco|precos|premium|premiums|index|indices)\b",        "prices"),
    (r"\b(utilization|utilizacao|capability.?utilization|capacity.?utilization|aisi)\b", "utilization"),
    (r"\b(tariff|tariffs|tarifa|anti.?dumping|antidumping|safeguard|section.?232|"
     r"export.?tax|countervailing|\bcvd\b|trade.?(war|barrier|measure|case|probe)|"
     r"import.?(duty|duties|quota|ban)|levy|dumping.?(probe|duty|case))\b",        "tariffs"),
    # "steel" sozinho não é tópico; indústria/setor/usina/política siderúrgica
    # (sem produto específico) entra como notícia de steel com take neutro.
    (r"\b(steel.?industry|steel.?sector|steel.?mill|steelmaker|steel.?maker|steelmaking|siderurgi\w*|decarboni\w*|green.?steel|net.?zero|carbon.?(border|tax|market|emissions?)|emissions?.?(standard|target|reduction))\b", "steel_industry"),
    # Macro / demanda-driver (sobretudo China): entra no relatório com take.
    (r"\b(econom\w+|macroeconom\w*|gdp|pmi|property\s?(sector|market|woes|prices|investment|developer\w*)?|"
     r"real\s?estate|housing|infrastructure|stimulus|fiscal\s?(stimulus|spending|package)?|monetary|"
     r"interest\s?rate|central\s?bank|rate\s?(cut|hike)|inflation|deflation|recession|reopen\w*|lockdown|"
     r"covid|pandemic|manufacturing\s?(pmi|activity|sector|output)?|industrial\s?(production|output|activity)|"
     r"construction\s?(activity|demand|sector|starts)|trade\s?(deal|talks|tensions?))\b", "macro"),
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
    "strengthening", "strengthened", "robust", "resilient", "outperform", "outperforms",
    # formas no passado / termos de mercado que faltavam (gabarito Platts + P&P)
    "increased", "gained", "bullish", "firmed",
    # anúncios de preço de produtor (price hike / raises prices) → alta
    "raise", "raises", "raised", "hike", "hikes", "hiked",
    # PT
    "sobe", "subiu", "alta", "aumento", "maior", "maiores", "melhora", "melhor",
    "recuperacao", "avanco", "forte", "fortalecendo", "crescimento",
    "elevacao", "elevou", "impulsionado", "acelerou", "aquecimento",
    # conjugações que faltavam (imprensa BR: "preços sobem/ganham/avançam…")
    "sobem", "subindo", "subir", "ganha", "ganham", "ganhando", "avanca", "avancam",
    "avancando", "impulsiona", "impulsionam", "impulso", "fortalece", "fortalecem",
    "valoriza", "valorizam", "valorizacao", "dispara", "disparam", "recupera",
    "recuperam", "recuperando", "cresce", "crescem", "crescendo", "acelera",
    "aceleram", "reage", "reagem", "sustenta", "sustentam", "aquecido", "aquecida",
    "aquece", "aquecem", "elevam", "eleva", "aumenta", "aumentam", "aumentando",
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
    # formas no passado / termos de mercado que faltavam (gabarito Platts)
    "decreased", "declined", "bearish", "softened",
    "slip", "slips", "slipped", "slipping",
    "dip", "dips", "dipped", "dipping",
    "slide", "slides", "slid", "sliding",
    "deteriorate", "deteriorates", "deteriorated", "deteriorating",
    # PT
    "cai", "caiu", "queda", "reducao", "menor", "menores", "piora", "fraco",
    "enfraquecendo", "recuo", "baixa", "retrocesso", "declinio", "colapso",
    "pressao", "pressionar", "despencou", "afundou",
    # conjugações que faltavam (imprensa BR: "preços caem/recuam/cedem…")
    "caem", "caindo", "cair", "recua", "recuam", "recuando", "despenca", "despencam",
    "despencando", "desaba", "desabam", "cede", "cedem", "enfraquece", "enfraquecem",
    "enfraquecer", "pioram", "piorando", "desacelera", "desaceleram", "desaceleracao",
    "tomba", "tombam", "afunda", "afundam", "encolhe", "encolhem", "derrete",
    "derretem", "diminui", "diminuem", "reduz", "reduzem", "cairam",
])

# Marcadores de estabilidade/mercado misto: quando presentes e o sinal direcional
# é fraco (|score| <= 1), o analista trata como NEUTRO ("=") — ele lê "remains
# stable, but higher prices" como estável, não como alta. (gabarito Platts)
_NEUTRAL_MARKER_RE = re.compile(
    r"\b(mixed|unchanged|stable|steady|stabiliz\w*|range.?bound|sideways|"
    r"little.?changed|little.?change|broadly.?(stable|flat)|essentially.?flat|"
    r"largely.?(stable|flat|unchanged)|hold(s)?.?steady|"
    r"estavel|estaveis|inalterad\w*|sem.?alteracao|de.?lado)\b"
    # "flat" como estabilidade — NÃO casar "flat steel/product/rolled" (é o produto)
    r"|\bflat\b(?!\s*(steel|product|products|rolled|roll|carbon|bar))",
    re.I,
)

# Movimento de preço quantificado (cifra, /tonelada ou %): sinal concreto que o
# analista prioriza mesmo com outro grade "flat" → NÃO neutralizar nesses casos.
_QUANTIFIED_MOVE_RE = re.compile(
    r"(\$|us\$|r\$|real|eur|€|rmb|yuan)\s?\d"
    r"|\d+\s?(/mt|/t|per\s?tonne|per\s?ton|/tonne|/lb)"
    r"|\d+(\.\d+)?\s?%",
    re.I,
)

# Sinais de que a manchete fala de PREÇO do produto (não de volume/estoque).
# Permitem disparar a regra de preço sem exigir a palavra literal "prices", mas
# sem capturar direção que pertence a inventories/production/exports.
_PRICE_SIGNAL_RE = re.compile(
    r"\b(price|prices|priced|pricing|premium|premiums|market|markets|"
    r"rally|rallies|rallied|bullish|bearish|offer|offers|bid|bids|"
    r"assessment|assessed|fob|cfr|cif|exw|spot|index|indices|hike|hikes|"
    r"quotation|quoted|quotes)\b", re.I,
)
# Tópicos de QUANTIDADE: quando presentes, a direção provavelmente é deles
# (estoque/produção/fluxo), não do preço — então a regra de preço só dispara se
# houver sinal explícito de preço acima.
_QUANTITY_TOPICS = frozenset({
    "inventories", "production", "exports", "imports", "supply", "demand",
    "capacity", "utilization",
})

# Direção de notícia MACRO (demanda-driver). Gabarito: macro entra com take;
# discriminador-chave é a RESSALVA/condicional (offset) → neutro.
_MACRO_OFFSET_RE = re.compile(
    r"\b(but|despite|however|yet|though|although|even as|no clarity|unclear|"
    r"uncertain\w*|loom\w*|may |might |could |would |limited|muted|mixed|"
    r"cautio\w*|await\w*|wait|seen |expected|likely|outlook|forecast|"
    r"mas|porem|apesar|incert\w*)\b", re.I,
)
_MACRO_POS_RE = re.compile(
    r"\b(stimulus|stimulat\w*|easing|eases|reopen\w*|recover\w*|rebound\w*|"
    r"growth|grow\w*|expansion|expand\w*|boost\w*|accelerat\w*|pickup|pick up|"
    r"upturn|improv\w*|resilient|robust|special bonds?|rate cut|"
    r"estimulo|retomada|recuperac\w*|crescimento)\b", re.I,
)
_MACRO_NEG_RE = re.compile(
    r"\b(slowdown|slowing|slows|contraction|contract\w*|recession|woes|"
    r"weak\w*|sluggish|subdued|downturn|deflation\w*|crisis|slump\w*|curb\w*|"
    r"cooling|cools?|stall\w*|debt crisis|default\w*|lockdown|"
    r"desaceler\w*|crise|fraqueza)\b", re.I,
)

# Termos de exclusão automática (tipo de conteúdo). Casam por SUBSTRING, e só
# contra TÍTULO / ContentType / fonte (ver _is_rationale).
# "pricing rational" (sem o "e") NÃO é erro nosso: é como a Fastmarkets passou a
# escrever o título da série semanal "Pricing Rationale: NBSK CIF China" a partir
# de 07/08/2026. As edições de 14/08 e 21/08 só ficaram de fora porque a palavra
# "rationale" aparecia no CORPO delas — bloqueio por acidente, pelo mesmo defeito
# que derrubava notícia boa; a de 07/08, sem a palavra no corpo, vazou para a
# dashboard. Com o escopo corrigido, a grafia errada precisa ser explícita.
_EXCLUDE_CONTENT_TYPES = frozenset(["rationale", "pricing rational"])

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
    r"futebol|futbol|copa do mundo|world cup|shakira|"
    r"jogador|jogadores|estadio|"
    r"selecao brasileira|selecao de futebol|partida de futebol|"
    r"eleic|election|elecciones|amlo|cnte|tepjf"
    r")(?!\w)", re.I,
)
# NOTA: "mundial" foi REMOVIDO daqui — em PT/ES = "global" ("produção mundial de
# aço", "mercado mundial de celulose" são ON-TOPIC). A Copa é coberta por
# "copa do mundo|world cup". "selecao"/"partida" soltos também saíram (excluíam
# "seleção de fornecedores", "partida da usina" = startup de alto-forno). A rede
# de segurança no_market_take_detected ainda barra futebol genuíno sem take.

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

# Finanças pessoais / consumo (varejo): clickbait que pega carona em "economia"/
# alias ambíguo (ex.: "vale a pena investir nos CDBs"). NUNCA é research de
# S&M/P&P. Termos sem acento (normalize_text remove acento). Exclui sem coberta.
_PERSONAL_FINANCE_RE = re.compile(
    r"(?<!\w)("
    r"cdb|cdbs|renda fixa|tesouro direto|tesouro selic|\blci\b|\blca\b|"
    r"poupanca|previdencia privada|fundos? imobiliario|\bfii\b|\bfiis\b|"
    r"cartao de credito|emprestimo pessoal|"
    # "consorcio" e "financiamento" só na acepção de CONSUMO (carro/imóvel) — o
    # consórcio EMPRESARIAL (ex.: "Consórcio da K-Infra vence Rota da Celulose") é legítimo.
    r"(consorcio|financiamento) (de |do |da )?(carro|imovel|imoveis|veiculo|casa|moto)|"
    r"vale a pena (investir|comprar|ter|abrir|fazer|contratar)|"
    r"quanto rende|rende mais|melhores investimentos|onde investir|como investir|"
    r"black friday|cashback|milhas aereas|nota do enem"
    r")(?!\w)", re.I,
)

# Tópicos de aço de baixo valor para o relatório (regra Platts "NÃO COLOCAR":
# billet, plates, wire rod, pig iron). Excluídos quando são o ASSUNTO PRIMÁRIO —
# i.e. só eles + modificadores genéricos e sem empresa coberta. Se vierem
# secundários a um tópico relevante (ex.: HRC), a notícia entra normalmente.
# Gabarito 8.639: wire rod e pig iron APARECEM com take (são produtos vendidos —
# wire rod segue rebar; pig iron export price → +). Saíram da exclusão de baixo
# valor; billet/plate seguem excluídos como assunto primário (regra Platts).
_LOW_VALUE_STEEL_TOPICS = frozenset(["billet", "plate"])

# Modificadores genéricos: sozinhos não elevam a relevância de um tópico de baixo
# valor (preço/produção/exportação de "billet" continua sendo notícia de billet).
_GENERIC_MODIFIER_TOPICS = frozenset([
    "prices", "production", "exports", "imports", "demand", "supply", "inventories",
])

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
#   'aura'  → palavra comum ("aura de mercado")
# Só contam como menção à empresa se houver contexto setorial/financeiro no texto.
# (O ticker "tx" da Ternium foi REMOVIDO da lista de aliases — colide com "TX" =
#  Texas e o contexto setorial não desambigua em notícia de aço dos EUA.)
_AMBIGUOUS_ALIAS_WORDS = frozenset({"vale", "aura"})

# Contexto que confirma que a menção é realmente à empresa
_COMPANY_CONTEXT_RE = re.compile(
    r"(?<!\w)("
    r"vale3|cmin3|ggbr4|usim5|csna3|suzb3|rani3|klbn11|aura33|"          # tickers BR
    r"minerio|mineradora|mineracao|ferro|pelota|niquel|cobre|ouro|"       # mineração
    r"aco|siderurg|steel|iron|mining|mineral|smelter|"                    # steel/mining
    r"celulose|pulp|paper|papel|"                                         # P&P
    r"acoes|acao|bolsa|b3|dividendo|balanco|resultado|trimestre|"         # finanças BR
    r"shares|stock|equity|earnings|quarter|production|producao|"          # finanças EN
    r"exports?|exportac|demanda|demand|capacidade|capacity|"              # mercado
    r"mine|mines|output|shipments?|operations|operacao|shutdown|"         # operacional
    r"tailings|barragem|dam|port|porto|refinery|refinaria"               # ativos/instalações
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


def detect_industry_players(text: str) -> dict[str, str]:
    """Players da indústria (não cobertos) mencionados → {nome: setor}.

    Usado só para a decisão de INCLUSÃO e dica de setor — não entra em
    covered_companies_mentioned nem aciona take company-specific.
    """
    norm = normalize_text(text)
    found: dict[str, str] = {}
    for name, sector in INDUSTRY_PLAYERS.items():
        pattern = r"(?<!\w)" + re.escape(name) + r"(?!\w)"
        if re.search(pattern, norm):
            found[name] = sector
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


# Negadores (texto já normalizado: sem apóstrofo → "haven't"→"haven t"; por isso
# os radicais hasn/haven/didn/etc. entram soltos). Negador até ~18 chars ANTES da
# palavra de direção inverte a polaridade: "demand has NOT fallen" → alta, não queda.
_NEG_LOOKBACK = re.compile(
    r"\b(not|no|never|without|hardly|barely|nor|"
    r"hasn|haven|hadn|didn|doesn|don|won|isn|aren|wasn|weren|cannot|cant|wont|"
    r"nao|sem|fails?\s+to|failed\s+to|fail\s+to|yet\s+to|unlikely\s+to)\b"
    r"[\w\s]{0,18}$", re.I,
)


def _negated(text: str, pos: int) -> bool:
    return bool(_NEG_LOOKBACK.search(text[:pos]))


def _direction_positions(text: str) -> tuple[list[int], list[int]]:
    """Posições (char) das palavras de direção up e down. Negação inverte a
    polaridade (ex.: 'demand has not fallen' conta como ALTA)."""
    ups, downs = [], []
    for m in re.finditer(r"\w+", text):
        w = m.group(0)
        neg = _negated(text, m.start())
        if w in _UP_WORDS:
            (downs if neg else ups).append(m.start())
        elif w in _DOWN_WORDS:
            (ups if neg else downs).append(m.start())
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
    """True se `text` identifica um documento "Rationale" (metodologia de preço).

    ⚠️ Casa por SUBSTRING nua, então só pode receber TÍTULO, ContentType ou nome
    da fonte — NUNCA o corpo/snippet do artigo. Uma notícia legítima que apenas
    *mencione* a palavra ("producers raise prices without clear rationale") não é
    um Rationale, e já foi barrada indevidamente por isso. Quem chama garante o
    escopo (ver should_exclude_news).
    """
    norm = normalize_text(text)
    if any(t in norm for t in _EXCLUDE_CONTENT_TYPES):
        return True
    if "rationale" in normalize_text(source):
        return True
    return False


def _mentions_restart(norm: str) -> bool:
    """Retomada/restart de capacidade (re-adiciona oferta → negativo)."""
    return bool(re.search(
        r"\b(restart\w*|resum\w*|recommission\w*|back.?online|fires?.?up|ramp.?up|ramp.?back)\b"
        r".{0,35}(mill|plant|furnace|line|production|capacity|machine|operation|output)|"
        r"(mill|plant|furnace|line|production|capacity|machine|operation)"
        r".{0,35}\b(restart\w*|resum\w*|recommission\w*|back.?online)\b",
        norm, re.I,
    ))


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

# Fontes de baixo aproveitamento (jornais amplos BR, 2-11% viram take): mantêm o
# PORTÃO APERTADO — no_market_take_detected segue EXCLUINDO (protege a cota das IAs
# do ruído). As demais (curadas/setoriais) têm o portão AFROUXADO: o que o robô não
# entende vira INCLUSÃO p/ a IA decidir (ela tem "no take"). Auditoria 2026-06-25.
_LOW_YIELD_SOURCES = frozenset([
    "metropoles", "g1 economia", "veja", "exame", "uol economia",
])
def _gate_is_tight(source: str) -> bool:
    """True = portão apertado (tabloide BR): exclui o que não tem take. False =
    fonte curada/setorial: deixa entrar p/ a IA decidir (ela tem 'no take')."""
    return normalize_text(source or "") in _LOW_YIELD_SOURCES


# Fontes CURADAS/DEDICADAS (terminais pagos que o analista escolheu a dedo): por decisão
# de negócio, TODA notícia entra no relatório — o classificador NÃO exclui por conteúdo,
# região ou baixa relevância (ex.: P&P europeu 'too_specific_europe', off-topic). SÓ
# "Rationale" (metodologia de preço) fica de fora — regra do usuário, idêntica ao Platts.
# (2026-08-03: Fastmarkets entrou a pedido do usuário — "não precisa ter filtro"; Platts
# já seguia essa regra, mas o classificador ainda vazava algumas exclusões de conteúdo.)
# 2026-09-02: "cvm" = comunicados oficiais das cobertas (fato relevante etc., via Market Watch) —
# fonte PRIMÁRIA; entra sempre.
_ALWAYS_INCLUDE_SOURCES = frozenset({"s&p platts", "fastmarkets", "cvm"})


def _is_curated_source(source: str) -> bool:
    """True se a fonte é curada → toda notícia entra, exceto Rationale."""
    return (source or "").strip().lower() in _ALWAYS_INCLUDE_SOURCES


def should_exclude_news(text: str, metadata: dict) -> tuple[bool, str]:
    """
    Decide se a notícia deve ser excluída do relatório.

    Retorna (deve_excluir, motivo).
    """
    source = metadata.get("source_name", "") or metadata.get("source", "") or ""
    content_type = metadata.get("content_type", "") or metadata.get("news_type", "") or ""

    # 1. Rationale automático — escopo: TÍTULO + ContentType + fonte, nunca o corpo.
    #    Coerente com as outras 2 camadas (platts_scraper._type_allowed via ContentType
    #    e filter.SOURCE_FILTER_RULES['S&P Platts']['title_exclude'] via título).
    #    Buscar "rationale" no texto inteiro derrubava notícia boa cujo corpo só citava
    #    a palavra — e, em fonte curada, rationale_news é a ÚNICA exclusão que barra
    #    (ver classify_take), então o falso positivo sumia da dash e do clipping.
    #    `title` ausente (None) = chamada legada que passa só o título em `text`.
    _title = metadata.get("title")
    rationale_scope = text if _title is None else _title
    if _is_rationale(rationale_scope, source) or _is_rationale("", content_type):
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

    # 2c. Finanças pessoais / consumo (CDB, renda fixa, "vale a pena investir/comprar",
    # cartão de crédito, financiamento de carro/imóvel…) sem empresa coberta → clickbait
    # de varejo, não é research setorial. Snippet de "economia" fazia esses entrarem.
    if _PERSONAL_FINANCE_RE.search(norm) and not covered:
        return True, "personal_finance"

    # 3. Sem tópicos, sem empresa coberta E sem player de indústria → baixa relevância.
    #    PORTÃO AFROUXADO (2026-06-25): só EXCLUI nas fontes de baixo aproveitamento
    #    (tabloides BR). Nas curadas/setoriais NÃO exclui aqui — segue p/ classify_take,
    #    que deixa entrar p/ a IA decidir (ela tem "no take"). Mata as exclusões falsas.
    if not topics and not covered and not detect_industry_players(text):
        if _gate_is_tight(source):
            return True, "no_market_take_detected"

    # 3+4. Tópicos de aço de baixo valor (pig iron, billet, wire rod, plate) como
    #      ASSUNTO PRIMÁRIO — só eles + modificadores genéricos e sem empresa
    #      coberta → fora do relatório (regra Platts "NÃO COLOCAR"). Se vierem
    #      secundários a um tópico relevante (ex.: HRC), a notícia entra.
    topic_set = set(topics)
    if (topic_set & _LOW_VALUE_STEEL_TOPICS
            and topic_set.issubset(_LOW_VALUE_STEEL_TOPICS | _GENERIC_MODIFIER_TOPICS)
            and not covered):
        reason = "irrelevant_commodity" if "pig_iron" in topic_set else "low_relevance"
        return True, reason

    return False, ""


# ─────────────────────────────────────────────────────────────────────────────
# 4. CLASSIFICAÇÃO DE SETOR
# ─────────────────────────────────────────────────────────────────────────────

_STEEL_TOPICS = frozenset([
    "hrc", "crc", "rebar", "wire_rod", "billet", "slab", "plate",
    "flat_steel", "structural", "pig_iron", "iron_ore", "pellets",
    "sinter", "met_coal", "scrap", "dri", "furnace", "utilization",
    "tariffs",          # comércio/antidumping/section 232 é tema central de steel
    "steel_industry",   # indústria/setor/usina/política siderúrgica (sem produto)
    "macro",            # macro/demanda-driver (China property/stimulus/PMI/…) entra como steel
])
_MINING_TOPICS = frozenset([
    "iron_ore", "pellets", "copper", "gold", "silver", "nickel", "aluminum",
    "zinc", "sinter", "mining",
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
    raw_text: str = "",
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

    # ── REGRA 0: expansão de capacidade de empresa coberta → NEUTRO ───────────
    # Gabarito: 89% dos casos de coberta+expansão são "=" (evento estratégico de
    # longo prazo, não sinal de preço de curto prazo). Cai no covered_generic ("=")
    # mais adiante. (NÃO usar "review" — sempre conta como erro no eval.)

    # ── REGRA 1: met coal (custo-insumo) ──────────────────────────────────────
    if "met_coal" in topic_set:
        dd = d("met_coal")
        if dd > 0:
            add(-1, "Alta de met coal pressiona custos das siderúrgicas.", "met_coal_up_neg")
        elif dd < 0:
            add(+1, "Queda de met coal reduz custos das siderúrgicas.", "met_coal_down_pos")
        else:
            add(0, "Movimentação de met coal detectada; direção incerta.", "met_coal_neutral", conf=0.50)

    # ── REGRA 2: scrap — majoritariamente NEUTRO (gabarito 8.639) ─────────────
    # Scrap como sujeito é ~58-73% neutro no gabarito; a inversão global tinha 43%
    # de erro. Só inverte de forma confiável no mercado doméstico dos EUA (8/8).
    # TR/BR/global → sem sinal direcional (cai para neutro). Quando scrap é só
    # driver de um produto (rebar "amid strong scrap"), o produto é quem manda.
    if "scrap" in topic_set and region == "us":
        dd = d("scrap")
        if dd > 0:
            add(-1, "Alta de scrap doméstico (US) pressiona custos.", "scrap_up_neg_us")
        elif dd < 0:
            add(+1, "Queda de scrap doméstico (US) reduz custos.", "scrap_down_pos_us")

    # ── REGRA 3: OCC — só QUEDA→+ (gabarito: alta é ambígua, NÃO é -) ─────────
    # OCC up no gabarito é 39%+/25%- (não negativo); occ_up_neg tinha 62% de erro.
    # Mantém só o lado limpo (queda de custo de aparas → alívio → +).
    if "occ" in topic_set:
        dd = d("occ")
        if dd < 0:
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
    # NOTA: "slab" deliberadamente FORA — regra Platts marca placa (slab) como
    # NEUTRO. Continua sendo detectado/incluído, mas não gera take direcional.
    _PRODUCT_TOPICS = {"hrc", "crc", "rebar", "wire_rod", "pig_iron", "flat_steel",
                       "structural", "iron_ore", "pellets", "copper", "gold",
                       "pulp", "paper", "tissue", "containerboard"}
    _product_topics = topic_set & _PRODUCT_TOPICS
    # NÃO exige mais a palavra literal "prices" (a regra antiga perdia "HRC
    # bullish", "iron ore market rises", "pellet premiums strong" → achatava
    # centenas de manchetes p/ "="). Dispara quando há SINAL DE PREÇO explícito
    # (price/market/premium/offers/rally/index/hike/bullish…) OU quando não há
    # tópico de QUANTIDADE que reivindique a direção — assim "iron ore inventories
    # rise" continua sendo lido como estoque (REGRA 5), não como preço.
    _price_signal = bool(_PRICE_SIGNAL_RE.search(norm))
    _quantity_present = bool(topic_set & _QUANTITY_TOPICS)
    if _product_topics and (_price_signal or not _quantity_present):
        for prod in sorted(_product_topics):
            pd = d(prod) or (d("prices") if "prices" in topic_set else 0)
            if pd > 0:
                add(+1, f"Alta de preços de {prod} beneficia produtores.", f"price_{prod}_up_pos")
            elif pd < 0:
                add(-1, f"Queda de preços de {prod} é negativa para produtores.", f"price_{prod}_down_neg")

    # ── REGRA 7b: P&P — fluxo de produto final (shipments/output) = proxy demanda ──
    # Gabarito: "US box shipments fall" / "containerboard output drops" → demanda
    # de embalagem em queda → -. Subida → +.
    if sector == "pulp_paper" and (topic_set & {"paper", "tissue", "containerboard", "pulp"}):
        for q in ("exports", "production"):
            if q in topic_set:
                qd = d(q)
                if qd > 0:
                    add(+1, "Fluxo/produção de produto P&P em alta (proxy de demanda).", f"pp_{q}_up_pos", conf=0.55)
                elif qd < 0:
                    add(-1, "Fluxo/produção de produto P&P em queda (proxy de demanda).", f"pp_{q}_down_neg", conf=0.55)
                break

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

    # ── REGRA 9b: restart/retomada de capacidade de terceiro → re-oferta → - ──
    # Gabarito P&P: restart/resume/fires up de máquina/usina re-adiciona oferta → -.
    if _mentions_restart(norm) and not has_covered:
        add(-1, "Retomada/restart de capacidade de terceiro re-adiciona oferta.",
            "third_party_restart_neg", conf=0.65)

    # ── REGRA 10: Turkish rebar — é PRODUTO, não inversão (gabarito 8.639) ────
    # A regra antiga (exports up→- / down→+) tinha 68-84% de ERRO. Rebar turco é
    # produto vendido no mercado global: a direção de PREÇO já é capturada pela
    # REGRA 7 (up→+, down→-), verificada 13/13 + 16/16 no gabarito. Inversão
    # REMOVIDA; a neutralização regional (rest_of_world) segue isentando turkish
    # rebar para preservar essa direção de produto.

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

    # ── REGRA 14: MACRO / demanda-driver (China etc.) ────────────────────────
    # Macro entra no relatório com take (gabarito: NÃO excluir). Discriminador:
    # ressalva/condicional (but/despite/may/loom/limited/expected) → neutro;
    # senão direção macro (estímulo/retomada/crescimento → +; desaceleração/
    # crise/property woes → -). Só dirige o take quando não há produto na manchete.
    if "macro" in topic_set and not (topic_set & _PRODUCT_TOPICS):
        if _MACRO_OFFSET_RE.search(norm):
            add(0, "Macro com ressalva/condicional — sem leitura direcional.", "macro_offset_neutral", conf=0.50)
        elif _MACRO_NEG_RE.search(norm):
            add(-1, "Macro desfavorável à demanda do setor.", "macro_down_neg", conf=0.60)
        elif _MACRO_POS_RE.search(norm):
            add(+1, "Macro favorável à demanda do setor.", "macro_up_pos", conf=0.60)
        else:
            add(0, "Notícia macro/setorial incluída sem direção clara.", "macro_neutral", conf=0.45)

    # ── REGRA 13: empresa coberta sem regra específica ────────────────────────
    # Regra Platts: "Company Specifics (=)" → take NEUTRO. (Capacidade de empresa
    # coberta — REGRA 0 — continua "review" por ser evento estratégico.)
    if has_covered and not rules:
        reasons.append(f"Notícia específica de empresa coberta ({', '.join(covered)}); sem sinal direcional de mercado.")
        rules.append("covered_company_generic")
        confidence_modifiers.append(0.50)
        return ("=",
                f"Notícia específica de empresa coberta ({', '.join(covered)}); sem sinal direcional de mercado.",
                0.50,
                rules)

    # ── Score → take ─────────────────────────────────────────────────────────
    if not rules:
        return ("=", "Sem sinal direcional claro detectado.", 0.40, ["no_signal"])

    conf_base = (sum(confidence_modifiers) / len(confidence_modifiers)) if confidence_modifiers else 0.50

    # score pode ser combinação de múltiplas regras; usa valor absoluto para medir
    # clareza. Conflito (sinais opostos) já é tratado pelo ramo abs_score==0 abaixo.
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

    # Marcador de estabilidade (stable/flat/mixed/unchanged) + sinal fraco → neutro.
    # Não sobrepõe sinais fortes (|score| >= 2) nem movimentos quantificados ($/t, %).
    # quantified check no texto CRU (normalize_text remove $, %, / e perde o sinal)
    if abs_score <= 1 and _NEUTRAL_MARKER_RE.search(norm) and not _QUANTIFIED_MOVE_RE.search(raw_text or norm):
        return ("=",
                "Mercado estável/misto (marcador de estabilidade); sinal direcional fraco.",
                round(min(conf_base, 0.60), 2),
                rules + ["neutral_marker"])

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
    _curated = _is_curated_source(meta.get("source_name", "") or meta.get("source", ""))

    # ── Exclusão ──────────────────────────────────────────────────────────────
    exclude, excl_reason = should_exclude_news(text, meta)
    # Fonte curada (Platts/FM): SÓ Rationale barra; qualquer outra exclusão de conteúdo
    # (região europeia/off-topic/baixa-relevância) é ignorada → toda notícia da fonte entra.
    if _curated and exclude and excl_reason != "rationale_news":
        exclude = False
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
    industry = detect_industry_players(text)
    region   = detect_region(text)
    sector   = _classify_sector(topics, covered, norm)

    # ── Player de indústria (não coberto) define o setor quando indeterminado ─
    if sector == "unknown" and industry:
        sector = next(iter(industry.values()))

    # ── Ajuste de região: empresa coberta brasileira → brazil quando região incerta
    if region == "unknown" and covered:
        _br_covered = _STEEL_COMPANIES | _MINING_COMPANIES | _PP_COMPANIES
        if any(c in _br_covered for c in covered):
            region = "brazil"

    # ── Verificação de relevância pós-setor ──────────────────────────────────
    # PORTÃO AFROUXADO (2026-06-25): tabloides BR continuam EXCLUINDO; curadas/
    # setoriais ENTRAM p/ a IA decidir (sector 'unknown', take robô '=' placeholder —
    # o take PUBLICADO é o take_llm; o robô não baliza o pulse). Mata exclusões falsas.
    if sector == "unknown" and not covered and not industry:
        _src = meta.get("source_name", "") or meta.get("source", "") or ""
        if _gate_is_tight(_src):
            return {
                "include_in_report":          False,
                "exclusion_reason":           "no_market_take_detected",
                "sector":                     "unknown",
                "region":                     region,
                "normalized_topics":          topics,
                "covered_companies_mentioned": covered,
                "take":                       "=",
                "take_reason":                "Sem setor ou empresa reconhecida (fonte de baixo aproveitamento).",
                "confidence":                 0.0,
                "matched_rules":              ["excluded"],
            }
        return {
            "include_in_report":          True,
            "exclusion_reason":           None,
            "sector":                     "unknown",
            "region":                     region,
            "normalized_topics":          topics,
            "covered_companies_mentioned": covered,
            "take":                       "=",
            "take_reason":                "Roteada p/ a IA decidir (sem tópico do robô; fonte curada).",
            "confidence":                 0.0,
            "matched_rules":              ["llm_review"],
        }

    # ── Exclusão de Europa (P&P) sem empresa coberta ──────────────────────────
    # Exclui se for só demanda local europeia sem benchmark global ou custo relevante.
    if sector == "pulp_paper" and region == "europe" and not covered and not _curated:
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
        norm, topics, covered, sector, region, raw_text=text
    )

    # ── Override regional (regra Platts) ──────────────────────────────────────
    # Steel/mining da Europa ou "resto do mundo" (exceto Turkish rebar) e sem
    # empresa coberta: MANTÉM no relatório, porém com take NEUTRO. Regiões-foco
    # (China/Ásia, US, Brasil) e notícias globais preservam o sinal direcional.
    if (sector != "pulp_paper"
            and region in ("europe", "rest_of_world")
            and not covered
            and not _mentions_turkish_rebar(norm)
            and take in ("+", "-")):
        take = "="
        take_reason = ("Europa/outras regiões sem empresa coberta — mantida no "
                       "relatório como neutra (regra regional Platts).")
        confidence = min(confidence, 0.50)
        matched_rules = list(matched_rules) + ["region_neutral"]

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
    # NÃO incluir 'source_name': o NOME da fonte ("Mining.com", "Portal Celulose",
    # "Siderurgia Brasil") injetava tópico/setor falso em TODO artigo da fonte.
    # O source ainda é avaliado à parte (metadata) p/ excluir rationale da Platts.
    fields = ["title", "headline", "snippet", "summary", "body",
              "commodity", "region"]
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
        # Escopo do bloqueio de Rationale (chave sempre presente, mesmo vazia).
        "title":        art.get("title") or art.get("headline") or "",
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
