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
    # Aço planos
    "HRC", "hot rolled coil", "bobina a quente",
    "CRC", "cold rolled coil", "bobina a frio",
    "galvanized", "galvanizado", "galvalume",
    "coated steel", "aço revestido",
    "heavy plate", "chapa grossa",
    "tinplate", "folha flandres",
    # Aço longos
    "rebar", "vergalhão",
    "wire rod", "fio máquina",
    "structural steel", "aço estrutural",
    "bar", "barra de aço",
    "angle", "perfil",
    # Semiacabados
    "slab", "placa", "billet", "tarugo", "bloom",
    "aço bruto", "crude steel", "steel output",
    # Matérias-primas
    "iron ore", "minério de ferro",
    "pellet", "pellets", "pellet premium",
    "sinter feed", "sinter",
    "lump ore",
    "coking coal", "carvão metalúrgico", "carvão coqueificável",
    "scrap", "sucata", "ferrous scrap",
    "pig iron", "ferro gusa",
    "DRI", "direct reduced iron",
    "HBI",
    # Processos
    "blast furnace", "alto-forno",
    "electric arc furnace", "EAF", "forno elétrico a arco",
    "BOF", "basic oxygen furnace",
    "continuous casting",
    # Metais não-ferrosos
    "nickel", "níquel",
    "copper", "cobre",
    "aluminium", "aluminum", "alumínio",
    "zinc", "zinco",
    "manganese", "manganês",
    "chromium", "cromo",
    "cobalt", "cobalto",
    "lithium", "lítio",
    "gold", "ouro",
    "rare earth", "terras raras",
    "critical minerals",
    "bauxite", "bauxita",
    "iron", "ferro",
    "minerais",
]

# ── Mercado Siderúrgico ────────────────────────────────────────────────────────
STEEL_MARKET = [
    "steel", "aço",
    "siderurgia", "siderúrgica",
    "steelmaking",
    "steel price", "preço aço",
    "mineração", "mining",
    "minério",
    "IODEX", "TSI 62%", "MBIO",
    "China stimulus", "estímulo China",
    "property sector China", "setor imobiliário China",
    "Chinese steel", "Chinese demand",
    "overcapacity China",
    "anti-dumping", "dumping",
    "safeguard", "salvaguarda",
    "tariff steel", "tarifas aço",
    "Section 232",
    "CBAM",                    # Carbon Border Adjustment Mechanism
    "carbon border",
    "green steel", "aço verde",
    "hydrogen steel", "aço hidrogênio",
    "decarbonization", "descarbonização",
    "frete marítimo", "freight",
    "Baltic Dry", "BDI",
    "CFEM",                    # Compensação Financeira pela Exploração Mineral
    "barragem de rejeitos", "tailings dam",
    "licenciamento ambiental",
    "cash cost",
    "capex",
]

# ── Pulp & Paper ───────────────────────────────────────────────────────────────
PULP_PAPER = [
    # Celulose
    "celulose", "pulp",
    "BHKP", "celulose de fibra curta", "hardwood pulp", "eucalyptus pulp",
    "NBSK", "softwood pulp",
    "BEKP", "BSKP", "BCTMP",
    "dissolving pulp", "celulose solúvel",
    "fluff pulp",
    "kraft pulp", "celulose kraft",
    "viscose", "rayon",
    "pulp price", "preço celulose",
    "FOEX", "PIX pulp",
    # Papel
    "papel", "paper",
    "paperboard", "papelão",
    "tissue",
    "containerboard", "papelão ondulado",
    "corrugated", "caixas onduladas",
    "coated paper",
    "newsprint", "jornal papel",
    "packaging paper", "papel embalagem",
    "paper demand", "demanda papel",
    "paper price", "preço papel",
    # Florestal
    "eucalyptus", "eucalipto",
    "pinus", "pine",
    "wood chip", "cavaco",
    "plantation", "plantação florestal",
    "FSC", "Cerflor",
    "deforestation", "desmatamento",
]

# ── Geral / Economia ──────────────────────────────────────────────────────────
GENERAL = [
    "Cimento", "cimento",
    "Cobre",
    "Ouro",
    "Minério",
    "ABRAMAT", "CBIC", "ANFAVEA",
    "construction steel", "aço construção",
    "auto steel", "aço automotivo",
    "B3", "CVM",
    "ANTAQ",
    "CADE",
    "IBAMA",
    "MME",                    # Ministério de Minas e Energia
    "ANM",                    # Agência Nacional de Mineração
    "IBRAM",                  # Instituto Brasileiro de Mineração
]

# ── Lista final ────────────────────────────────────────────────────────────────
ALL_KEYWORDS = list(set(
    COMPANIES_BR + COMPANIES_INTL +
    STEEL_PRODUCTS + STEEL_MARKET +
    PULP_PAPER + GENERAL
))

# Configurações
WINDOW_HOURS   = 48
SUPABASE_TABLE = "news_articles"
MAX_PER_SOURCE = 30
