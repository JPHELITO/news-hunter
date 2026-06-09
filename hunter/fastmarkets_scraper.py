"""Scraper Fastmarkets PP News — Fase 1 apenas (headlines).

Intercepta POST /search/v3/query no dashboard PP News.
Retorna apenas título + snippet + link. Sem Fase 2 (sem corpo completo).
Sessão mantida viva via store remoto (roll-forward, hunter.playwright_session) + AUTO-LOGIN
de recuperação: se a sessão morrer, navega pro dashboard e deixa o redirect OAuth levar à
tela de login (auth.fastmarkets.com/?ReturnUrl=/connect/authorize/...), então preenche as
credenciais. Detalhe-chave: ir na URL PELADA de login NÃO funciona — tem que ser via o
redirect do dashboard (que preserva o ReturnUrl do OAuth). Fallback manual (raro):
scripts/capture_fastmarkets_session.py.
"""
from __future__ import annotations

import html as _html
import json
import logging
import re
from datetime import datetime, timezone

from .fetcher import RawArticle
from .playwright_session import (
    launch_browser,
    load_credentials,
    new_context,
    pull_session,
    run_in_thread,
    save_state,
    state_path,
)

log = logging.getLogger(__name__)

_DASHBOARD_URL = "https://dashboard.fastmarkets.com/w/rUks4Ah2y8TjDB8L8RtS9L/pp-news"
_MAX_AGE_HOURS = 72
_TIMEOUT = 180  # segundos máximos no thread

# Health: True se não conseguiu estabelecer sessão nesta execução (lido por hunt.py).
_login_failed = False


def _set_login_failed(v: bool) -> None:
    global _login_failed
    _login_failed = v


def get_fastmarkets_health() -> dict:
    """Saúde da última execução: {'login_failed': bool}. True = sessão não pôde ser
    estabelecida (expirada + autologin falhou, ou sem credenciais)."""
    return {"login_failed": _login_failed}


def _html_to_text(h: str) -> str:
    text = re.sub(r"<[^>]+>", " ", h)
    text = _html.unescape(text)
    text = text.replace("�", "")
    return re.sub(r"\s+", " ", text).strip()


def _parse_date(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
    except (ValueError, TypeError):
        return None


_LOGIN_HOST = "auth.fastmarkets.com"
_USER_SEL = "input[name='username'], input[id='userEmail'], input[type='email']"
_PASS_SEL = "input[name='password'], input[id='password']"
_SUBMIT_SEL = "button[id='login-button'], button:has-text('Sign in'), input[type='submit']"


def _fm_login(page, creds) -> bool:
    """Auto-login OAuth do FM. Vai pro dashboard e deixa o redirect levar à tela de login
    (auth.fastmarkets.com/?ReturnUrl=/connect/authorize/...) — esse ReturnUrl é o que faz a
    sessão valer pro dashboard (login na URL pelada falha). Preenche e envia. Nunca loga a senha.
    """
    log.info("fastmarkets_scraper: auto-login para %s...", creds["email"])
    try:
        got_login = False
        for _ in range(3):
            page.goto("https://dashboard.fastmarkets.com/", wait_until="load", timeout=45_000)
            try:
                page.wait_for_selector(_USER_SEL, timeout=22_000, state="visible")
                got_login = True
                break
            except Exception:
                continue
        if not got_login:
            log.warning("fastmarkets_scraper: tela de login não apareceu (dashboard não redirecionou)")
            return False
        page.fill(_USER_SEL, creds["email"])
        page.fill(_PASS_SEL, creds["password"])
        try:
            rm = page.query_selector("input[name='rememberMe']")
            if rm and not rm.is_checked():
                rm.check()
        except Exception:
            pass
        page.click(_SUBMIT_SEL)
        try:
            page.wait_for_url(
                lambda u: "dashboard.fastmarkets.com" in u and _LOGIN_HOST not in u, timeout=40_000)
        except Exception:
            page.wait_for_timeout(5_000)
        # Submetido. A confirmação REAL de "logado" é a API do dashboard responder (o loop
        # externo revisita o dashboard e valida). Login errado cai como não-autenticado lá →
        # vira login_failed. Por isso retornamos True após submeter (sem julgar pela URL).
        log.info("fastmarkets_scraper: login submetido (%s)",
                 "ja no dashboard" if _LOGIN_HOST not in page.url else "aguardando confirmacao")
        return True
    except Exception as e:
        log.warning("fastmarkets_scraper: auto-login erro: %s", e)
        return False


def _scrape() -> list[RawArticle]:
    """Executa em thread. Fase 1 apenas — intercepta API, sem navegar artigos."""
    from playwright.sync_api import sync_playwright

    pull_session("fastmarkets")          # puxa a sessão rolada-pra-frente do store remoto
    state_file = state_path("fastmarkets")
    creds = load_credentials("fastmarkets")
    if not state_file.exists() and not creds:
        log.warning("fastmarkets_scraper: sem sessão nem credenciais "
                    "(rode scripts/capture_fastmarkets_session.py ou configure FASTMARKETS_CREDENTIALS)")
        _set_login_failed(True)
        return []

    results_meta: list[dict] = []
    seen_ids: set[str] = set()
    api_seen = {"v": False}  # True quando a API de notícias responde = sessão autenticada

    def on_response(response):
        url = response.url
        if "search/v3/query" not in url or response.request.method != "POST":
            return
        api_seen["v"] = True  # o dashboard só chama essa API quando logado
        try:
            try:
                raw = response.text()
            except Exception:
                raw = response.body().decode("utf-8", errors="replace")
            raw = raw.replace("�", "")
            data = json.loads(raw)
            result_list = data if isinstance(data, list) else [data]
            for wrap in result_list:
                for item in wrap.get("result", {}).get("values", []):
                    if item.get("type") != "newsArticle":
                        continue
                    art = item["newsArticle"]
                    article_id = art.get("id", "")
                    if not article_id or article_id in seen_ids:
                        continue
                    seen_ids.add(article_id)
                    summary_html = art.get("summary") or ""
                    body_html    = (art.get("bodyHtml") or art.get("body") or
                                   art.get("content") or "")
                    snippet = (
                        _html_to_text(summary_html)[:360]
                        or _html_to_text(body_html)[:360]
                    )
                    results_meta.append({
                        "id": article_id,
                        "title": art.get("title") or "",
                        "snippet": snippet,
                        "published_at": art.get("publishedDate"),
                        "url": f"https://dashboard.fastmarkets.com/a/{article_id}",
                    })
        except Exception as e:
            log.debug("fastmarkets_scraper: parse error: %s", e)

    def _authenticated() -> bool:
        # FM NÃO redireciona pro login quando a sessão cai — o dashboard fica em branco.
        # O sinal confiável de "logado" é a API de notícias (search/v3/query) ter respondido.
        return api_seen["v"] or bool(results_meta)

    with sync_playwright() as p:
        browser = launch_browser(p)
        ctx = new_context(browser, "fastmarkets", on_response=on_response,
                          use_state=state_file.exists())
        page = ctx.new_page()

        try:
            log.info("fastmarkets_scraper: carregando dashboard PP News...")
            page.goto(_DASHBOARD_URL, wait_until="domcontentloaded", timeout=45_000)
            # Sessão viva = a API de notícias (search/v3/query) responde. Espera até ~20s.
            for _ in range(20):
                if _authenticated():
                    break
                page.wait_for_timeout(1_000)

            # Warm-state morto? Tenta o auto-login (redirect OAuth do dashboard) e revisita.
            if not _authenticated() and creds:
                if _fm_login(page, creds):
                    api_seen["v"] = False
                    page.goto(_DASHBOARD_URL, wait_until="domcontentloaded", timeout=45_000)
                    for _ in range(20):
                        if _authenticated():
                            break
                        page.wait_for_timeout(1_000)

            ok = _authenticated()
            _set_login_failed(not ok)
            if not ok:
                log.warning("fastmarkets_scraper: não autenticou (warm-state morto + auto-login "
                            "falhou). Se persistir, re-seed via capture_fastmarkets_session.py")
                return []
            # Autenticado: rola a sessão pra frente (salva a versão renovada local + store).
            save_state(ctx, "fastmarkets")
            log.info("fastmarkets_scraper: %d headlines interceptados", len(results_meta))
        except Exception as e:
            log.warning("fastmarkets_scraper: erro na navegacao: %s", e)
        finally:
            page.close()
            browser.close()

    # Filtra por janela de tempo e converte para RawArticle
    now_utc = datetime.now(timezone.utc)
    out: list[RawArticle] = []
    for m in results_meta:
        pub = _parse_date(m.get("published_at"))
        if pub and (now_utc - pub).total_seconds() / 3600 > _MAX_AGE_HOURS:
            continue
        if not m.get("title"):
            continue
        out.append(RawArticle(
            url=m["url"],
            domain="dashboard.fastmarkets.com",
            source_name="Fastmarkets",
            title=m["title"],
            snippet=m.get("snippet", ""),
            published_at=pub,
            found_at=now_utc,
            needs_filter=False,  # fonte dedicada de P&P — aceita tudo
        ))
    return out


def collect_fastmarkets_headlines() -> list[RawArticle]:
    """Ponto de entrada — executa em thread com timeout."""
    return run_in_thread(_scrape, _TIMEOUT, "fastmarkets")
