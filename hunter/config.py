"""Keywords e configurações do News Hunter."""

# ── Steel & Mining ─────────────────────────────────────────────────────────────
STEEL_MINING_KW = [
    # Empresas BR
    "VALE", "CSN", "Gerdau", "Usiminas", "Ternium", "ArcelorMittal",
    "CBMM", "CBA",
    # Produtos
    "iron ore", "minério", "steel", "aço", "HRC", "CRC", "slab", "placa",
    "billet", "tarugo", "wire rod", "vergalhão", "rebar", "coated",
    "hot rolled", "cold rolled", "galvanized",
    # Matérias-primas
    "coking coal", "carvão", "scrap", "sucata", "DRI", "pig iron", "gusa",
    "nickel", "níquel", "copper", "cobre", "aluminium", "alumínio",
    # Mercado
    "IODEX", "TSI", "steelmaking", "siderurgia", "siderúrgica",
    "blast furnace", "alto-forno", "electric arc", "forno elétrico",
    "iron ore price", "steel price", "aço preço",
]

# ── Pulp & Paper ───────────────────────────────────────────────────────────────
PULP_PAPER_KW = [
    # Empresas
    "Suzano", "Klabin", "Eldorado", "CMPC", "Fibria", "APP", "Nine Dragons",
    "Resolute", "Sappi", "Stora Enso", "UPM",
    # Produtos
    "pulp", "celulose", "paper", "papel", "paperboard", "tissue",
    "BHKP", "NBSK", "BEKP", "BSKP",
    "hardwood pulp", "softwood pulp", "eucalyptus pulp",
    "eucalipto", "pinus", "viscose", "rayon",
    # Mercado
    "FOEX", "PIX pulp", "cellulose price", "preço celulose",
    "pulp price", "paper price",
]

# Todos os keywords combinados
ALL_KEYWORDS = list(set(STEEL_MINING_KW + PULP_PAPER_KW))

# Janela de tempo: só artigos das últimas N horas
WINDOW_HOURS = 48

# Supabase: tabela de destino
SUPABASE_TABLE = "news_articles"

# Máximo de artigos por fonte por run (evita flood de uma única fonte)
MAX_PER_SOURCE = 30
