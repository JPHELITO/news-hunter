"""Helpers compartilhados de sessão/login Playwright para scrapers de provedores.

Concentra a parte genérica (launch do browser, criação de contexto com storage_state,
carregamento de credenciais, detecção de página de login, re-save do state e o loop
"detecta login → faz login → re-salva → tenta de novo") usada por scrapers que dependem
de sessão autenticada (Platts, Fastmarkets).

As partes específicas de cada provedor — URL alvo, o preenchimento do formulário de login
e a interceptação/scraping de respostas — ficam em cada scraper e são INJETADAS aqui via
callables. Este módulo NÃO importa nenhum scraper (dependência one-way, sem ciclo).
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Callable

from .cookies import get_cookies_dir

log = logging.getLogger(__name__)

# Constantes copiadas verbatim dos scrapers existentes para manter comportamento idêntico.
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
DEFAULT_VIEWPORT = {"width": 1440, "height": 900}
DEFAULT_LOCALE = "en-US"
_LAUNCH_ARGS = ["--disable-blink-features=AutomationControlled"]
_WEBDRIVER_INIT = "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"


# ───────────────────────────────────────────────────────────────────────────
# Credenciais e state
# ───────────────────────────────────────────────────────────────────────────
def load_credentials(provider: str) -> dict | None:
    """Lê {provider}_credentials.json de get_cookies_dir(). Retorna dict com
    'email'/'password' ou None se ausente/inválido."""
    creds_file = get_cookies_dir() / f"{provider}_credentials.json"
    if not creds_file.exists():
        return None
    try:
        c = json.loads(creds_file.read_text(encoding="utf-8"))
        if c.get("email") and c.get("password"):
            return c
    except Exception:
        pass
    return None


def state_path(provider: str) -> Path:
    """Caminho do arquivo de storage_state do provedor."""
    return get_cookies_dir() / f"{provider}_state.json"


def save_state(ctx, provider: str) -> None:
    """Re-salva o storage_state (cookies + localStorage) do contexto após login."""
    try:
        sp = state_path(provider)
        sp.write_text(json.dumps(ctx.storage_state()), encoding="utf-8")
        log.info("%s: storage_state salvo em %s", provider, sp)
    except Exception as e:
        log.warning("%s: falha ao salvar storage_state: %s", provider, e)


# ───────────────────────────────────────────────────────────────────────────
# Detecção de página de login
# ───────────────────────────────────────────────────────────────────────────
def is_login_page(page, login_hosts: tuple[str, ...] = ()) -> bool:
    """Heurística genérica: URL contém marcador de login, host de auth do provedor,
    ou o título indica tela de login."""
    url = (page.url or "").lower()
    if any(h.lower() in url for h in login_hosts):
        return True
    if any(x in url for x in ("/login", "/signin", "/auth")):
        return True
    try:
        title = (page.title() or "").lower()
        if any(x in title for x in ("sign in", "log in", "login")):
            return True
    except Exception:
        pass
    return False


# ───────────────────────────────────────────────────────────────────────────
# Browser / contexto
# ───────────────────────────────────────────────────────────────────────────
def launch_browser(p):
    """Launch headless: tenta o Chrome do sistema (channel='chrome'); se indisponível,
    cai pro Chromium empacotado. Sempre com a flag anti-automação."""
    try:
        return p.chromium.launch(headless=True, args=_LAUNCH_ARGS, channel="chrome")
    except Exception:
        return p.chromium.launch(headless=True, args=_LAUNCH_ARGS)


def new_context(browser, provider: str, on_response: Callable | None = None,
                use_state: bool = True):
    """Cria contexto com UA/viewport/locale padrão. Usa storage_state do provedor se
    existir e use_state=True. Aplica init script de webdriver e registra on_response."""
    kwargs: dict = {
        "user_agent": DEFAULT_USER_AGENT,
        "viewport": DEFAULT_VIEWPORT,
        "locale": DEFAULT_LOCALE,
    }
    sp = state_path(provider)
    if use_state and sp.exists():
        kwargs["storage_state"] = str(sp)
    ctx = browser.new_context(**kwargs)
    ctx.add_init_script(_WEBDRIVER_INIT)
    if on_response is not None:
        ctx.on("response", on_response)
    return ctx


# ───────────────────────────────────────────────────────────────────────────
# Loop de navegação com auto-login
# ───────────────────────────────────────────────────────────────────────────
def navigate_with_login(page, ctx, provider: str, *, target_url: str,
                        login_fn: Callable, login_hosts: tuple[str, ...] = (),
                        max_attempts: int = 2, post_nav: Callable | None = None,
                        goto_timeout: int = 45_000, pre_check_wait_ms: int = 0) -> bool:
    """Navega até target_url; se cair em página de login, chama login_fn(page, ctx) e
    re-salva o state, tentando de novo (até max_attempts). Retorna True se terminou numa
    página NÃO-login.

    login_fn deve apenas preencher/submeter o formulário e retornar bool (sucesso); o
    re-save do state é feito AQUI (fonte única), evitando double-save. post_nav(page) é
    um hook opcional executado após uma navegação bem-sucedida (ex.: esperar networkidle).

    pre_check_wait_ms: espera após cada goto ANTES de checar login — necessário para apps
    SPA que redirecionam pro login via JS (client-side) alguns segundos depois do load
    (ex.: core.spglobal.com → /login). Sem essa espera, a checagem roda cedo demais e
    "acha" que está logado.
    """
    attempts = 0
    while attempts <= max_attempts:
        page.goto(target_url, wait_until="domcontentloaded", timeout=goto_timeout)
        if pre_check_wait_ms:
            page.wait_for_timeout(pre_check_wait_ms)
        if is_login_page(page, login_hosts):
            attempts += 1
            if attempts > max_attempts:
                log.warning("%s: sessão expirada após %d tentativas de login",
                            provider, max_attempts)
                return False
            if not login_fn(page, ctx):
                return False
            save_state(ctx, provider)
            continue
        if post_nav is not None:
            post_nav(page)
        if not is_login_page(page, login_hosts):
            return True
    return False


# ───────────────────────────────────────────────────────────────────────────
# Execução em thread com timeout (padrão dos scrapers)
# ───────────────────────────────────────────────────────────────────────────
def run_in_thread(scrape_fn: Callable[[], list], timeout: int, provider: str) -> list:
    """Roda scrape_fn() numa daemon thread com join(timeout). Retorna [] em timeout/erro."""
    out: list = []
    err: list = []

    def run():
        try:
            out.extend(scrape_fn())
        except Exception as e:
            err.append(e)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout=timeout)
    if t.is_alive():
        log.warning("%s: timeout após %ds", provider, timeout)
        return []
    if err:
        log.warning("%s: erro: %s", provider, err[0])
        return []
    return out
