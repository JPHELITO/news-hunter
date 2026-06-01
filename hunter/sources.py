"""
Fontes RSS do News Hunter.

filter=False → query já filtra (Google News) → aceita tudo
filter=True  → feed genérico → aplica keyword matching
"""

SOURCES = [
    # ══ Mining.com ════════════════════════════════════════════════════════════
    {
        "label": "Mining.com",
        "url": "https://www.mining.com/topics/iron-ore/feed/",
        "filter": False,   # feed temático: tudo é relevante
    },
    {
        "label": "Mining.com",
        "url": "https://www.mining.com/topics/steel/feed/",
        "filter": False,
    },
    {
        "label": "Mining.com",
        "url": "https://www.mining.com/topics/nickel/feed/",
        "filter": True,    # nickel precisa filtrar (nem sempre S&M)
    },
    {
        "label": "Mining.com",
        "url": "https://www.mining.com/topics/copper/feed/",
        "filter": True,
    },

    # ══ Google News — Inglês (pré-filtrado pela query) ════════════════════════
    {
        "label": "Google News",
        "url": "https://news.google.com/rss/search?q=iron+ore+price+steel&hl=en&gl=US&ceid=US:en",
        "filter": False,
    },
    {
        "label": "Google News",
        "url": "https://news.google.com/rss/search?q=VALE+iron+ore+mining&hl=en&gl=US&ceid=US:en",
        "filter": False,
    },
    {
        "label": "Google News",
        "url": "https://news.google.com/rss/search?q=Gerdau+OR+CSN+OR+Usiminas+steel&hl=en&gl=US&ceid=US:en",
        "filter": False,
    },
    {
        "label": "Google News",
        "url": "https://news.google.com/rss/search?q=ArcelorMittal+OR+Ternium+steel+production&hl=en&gl=US&ceid=US:en",
        "filter": False,
    },
    {
        "label": "Google News",
        "url": "https://news.google.com/rss/search?q=HRC+CRC+steel+price+flat&hl=en&gl=US&ceid=US:en",
        "filter": False,
    },
    {
        "label": "Google News",
        "url": "https://news.google.com/rss/search?q=iron+ore+China+import+price&hl=en&gl=US&ceid=US:en",
        "filter": False,
    },
    {
        "label": "Google News",
        "url": "https://news.google.com/rss/search?q=pulp+paper+cellulose+price&hl=en&gl=US&ceid=US:en",
        "filter": False,
    },
    {
        "label": "Google News",
        "url": "https://news.google.com/rss/search?q=Suzano+OR+Klabin+OR+Eldorado+pulp&hl=en&gl=US&ceid=US:en",
        "filter": False,
    },
    {
        "label": "Google News",
        "url": "https://news.google.com/rss/search?q=BHKP+OR+NBSK+pulp+price&hl=en&gl=US&ceid=US:en",
        "filter": False,
    },

    # ══ Google Notícias — Português ════════════════════════════════════════════
    {
        "label": "Google Notícias",
        "url": "https://news.google.com/rss/search?q=siderurgia+minério+aço&hl=pt-BR&gl=BR&ceid=BR:pt-419",
        "filter": False,
    },
    {
        "label": "Google Notícias",
        "url": "https://news.google.com/rss/search?q=VALE+CSN+Gerdau+Usiminas&hl=pt-BR&gl=BR&ceid=BR:pt-419",
        "filter": False,
    },
    {
        "label": "Google Notícias",
        "url": "https://news.google.com/rss/search?q=celulose+papel+Suzano+Klabin&hl=pt-BR&gl=BR&ceid=BR:pt-419",
        "filter": False,
    },
    {
        "label": "Google Notícias",
        "url": "https://news.google.com/rss/search?q=preço+aço+minério+de+ferro&hl=pt-BR&gl=BR&ceid=BR:pt-419",
        "filter": False,
    },
]
