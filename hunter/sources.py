"""
Fontes RSS do News Hunter — Steel & Mining + Pulp & Paper.

Apenas RSS direto dos próprios sites. Sem Google News.
Fontes sem RSS público são tratadas em hunter/html_scrapers.py.

filter=False → feed temático/setorial → aceita tudo
filter=True  → feed geral → aplica keyword matching
"""

SOURCES = [
    # ══ MINING.COM — feeds por commodity ══════════════════════════════════════
    # (os feeds de CATEGORIA steel/mining retornam 404 — só existem os de commodity)
    {"label": "Mining.com",            "url": "https://www.mining.com/commodity/iron-ore/feed/", "filter": False},
    {"label": "Mining.com",            "url": "https://www.mining.com/commodity/copper/feed/",   "filter": False},
    {"label": "Mining.com",            "url": "https://www.mining.com/commodity/nickel/feed/",   "filter": False},
    {"label": "Mining.com",            "url": "https://www.mining.com/commodity/gold/feed/",     "filter": False},

    # ══ BRASIL — Imprensa econômica (RSS oficiais) ════════════════════════════
    {"label": "Valor Econômico",       "url": "https://pox.globo.com/rss/valor/empresas",         "filter": True},
    {"label": "Folha de S.Paulo",      "url": "https://feeds.folha.uol.com.br/mercado/rss091.xml","filter": True},
    {"label": "G1 Economia",           "url": "https://g1.globo.com/rss/g1/economia/",           "filter": True},
    {"label": "InfoMoney",             "url": "https://www.infomoney.com.br/feed/",              "filter": True},
    {"label": "Exame",                 "url": "https://exame.com/feed/",                         "filter": True},
    {"label": "Money Times",           "url": "https://www.moneytimes.com.br/feed/",             "filter": True},
    {"label": "Metrópoles",            "url": "https://www.metropoles.com/feed",                 "filter": True},
    {"label": "UOL Economia",          "url": "https://rss.uol.com.br/feed/economia.xml",        "filter": True},
    {"label": "Veja",                  "url": "https://veja.abril.com.br/feed/",                 "filter": True},
    # CNN Brasil: migrado de scraper HTML (frágil, por classe CSS) p/ RSS oficial
    # (60 itens, com data) — mais robusto. Feed geral → keyword filtering.
    {"label": "CNN Brasil",            "url": "https://www.cnnbrasil.com.br/feed/",              "filter": True},

    # ══ BRASIL — Setoriais (RSS oficiais) ═════════════════════════════════════
    {"label": "Portal Celulose",       "url": "https://portalcelulose.com.br/feed/",             "filter": False},
    {"label": "Siderurgia Brasil",     "url": "https://siderurgiabrasil.com.br/feed/",           "filter": False},
    {"label": "Ibá",                   "url": "https://iba.org/feed/",                           "filter": False},
    {"label": "ABTCP",                 "url": "https://newspulpaper.com/feed/",                  "filter": False},
    # Instituto Aço Brasil e IBRAM: RSS morto/congelado → movidos para
    # html_scrapers.py (scraping da página de notícias).
]
