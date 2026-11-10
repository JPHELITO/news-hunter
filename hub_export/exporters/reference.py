"""Mapas CONSTANTES portados do site atual (IBBA-Research-Dashboard/index.html, staging e055467).

Mudou lá (um grupo ganhou papel, uma bolsa nova) → muda aqui também. O teste
`test_hub_export_builders.py` confere que toda bolsa do QUOTES_LIST tem entrada em EXCHANGES e
que todo papel dos grupos existe no QUOTES_LIST.

Referências no index.html: grupos 2717-2735 (`G_*`, `HM_MAX`); bolsas 2889-2911 (`EXCH_CCY`,
`EXCH_TZ`, `EXCH_NAME`); benchmark 2981-2987 (`BENCH_BY_EXCH`, `BENCH_LABEL`).
"""
from __future__ import annotations

# ── heatmap ────────────────────────────────────────────────────────────────────────────────────
HM_MAX = 20     # teto de cartões = a grade 4×5

G_COVERAGE = ["AUGO", "SUZB3.SA", "VALE", "GMEXICOB.MX", "CMIN3.SA", "SCCO", "KLBN11.SA", "TX",
              "CSNA3.SA", "CMPC.SN", "USIM5.SA", "RANI3.SA", "GGBR4.SA", "COPEC.SN",
              "CS.TO", "ERO", "HBM.TO", "LUN.TO", "ARA.TO", "VMM.AX"]
G_STEEL = ["CSNA3.SA", "GGBR4.SA", "GGB", "MT", "NUE", "TX", "USIM5.SA", "CMC", "GOAU4.SA", "STLD"]
G_IRON = ["VALE", "CMIN3.SA", "FMG.AX", "RIO", "BHP", "AAL.L", "CAP.SN", "BRAP3.SA"]
G_GOLD = ["AUGO", "ARIS", "BVN", "AEM", "B", "HOC.L"]
G_COPPER = ["ERO", "SCCO", "GMEXICOB.MX", "CS.TO", "B", "HBM.TO", "LUN.TO"]
G_RARE = ["MEI.AX", "VMM.AX", "ARA.TO"]
G_PP = ["SW", "IP", "SUZB3.SA", "KLBN11.SA", "RANI3.SA", "CMPC.SN", "COPEC.SN", "UPM.HE", "STERV.HE"]
G_INDEX = ["GDX", "SPX", "MATB11.SA", "NASDAQ", "IBOV", "SPTSX", "ASX200", "MEXBOL", "IPSA",
           "FTSE100", "OMXH25"]

# (id, rótulo pt-br, rótulo en-us, papéis) — na ordem do seletor do site (HM_PRESETS).
HEATMAP_GROUPS = [
    ("coverage",    "Nossa cobertura",   "Our coverage", G_COVERAGE),
    ("steel",       "Aço",               "Steel",        G_STEEL),
    ("iron_ore",    "Minério de ferro",  "Iron Ore",     G_IRON),
    ("gold",        "Ouro",              "Gold",         G_GOLD),
    ("copper",      "Cobre",             "Copper",       G_COPPER),
    ("rare_earths", "Terras raras",      "Rare Earths",  G_RARE),
    ("pp",          "Papel e celulose",  "P&P",          G_PP),
    ("index",       "Índices",           "Index",        G_INDEX),
]

# O índice principal da bolsa onde AQUELA linha negocia (modo relativo do heatmap).
BENCH_BY_EXCH = {"B3": "IBOV", "NYSE": "SPX", "NASDAQ": "SPX", "US": "SPX", "TSX": "SPTSX",
                 "ASX": "ASX200", "BMV": "MEXBOL", "BCS": "IPSA", "LSE": "FTSE100", "HEL": "OMXH25"}
BENCH_LABEL = {"IBOV": "IBOV", "SPX": "S&P 500", "SPTSX": "S&P/TSX", "ASX200": "ASX 200",
               "MEXBOL": "IPC", "IPSA": "IPSA", "FTSE100": "FTSE 100", "OMXH25": "OMXH25"}

# ── bolsas ─────────────────────────────────────────────────────────────────────────────────────
# nome = EXCH_NAME do site, ou o próprio código (é o que o site mostra quando não há nome);
# fuso = EXCH_TZ; moeda = EXCH_CCY (None onde o site não mostra moeda).
# ⚠️ AMS (MT.AS) e CME (SP500_FUT) NÃO estão no EXCH_TZ do site (que cai em America/Sao_Paulo
# por omissão); aqui vão com o fuso VERDADEIRO da bolsa. Nenhum dos dois aparece em grupo do
# heatmap hoje — se um dia aparecer, o selo de data já sai certo.
EXCHANGES = {
    "B3":     {"name": "B3",        "tz": "America/Sao_Paulo",   "currency": "BRL"},
    "NYSE":   {"name": "NYSE",      "tz": "America/New_York",    "currency": "USD"},
    "NASDAQ": {"name": "Nasdaq",    "tz": "America/New_York",    "currency": "USD"},
    "US":     {"name": "US market", "tz": "America/New_York",    "currency": "USD"},
    "TSX":    {"name": "TSX",       "tz": "America/Toronto",     "currency": "CAD"},
    "ASX":    {"name": "ASX",       "tz": "Australia/Sydney",    "currency": "AUD"},
    "BMV":    {"name": "Mexico",    "tz": "America/Mexico_City", "currency": "MXN"},
    "BCS":    {"name": "Santiago",  "tz": "America/Santiago",    "currency": "CLP"},
    "LSE":    {"name": "LSE",       "tz": "Europe/London",       "currency": "GBp"},
    "HEL":    {"name": "Helsinki",  "tz": "Europe/Helsinki",     "currency": "EUR"},
    "AMS":    {"name": "AMS",       "tz": "Europe/Amsterdam",    "currency": None},
    "CME":    {"name": "CME",       "tz": "America/Chicago",     "currency": None},
}
# Bolsa que surgir no QUOTES_LIST sem entrada acima: sai com o código como nome e o fuso de
# omissão do site, e o run loga aviso (o teste já falha antes disso chegar ao ar).
FALLBACK_TZ = "America/Sao_Paulo"

# ── universo da aba Market (Onda 2: família history-<id>.json, PLANO Apêndice F.1) ───────────────
# Porte do `KNOWN` da market.html (origin/main f636222, linhas 778-801) sem os `@` das commodities:
# os grupos do heatmap acima + as linhas da B3 de Vale e Aura (a cobertura da Market usa VALE3.SA e
# AURA33.SA; a da Home, as ADRs). É decisão de PUBLICAÇÃO (quais históricos vão ao Mindassets), por
# isso mora aqui e não no config.json; os 5 papéis da quote_history que a Market não mostra (BHP.AX,
# GLEN.L, MT.AS, RIO.L, SP500_FUT, insumos do blast) não saem. O teste confere contra a lista do site.
# A ORDEM é a prioridade da carga inicial (O28) e a do history-index.json: os 20 da cobertura (na
# ordem do G_COVERAGE da Market), depois os índices, depois os pares (na ordem do KNOWN do site).
MARKET_COVERAGE = ["AURA33.SA", "SUZB3.SA", "VALE3.SA", "GMEXICOB.MX", "CMIN3.SA", "SCCO",
                   "KLBN11.SA", "TX", "CSNA3.SA", "CMPC.SN", "USIM5.SA", "RANI3.SA", "GGBR4.SA",
                   "COPEC.SN", "CS.TO", "ERO", "HBM.TO", "LUN.TO", "ARA.TO", "VMM.AX"]
MARKET_PEERS = ["GGB", "MT", "NUE", "CMC", "GOAU4.SA", "STLD",                  # aço
                "VALE", "FMG.AX", "RIO", "BHP", "AAL.L", "CAP.SN", "BRAP3.SA",  # minério
                "AUGO", "ARIS", "BVN", "AEM", "B", "HOC.L",                     # ouro
                "MEI.AX",                                                       # terras raras
                "SW", "IP", "UPM.HE", "STERV.HE"]                               # papel e celulose
MARKET_UNIVERSE = MARKET_COVERAGE + G_INDEX + MARKET_PEERS                      # 20 + 11 + 24 = 55
