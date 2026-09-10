"""Scraper Platts (S&P Global) — Fase 1 apenas (headlines) + preços.

Intercepta o feed de headlines (content-bff/.../search/blendedsearch na view
"Enhanced"; content-bff/v1/search na "Classic" legada — ver _is_headline_search_url).
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
#
# ⚠️ ESTA LISTA JÁ SERVIU PARA VASCULHAR A REDE — E ERA POR AÍ QUE ENTRAVA PREÇO VELHO.
# Até 10/09/2026 toda resposta JSON da página era varrida atrás destes símbolos. Só que
# as MATÉRIAS da Platts também carregam preço: uma análise escrita em 08/09 traz dentro
# dela o assessment de 08/09. Como o último achado vencia, o buffer terminava a fase de
# notícias com números de dias anteriores — sem data e sem variação, que a varredura não
# tinha como saber. Enquanto a leitura do DOM funcionava, ela sobrescrevia tudo e ninguém
# via. Quando a grid não renderizava (medido: 1 de 2 execuções seguidas), o lixo ficava e
# era publicado. Foi assim que o IODEX 61% apareceu a 100,6 (o valor de 08/09) com a data
# de 09/09 e a variação em branco. Reproduzido ao vivo em 10/09/2026.
# Preço agora vem SÓ da grid, onde preço, variação e data saem da mesma linha.
_PRICE_SYMBOLS = {
    # Watchlist 'Dashboard' do Platts — lidos do DOM da grid.
    # Mantido em sincronia com PLATTS_COMMODITIES em prices.py.
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
    """Saúde da última execução: login + quantos preços a grid entregou.

    `login_failed=True` = sessão não pôde ser estabelecida (expirada + autologin falhou,
    ou sem credenciais). `prices` = símbolos lidos da watchlist; ZERO com a sessão viva é
    o sinal de que a grid não renderizou — a falha que ficava calada, porque o preço
    velho continuava na tela como se fosse do dia.
    """
    return {"login_failed": _login_failed, "prices": len(_platts_prices)}

# Regra de negócio (usuário): TODA notícia da Platts entra no news hunter/clipinator,
# EXCETO "Rationale" (metodologia de preço — "NÃO usar notícias Rationale"). Barrado
# em 3 camadas coerentes: aqui na origem (ContentType), por título no filter.py e no
# classificador (_EXCLUDE_CONTENT_TYPES) — todas BLOCKLIST de "rationale".
#
# ⚠️ ISTO ERA UM WHITELIST (_WANTED_TYPES = {News, Top News, Flash, Market Commentary,
# Blog, Headline Analysis}) → descartava EM SILÊNCIO qualquer ContentType fora da
# lista, inclusive "Analysis" (matérias analíticas COM TABELAS E IMAGENS, ex.:
# "Chinese HRC market faces supply pressure amid falling exports, sluggish demand").
# Invertido para BLOCKLIST: só "Rationale" é barrado; o resto passa — e tipo NOVO
# que a Platts criar entra sozinho, sem precisar mexer no código. Casa por SUBSTRING
# em minúsculas, então "Pricing Rationale"/"Rationale" também são pegos.
_BLOCKED_TYPES = ("rationale",)


def _type_allowed(content_type: str) -> bool:
    """True se o ContentType da Platts deve entrar. Bloqueia só 'Rationale'
    (substring, case-insensitive); todo o resto passa (regra de negócio)."""
    ct = (content_type or "").lower()
    return not any(b in ct for b in _BLOCKED_TYPES)


# ── Endpoint(s) do feed de headlines ──────────────────────────────────────────
# ⚠️ 2026-08: a view "Enhanced" (nova) do Core usa DOIS endpoints de LISTA, e a
# distinção importa (foi o que fez News/Feature/Analysis pararem de entrar mesmo depois
# do 1º conserto que só pegava 'blendedsearch'):
#   • allInsights (feed geral)         → content-bff/v4/search             (path termina em /search)
#   • insightsResult (busca filtrada)  → content-bff/v4/search/blendedsearch (termina em /blendedsearch)
#   • (legado Classic)                 → content-bff/v1/search             (termina em /search)
# Todos têm a MESMA forma de resposta (Items[] com Id/Headline/ContentType/…). Casamos por
# FIM DE PATH (/search OU /blendedsearch) → pega os dois + versão futura (v5…), e EXCLUI os
# vizinhos que NÃO são lista de artigos: /search/facets, /search/blendedcascadingfacets,
# /search/blendedtypes, /search/events, /search/image/<id>, /search/article/<id>.
def _is_headline_search_url(url: str) -> bool:
    """True se a resposta é uma LISTA de headlines da Platts (allInsights base /search,
    insightsResult /blendedsearch, ou o legado Classic v1/search) — nunca facetas/eventos/imagem."""
    from urllib.parse import urlparse
    try:
        path = urlparse(url or "").path.lower()
    except Exception:
        return False
    return "content-bff" in path and (path.endswith("/search") or path.endswith("/blendedsearch"))


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


# Formatos de data que o workspace Platts costuma exibir na coluna "Assessed Date".
_ASSESSED_DATE_FORMATS = (
    "%d-%b-%Y",   # 10-Jun-2025
    "%d %b %Y",   # 10 Jun 2025
    "%d-%b-%y",   # 10-Jun-25
    "%d/%m/%Y",   # 10/06/2025
    "%m/%d/%Y",   # 06/10/2025
    "%Y-%m-%d",   # 2025-06-10 (ISO)
    "%b %d, %Y",  # Jun 10, 2025
    "%d %B %Y",   # 10 June 2025
)


def _parse_assessed_date(text: str | None) -> str | None:
    """Converte o texto da coluna 'Assessed Date' do Platts em ISO 'YYYY-MM-DD'.

    Tenta o parser ISO nativo e depois vários formatos comuns do workspace;
    retorna None se nenhum casar (o valor cru é logado para ajuste posterior).
    """
    if not text:
        return None
    t = text.strip()
    try:
        return datetime.fromisoformat(t.replace("Z", "+00:00")).date().isoformat()
    except (ValueError, TypeError):
        pass
    for fmt in _ASSESSED_DATE_FORMATS:
        try:
            return datetime.strptime(t, fmt).date().isoformat()
        except (ValueError, TypeError):
            continue
    return None


def _entradas_da_grid(dom_rows: dict) -> tuple[dict[str, dict], list[str]]:
    """Linhas cruas da grid → `{símbolo: {price, assessed_at, change_pct?, ...}}`.

    Devolve também a lista do que foi descartado, para o log.

    ⚠️ REGRA DA CASA: PREÇO SEM DATA NÃO É PREÇO. A linha só entra se a célula da data
    existir e for legível — preço, variação e data têm de sair da MESMA leitura da MESMA
    linha. Publicar o preço sozinho deixava a data anterior de pé, e a tela dizia "ontem"
    sobre um número de anteontem, com a variação em branco. Foi exatamente o que
    aconteceu com o IODEX 61% em 09/09/2026.
    """
    out: dict[str, dict] = {}
    descartadas: list[str] = []
    for sym, raw in (dom_rows or {}).items():
        if not isinstance(raw, dict):
            continue
        val = _parse_price(raw.get("price"))
        if val is None:
            continue
        assessed_raw = (raw.get("assessed") or "").strip()
        iso = _parse_assessed_date(assessed_raw) if assessed_raw else None
        if not iso:
            descartadas.append(f"{sym}({assessed_raw or 'vazia'})")
            continue
        entry = {"price": val, "assessed_at": iso}
        chg = _parse_price(raw.get("change") or "")
        if chg is not None:
            entry["change_pct"] = chg
        desc = (raw.get("desc") or "").strip()
        if desc:
            entry["desc"] = desc
        freq = (raw.get("freq") or "").strip()
        if freq:
            entry["freq"] = freq
        out[sym] = entry
    return out, descartadas


# JS para ler preços direto da tabela AG-Grid renderizada (DOM).
# Mais robusto que interceptar rede — lê exatamente o que está na tela.
_DOM_PRICE_JS = """
() => {
  // Lê a watchlist INTEIRA da grid AG-Grid (a 'Dashboard' é a config do usuário):
  // cada linha vira {price, change%, desc}. Descobre as colunas pelo cabeçalho.
  const norm = el => (el.textContent||'').trim().toLowerCase().replace(/\\s+/g,'');
  let symCol=null, descCol=null, priceCol=null, chgCol=null, dateCol=null, freqCol=null;
  document.querySelectorAll('.ag-header-cell, [role="columnheader"]').forEach(h => {
    const t = norm(h), id = h.getAttribute('col-id');
    if (!id) return;
    if (!symCol  && (t==='symbol'||t==='code'||t==='ticker'||t==='mdcsymbol')) symCol=id;
    if (!descCol && (t.indexOf('description')!==-1||t==='name'||t==='symbolname'||t==='symboldescription')) descCol=id;
    if (!priceCol&& (t==='price'||t==='bate'||t==='value'||t==='last'||t==='bid'||t==='assessment'||t==='mid')) priceCol=id;
    if (!chgCol  && t.indexOf('change')!==-1 && t.indexOf('%')!==-1) chgCol=id;
    if (!dateCol && (t.indexOf('assessed')!==-1 || (t.indexOf('assess')!==-1 && t.indexOf('date')!==-1)
                     || t==='assessmentdate' || t==='assessdate' || t==='asofdate' || t==='date'
                     || t==='assessmenttime')) dateCol=id;
    if (!freqCol && (t.indexOf('frequency')!==-1 || t==='freq')) freqCol=id;
  });
  // Fallback robusto: se o cabeçalho da data não casou (nome diferente), detecta a coluna
  // pelo CONTEÚDO — a coluna cujas células parecem data. (Só roda se as colunas existem no DOM.)
  if (!dateCol) {
    const DATE_RE = /\\b\\d{1,2}[-\\s\\/.][A-Za-z]{3,9}\\b|\\b[A-Za-z]{3,9}\\.?\\s+\\d{1,2}\\b|\\b\\d{4}-\\d{2}-\\d{2}\\b/;
    const votes = {};
    [...document.querySelectorAll('.ag-row, [role="row"]')].slice(0,50).forEach(r => {
      r.querySelectorAll('[col-id]').forEach(c => {
        const id=c.getAttribute('col-id'), tx=(c.textContent||'').trim();
        if (id && id!==priceCol && id!==chgCol && id!==symCol && DATE_RE.test(tx)) votes[id]=(votes[id]||0)+1;
      });
    });
    let best=null,bn=0; for (const id in votes) if (votes[id]>bn){bn=votes[id];best=id;}
    if (best && bn>=3) dateCol=best;
  }
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
    out[sym] = {price: price, change: chgCol?cellOf(row,chgCol):null, desc: descCol?cellOf(row,descCol):null,
                assessed: dateCol?cellOf(row,dateCol):null, freq: freqCol?cellOf(row,freqCol):null};
  });
  return {rows: out, rowCount: document.querySelectorAll('.ag-row, [role="row"]').length,
          cols: {sym: !!symCol, desc: !!descCol, price: !!priceCol, chg: !!chgCol,
                 date: !!dateCol, freq: !!freqCol}};
}
"""


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
        # Headlines — v4 blendedsearch (Enhanced) OU v1/search (Classic legado)
        if _is_headline_search_url(url):
            try:
                data = json.loads(response.body().decode("utf-8", errors="replace"))
                for item in data.get("Items", []):
                    article_id = item.get("Id", "")
                    if not article_id or article_id in seen_ids:
                        continue
                    content_type = item.get("ContentType") or "News"
                    if not _type_allowed(content_type):   # bloqueia só "Rationale"; resto passa
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

        # ⚠️ NÃO EXISTE MAIS CAPTURA DE PREÇO POR AQUI — ver o comentário grande em
        # _PRICE_SYMBOLS. Preço só entra pelo DOM da watchlist, junto com a data e a
        # variação da MESMA linha. Toda resposta JSON da página era vasculhada atrás dos
        # símbolos, e as matérias trazem o assessment do dia em que foram escritas: era
        # daí que vinha o número velho.

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
            # allInsights — feed geral (Enhanced dispara content-bff/v4/search, base) → News/Feature/…
            page.evaluate("window.location.hash = '#platts/allInsights'")
            page.wait_for_timeout(14_000)

            # Autenticado: rola a sessão pra frente (salva versão renovada local + store).
            #
            # ⚠️ ISTO FICAVA LOGO APÓS O navigate_with_login, E ERA CEDO DEMAIS. O app só
            # renova o access token do Okta alguns segundos depois de subir, já dentro do
            # feed. Salvando antes disso, o store recebia o token VELHO — e como ele vive
            # ~1h, o caminho rápido por API do clipping (reader._platts_body_via_api) caía
            # em 401 quase sempre e TODA notícia da Platts ia parar no fluxo do navegador,
            # 70s cada uma, para voltar vazia. Medido em 2026-09-09: salvando aqui o token
            # gravado vale ~1h; salvando antes, já nascia vencido.
            save_state(ctx, "platts")

            # insightsResult SEM filtro — TODOS os tipos via blendedsearch (50 itens; robusto p/
            # News/Feature/Analysis, já que o base-search do allInsights vem parcial/flaky). ⚠️ Sem
            # esta navegação, News/Feature/Analysis param de entrar (só Market Commentary sobrevivia).
            try:
                page.evaluate("window.location.hash = '#platts/insightsResult'")
                page.wait_for_timeout(12_000)
            except Exception:
                pass

            # Market Commentary — o tipo mais frequente, garantido em navegação PRÓPRIA (o feed
            # 'todos' pode ficar dominado por Rationale/News e empurrar MC p/ fora do top-50).
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
            # ⚠️ Alarga a janela ANTES de abrir a grid: o AG-Grid VIRTUALIZA colunas fora da área
            # visível à direita — em viewport estreito (1440) a última coluna ("Assessed Date") não
            # renderiza e o scraper não lê a data (assessed_at congelava em valor antigo). Largura
            # folgada garante que TODAS as colunas existam no DOM. NÃO reduzir.
            try:
                page.set_viewport_size({"width": 2600, "height": 1400}); page.wait_for_timeout(800)
            except Exception as e:
                log.debug("platts_scraper: set_viewport_size falhou: %s", e)
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

                # Espera a grid EXISTIR em vez de dormir um tempo fixo: ela é assíncrona e
                # a troca de aba a reconstrói. Medido em 10/09/2026: em duas execuções
                # seguidas, uma leu 20 símbolos e a outra leu ZERO (rowCount=0, nenhum
                # cabeçalho) — a grid simplesmente ainda não estava lá. Sair mais cedo
                # quando ela vem rápido, e insistir até 30s quando demora.
                try:
                    page.wait_for_selector(".ag-row", timeout=30_000)
                except Exception:
                    log.warning("platts_scraper: a grid do workspace não renderizou em 30s")

                # Tenta ler do DOM (a grid existe, mas as células podem chegar depois)
                dom_rows = {}
                row_count = 0
                cols_found = {}
                for attempt in range(4):
                    try:
                        res = page.evaluate(_DOM_PRICE_JS)
                        dom_rows = res.get("rows", {}) if isinstance(res, dict) else {}
                        row_count = res.get("rowCount", 0) if isinstance(res, dict) else 0
                        cols_found = res.get("cols", {}) if isinstance(res, dict) else {}
                        # exige a coluna da DATA: sem ela nenhuma linha é publicável
                        # (preço sem data não é preço — ver o merge abaixo), então ler
                        # de novo é melhor que sair com uma safra inteira que será jogada fora.
                        if dom_rows and cols_found.get("date"):
                            break
                        page.wait_for_timeout(4_000)
                    except Exception as e:
                        log.debug("platts_scraper: DOM read attempt %d falhou: %s", attempt, e)
                        page.wait_for_timeout(4_000)

                log.info("platts_scraper: DOM grid rows=%d, cols=%s, símbolos (%d)=%s",
                         row_count, cols_found, len(dom_rows), list(dom_rows.keys()))

                lidas, sem_data = _entradas_da_grid(dom_rows)
                price_buf.update(lidas)
                if sem_data:
                    log.warning("platts_scraper: %d linha(s) sem data de assessment legível — "
                                "descartadas: %s", len(sem_data), ", ".join(sem_data))
                log.info("platts_scraper: %d símbolos capturados via DOM (watchlist inteira)", len(price_buf))

                faltando = sorted(_PRICE_SYMBOLS - set(price_buf))
                if faltando:
                    log.warning("platts_scraper: %d símbolo(s) registrados NÃO vieram da grid "
                                "(mantêm o valor anterior): %s", len(faltando), ", ".join(faltando))
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
