"""Busca do CORPO das matérias (HTML seguro) para o clipping.

- Regular (mining.com/GMK/...): requests+bs4 (reusa _fetch_body_regular do build.py).
- Autenticadas/SPA (Platts/Fastmarkets/Valor/Estadão): Playwright reaproveitando as sessões
  keep-alive do news-hunter (source_sessions no Supabase, via hunter.playwright_session).
  Estadão dispensa login (bypass Zephr); Platts usa o DOM-walker dedicado (.newsSection-body[0]).

Retorna '' quando não conseguiu — o Word/e-mail mostram um aviso no lugar. A validação
end-to-end das fontes AUTENTICADAS acontece na Fase 3 (com as sessões vivas no Actions);
o caminho REGULAR é provado localmente já na Fase 2.
"""
from __future__ import annotations

import logging
import os
from urllib.parse import urlparse

log = logging.getLogger(__name__)

# domínio → provider de sessão do news-hunter (source_sessions)
_PROVIDER = {"core.spglobal.com": "platts", "dashboard.fastmarkets.com": "fastmarkets"}
# fontes que exigem Playwright (com ou sem sessão)
_AUTH = frozenset(["core.spglobal.com", "dashboard.fastmarkets.com",
                   "valor.globo.com", "www.estadao.com.br"])
_TIMEOUT_S = 90


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
            return _fetch_authed(url, dom) or ""
        except Exception as e:  # nunca deixa a geração cair por causa de 1 corpo
            log.warning("clipping: corpo autenticado falhou (%s): %s", dom, e)
            return ""
    try:
        from .build import _fetch_body_regular
        return _fetch_body_regular(url) or ""
    except Exception as e:
        log.warning("clipping: corpo regular falhou (%s): %s", url, e)
        return ""


# ── Autenticado (Playwright) ──────────────────────────────────────────────────
def _fetch_authed(url: str, domain: str) -> str:
    from playwright.sync_api import sync_playwright
    provider = _PROVIDER.get(domain)

    def run() -> str:
        ps = None
        if provider:
            try:
                from hunter import playwright_session as ps_mod
                ps = ps_mod
                ps.pull_session(provider)          # rola a sessão do Supabase p/ o state local
            except Exception as e:
                log.warning("clipping: sessão %s indisponível: %s", provider, e)
        with sync_playwright() as p:
            browser = ps.launch_browser(p) if ps else p.chromium.launch(headless=True)
            ctx = _make_context(browser, provider, domain, ps)
            page = ctx.new_page()
            try:
                if domain == "core.spglobal.com":
                    return _extract_platts(page, url)
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(1800)        # deixa o SPA hidratar / bypass Zephr
                return _extract_generic(page, url)
            finally:
                try:
                    browser.close()
                except Exception:
                    pass

    # roda numa thread com timeout quando temos o helper do news-hunter
    if provider:
        try:
            from hunter import playwright_session as ps_mod
            res = ps_mod.run_in_thread(run, _TIMEOUT_S, provider)
            return res if isinstance(res, str) else ""
        except Exception:
            pass
    return run()


def _make_context(browser, provider, domain, ps):
    # Platts/Fastmarkets: contexto com o storage_state da sessão do news-hunter
    if ps and provider:
        try:
            return ps.new_context(browser, provider)
        except Exception as e:
            log.warning("clipping: new_context(%s): %s", provider, e)
    # Valor/Estadão: sessão local opcional em COOKIES_DIR (Estadão funciona sem login)
    state = None
    ck = os.environ.get("COOKIES_DIR")
    if ck:
        from pathlib import Path
        key = {"valor.globo.com": "valor", "www.estadao.com.br": "estadao"}.get(domain)
        f = (Path(ck) / f"{key}_state.json") if key else None
        if f and f.exists():
            state = str(f)
    return browser.new_context(storage_state=state) if state else browser.new_context()


def _extract_generic(page, url: str) -> str:
    from bs4 import BeautifulSoup
    from .html_utils import article_to_safe_html, extract_article_container
    soup = BeautifulSoup(page.content(), "lxml")
    container = extract_article_container(soup, url)
    return article_to_safe_html(str(container)) if container else ""


def _extract_platts(page, url: str) -> str:
    # Platts (Angular SPA): DOM-walker dedicado — SEMPRE .newsSection-body[0] (regra inviolável)
    from .html_utils import PLATTS_DOM_WALK_JS, platts_dom_items_to_html
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(3500)
    try:
        page.wait_for_selector(".newsSection-body", timeout=15000)
    except Exception:
        pass
    data = page.evaluate(PLATTS_DOM_WALK_JS)
    return platts_dom_items_to_html(data, page) if data else ""
