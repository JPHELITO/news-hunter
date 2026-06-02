"""
Fontes RSS do News Hunter — Steel & Mining + Pulp & Paper.

filter=False → feed/query já é temático → aceita tudo
filter=True  → feed genérico → aplica keyword matching
"""

# Helper para Google News queries (EN e PT) — com filtro de tempo nativo
# O operador `when:Nh` força o Google News a retornar APENAS artigos das
# últimas N horas. Sem isso, o RSS retorna por relevância (não cronológico)
# e mistura artigos de dias atrás com os de minutos.
_GN_WHEN = "when:6h"

def _gn_en(query: str) -> str:
    q = f"{query}+{_GN_WHEN}"
    return f"https://news.google.com/rss/search?q={q}&hl=en&gl=US&ceid=US:en"

def _gn_pt(query: str) -> str:
    q = f"{query}+{_GN_WHEN}"
    return f"https://news.google.com/rss/search?q={q}&hl=pt-BR&gl=BR&ceid=BR:pt-419"


SOURCES = [

    # ══ FEEDS RSS DIRETOS ═════════════════════════════════════════════════════

    # Mining.com — feed principal com filtro de keywords
    {"label": "Mining.com",      "url": "https://www.mining.com/feed/",                  "filter": True},

    # Portal Celulose
    {"label": "Portal Celulose", "url": "https://portalcelulose.com.br/feed/",          "filter": False},

    # InfoMoney
    {"label": "InfoMoney",       "url": "https://www.infomoney.com.br/feed/",           "filter": True},

    # Exame
    {"label": "Exame",           "url": "https://exame.com/feed/",                      "filter": True},

    # Folha de S.Paulo — Mercado
    {"label": "Folha de S.Paulo","url": "https://feeds.folha.uol.com.br/mercado/rss091.xml", "filter": True},

    # ══ GOOGLE NEWS — VALOR ECONÔMICO (uma query por tema) ══════════════════════
    {"label": "Valor Econômico", "url": _gn_pt("site:valor.globo.com+VALE"),           "filter": True},
    {"label": "Valor Econômico", "url": _gn_pt("site:valor.globo.com+siderurgia"),     "filter": True},
    {"label": "Valor Econômico", "url": _gn_pt("site:valor.globo.com+minério"),        "filter": True},
    {"label": "Valor Econômico", "url": _gn_pt("site:valor.globo.com+celulose"),       "filter": True},
    {"label": "Valor Econômico", "url": _gn_pt("site:valor.globo.com+Suzano"),         "filter": True},
    {"label": "Valor Econômico", "url": _gn_pt("site:valor.globo.com+Gerdau"),         "filter": True},

    # ══ GOOGLE NEWS — ESTADÃO ════════════════════════════════════════════════
    {"label": "Estadão",         "url": _gn_pt("site:estadao.com.br+minério"),         "filter": True},
    {"label": "Estadão",         "url": _gn_pt("site:estadao.com.br+siderurgia"),      "filter": True},
    {"label": "Estadão",         "url": _gn_pt("site:estadao.com.br+celulose"),        "filter": True},

    # ══ GOOGLE NEWS — REUTERS ════════════════════════════════════════════════
    {"label": "Reuters",         "url": _gn_en("site:reuters.com+iron+ore"),           "filter": True},
    {"label": "Reuters",         "url": _gn_en("site:reuters.com+steel"),              "filter": True},
    {"label": "Reuters",         "url": _gn_en("site:reuters.com+VALE"),               "filter": True},
    {"label": "Reuters",         "url": _gn_en("site:reuters.com+pulp+paper"),         "filter": True},

    # ══ GOOGLE NEWS — BLOOMBERG ════════════════════════════════════════════════
    {"label": "Bloomberg",       "url": _gn_en("site:bloomberg.com+iron+ore"),         "filter": True},
    {"label": "Bloomberg",       "url": _gn_en("site:bloomberg.com+steel"),            "filter": True},
    {"label": "Bloomberg",       "url": _gn_en("site:bloomberg.com+pulp"),             "filter": True},
    {"label": "Bloomberg",       "url": _gn_en("site:bloomberg.com+VALE"),             "filter": True},

    # ══ GOOGLE NEWS — S&P PLATTS ═════════════════════════════════════════════
    {"label": "S&P Platts",      "url": _gn_en("site:spglobal.com+steel"),             "filter": True},
    {"label": "S&P Platts",      "url": _gn_en("site:spglobal.com+iron+ore"),          "filter": True},
    {"label": "S&P Platts",      "url": _gn_en("site:spglobal.com+pulp"),              "filter": True},
    {"label": "S&P Platts",      "url": _gn_en("site:spglobal.com+coal"),              "filter": True},

    # ══ GOOGLE NEWS — FASTMARKETS ════════════════════════════════════════════
    {"label": "Fastmarkets",     "url": _gn_en("site:fastmarkets.com+steel"),          "filter": True},
    {"label": "Fastmarkets",     "url": _gn_en("site:fastmarkets.com+iron+ore"),       "filter": True},
    {"label": "Fastmarkets",     "url": _gn_en("site:fastmarkets.com+pulp"),           "filter": True},
    {"label": "Fastmarkets",     "url": _gn_en("site:fastmarkets.com+scrap"),          "filter": True},

    # ══ GOOGLE NEWS — ARGUS ══════════════════════════════════════════════════
    {"label": "Argus",           "url": _gn_en("site:argusmedia.com+steel"),           "filter": True},
    {"label": "Argus",           "url": _gn_en("site:argusmedia.com+iron+ore"),        "filter": True},
    {"label": "Argus",           "url": _gn_en("site:argusmedia.com+coal"),            "filter": True},
    {"label": "Argus",           "url": _gn_en("site:argusmedia.com+pulp"),            "filter": True},

    # ══ GOOGLE NEWS — ESPECIALIZADAS ══════════════════════════════════════════
    {"label": "Mysteel",         "url": _gn_en("site:mysteel.net+steel"),              "filter": True},
    {"label": "Mysteel",         "url": _gn_en("site:mysteel.net+iron+ore"),           "filter": True},
    {"label": "SMM",             "url": _gn_en("site:metal.com+steel"),                "filter": True},
    {"label": "SMM",             "url": _gn_en("site:metal.com+nickel"),               "filter": True},
    {"label": "Kallanish",       "url": _gn_en("site:kallanish.com+steel"),            "filter": True},
    {"label": "ICIS",            "url": _gn_en("site:icis.com+pulp"),                  "filter": True},

    # ══ GOOGLE NEWS — G1 / CNN / Money Times ══════════════════════════════════
    {"label": "G1",              "url": _gn_pt("site:g1.globo.com+minério"),           "filter": True},
    {"label": "G1",              "url": _gn_pt("site:g1.globo.com+siderurgia"),        "filter": True},
    {"label": "CNN Brasil",      "url": _gn_pt("site:cnnbrasil.com.br+VALE"),          "filter": True},
    {"label": "CNN Brasil",      "url": _gn_pt("site:cnnbrasil.com.br+minério"),       "filter": True},
    {"label": "Money Times",     "url": _gn_pt("site:moneytimes.com.br+VALE"),         "filter": True},
    {"label": "Money Times",     "url": _gn_pt("site:moneytimes.com.br+Gerdau"),       "filter": True},
    {"label": "Money Times",     "url": _gn_pt("site:moneytimes.com.br+Suzano"),       "filter": True},

    # ══ GOOGLE NEWS — EMPRESAS (queries focadas) ══════════════════════════════
    {"label": "Google News",     "url": _gn_en("VALE+iron+ore"),                       "filter": True},
    {"label": "Google News",     "url": _gn_en("Gerdau+steel"),                        "filter": True},
    {"label": "Google News",     "url": _gn_en("CSN+steel"),                           "filter": True},
    {"label": "Google News",     "url": _gn_en("Usiminas+steel"),                      "filter": True},
    {"label": "Google News",     "url": _gn_en("Suzano+pulp"),                         "filter": True},
    {"label": "Google News",     "url": _gn_en("Klabin+paper"),                        "filter": True},
    {"label": "Google News",     "url": _gn_en("Rio+Tinto+iron+ore"),                  "filter": True},
    {"label": "Google News",     "url": _gn_en("BHP+iron+ore"),                        "filter": True},
    {"label": "Google News",     "url": _gn_en("Fortescue+iron+ore"),                  "filter": True},
    {"label": "Google News",     "url": _gn_en("ArcelorMittal+steel"),                 "filter": True},
    {"label": "Google News",     "url": _gn_en("Simandou+iron+ore"),                   "filter": True},

    # ══ GOOGLE NEWS — MERCADO (queries simples e focadas) ═════════════════════
    {"label": "Google News",     "url": _gn_en("iron+ore+price"),                      "filter": True},
    {"label": "Google News",     "url": _gn_en("steel+price+HRC"),                     "filter": True},
    {"label": "Google News",     "url": _gn_en("coking+coal+price"),                   "filter": True},
    {"label": "Google News",     "url": _gn_en("steel+scrap+price"),                   "filter": True},
    {"label": "Google News",     "url": _gn_en("iron+ore+pellet"),                     "filter": True},
    {"label": "Google News",     "url": _gn_en("China+steel+output"),                  "filter": True},
    {"label": "Google News",     "url": _gn_en("steel+anti-dumping"),                  "filter": True},
    {"label": "Google News",     "url": _gn_en("green+steel"),                         "filter": True},
    {"label": "Google News",     "url": _gn_en("pulp+price+BHKP"),                     "filter": True},
    {"label": "Google News",     "url": _gn_en("tissue+paper+demand"),                 "filter": True},
    {"label": "Google News",     "url": _gn_en("dissolving+pulp"),                     "filter": True},
    {"label": "Google News",     "url": _gn_en("rebar+steel+price"),                   "filter": True},
    {"label": "Google News",     "url": _gn_en("DRI+direct+reduced+iron"),             "filter": True},
    {"label": "Google News",     "url": _gn_en("CBAM+steel"),                          "filter": True},

    # ══ GOOGLE NEWS — ASSOCIAÇÕES BRASILEIRAS ════════════════════════════════
    {"label": "Instituto Aço Brasil","url": _gn_pt("\"Instituto Aço Brasil\""),         "filter": True},
    {"label": "IBRAM",           "url": _gn_pt("IBRAM+mineração"),                      "filter": True},
    {"label": "Ibá",             "url": _gn_pt("\"Indústria Brasileira de Árvores\""),  "filter": True},
    {"label": "ANM",             "url": _gn_pt("\"Agência Nacional de Mineração\""),    "filter": True},
    {"label": "ABTCP",           "url": _gn_pt("ABTCP+celulose"),                       "filter": True},
    {"label": "Siderurgia Brasil","url": _gn_pt("\"Siderurgia Brasil\""),               "filter": True},
    {"label": "ANTAQ",           "url": _gn_pt("ANTAQ+minério"),                        "filter": True},

    # ══ GOOGLE NEWS — ASSOCIAÇÕES INTERNACIONAIS ══════════════════════════════
    {"label": "World Steel Assoc","url": _gn_en("\"World Steel Association\""),         "filter": True},
    {"label": "AISI",            "url": _gn_en("AISI+steel+production"),               "filter": True},
    {"label": "CISA",            "url": _gn_en("CISA+China+steel"),                    "filter": True},
    {"label": "CEPI",            "url": _gn_en("CEPI+paper"),                          "filter": True},

    # ══ GOOGLE NOTÍCIAS — PORTUGUÊS ════════════════════════════════════════════
    {"label": "Google Notícias", "url": _gn_pt("minério+de+ferro"),                    "filter": True},
    {"label": "Google Notícias", "url": _gn_pt("siderurgia+brasileira"),               "filter": True},
    {"label": "Google Notícias", "url": _gn_pt("preço+celulose"),                      "filter": True},
    {"label": "Google Notícias", "url": _gn_pt("barragem+rejeitos+mineração"),         "filter": True},
    {"label": "Google Notícias", "url": _gn_pt("CFEM+mineração"),                      "filter": True},
    {"label": "Google Notícias", "url": _gn_pt("aço+importação+dumping"),              "filter": True},
    {"label": "Google Notícias", "url": _gn_pt("pellets+embarques+minério"),           "filter": True},
]
