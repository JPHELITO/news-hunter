"""Scraper Fastmarkets — headlines PP News (Fase 1) + preços PIX de celulose.

Intercepta POST /search/v3/query no dashboard PP News.
Retorna apenas título + snippet + link. Sem Fase 2 (sem corpo completo).

PREÇOS (mesma sessão, sem navegação extra): a aba "PIX Pulp Prices" do workspace é
alimentada pela API `physical/v2/prices/history`, que aceita o MESMO Bearer que o
dashboard de notícias já manda em toda chamada a api.fastmarkets.com. Então
capturamos o token do tráfego das notícias e fazemos UMA chamada à API — nada de
abrir a aba de preços e raspar a grade (a API dá preço, variação e a série inteira,
que o DOM não dá). Espelha o papel do get_platts_prices() no scraper do Platts.
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

# ── Preços PIX de celulose ────────────────────────────────────────────────────
# Os símbolos são as LINHAS da aba "PIX Pulp Prices" do workspace do analista, que é
# ordenada por `location` (China → East Coast US → Europe). Recorte pedido pelo usuário:
#   linhas 1-2   → celulose China (net, CFR, US$)
#   linhas 3-4   → RESALE doméstico China (exw, yuan)
#   linhas 13-16 → Europa; ficam só as duas em US$ (as em EUR são as mesmas em outra moeda)
# Para adicionar/remover um preço: editar aqui + FASTMARKETS_COMMODITIES em prices.py.
_PRICE_SYMBOLS = (
    "FP-PLP-0034",   # PIX Pulp China NBSK Net       — USD/t
    "FP-PLP-0033",   # PIX Pulp China BHKP Net       — USD/t
    "FP-PLP-0068",   # Pulp, eucalyptus  (dom, net), exw China — CNY/t  (resale)
    "FP-PLP-0070",   # Pulp, radiata pine (dom, net), exw China — CNY/t  (resale)
    "FP-PLP-0039",   # PIX Pulp NBSK USD (Europa)    — USD/t
    "FP-PLP-0040",   # PIX Pulp BHKP USD (Europa)    — USD/t
)
_PRICES_API = "https://api.fastmarkets.com/physical/v2/prices/history?language=en"
# A janela pedida à API. O carrossel desenha no máximo ~1 ano e o histórico privado
# guarda ~500 pontos; 2015 dá folga de sobra sem trazer 30 anos à toa.
_PRICES_FROM = "2015-1-1"

# Cache preenchido pelo _scrape() como efeito colateral, lido pelo thread principal
# após o join via get_fastmarkets_prices() — mesmo contrato do _platts_prices.
_fm_prices: dict[str, dict] = {}

# Health: True se não conseguiu estabelecer sessão nesta execução (lido por hunt.py).
_login_failed = False


def _set_login_failed(v: bool) -> None:
    global _login_failed
    _login_failed = v


def get_fastmarkets_health() -> dict:
    """Saúde da última execução: {'login_failed': bool}. True = sessão não pôde ser
    estabelecida (expirada + autologin falhou, ou sem credenciais)."""
    return {"login_failed": _login_failed}


def get_fastmarkets_prices() -> dict[str, dict]:
    """Preços PIX de celulose capturados na última sessão. Chamar após collect_*().

    Chaves: símbolo Fastmarkets (ex.: 'FP-PLP-0034'). Valores:
    {'price': float,           # mid do último assessment
     'change_pct': float|None, # variação vs. o assessment anterior, em %
     'assessed_at': 'YYYY-MM-DD',
     'series': [[epoch, valor], ...]}   # histórico crescente (semanal)
    Vazio se a sessão caiu ou a API não respondeu — quem chama mantém o valor antigo.
    """
    return dict(_fm_prices)


def _html_to_text(h: str) -> str:
    text = re.sub(r"<[^>]+>", " ", h)
    text = _html.unescape(text)
    text = text.replace("�", "")
    return re.sub(r"\s+", " ", text).strip()


def _clean_title(t: str) -> str:
    """A API do FM devolve o TÍTULO com código de HTML dentro ("Arauco&rsquo;s Sucuri&uacute;").

    O snippet já passava pelo _html_to_text (que decodifica); o título ia cru pro banco e
    saía literal no clipping. Só decodifica — sem tirar tag, que em manchete não existe.
    """
    return re.sub(r"\s+", " ", _html.unescape(t or "").replace("�", "")).strip()


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


def _collect_prices(ctx, bearer: str) -> dict[str, dict]:
    """UMA chamada à API de preços do FM com o Bearer que o dashboard já usa.

    Feita pelo `ctx.request` do Playwright (não por fetch injetado na página): a API mora
    em api.fastmarkets.com e o CORS recusa o fetch de dentro de dashboard.fastmarkets.com
    quando não é o próprio app quem pede. O ctx.request sai do contexto do browser — mesmos
    cookies, sem CORS.

    A resposta traz, por símbolo, a lista `prices` do mais RECENTE para o mais antigo, com
    low/mid/high, a data do assessment e `midChangeSincePreviousProportion` (proporção:
    0.005779 = +0,58%). Devolve {} em qualquer falha — preço é acessório, nunca derruba
    a coleta de manchetes.
    """
    if not bearer:
        log.info("fastmarkets_prices: sem Bearer no tráfego do dashboard — preços pulados")
        return {}
    try:
        r = ctx.request.post(
            _PRICES_API,
            headers={"Authorization": bearer,
                     "Accept": "application/json, text/plain, */*",
                     "Origin": "https://dashboard.fastmarkets.com",
                     "Referer": "https://dashboard.fastmarkets.com/"},
            multipart={"symbols": ",".join(_PRICE_SYMBOLS),
                       "fields": "MidChangeSincePreviousProportion,AssessmentPeriod,PreliminaryPrice",
                       "fromDate": _PRICES_FROM, "toDate": "2035-1-1"},
            timeout=30_000)
        if not r.ok:
            log.warning("fastmarkets_prices: API %s: %s", r.status, r.text()[:200])
            return {}
        data = r.json()
    except Exception as e:
        log.warning("fastmarkets_prices: chamada falhou: %s", e)
        return {}

    for inv in (data.get("invalidInstruments") or []):
        log.warning("fastmarkets_prices: símbolo recusado %s: %s",
                    inv.get("symbol"), inv.get("message"))

    out: dict[str, dict] = {}
    for inst in (data.get("instruments") or []):
        sym = inst.get("symbol")
        prices = inst.get("prices") or []
        if sym not in _PRICE_SYMBOLS or not prices:
            continue
        # Guarda (data, ponto) para achar o assessment mais RECENTE pela data, em vez de
        # confiar na ordem da resposta — assim preço, variação e assessed_at saem sempre
        # da MESMA linha, mesmo que a API mude a ordenação.
        pontos = []
        for pt in prices:
            val, day = pt.get("mid"), pt.get("date")
            if val is None or not day:
                continue
            try:
                dia = str(day)[:10]
                ep = int(datetime.fromisoformat(dia + "T00:00:00+00:00").timestamp())
            except (TypeError, ValueError):
                continue
            try:
                pontos.append((ep, dia, round(float(val), 4), pt))
            except (TypeError, ValueError):
                continue
        if not pontos:
            continue
        pontos.sort(key=lambda x: x[0])
        ep_ult, dia_ult, val_ult, bruto_ult = pontos[-1]
        prop = bruto_ult.get("midChangeSincePreviousProportion")
        out[sym] = {
            "price":       val_ult,
            "change_pct":  round(float(prop) * 100, 4) if prop is not None else None,
            "assessed_at": dia_ult,
            "series":      [[ep, val] for ep, _, val, _ in pontos],
        }
        log.info("fastmarkets_prices: %s = %s (%s, %d pontos)",
                 sym, out[sym]["price"], out[sym]["assessed_at"], len(pontos))
    if len(out) < len(_PRICE_SYMBOLS):
        log.warning("fastmarkets_prices: %d de %d símbolos vieram",
                    len(out), len(_PRICE_SYMBOLS))
    return out


def _scrape() -> list[RawArticle]:
    """Executa em thread. Fase 1 apenas — intercepta API, sem navegar artigos."""
    from playwright.sync_api import sync_playwright

    _fm_prices.clear()                   # cache de preços é por execução
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
    auth = {"bearer": ""}    # Bearer do app, pescado do tráfego → serve p/ a API de preços

    def on_request(request):
        # Toda chamada do dashboard a api.fastmarkets.com leva o mesmo access token OAuth.
        # Guardamos o primeiro que aparecer para reusar na API de preços (evita abrir a aba).
        if not auth["bearer"] and "api.fastmarkets.com" in request.url:
            h = request.headers.get("authorization", "")
            if h.startswith("Bearer "):
                auth["bearer"] = h

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
                        "title": _clean_title(art.get("title") or ""),
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
        ctx.on("request", on_request)
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
            # Preços PIX de celulose: UMA chamada de API reusando o token do dashboard.
            # Isolado — se a API mudar/negar, as manchetes seguem normalmente.
            try:
                _fm_prices.update(_collect_prices(ctx, auth["bearer"]))
            except Exception as e:
                log.warning("fastmarkets_scraper: preços falharam (não-fatal): %s", e)
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
