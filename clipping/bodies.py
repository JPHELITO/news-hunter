"""Busca do CORPO das matérias (HTML seguro) para o clipping.

- Autenticadas/SPA (Platts/Fastmarkets/Valor/Estadão): reader.py — fluxos DEDICADOS por fonte
  (portados do clipinator antigo), reaproveitando as sessões keep-alive do news-hunter
  (Supabase source_sessions p/ Platts/FM; state files em COOKIES_DIR p/ Valor/Estadão).
- Regulares (mining.com / portalcelulose.com.br / ...): requests+bs4 (_fetch_body_regular).

Retorna '' quando não conseguiu — o Word/e-mail mostram um aviso no lugar.
"""
from __future__ import annotations

import logging
from urllib.parse import urlparse

log = logging.getLogger(__name__)

# fontes com raspador dedicado (Playwright + sessão)
_AUTH = frozenset(["core.spglobal.com", "dashboard.fastmarkets.com",
                   "valor.globo.com", "www.estadao.com.br"])


def norm_domain(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if host.endswith("estadao.com.br"):
        return "www.estadao.com.br"
    if host.endswith("globo.com") and "valor" in host:
        return "valor.globo.com"
    return host[4:] if host.startswith("www.") else host


def fetch_body(url: str) -> str:
    """HTML seguro do corpo. Escolhe o método pela origem da URL."""
    dom = norm_domain(url)
    if dom in _AUTH:
        try:
            from .reader import fetch_article        # roda em thread com timeout de 90s
            _title, body = fetch_article(url)
            return body or ""
        except Exception as e:
            log.warning("clipping: corpo autenticado falhou (%s): %s", dom, e)
            return ""
    try:
        from .build import _fetch_body_regular
        return _fetch_body_regular(url) or ""
    except Exception as e:
        log.warning("clipping: corpo regular falhou (%s): %s", url, e)
        return ""
