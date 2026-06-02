"""Scraper Fastmarkets PP News — Fase 1 apenas (headlines).

Intercepta POST /search/v3/query no dashboard PP News.
Retorna apenas título + snippet + link. Sem Fase 2 (sem corpo completo).
Suporta auto-login com fastmarkets_credentials.json.
"""
from __future__ import annotations

import html as _html
import json
import logging
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

from .cookies import get_cookies_dir
from .fetcher import RawArticle

log = logging.getLogger(__name__)

_DASHBOARD_URL = "https://dashboard.fastmarkets.com/w/rUks4Ah2y8TjDB8L8RtS9L/pp-news"
_LOGIN_URL     = "https://auth.fastmarkets.com/"
_MAX_AGE_HOURS = 72
_MAX_LOGIN_ATTEMPTS = 2
_TIMEOUT = 180  # segundos máximos no thread


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


def _load_credentials() -> dict | None:
    creds_file = get_cookies_dir() / "fastmarkets_credentials.json"
    if not creds_file.exists():
        return None
    try:
        c = json.loads(creds_file.read_text(encoding="utf-8"))
        if c.get("email") and c.get("password"):
            return c
    except Exception:
        pass
    return None


def _is_login_page(page) -> bool:
    url = page.url.lower()
    if "auth.fastmarkets.com" in url or "/login" in url or "/signin" in url:
        return True
    try:
        title = page.title().lower()
        if any(x in title for x in ("sign in", "log in", "login")):
            return True
    except Exception:
        pass
    return False


def _do_auto_login(page, ctx) -> bool:
    creds = _load_credentials()
    if not creds:
        log.warning("fastmarkets_scraper: sem credenciais para auto-login")
        return False
    log.info("fastmarkets_scraper: auto-login para %s...", creds["email"])
    try:
        if not _is_login_page(page):
            page.goto(_LOGIN_URL, wait_until="domcontentloaded", timeout=20_000)
            page.wait_for_timeout(2_000)
        page.wait_for_selector("input[name='username'], input[id='userEmail']", timeout=10_000)
        page.fill("input[name='username']", creds["email"])
        page.wait_for_selector("input[name='password'], input[id='password']", timeout=5_000)
        page.fill("input[name='password']", creds["password"])
        try:
            rm = page.query_selector("input[name='rememberMe']")
            if rm and not rm.is_checked():
                rm.check()
        except Exception:
            pass
        page.click("button[id='login-button'], button[type='submit']")
        try:
            page.wait_for_url("https://dashboard.fastmarkets.com/**", timeout=25_000)
        except Exception:
            page.wait_for_timeout(3_000)
            if _is_login_page(page):
                log.warning("fastmarkets_scraper: auto-login falhou")
                return False
        try:
            page.wait_for_load_state("networkidle", timeout=15_000)
        except Exception:
            page.wait_for_timeout(5_000)
        if _is_login_page(page):
            return False
        # Salva novo state
        state_file = get_cookies_dir() / "fastmarkets_state.json"
        new_state = ctx.storage_state()
        state_file.write_text(json.dumps(new_state), encoding="utf-8")
        log.info("fastmarkets_scraper: auto-login OK — state salvo")
        return True
    except Exception as e:
        log.warning("fastmarkets_scraper: auto-login erro: %s", e)
        return False


def _scrape() -> list[RawArticle]:
    """Executa em thread. Fase 1 apenas — intercepta API, sem navegar artigos."""
    from playwright.sync_api import sync_playwright

    state_file = get_cookies_dir() / "fastmarkets_state.json"
    creds = _load_credentials()
    if not state_file.exists() and not creds:
        log.warning("fastmarkets_scraper: sem state file nem credenciais")
        return []

    results_meta: list[dict] = []
    seen_ids: set[str] = set()

    def on_response(response):
        url = response.url
        if "search/v3/query" not in url or response.request.method != "POST":
            return
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

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled"],
                channel="chrome",
            )
        except Exception:
            browser = p.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled"],
            )

        ctx_kwargs: dict = {
            "user_agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            "viewport": {"width": 1440, "height": 900},
            "locale": "en-US",
        }
        if state_file.exists():
            ctx_kwargs["storage_state"] = str(state_file)

        ctx = browser.new_context(**ctx_kwargs)
        ctx.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        ctx.on("response", on_response)
        page = ctx.new_page()

        login_attempts = 0
        try:
            log.info("fastmarkets_scraper: carregando dashboard PP News...")
            while login_attempts <= _MAX_LOGIN_ATTEMPTS:
                page.goto(_DASHBOARD_URL, wait_until="domcontentloaded", timeout=45_000)
                if _is_login_page(page):
                    login_attempts += 1
                    if login_attempts > _MAX_LOGIN_ATTEMPTS:
                        log.warning("fastmarkets_scraper: sessão expirada após %d tentativas", _MAX_LOGIN_ATTEMPTS)
                        return []
                    if not _do_auto_login(page, ctx):
                        return []
                    continue
                try:
                    page.wait_for_load_state("networkidle", timeout=30_000)
                except Exception:
                    page.wait_for_timeout(15_000)
                if not _is_login_page(page):
                    break

            log.info("fastmarkets_scraper: %d headlines interceptados", len(results_meta))
        except Exception as e:
            log.warning("fastmarkets_scraper: erro na navegação: %s", e)
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
    out: list[RawArticle] = []
    err: list[Exception] = []

    def run():
        try:
            out.extend(_scrape())
        except Exception as e:
            err.append(e)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout=_TIMEOUT)
    if t.is_alive():
        log.warning("fastmarkets_scraper: timeout após %ds", _TIMEOUT)
        return []
    if err:
        log.warning("fastmarkets_scraper: erro: %s", err[0])
        return []
    return out
