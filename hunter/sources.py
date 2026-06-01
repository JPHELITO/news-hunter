"""
Fontes RSS do News Hunter — Steel & Mining + Pulp & Paper.

filter=False → feed/query já é temático → aceita tudo
filter=True  → feed genérico → aplica keyword matching
"""

# Helper para Google News queries (EN e PT)
def _gn_en(query: str) -> str:
    return f"https://news.google.com/rss/search?q={query}&hl=en&gl=US&ceid=US:en"

def _gn_pt(query: str) -> str:
    return f"https://news.google.com/rss/search?q={query}&hl=pt-BR&gl=BR&ceid=BR:pt-419"

def _gn_en_site(query: str, site: str) -> str:
    return _gn_en(f"{query}+site:{site}")


SOURCES = [

    # ══ FEEDS RSS DIRETOS ═════════════════════════════════════════════════════

    # Mining.com — temáticos (100% relevante, sem filtro)
    {"label": "Mining.com",      "url": "https://www.mining.com/topics/iron-ore/feed/",  "filter": False},
    {"label": "Mining.com",      "url": "https://www.mining.com/topics/steel/feed/",     "filter": False},
    {"label": "Mining.com",      "url": "https://www.mining.com/topics/coal/feed/",      "filter": True},
    {"label": "Mining.com",      "url": "https://www.mining.com/topics/nickel/feed/",    "filter": True},
    {"label": "Mining.com",      "url": "https://www.mining.com/topics/copper/feed/",    "filter": True},
    {"label": "Mining.com",      "url": "https://www.mining.com/topics/lithium/feed/",   "filter": True},

    # SteelOrbis
    {"label": "SteelOrbis",      "url": "https://www.steelorbis.com/rss/rss.asp",        "filter": False},

    # Mining Weekly (África do Sul — cobertura global)
    {"label": "Mining Weekly",   "url": "https://www.miningweekly.com/rss.xml",          "filter": True},

    # World Steel Association
    {"label": "World Steel",     "url": "https://www.worldsteel.org/rss.xml",            "filter": False},

    # Paper Advance
    {"label": "Paper Advance",   "url": "https://www.paperadvance.com/rss",              "filter": True},

    # Tissue Online
    {"label": "Tissue Online",   "url": "https://www.tissueonline.com.br/feed/",         "filter": True},

    # Portal Celulose
    {"label": "Portal Celulose", "url": "https://portalcelulose.com.br/feed/",           "filter": False},

    # InfoMoney
    {"label": "InfoMoney",       "url": "https://www.infomoney.com.br/feed/",            "filter": True},

    # Exame
    {"label": "Exame",           "url": "https://exame.com/feed/",                       "filter": True},

    # Folha de S.Paulo — Mercado
    {"label": "Folha de S.Paulo","url": "https://feeds.folha.uol.com.br/mercado/rss091.xml", "filter": True},

    # ══ GOOGLE NEWS — PUBLICAÇÕES FINANCEIRAS/JORNAIS (site:) ════════════════

    # Valor Econômico
    {"label": "Valor Econômico", "url": _gn_pt("site:valor.globo.com+mineração+siderurgia+celulose+aço"), "filter": False},
    {"label": "Valor Econômico", "url": _gn_pt("site:valor.globo.com+VALE+Gerdau+Suzano+Klabin+CSN"),     "filter": False},

    # Estadão
    {"label": "Estadão",         "url": _gn_pt("site:estadao.com.br+mineração+aço+celulose+siderurgia"),  "filter": False},

    # Reuters (commodities/metals)
    {"label": "Reuters",         "url": _gn_en("site:reuters.com+iron+ore+steel+mining"),                 "filter": False},
    {"label": "Reuters",         "url": _gn_en("site:reuters.com+pulp+paper+cellulose"),                  "filter": False},

    # Bloomberg
    {"label": "Bloomberg",       "url": _gn_en("site:bloomberg.com+iron+ore+steel+mining"),               "filter": False},
    {"label": "Bloomberg",       "url": _gn_en("site:bloomberg.com+pulp+paper+cellulose"),                "filter": False},

    # G1 / O Globo
    {"label": "G1",              "url": _gn_pt("site:g1.globo.com+minério+siderurgia+celulose"),          "filter": True},

    # CNN Brasil
    {"label": "CNN Brasil",      "url": _gn_pt("site:cnnbrasil.com.br+VALE+aço+minério+celulose"),        "filter": True},

    # Money Times
    {"label": "Money Times",     "url": _gn_pt("site:moneytimes.com.br+VALE+Gerdau+Suzano+Klabin"),       "filter": True},

    # ══ GOOGLE NEWS — FONTES ESPECIALIZADAS EM COMMODITIES ════════════════════

    # S&P Global / Platts
    {"label": "S&P Platts",      "url": _gn_en("site:spglobal.com+steel+iron+ore+metallurgical+coal"),   "filter": False},
    {"label": "S&P Platts",      "url": _gn_en("site:spglobal.com+pulp+paper+cellulose+BHKP+NBSK"),      "filter": False},

    # Fastmarkets
    {"label": "Fastmarkets",     "url": _gn_en("site:fastmarkets.com+steel+iron+ore+scrap+HRC"),         "filter": False},
    {"label": "Fastmarkets",     "url": _gn_en("site:fastmarkets.com+pulp+paper+BHKP+NBSK"),             "filter": False},

    # Argus Media
    {"label": "Argus",           "url": _gn_en("site:argusmedia.com+steel+iron+ore+coking+coal"),        "filter": False},
    {"label": "Argus",           "url": _gn_en("site:argusmedia.com+pulp+paper"),                        "filter": False},

    # Mysteel (mercado chinês)
    {"label": "Mysteel",         "url": _gn_en("site:mysteel.net+steel+iron+ore"),                       "filter": False},

    # SMM — Shanghai Metals Market
    {"label": "SMM",             "url": _gn_en("site:metal.com+steel+iron+nickel+copper"),               "filter": False},

    # Kallanish Steel
    {"label": "Kallanish",       "url": _gn_en("site:kallanish.com+steel+iron+ore"),                     "filter": False},

    # MEPS International
    {"label": "MEPS",            "url": _gn_en("site:meps.co.uk+steel+price"),                           "filter": False},

    # ICIS (fibras/celulose)
    {"label": "ICIS",            "url": _gn_en("site:icis.com+pulp+paper+cellulose"),                    "filter": False},

    # ══ GOOGLE NEWS — EMPRESAS BRASILEIRAS ════════════════════════════════════

    {"label": "Google News",     "url": _gn_en("VALE+iron+ore+mining+results"),                          "filter": False},
    {"label": "Google News",     "url": _gn_en("Gerdau+OR+CSN+OR+Usiminas+steel+production"),           "filter": False},
    {"label": "Google News",     "url": _gn_en("Suzano+OR+Klabin+OR+Eldorado+pulp+results"),            "filter": False},
    {"label": "Google News",     "url": _gn_en("Votorantim+OR+CBA+aluminum+aluminium"),                  "filter": True},
    {"label": "Google News",     "url": _gn_en("Anglo+American+OR+Fortescue+OR+Rio+Tinto+iron+ore"),    "filter": False},
    {"label": "Google News",     "url": _gn_en("ArcelorMittal+OR+Ternium+OR+Nucor+steel"),              "filter": False},
    {"label": "Google News",     "url": _gn_en("Samarco+OR+MRN+OR+Kinross+Brazil+mining"),              "filter": True},
    {"label": "Google News",     "url": _gn_en("Simandou+iron+ore+Guinea"),                             "filter": False},

    # ══ GOOGLE NEWS — MERCADO DE AÇO & MINÉRIO ════════════════════════════════

    {"label": "Google News",     "url": _gn_en("iron+ore+price+IODEX+62%25+fines"),                     "filter": False},
    {"label": "Google News",     "url": _gn_en("HRC+hot+rolled+coil+steel+price"),                      "filter": False},
    {"label": "Google News",     "url": _gn_en("rebar+wire+rod+steel+long+products"),                   "filter": False},
    {"label": "Google News",     "url": _gn_en("coking+coal+metallurgical+coal+price"),                 "filter": False},
    {"label": "Google News",     "url": _gn_en("steel+scrap+price+ferrous"),                            "filter": False},
    {"label": "Google News",     "url": _gn_en("pellet+premium+iron+ore+pellet"),                       "filter": False},
    {"label": "Google News",     "url": _gn_en("China+steel+output+production+overcapacity"),           "filter": False},
    {"label": "Google News",     "url": _gn_en("China+stimulus+property+steel+demand"),                 "filter": False},
    {"label": "Google News",     "url": _gn_en("steel+anti-dumping+tariff+safeguard"),                  "filter": False},
    {"label": "Google News",     "url": _gn_en("CBAM+carbon+border+steel"),                             "filter": False},
    {"label": "Google News",     "url": _gn_en("green+steel+hydrogen+decarbonization"),                 "filter": False},
    {"label": "Google News",     "url": _gn_en("Baltic+Dry+Index+iron+ore+freight"),                    "filter": False},

    # ══ GOOGLE NEWS — MERCADO DE CELULOSE & PAPEL ═════════════════════════════

    {"label": "Google News",     "url": _gn_en("BHKP+NBSK+pulp+price+FOEX"),                           "filter": False},
    {"label": "Google News",     "url": _gn_en("tissue+containerboard+packaging+paper+demand"),         "filter": False},
    {"label": "Google News",     "url": _gn_en("dissolving+pulp+viscose+rayon"),                        "filter": False},
    {"label": "Google News",     "url": _gn_en("fluff+pulp+absorbent+hygiene"),                         "filter": False},
    {"label": "Google News",     "url": _gn_en("eucalyptus+plantation+wood+chip"),                      "filter": True},

    # ══ GOOGLE NEWS — ASSOCIAÇÕES E ÓRGÃOS BRASILEIROS ════════════════════════

    {"label": "Instituto Aço Brasil", "url": _gn_pt("site:acobrasil.org.br OR \"Instituto Aço Brasil\""), "filter": False},
    {"label": "IBRAM",           "url": _gn_pt("site:ibram.org.br OR IBRAM+mineração+Brasil"),          "filter": False},
    {"label": "Ibá",             "url": _gn_pt("site:iba.org OR \"Indústria Brasileira de Árvores\""),  "filter": False},
    {"label": "ABTCP",           "url": _gn_pt("ABTCP+celulose+papel+Brasil"),                          "filter": False},
    {"label": "ANM",             "url": _gn_pt("ANM+\"Agência Nacional de Mineração\"+CFEM"),           "filter": False},
    {"label": "Siderurgia Brasil","url": _gn_pt("\"Siderurgia Brasil\""),                               "filter": False},
    {"label": "ABM Brasil",      "url": _gn_pt("\"ABM Brasil\"+metalurgia+mineração"),                  "filter": False},
    {"label": "CBCA",            "url": _gn_pt("CBCA+\"construção com aço\""),                          "filter": False},
    {"label": "ABRAMAT",         "url": _gn_pt("ABRAMAT+materiais+construção+aço"),                     "filter": True},
    {"label": "ANFAVEA",         "url": _gn_pt("ANFAVEA+automóveis+aço"),                               "filter": True},
    {"label": "ANTAQ",           "url": _gn_pt("ANTAQ+porto+graneis+minério"),                          "filter": True},
    {"label": "MME",             "url": _gn_pt("\"Ministério+Minas+Energia\"+minério+siderurgia"),      "filter": True},

    # ══ GOOGLE NEWS — ASSOCIAÇÕES INTERNACIONAIS ══════════════════════════════

    {"label": "World Steel Assoc","url": _gn_en("\"World Steel Association\"+production+output"),       "filter": False},
    {"label": "AISI",            "url": _gn_en("AISI+\"American Iron and Steel\"+production"),         "filter": False},
    {"label": "CISA",            "url": _gn_en("CISA+\"China Iron and Steel\"+output"),                "filter": False},
    {"label": "CEPI",            "url": _gn_en("CEPI+\"European paper\"+production"),                  "filter": False},

    # ══ GOOGLE NOTÍCIAS — PORTUGUÊS ════════════════════════════════════════════

    {"label": "Google Notícias", "url": _gn_pt("minério+de+ferro+preço+VALE+CSN"),                     "filter": False},
    {"label": "Google Notícias", "url": _gn_pt("siderurgia+aço+Gerdau+Usiminas+CSN"),                  "filter": False},
    {"label": "Google Notícias", "url": _gn_pt("celulose+papel+Suzano+Klabin+Eldorado"),               "filter": False},
    {"label": "Google Notícias", "url": _gn_pt("licenciamento+ambiental+barragem+mineração"),           "filter": True},
    {"label": "Google Notícias", "url": _gn_pt("CFEM+royalties+mineração+arrecadação"),                "filter": False},
    {"label": "Google Notícias", "url": _gn_pt("aço+importação+dumping+tarifas+siderurgia"),           "filter": False},
    {"label": "Google Notícias", "url": _gn_pt("celulose+fibra+curta+preço+mercado"),                  "filter": False},
    {"label": "Google Notícias", "url": _gn_pt("minério+pellets+embarques+exportação"),                "filter": False},
]
