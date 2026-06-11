"""Scraper Platts (S&P Global) — Fase 1 apenas (headlines) + preços.

Intercepta POST content-bff/v1/search para headlines.
Intercepta JSON responses durante navegação ao workspace para preços IODEX.
Retorna apenas título + snippet + link. Sem Fase 2 (sem corpo completo).
Requer platts_state.json (sessão válida do browser).
"""
from __future__ import annotations

import html
import json
import logging
import re
from datetime import datetime, timezone
from urllib.parse import quote

from .fetcher import RawArticle
from .playwright_session import (
    is_login_page,
    launch_browser,
    load_credentials,
    navigate_with_login,
    new_context,
    pull_session,
    run_in_thread,
    save_state,
    state_path,
)

log = logging.getLogger(__name__)

# Símbolos de preço que queremos capturar do workspace Platts:
#   IODBZ00 = Iron Ore 61% (IODEX CFR China) | STHRZ02 = HRC China
#   STCBM00 = Rebar Turkey                    | PLVHA00 = Asian Met Coal
_PRICE_SYMBOLS = {
    # Watchlist 'Dashboard' do Platts — capturados via feed de rede (_extract_prices)
    # e/ou DOM. Mantido em sincronia com PLATTS_COMMODITIES em prices.py.
    "IODBZ00", "STHRZ02", "STCBM00", "PLVHA00",          # core
    "IOPRM00", "IODFE00", "IOMGD00",                     # IO grades/diff
    "IOPBQ00", "IOBBA00", "IONHA00", "IOMAA00", "IOJBA00",  # IO marcas/blends
    "IOBFC04", "IOFBC00", "IOFAC00",                     # pellet premium + frete
    "TSIPQ01", "TSIPQ02", "TSIPQ03", "TSIPY01",          # forwards
    "HCCAU00",                                            # HCC low vol
}

# Cache de preços preenchido pelo _scrape() como efeito colateral.
# Lido pelo thread principal após join via get_platts_prices().
_platts_prices: dict[str, dict] = {}

# Health: True se não conseguiu estabelecer sessão nesta execução (lido por hunt.py
# após o run, para o "sinal de vida" / watchdog). Processo CI roda 1x → começa False.
_login_failed = False


def _set_login_failed(v: bool) -> None:
    global _login_failed
    _login_failed = v


def get_platts_health() -> dict:
    """Saúde da última execução: {'login_failed': bool}. True = sessão não pôde ser
    estabelecida (expirada + autologin falhou, ou sem credenciais)."""
    return {"login_failed": _login_failed}

# "Rationale" foi REMOVIDO deliberadamente: por regra de negócio (ver imagem de
# regras Platts — "NÃO usar notícias Rationale"), esse tipo de conteúdo não entra
# no relatório. Barrado aqui na origem (ContentType) + por título no filter.py.
_WANTED_TYPES = {"News", "Top News", "Flash", "Market Commentary",
                 "Blog", "Headline Analysis"}

_TIMEOUT = 180  # segundos máximos no thread (login a frio adiciona gotos + waits)


def _html_to_text(h: str) -> str:
    text = re.sub(r"<[^>]+>", " ", h)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _parse_date(s: str | None) -> datetime | None:
    if not s or s.startswith("0001"):
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
    except (ValueError, TypeError):
        return None


def _article_url(article_id: str, content_type: str = "News") -> str:
    ct = quote(content_type, safe="")
    return f"https://core.spglobal.com/#platts/insightsArticle?articleID={article_id}&insightsType={ct}"


def _parse_price(text: str) -> float | None:
    """Converte texto de preço para float, lidando com vírgula decimal (103,35)
    e separador de milhar (1.234,56 ou 1,234.56)."""
    if not text:
        return None
    t = text.strip().replace(" ", "")
    # Remove qualquer símbolo de moeda / sufixo
    t = re.sub(r"[^\d.,\-]", "", t)
    if not t:
        return None
    has_comma = "," in t
    has_dot = "." in t
    try:
        if has_comma and has_dot:
            # O último separador é o decimal
            if t.rfind(",") > t.rfind("."):
                t = t.replace(".", "").replace(",", ".")   # 1.234,56 → 1234.56
            else:
                t = t.replace(",", "")                      # 1,234.56 → 1234.56
        elif has_comma:
            # Só vírgula → decimal europeu/brasileiro (103,35 → 103.35)
            t = t.replace(",", ".")
        return float(t)
    except (ValueError, TypeError):
        return None


# JS para ler preços direto da tabela AG-Grid renderizada (DOM).
# Mais robusto que interceptar rede — lê exatamente o que está na tela.
_DOM_PRICE_JS = """
() => {
  // Lê a watchlist INTEIRA da grid AG-Grid (a 'Dashboard' é a config do usuário):
  // cada linha vira {price, change%, desc}. Descobre as colunas pelo cabeçalho.
  const norm = el => (el.textContent||'').trim().toLowerCase().replace(/\\s+/g,'');
  let symCol=null, descCol=null, priceCol=null, chgCol=null;
  document.querySelectorAll('.ag-header-cell, [role="columnheader"]').forEach(h => {
    const t = norm(h), id = h.getAttribute('col-id');
    if (!id) return;
    if (!symCol  && (t==='symbol'||t==='code'||t==='ticker'||t==='mdcsymbol')) symCol=id;
    if (!descCol && (t.indexOf('description')!==-1||t==='name'||t==='symbolname'||t==='symboldescription')) descCol=id;
    if (!priceCol&& (t==='price'||t==='bate'||t==='value'||t==='last'||t==='bid'||t==='assessment'||t==='mid')) priceCol=id;
    if (!chgCol  && t.indexOf('change')!==-1 && t.indexOf('%')!==-1) chgCol=id;
  });
  const SYM_RE = /^[A-Z][A-Z0-9]{4,9}$/;          // símbolo Platts (ex.: IODBZ00)
  const cellOf = (row,id) => { if(!id) return null; const c=row.querySelector('[col-id="'+id+'"]'); return c?(c.textContent||'').trim():null; };
  const out = {};
  document.querySelectorAll('.ag-row, [role="row"]').forEach(row => {
    let sym = symCol ? cellOf(row,symCol) : null;
    if (!sym) {                                   // fallback: célula que pareça símbolo
      row.querySelectorAll('.ag-cell, [role="gridcell"], td').forEach(c => {
        const t=(c.textContent||'').trim(); if(!sym && SYM_RE.test(t)) sym=t;
      });
    }
    if (!sym || !SYM_RE.test(sym)) return;        // ignora cabeçalho/linhas de grupo
    let price = priceCol ? cellOf(row,priceCol) : null;
    if (!price) {                                 // fallback: 1ª célula numérica
      row.querySelectorAll('.ag-cell, [role="gridcell"], td').forEach(c => {
        const t=(c.textContent||'').trim();
        if(!price && /\\d/.test(t) && /^-?[\\d.,]{1,12}$/.test(t)) price=t;
      });
    }
    if (!price) return;
    out[sym] = {price: price, change: chgCol?cellOf(row,chgCol):null, desc: descCol?cellOf(row,descCol):null};
  });
  return {rows: out, rowCount: document.querySelectorAll('.ag-row, [role="row"]').length,
          cols: {sym: !!symCol, desc: !!descCol, price: !!priceCol, chg: !!chgCol}};
}
"""


def _extract_prices(data, out: dict) -> None:
    """Busca recursivamente preços dos símbolos _PRICE_SYMBOLS em qualquer resposta JSON."""
    if isinstance(data, dict):
        sym = (data.get("symbol") or data.get("Symbol") or
               data.get("code")   or data.get("Code")   or
               data.get("ticker") or data.get("Ticker") or "")
        if sym in _PRICE_SYMBOLS:
            price_val = (
                data.get("price")          or data.get("Price")          or
                data.get("value")          or data.get("Value")          or
                data.get("latestValue")    or data.get("assessedPrice")  or
                data.get("closePrice")     or data.get("settlementPrice") or
                data.get("lastPrice")      or data.get("midPrice")
            )
            if price_val is not None:
                try:
                    out[sym] = {"price": float(price_val)}
                    log.info("platts_prices: %s = %s", sym, price_val)
                except (TypeError, ValueError):
                    pass
        for v in data.values():
            _extract_prices(v, out)
    elif isinstance(data, list):
        for item in data:
            _extract_prices(item, out)


# ───────────────────────────────────────────────────────────────────────────
# Auto-login (Okta, core.spglobal.com) — 2 passos: identifier → Next → senha → submit
# ───────────────────────────────────────────────────────────────────────────
_LOGIN_URL = "https://core.spglobal.com/login"
_LOGIN_HOSTS = ("core.spglobal.com/login", "okta")
_MAX_LOGIN_ATTEMPTS = 2

# Seletores multi-candidato: só o passo 1 do Okta foi reconhecido no DOM real;
# os do passo 2 (senha) usam fallbacks padrão do widget Okta.
_ID_SELECTORS = (
    "input[name='identifier']",
    "input[autocomplete='username']",
    "input[type='email']",
    "input[name='username']",
)
_NEXT_SELECTORS = (
    "input[type='submit'][value='Next']",
    "button:has-text('Next')",
    "input[type='submit']",
    "button[type='submit']",
)
_PW_SELECTORS = (
    "input[name='credentials.passcode']",
    "input[type='password']",
    "input[autocomplete='current-password']",
    "input[name='password']",
)
_SUBMIT_SELECTORS = (
    "input[type='submit'][value='Verify']",
    "button:has-text('Verify')",
    "button:has-text('Sign in')",
    "input[type='submit']",
    "button[type='submit']",
)

# Okta IDX "Verify it's you with a security method" — link que seleciona o autenticador
# Password (quando a conta também tem Email OTP disponível como método alternativo).
_AUTH_PASSWORD_SELECTORS = (
    "a[aria-label^='Select Password']",
    "[data-se='okta_password'] a[data-se='button']",
    "[data-se='okta_password'] a",
)


def _fill_first(page, selectors, value, timeout=10_000) -> bool:
    """Preenche o primeiro seletor visível encontrado. True se preencheu."""
    for sel in selectors:
        try:
            page.wait_for_selector(sel, timeout=timeout, state="visible")
            page.fill(sel, value)
            return True
        except Exception:
            continue
    return False


def _click_first(page, selectors, timeout=8_000) -> bool:
    """Clica no primeiro seletor visível encontrado. True se clicou."""
    for sel in selectors:
        try:
            el = page.wait_for_selector(sel, timeout=timeout, state="visible")
            if el:
                el.click()
                return True
        except Exception:
            continue
    return False


def _query_any(page, selectors):
    """Primeiro elemento que casar com algum seletor (ou None). Perfura shadow DOM
    (page.query_selector perfura shadow roots abertos; o widget Okta usa shadow DOM)."""
    for sel in selectors:
        try:
            el = page.query_selector(sel)
            if el:
                return el
        except Exception:
            continue
    return None


def _check_remember_me(page) -> None:
    """Marca 'rememberMe' se presente (best-effort; pode estar no passo 1 ou 2)."""
    try:
        rm = page.query_selector("input[name='rememberMe']")
        if rm and not rm.is_checked():
            rm.check()
    except Exception:
        pass


def _platts_login(page, ctx) -> bool:
    """Login no Okta da Platts com credenciais (platts_credentials.json).

    Fluxo de 2 passos: identifier → (Next, se a senha ainda não apareceu) → senha → submit.
    NUNCA loga a senha. Retorna True se saiu da tela de login.
    """
    creds = load_credentials("platts")
    if not creds:
        log.warning("platts_scraper: sem credenciais para auto-login")
        return False
    log.info("platts_scraper: auto-login para %s...", creds["email"])  # só email, nunca a senha
    try:
        if not is_login_page(page, _LOGIN_HOSTS):
            page.goto(_LOGIN_URL, wait_until="domcontentloaded", timeout=20_000)
        page.wait_for_timeout(2_000)

        if not _fill_first(page, _ID_SELECTORS, creds["email"]):
            log.warning("platts_scraper: campo identifier não encontrado")
            return False
        _check_remember_me(page)

        # Passo identifier → próximo. Só clica "Next" se a senha ainda não apareceu.
        if not _query_any(page, _PW_SELECTORS):
            _click_first(page, _NEXT_SELECTORS)

        # Okta IDX pode inserir um seletor de autenticador (Email OTP vs Password) entre o
        # email e a senha. Aguarda até ~12s por: o campo de senha (foi direto) OU o link
        # "Select Password" do chooser; se for o chooser, seleciona o autenticador Password.
        chooser = None
        for _ in range(12):
            if _query_any(page, _PW_SELECTORS):
                break
            chooser = _query_any(page, _AUTH_PASSWORD_SELECTORS)
            if chooser:
                break
            page.wait_for_timeout(1_000)
        if chooser:
            log.info("platts_scraper: chooser Okta — selecionando autenticador Password")
            chooser.click()
            page.wait_for_timeout(2_000)

        if not _fill_first(page, _PW_SELECTORS, creds["password"]):
            log.warning("platts_scraper: campo de senha não encontrado (passo da senha)")
            return False
        _check_remember_me(page)
        _click_first(page, _SUBMIT_SELECTORS)

        # Aguarda voltar ao app autenticado (core.spglobal.com sem /login).
        try:
            page.wait_for_url(
                lambda u: "core.spglobal.com" in u and "/login" not in u,
                timeout=30_000,
            )
        except Exception:
            page.wait_for_timeout(5_000)
        try:
            page.wait_for_load_state("networkidle", timeout=15_000)
        except Exception:
            page.wait_for_timeout(5_000)

        ok = not is_login_page(page, _LOGIN_HOSTS)
        log.info("platts_scraper: auto-login %s", "OK" if ok else "FALHOU")
        return ok
    except Exception as e:
        log.warning("platts_scraper: auto-login erro: %s", e)  # nunca loga credenciais
        return False


def _scrape() -> list[RawArticle]:
    """Executa em thread. Intercepta headlines + preços IODEX do workspace."""
    from playwright.sync_api import sync_playwright

    pull_session("platts")               # puxa a sessão rolada-pra-frente do store remoto
    sp = state_path("platts")
    creds = load_credentials("platts")
    if not sp.exists() and not creds:
        log.warning("platts_scraper: sem state file nem credenciais em %s", sp)
        _set_login_failed(True)
        return []

    results: list[RawArticle] = []
    seen_ids: set[str] = set()
    now_utc = datetime.now(timezone.utc)
    price_buf: dict[str, dict] = {}

    def on_response(response):
        url = response.url
        # Headlines
        if "content-bff/v1/search" in url and "image" not in url:
            try:
                data = json.loads(response.body().decode("utf-8", errors="replace"))
                for item in data.get("Items", []):
                    article_id = item.get("Id", "")
                    if not article_id or article_id in seen_ids:
                        continue
                    content_type = item.get("ContentType", "News")
                    if content_type not in _WANTED_TYPES:
                        continue
                    seen_ids.add(article_id)

                    headline = item.get("Headline") or item.get("Name") or ""
                    if not headline:
                        continue

                    summary_html  = item.get("Summary") or ""
                    body_html     = item.get("Body") or ""
                    content_prev  = item.get("Content") or ""
                    snippet = (
                        _html_to_text(summary_html)[:360]
                        or _html_to_text(body_html)[:360]
                        or content_prev[:360]
                    )
                    pub = _parse_date(item.get("UpdatedDate") or item.get("RtpTimestamp"))

                    results.append(RawArticle(
                        url=_article_url(article_id, content_type),
                        domain="core.spglobal.com",
                        source_name="S&P Platts",
                        title=headline,
                        snippet=snippet,
                        published_at=pub,
                        found_at=now_utc,
                        needs_filter=True,
                    ))
            except Exception as e:
                log.debug("platts_scraper headlines parse error: %s", e)
            return

        # Preços — inspeciona toda resposta JSON em busca dos símbolos IODEX
        if response.status != 200:
            return
        ct = response.headers.get("content-type", "")
        if "json" not in ct:
            return
        try:
            data = json.loads(response.body().decode("utf-8", errors="replace"))
            _extract_prices(data, price_buf)
        except Exception:
            pass

    with sync_playwright() as p:
        browser = launch_browser(p)
        ctx = new_context(browser, "platts", on_response=on_response, use_state=sp.exists())
        page = ctx.new_page()

        try:
            log.info("platts_scraper: carregando core.spglobal.com...")
            # Navega; se a sessão expirou (redirect Okta), faz auto-login e re-salva o state.
            ok = navigate_with_login(
                page, ctx, "platts",
                target_url="https://core.spglobal.com/",
                login_fn=_platts_login,
                login_hosts=_LOGIN_HOSTS,
                max_attempts=_MAX_LOGIN_ATTEMPTS,
                post_nav=None,
                goto_timeout=40_000,
                pre_check_wait_ms=6_000,  # SPA redireciona p/ /login via JS após ~alguns s
            )
            _set_login_failed(not ok)
            if not ok:
                log.warning("platts_scraper: sem sessão válida (auto-login falhou/sem credenciais)")
                return []
            # Autenticado: rola a sessão pra frente (salva versão renovada local + store).
            save_state(ctx, "platts")

            # allInsights — News, Flash, Rationale, etc.
            page.evaluate("window.location.hash = '#platts/allInsights'")
            page.wait_for_timeout(18_000)

            # Market Commentary
            try:
                page.evaluate(
                    "window.location.hash = "
                    "'#platts/insightsResult?contentType=Market%20Commentary'"
                )
                page.wait_for_timeout(12_000)
            except Exception:
                pass

            log.info("platts_scraper: %d headlines coletados", len(results))

            # Navega ao workspace com watchlist de Iron Ore para capturar preços IODEX.
            # Método primário: ler a tabela AG-Grid renderizada (DOM).
            # Fallback: interceptação de rede (price_buf já preenchido via on_response).
            try:
                page.evaluate(
                    "window.location.hash = "
                    "'#platts/workspace?workspace=New%20Workspace&type=private'"
                )
                page.wait_for_timeout(12_000)  # tempo para a grid renderizar + dados chegarem

                # A aba "Dashboard" do workspace é a que tem os 4 símbolos (inclui Met Coal/
                # PLVHA00). A aba padrão (Watchlist1) tem ~13 linhas e NÃO traz o PLVHA00 →
                # clicar na Dashboard garante a grid certa (span.tab-label, role=button).
                try:
                    page.click("span.tab-label:text-is('Dashboard')", timeout=6_000)
                    page.wait_for_timeout(5_000)
                    log.info("platts_scraper: aba Dashboard do workspace selecionada")
                except Exception as e:
                    log.debug("platts_scraper: aba Dashboard não clicada: %s", e)

                # Tenta ler do DOM até 3x (a grid pode demorar a popular)
                dom_rows = {}
                row_count = 0
                cols_found = {}
                for attempt in range(3):
                    try:
                        res = page.evaluate(_DOM_PRICE_JS)
                        dom_rows = res.get("rows", {}) if isinstance(res, dict) else {}
                        row_count = res.get("rowCount", 0) if isinstance(res, dict) else 0
                        cols_found = res.get("cols", {}) if isinstance(res, dict) else {}
                        if dom_rows:
                            break
                        page.wait_for_timeout(4_000)
                    except Exception as e:
                        log.debug("platts_scraper: DOM read attempt %d falhou: %s", attempt, e)
                        page.wait_for_timeout(4_000)

                log.info("platts_scraper: DOM grid rows=%d, cols=%s, símbolos (%d)=%s",
                         row_count, cols_found, len(dom_rows), list(dom_rows.keys()))

                # Parseia e mescla no price_buf (DOM tem prioridade sobre rede).
                # Watchlist INTEIRA: cada linha = {price, change%, desc}.
                for sym, raw in dom_rows.items():
                    if not isinstance(raw, dict):
                        continue
                    val = _parse_price(raw.get("price"))
                    if val is None:
                        continue
                    entry = {"price": val}
                    chg = _parse_price(raw.get("change") or "")
                    if chg is not None:
                        entry["change_pct"] = chg
                    desc = (raw.get("desc") or "").strip()
                    if desc:
                        entry["desc"] = desc
                    price_buf[sym] = entry
                log.info("platts_scraper: %d símbolos capturados via DOM (watchlist inteira)", len(price_buf))

                log.info("platts_scraper: preços finais capturados: %s", list(price_buf.keys()))
            except Exception as e:
                log.warning("platts_scraper: workspace navigation error: %s", e)

        except Exception as e:
            log.warning("platts_scraper: erro na navegação: %s", e)
        finally:
            page.close()
            browser.close()

    # Publica no cache de módulo (lido pelo thread principal após join)
    global _platts_prices
    _platts_prices = price_buf

    return results


def collect_platts_headlines() -> list[RawArticle]:
    """Ponto de entrada — executa em thread com timeout."""
    return run_in_thread(_scrape, _TIMEOUT, "platts")


def get_platts_prices() -> dict[str, dict]:
    """Retorna preços Platts capturados durante a última sessão Playwright.

    Deve ser chamado após collect_platts_headlines() completar.
    Chaves: 'IODBZ00' (Iron Ore 61%), 'STHRZ02' (HRC China),
            'STCBM00' (Rebar Turkey), 'PLVHA00' (Asian Met Coal).
    Valores: {'price': float}.
    """
    return dict(_platts_prices)
