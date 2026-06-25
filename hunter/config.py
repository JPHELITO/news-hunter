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
    "Southern Copper", "SCCO",
    "Grupo Mexico", "GMexico", "Ferromex", "Buenavista",
    "Tia Maria",
    "CMPC", "Empresas CMPC", "Softys",
    # COPEC/Arauco estavam AUSENTES — manchete de coberta sem outra keyword era
    # descartada antes de classificar (bug achado na auditoria 2026-06-25).
    "Copec", "Empresas Copec", "Arauco", "Celulosa Arauco",
    "APP",             # Asia Pulp & Paper
    "Bracell", "Sun Paper", "Sateri",   # competidores de celulose (sinal de oferta)
    "Nine Dragons",
    "Resolute Forest",
    "Stora Enso",
    "UPM", "Sappi",
]

# ── Produtos — Siderurgia & Mineração ─────────────────────────────────────────
STEEL_PRODUCTS = [
    # Aço planos (termos compostos — evitam falsos positivos)
    "HRC", "hot rolled coil", "hot-rolled coil", "hot-rolled steel", "bobina a quente",
    "CRC", "cold rolled coil", "cold-rolled coil", "cold-rolled steel", "bobina a frio",
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
    "nickel price", "nickel prices", "nickel ore", "níquel",
    "copper price", "copper prices", "copper ore", "minério de cobre",
    "aluminium smelter", "aluminium price", "aluminium prices", "alumínio",
    "zinc price", "zinc prices", "zinco",
    "manganese ore", "manganês",
    "cobalt price", "cobalt prices", "cobalto",
    "lithium price", "lithium prices", "lítio",
    "rare earth", "terras raras",
    "critical minerals", "minerais críticos",
    "bauxite", "bauxita",
    "ferro-liga", "ferroalloy",
    "minério de ferro",
    "minerais estratégicos",
    # Benchmarks/grades de minério + cobre (gaps achados na auditoria 2026-06-25)
    "62%Fe", "65%Fe", "high-grade premium", "low-grade discount", "grade premium",
    "molybdenum", "moly", "treatment charge", "refining charge", "TC/RC",
    "Cobre Panama", "Escondida", "Codelco", "Antofagasta",
]

# ── Mercado Siderúrgico ────────────────────────────────────────────────────────
STEEL_MARKET = [
    "steel price", "steel prices", "preço do aço", "preços do aço", "preço aço", "preços aço",
    "steel demand", "steel output", "steel outputs", "steel capacity", "produção de aço",
    "iron ore prices", "pellet prices",
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
    # Logística/China/ativos (gaps auditoria 2026-06-25)
    "Capesize", "C3 freight", "frete Capesize",
    "CMRG", "China Mineral Resources Group",
    "Pesqueria", "Pesquería",
    "Mariana", "Brumadinho",
]

# ── Pulp & Paper ───────────────────────────────────────────────────────────────
PULP_PAPER = [
    # Celulose (termos compostos ou específicos)
    "celulose", "mercado de celulose", "preço celulose",
    "pulp price", "pulp prices", "preços da celulose", "pulp market", "pulp demand",
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
    "paper price", "paper prices", "preço papel", "preços do papel",
    # Florestal / setorial
    "eucalipto", "eucalyptus plantation",
    "pinus", "cavaco de madeira",
    "setor florestal", "indústria de base florestal",
    "Ibá", "ABTCP",
    # Gaps P&P (auditoria 2026-06-25)
    "kraftliner", "PIX kraftliner", "recovered fiber", "recovered paper",
    "EUDR", "EU Deforestation", "NBSK-BEK", "fluff pulp", "dissolving pulp",
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
WINDOW_HOURS   = 72  # janela de ingestão. Só descarta itens de RSS com published_at
                     # mais antigo que isto. NÃO é a janela de exibição — o front-end
                     # já filtra a home em 48h (published_at). Mantemos 72h (>48h + margem)
                     # para nunca jogar fora, na coleta, algo que a home mostraria.
                     # (Antes era 6h — resquício do Google News, derrubava fontes que
                     # publicam menos de uma vez a cada 6h, ex.: Siderurgia Brasil, ABTCP.)
                     # Aumentar é seguro: o push usa ignore-duplicates (URL repetida = ignorada).
SUPABASE_TABLE = "news_articles"
MAX_PER_SOURCE = 50
