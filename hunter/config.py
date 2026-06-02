"""Keywords e configurações do News Hunter — S&M e P&P."""

# ── Empresas Brasileiras ───────────────────────────────────────────────────────
COMPANIES_BR = [
    "VALE", "Vale S.A.", "Vale mining",
    "CSN", "Companhia Siderúrgica Nacional",
    "Gerdau", "GGBR",
    "Usiminas", "USIM",
    "Klabin", "KLBN",
    "Suzano", "SUZB",
    "Eldorado", "Eldorado Brasil",
    "Irani", "RANI",
    "CBA", "Companhia Brasileira de Alumínio",
    "Votorantim", "VMC",
    "Anglo American",
    "AUGO", "AURA",
    "Simandou",
    "Inesfa",
    "Mubadala",
    "Samarco",
    "MRN",           # Mineração Rio do Norte
    "Kinross",
    "Sigma Lithium",
    "Sigma",
]

# ── Empresas Internacionais ────────────────────────────────────────────────────
COMPANIES_INTL = [
    "BHP", "Rio Tinto", "Fortescue", "FMG",
    "ArcelorMittal",
    "Ternium",
    "Nucor",
    "US Steel",
    "Nippon Steel",
    "POSCO",
    "Baosteel", "Baoshan",
    "SAIL",            # Steel Authority of India
    "Tata Steel",
    "CMRG",
    "First Quantum",
    "Southern Copper",
    "Grupo Mexico",
    "Tia Maria",
    "CMPC",
    "APP",             # Asia Pulp & Paper
    "Nine Dragons",
    "Resolute Forest",
    "Stora Enso",
    "UPM", "Sappi",
]

# ── Produtos — Siderurgia & Mineração ─────────────────────────────────────────
STEEL_PRODUCTS = [
    # Aço planos (termos compostos — evitam falsos positivos)
    "HRC", "hot rolled coil", "bobina a quente",
    "CRC", "cold rolled coil", "bobina a frio",
    "galvanized steel", "galvanizado", "galvalume",
    "coated steel", "aço revestido",
    "heavy plate", "chapa grossa",
    "tinplate", "folha flandres",
    # Aço longos
    "rebar", "vergalhão",
    "wire rod", "fio máquina",
    "structural steel", "aço estrutural",
    "barra de aço",
    # Semiacabados
    "steel slab", "slab de aço", "billet", "tarugo", "bloom",
    "aço bruto", "crude steel", "steel output", "steel production",
    # Minério e matérias-primas
    "iron ore", "minério de ferro",
    "iron ore price", "preço do minério",
    "iron ore pellet", "pellet premium", "pellet", "pellets",
    "sinter feed",
    "lump ore",
    "coking coal", "carvão metalúrgico", "carvão coqueificável", "met coal",
    "ferrous scrap", "steel scrap", "sucata de aço", "sucata ferrosa",
    "pig iron", "ferro gusa",
    "DRI", "direct reduced iron", "direct reduction",
    "HBI",
    # Processos
    "blast furnace", "alto-forno",
    "electric arc furnace", "EAF",
    "basic oxygen furnace", "BOF",
    "steelmaking", "pelotização", "pelletizing",
    # Metais não-ferrosos (só termos específicos do setor)
    "nickel price", "nickel ore", "níquel",
    "copper price", "copper ore", "minério de cobre",
    "aluminium smelter", "aluminium price", "alumínio",
    "zinc price", "zinco",
    "manganese ore", "manganês",
    "cobalt price", "cobalto",
    "lithium price", "lítio",
    "rare earth", "terras raras",
    "critical minerals", "minerais críticos",
    "bauxite", "bauxita",
    "ferro-liga", "ferroalloy",
    "minério de ferro",
    "minerais estratégicos",
]

# ── Mercado Siderúrgico ────────────────────────────────────────────────────────
STEEL_MARKET = [
    "steel price", "preço do aço", "preço aço",
    "steel demand", "steel output", "steel capacity",
    "siderurgia", "siderúrgica", "indústria siderúrgica",
    "mineração", "setor mineral",
    "IODEX", "TSI 62%",
    "China stimulus", "Chinese steel demand", "China steel output",
    "property sector China", "setor imobiliário China",
    "China overcapacity", "sobrecapacidade China",
    "anti-dumping aço", "steel anti-dumping", "dumping de aço",
    "steel safeguard", "salvaguarda aço",
    "steel tariff", "tarifas aço", "Section 232",
    "CBAM", "carbon border adjustment",
    "green steel", "aço verde", "aço de baixo carbono",
    "hydrogen steel", "aço hidrogênio",
    "steel decarbonization", "descarbonização siderurgia",
    "frete marítimo minério", "ore freight",
    "Baltic Dry Index", "BDI",
    "CFEM",
    "barragem de rejeitos", "tailings dam", "rompimento barragem",
    "licenciamento ambiental mineração",
    "cash cost mineração", "cash cost siderurgia",
    "importação de aço", "steel imports", "steel exports",
    "capacidade instalada aço",
]

# ── Pulp & Paper ───────────────────────────────────────────────────────────────
PULP_PAPER = [
    # Celulose (termos compostos ou específicos)
    "celulose", "mercado de celulose", "preço celulose",
    "pulp price", "pulp market", "pulp demand",
    "BHKP", "celulose de fibra curta", "hardwood pulp", "eucalyptus pulp",
    "NBSK", "softwood pulp", "celulose de fibra longa",
    "BEKP", "BSKP", "BCTMP",
    "dissolving pulp", "celulose solúvel",
    "fluff pulp",
    "kraft pulp", "celulose kraft",
    "viscose staple fiber", "viscose rayon",
    "FOEX", "PIX pulp",
    "pulp inventory", "estoque celulose",
    # Papel (termos compostos)
    "papel e celulose", "pulp and paper",
    "papel kraft", "packaging paper", "papel embalagem",
    "paperboard", "cartão", "cartão para embalagem",
    "tissue paper", "papel tissue", "papel higiênico industrial",
    "containerboard", "papelão ondulado", "caixas onduladas",
    "corrugated board",
    "coated paper", "papel revestido",
    "newsprint",
    "paper demand", "demanda papel",
    "paper price", "preço papel",
    # Florestal / setorial
    "eucalipto", "eucalyptus plantation",
    "pinus", "cavaco de madeira",
    "setor florestal", "indústria de base florestal",
    "Ibá", "ABTCP",
]

# ── Regulatório & Setorial ────────────────────────────────────────────────────
REGULATORY = [
    "ABRAMAT",
    "ANFAVEA", "aço automotivo",
    "construction steel", "aço construção",
    "ANTAQ", "porto minério", "embarques minério",
    "IBAMA mineração", "IBAMA siderurgia",
    "ANM", "Agência Nacional de Mineração",
    "IBRAM", "Instituto Brasileiro de Mineração",
    "MME", "Ministério de Minas e Energia",
    "CADE siderurgia", "CADE mineração",
]

# ── Lista final ────────────────────────────────────────────────────────────────
ALL_KEYWORDS = list(set(
    COMPANIES_BR + COMPANIES_INTL +
    STEEL_PRODUCTS + STEEL_MARKET +
    PULP_PAPER + REGULATORY
))

# Configurações
WINDOW_HOURS   = 12   # só notícias publicadas nas últimas 12h
SUPABASE_TABLE = "news_articles"
MAX_PER_SOURCE = 50
