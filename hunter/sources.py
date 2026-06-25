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
    # filter=True (keyword): Mining.com é AMPLO (ouro, lítio, urânio…) → aceitar tudo
    # floodaria ~93 extras/ciclo fora do foco. Keyword mantém iron-ore/copper/nickel +
    # empresas cobertas (Vale, Aura, Kinross). NÃO é fonte setorial estreita.
    {"label": "Mining.com",            "url": "https://www.mining.com/commodity/iron-ore/feed/", "filter": True},
    {"label": "Mining.com",            "url": "https://www.mining.com/commodity/copper/feed/",   "filter": True},
    {"label": "Mining.com",            "url": "https://www.mining.com/commodity/nickel/feed/",   "filter": True},
    {"label": "Mining.com",            "url": "https://www.mining.com/commodity/gold/feed/",     "filter": True},

    # ══ BRASIL — Imprensa econômica (RSS oficiais) ════════════════════════════
    {"label": "Valor Econômico",       "url": "https://pox.globo.com/rss/valor/empresas",         "filter": True},
    {"label": "Folha de S.Paulo",      "url": "https://feeds.folha.uol.com.br/mercado/rss091.xml","filter": True},
    {"label": "InfoMoney",             "url": "https://www.infomoney.com.br/feed/",              "filter": True},
    {"label": "Exame",                 "url": "https://exame.com/feed/",                         "filter": True},
    {"label": "Money Times",           "url": "https://www.moneytimes.com.br/feed/",             "filter": True},
    {"label": "UOL Economia",          "url": "https://rss.uol.com.br/feed/economia.xml",        "filter": True},
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

    # ══ INTERNACIONAL — proxy de sentimento Ásia/Oceania/Europa ═══════════════
    # Publicam ANTES do Brasil acordar → leitura antecipada do dia (China=minério/
    # aço/celulose; Austrália=mineração; Europa=aço/P&P). Adicionadas 2026-06-25
    # (auditoria de fontes). RSS oficiais, EN (o classificador lê EN), verificadas ao vivo.
    # Setoriais estreitas = filter=False (aceita tudo); amplas = filter=True (keyword).
    {"label": "GMK Center",            "url": "https://gmk.center/en/feed/",                          "filter": False},  # aço + minério global (EUROFER/China/trade)
    {"label": "China Pulp & Paper",    "url": "https://www.chinapulppaper.com/?format=feed&type=rss", "filter": False},  # P&P China → demanda p/ Suzano/Klabin/Eldorado
    {"label": "Papnews",               "url": "https://www.papnews.com/feed/",                        "filter": False},  # P&P global/Europa (já cita nomes BR)
    {"label": "SCMP China",            "url": "https://www.scmp.com/rss/318421/feed",                 "filter": True},   # China economia/demanda (amplo → keyword)
    {"label": "Australian Mining",     "url": "https://www.australianmining.com.au/feed/",            "filter": True},   # mineração AU: Rio/BHP/FMG (amplo → keyword)
]
