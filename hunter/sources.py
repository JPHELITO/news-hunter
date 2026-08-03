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
    # ⚠️ 2026-07-27: iron-ore/copper/nickel são TEMÁTICOS e on-topic (Vale faz minério +
    # base metals) → filter=False (aceita tudo, só title-blocklist), como as outras setoriais.
    # Antes tinham filter=True (keyword), mas "gold"/"copper"/"nickel" NÃO estão em ALL_KEYWORDS
    # (só "iron ore"), então gutavam os feeds: medido copper 42%, nickel 31% passavam — o resto
    # sumia do feed E do clipping. GOLD FICA filter=True: ~94% é júnior fora da cobertura
    # (Barrick/Newmont/Agnico) — aceitar-tudo inundaria e queimaria as 4 IAs grátis; as cobertas
    # (Aura/Kinross/Eldorado) seguem passando por keyword.
    {"label": "Mining.com",            "url": "https://www.mining.com/commodity/iron-ore/feed/", "filter": False},
    {"label": "Mining.com",            "url": "https://www.mining.com/commodity/copper/feed/",   "filter": False},
    {"label": "Mining.com",            "url": "https://www.mining.com/commodity/nickel/feed/",   "filter": False},
    {"label": "Mining.com",            "url": "https://www.mining.com/commodity/gold/feed/",     "filter": True},
    # 2026-08-03 (pedido do usuário): categoria CRITICAL MINERALS — aceita-tudo. Os feeds de
    # commodity NÃO cobriam essa categoria (cobre/níquel/terras raras/lítio + policy), então o
    # feed geral perdia coisas como "Trump expected to attend event with mining execs amid
    # critical minerals push". On-topic p/ a cobertura (base metals/minerais críticos).
    {"label": "Mining.com",            "url": "https://www.mining.com/category/critical-minerals/feed/", "filter": False},

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
