"""Leitor de artigos via Playwright para SPAs autenticadas (Platts, Fastmarkets).

Usa os arquivos platts_state.json / fastmarkets_state.json salvos pelo
news_generator/login.py — mesmos cookies, mesmos seletores CSS.

Diferente do leitor antigo (inner_text → texto plano), este usa innerHTML +
article_to_safe_html para preservar a estrutura de parágrafos e imagens,
produzindo o mesmo formato HTML seguro que o scraper em lote (Phase 2).
"""
from __future__ import annotations

import html as _html_mod
import logging
import re
import threading
from pathlib import Path
from urllib.parse import urlparse

log = logging.getLogger(__name__)

def _cookies_dir() -> Path:
    """Pasta das sessões: COOKIES_DIR (Actions) OU news_generator/cookies (local)."""
    try:
        from hunter.cookies import get_cookies_dir
        return get_cookies_dir()
    except Exception:
        return Path(__file__).resolve().parent.parent.parent / "news_generator" / "cookies"

# domínio → provider da sessão viva do news-hunter (Supabase source_sessions).
# As 4 fontes autenticadas do clipping usam o MESMO keep-alive do Platts/FM:
#   pull_session  → puxa a sessão rolada-pra-frente ANTES de raspar
#   save_state    → regrava a sessão renovada DEPOIS de um scrape bem-sucedido
# → a sessão "rola pra frente" a cada uso e quase nunca expira. Valor/Estadão ainda
# ganham um toque periódico (clipping.keepalive) p/ nunca esfriar entre clippings.
_SUPA_PROVIDER = {
    "core.spglobal.com":          "platts",
    "dashboard.fastmarkets.com":  "fastmarkets",
    "valor.globo.com":            "valor",
    "www.estadao.com.br":         "estadao",
}

def _ensure_session(domain: str) -> None:
    """Puxa a sessão viva do Supabase (roll-forward) p/ o cookies dir antes de raspar."""
    prov = _SUPA_PROVIDER.get(domain)
    if not prov:
        return
    try:
        from hunter import playwright_session as ps
        ps.pull_session(prov)
    except Exception as e:
        log.warning("reader: pull_session(%s) indisponível: %s", prov, e)

def _roll_forward(domain: str, ctx) -> None:
    """Regrava a sessão renovada de volta no store (roll-forward), IGUAL Platts/FM.
    Chamado só quando o scrape confirmou sessão viva (corpo real). Best-effort —
    nunca sobregrava o store com uma sessão morta (só rola quando deu certo)."""
    prov = _SUPA_PROVIDER.get(domain)
    if not prov:
        return
    try:
        from hunter import playwright_session as ps
        ps.save_state(ctx, prov)
    except Exception as e:
        log.warning("reader: save_state(%s) indisponível: %s", prov, e)

def _norm_domain(netloc: str) -> str:
    d = (netloc or "").lower()
    if d.endswith("estadao.com.br"):
        return "www.estadao.com.br"
    if d.endswith("valor.globo.com"):
        return "valor.globo.com"
    return d

PLAYWRIGHT_DOMAINS: frozenset[str] = frozenset([
    "core.spglobal.com",
    "dashboard.fastmarkets.com",
    "valor.globo.com",
    "www.estadao.com.br",
])

_SITE_CONFIG: dict[str, dict] = {
    "core.spglobal.com": {
        "state_file": "platts_state.json",
        # Angular SPA: não suporta navegação direta por URL — precisa carregar
        # a homepage antes de navegar ao artigo via hash routing.
        # Usa innerText dos containers (imune a spans Angular) para extrair o corpo.
        "use_platts_flow": True,
        "wait_for": ".newsSection-headline",
        "title": [".newsSection-headline"],
    },
    "dashboard.fastmarkets.com": {
        "state_file": "fastmarkets_state.json",
        "title": ["h1", "[class*='headline']", "[class*='article-title']", "[class*='ArticleTitle']"],
        # A página do artigo (/a/…) é: .article-container > [.first-row news-head (LIXO: back-link +
        # título + "Published by" + data)] + [.second-row.content-container (O CORPO + imagem)].
        # → pegar o .content-container DENTRO do artigo pula o cabeçalho. NUNCA usar .article-container
        # como primeira opção (inclui o first-row). (DOM inspecionado ao vivo 2026-07-28.)
        "body": [
            ".article-container .content-container",   # o corpo (second-row), SEM o cabeçalho
            ".second-row.content-container",
            "[class*='article-body']",
            "[class*='ArticleBody']",
            "[class*='articleBody']",
            "article",
            ".article-container",                      # último recurso: layout raro sem content-container
        ],
    },
    "valor.globo.com": {
        "state_file": "valor_state.json",
        "title": [".content-head__title", "h1"],
        # Valor: combina lide (.content-text) + corpo (.wall) na leitura do artigo.
        # O flag use_valor_flow ativa extração dupla em _fetch_worker.
        "use_valor_flow": True,
        "locale": "pt-BR",
    },
    "www.estadao.com.br": {
        "state_file": "estadao_state.json",
        # Estadão usa Zephr (paywall client-side) — conteúdo está no DOM antes do JS.
        # Usa domcontentloaded para capturar antes do overlay Zephr ser aplicado.
        "use_estadao_flow": True,
        "title": ["h1", "[class*='article__header-title']", "[class*='article-title']"],
        "body": [
            "[class*='news-body']",        # Estadão Styled Components — classe principal
            "[class*='article-body']",
            "[data-testid='article-body']",
            "[class*='article__content']",
            ".article-body",
            ".article__content",
            "[class*='content-article']",
        ],
        "lede": [
            "[class*='article__header-summary']",
            "[class*='article-summary']",
            ".article-summary",
            "h2.subtitle",
        ],
        "locale": "pt-BR",
        "wait_until": "domcontentloaded",   # não espera networkidle (bloqueia Zephr)
    },
}


def _is_login_related(url: str) -> bool:
    """True se a URL indica página de login/auth."""
    u = url.lower()
    return "id.globo.com" in u or "login.globo.com" in u or "contas.globo.com" in u or "/login" in u


# Marcadores da tela de assinatura do Valor. Quando a sessão morre, o .wall vem como
# um CTA de assinatura em vez do artigo → estes termos o denunciam. Só derrubam blocos
# CURTOS: um artigo real e longo NÃO é rejeitado mesmo que cite "assinante" no texto.
_VALOR_SUB_MARKERS = (
    "assine o valor", "assine agora", "já é assinante", "ja e assinante",
    "para continuar lendo", "conteúdo exclusivo para assinantes",
    "conteudo exclusivo para assinantes", "faça seu login", "faca seu login",
    "seja assinante", "acesso ilimitado", "assine já", "assine ja",
)


def _looks_like_sub_screen(html: str) -> bool:
    """True se o HTML parece a tela de assinatura (bloco curto com CTA de paywall)."""
    txt = _html_mod.unescape(re.sub(r"<[^>]+>", " ", html or "")).lower()
    if len(txt) > 800:          # artigo real e longo → nunca é só a tela de assinatura
        return False
    return any(m in txt for m in _VALOR_SUB_MARKERS)


def _state_file_for(domain: str) -> str | None:
    cfg = _SITE_CONFIG.get(domain, {})
    fname = cfg.get("state_file")
    if not fname:
        return None
    p = _cookies_dir() / fname
    return str(p) if p.exists() else None


def _fetch_worker(url: str, domain: str) -> tuple[str, str]:
    """Retorna (titulo, corpo_html).

    corpo_html é HTML seguro gerado por article_to_safe_html — preserva
    parágrafos, headings, listas e imagens, igual ao Phase-2 do scraper em lote.
    """
    from playwright.sync_api import sync_playwright
    from .html_utils import article_to_safe_html

    cfg = _SITE_CONFIG.get(domain, {})
    _ensure_session(domain)                 # Platts/FM: rola a sessão viva do Supabase p/ o state file
    state = _state_file_for(domain)

    if state:
        log.debug("playwright_reader: carregando sessão de %s", state)
    else:
        log.warning("playwright_reader: sem state file para %s — tentando sem autenticação", domain)

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
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "viewport": {"width": 1280, "height": 800},
            "locale": cfg.get("locale", "en-US"),
        }
        if state:
            ctx_kwargs["storage_state"] = state

        ctx = browser.new_context(**ctx_kwargs)
        ctx.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        page = ctx.new_page()
        title = ""
        body_html = ""

        try:
            if cfg.get("use_platts_flow"):
                # Platts Angular SPA: navegar direto para o artigo não funciona
                # porque o Angular router redireciona enquanto ainda inicializa.
                # Solução: carrega a homepage primeiro (Angular inicializa),
                # depois navega para o hash do artigo.
                page.goto("https://core.spglobal.com/", wait_until="domcontentloaded", timeout=40_000)
                try:
                    page.wait_for_load_state("networkidle", timeout=12_000)
                except Exception:
                    page.wait_for_timeout(5_000)
                # Verifica se caiu na página de login (antes ou depois de carregar)
                if "login" in page.url.lower():
                    log.warning("playwright_reader: Platts sessão expirada (login page)")
                    return "", ""
                # Agora navega para o artigo específico via hash
                page.goto(url, wait_until="domcontentloaded", timeout=25_000)
                try:
                    page.wait_for_load_state("networkidle", timeout=12_000)
                except Exception:
                    page.wait_for_timeout(5_000)
                # Verifica login novamente (o artigo pode redirecionar para auth)
                if "login" in page.url.lower():
                    log.warning("playwright_reader: Platts redirecionou para login no artigo")
                    return "", ""
                # Espera explícita pelo corpo do artigo (Angular lazy-load).
                # PASSO 1: aguarda o elemento aparecer no DOM.
                # PASSO 2: aguarda o innerText ficar não-vazio — o seletor
                # surge antes do Angular terminar de renderizar o conteúdo,
                # então sem este segundo passo bdy sempre é "".
                try:
                    page.wait_for_selector(".newsSection-body", timeout=18_000)
                    # Aguarda Angular preencher o conteúdo
                    try:
                        page.wait_for_function(
                            "(function() {"
                            "  var el = document.querySelector('.newsSection-body');"
                            "  return el && el.innerText && el.innerText.trim().length > 100;"
                            "})",
                            timeout=12_000,
                        )
                    except Exception:
                        page.wait_for_timeout(3_000)
                except Exception:
                    # Body pode demorar — aguarda mais um pouco e continua
                    page.wait_for_timeout(4_000)
            elif cfg.get("use_valor_flow"):
                # Valor Econômico: navegação direta funciona (site server-side).
                # Extrai .content-text (lide) + .wall (corpo), combinados.
                page.goto(url, wait_until="domcontentloaded", timeout=40_000)
                try:
                    page.wait_for_selector(
                        ".content-text, .wall, article",
                        timeout=12_000,
                    )
                except Exception:
                    page.wait_for_timeout(3_000)
                if _is_login_related(page.url):
                    log.warning("playwright_reader: Valor sessão expirada (login page)")
                    return "", ""

                # Extrai lide + corpo via DOM
                try:
                    import json as _json
                    dom_json: str = page.evaluate("""() => {
                        function getHtml(sel) {
                            const el = document.querySelector(sel);
                            return el ? el.innerHTML.trim() : '';
                        }
                        const noPaywall  = !!document.querySelector('[class*="no-paywall"]');
                        const hasPaywall = !!document.querySelector('[class*="paywall__wall"]');
                        // Paywall NOVO do Valor (Falkor): corpo .mc-article-body.cropped +
                        // .wall.protected-content com "Faça o seu login" = sessão expirada → RECORTADO.
                        const wallEl  = document.querySelector('.wall');
                        const wallTxt = wallEl ? (wallEl.innerText || '') : '';
                        const cropped = !!document.querySelector('.mc-article-body.cropped')
                                     || !!document.querySelector('.wall.protected-content')
                                     || /Fa[çc]a o seu login|seja assinante|assine o valor/i.test(wallTxt);
                        return JSON.stringify({
                            no_paywall:   noPaywall,
                            has_paywall:  hasPaywall,
                            cropped:      cropped,
                            content_text: getHtml('.content-text'),
                            wall:         getHtml('.wall'),
                        });
                    }""")
                    _dv = _json.loads(dom_json)
                    _no_pw   = _dv.get("no_paywall", False)
                    _has_pw  = _dv.get("has_paywall", False)
                    _cropped = _dv.get("cropped", False)
                    _parts: list[str] = []
                    # Inclui lide (.content-text) E corpo (.wall) sempre que NÃO houver paywall
                    # ATIVO nem corte (ou seja: logado, artigo INTEIRO). _cropped = sessão
                    # expirada → artigo RECORTADO: NÃO inclui o pedaço (melhor vazio + aviso do
                    # que um corpo cortado calado). A guarda _looks_like_sub_screen descarta a
                    # tela de assinatura curta.
                    _open = (_no_pw or not _has_pw) and not _cropped
                    _ct = _dv.get("content_text") or ""
                    _wl = _dv.get("wall") or ""
                    if _ct and _open and not _looks_like_sub_screen(_ct):
                        _parts.append(_ct)
                    if _wl and _open and not _looks_like_sub_screen(_wl):
                        _parts.append(_wl)
                    if _parts:
                        body_html = article_to_safe_html("\n".join(_parts))
                        # Sessão OK — limpa alerta anterior se existir
                        try:
                            from .store import clear_session_alert
                            clear_session_alert("valor")
                        except Exception:
                            pass
                    elif _cropped or (_has_pw and not _no_pw):
                        log.warning(
                            "playwright_reader: Valor RECORTADO/paywall (sessão expirada) — %s",
                            url[-80:],
                        )
                        try:
                            from .store import set_session_alert
                            set_session_alert(
                                "valor",
                                "Sessão do Valor Econômico expirada — os artigos vêm CORTADOS (paywall). "
                                "Rode 'Atualizar Valor.bat' para renovar.",
                            )
                        except Exception:
                            pass
                except Exception as _e:
                    log.debug("playwright_reader: Valor DOM error: %s", _e)

                # Título
                for _sel in cfg.get("title", []):
                    try:
                        _el = page.query_selector(_sel)
                        if _el:
                            _t = _el.inner_text().strip()
                            if _t:
                                title = _t
                                break
                    except Exception:
                        pass
                if not title:
                    title = page.title().strip()
                # Sessão viva confirmada (corpo real) → rola a sessão pra frente no store
                if body_html:
                    _roll_forward(domain, ctx)
                # Retorna direto — pula o bloco genérico abaixo
                return title, body_html

            elif cfg.get("use_estadao_flow"):
                # Estadão usa Zephr (paywall client-side) — conteúdo está no DOM
                # antes do JS do Zephr rodar. domcontentloaded captura antes do overlay.
                wait_until = cfg.get("wait_until", "domcontentloaded")
                page.goto(url, wait_until=wait_until, timeout=40_000)
                if _is_login_related(page.url) or "acesso.estadao.com.br" in page.url.lower():
                    log.warning("playwright_reader: Estadão sessão expirada (login page)")
                    try:
                        from .store import set_session_alert
                        set_session_alert(
                            "estadao",
                            "Sessão do Estadão expirada — artigos aparecem com paywall. "
                            "Execute <code>python login.py --estadao</code> para renovar.",
                        )
                    except Exception:
                        pass
                    return "", ""
                try:
                    page.wait_for_selector(
                        "[class*='news-body'], [class*='article-body'], [class*='article__content'], article",
                        timeout=12_000,
                    )
                except Exception:
                    page.wait_for_timeout(2_000)

                # Extrai lide + corpo via DOM — mesmos seletores do estadao_scraper
                try:
                    import json as _json
                    from .estadao_scraper import _estadao_clean_js
                    _est_js = _estadao_clean_js().replace(
                        "is_paywalled", "paywalled"
                    )
                    _est_json: str = page.evaluate(_est_js)
                    _est = _json.loads(_est_json)
                    _parts: list[str] = []
                    if _est.get("lede"):
                        _parts.append(_est["lede"])
                    if _est.get("body"):
                        _parts.append(_est["body"])
                    if _parts:
                        body_html = article_to_safe_html("\n".join(_parts))
                    elif _est.get("paywalled"):
                        log.warning(
                            "playwright_reader: Estadão artigo paywalled (sessão expirada?) — %s",
                            url[-80:],
                        )
                except Exception as _e:
                    log.debug("playwright_reader: Estadão DOM error: %s", _e)

                # Fallback: _estadao_clean_js vazio (ex.: einvestidor / layout novo) →
                # usa os seletores de corpo da config (news-body, article-body, ...).
                if not body_html:
                    for _bsel in cfg.get("body", []):
                        try:
                            _bel = page.query_selector(_bsel)
                            if _bel:
                                _binner = _bel.inner_html().strip()
                                if _binner and len(_binner) > 100:
                                    body_html = article_to_safe_html(_binner)
                                    break
                        except Exception:
                            pass

                # Título
                for _sel in cfg.get("title", []):
                    try:
                        _el = page.query_selector(_sel)
                        if _el:
                            _t = _el.inner_text().strip()
                            if _t:
                                title = _t
                                break
                    except Exception:
                        pass
                if not title:
                    title = page.title().strip()
                # Sessão viva confirmada (corpo real) → rola a sessão pra frente no store
                if body_html:
                    _roll_forward(domain, ctx)
                return title, body_html

            else:
                page.goto(url, wait_until="domcontentloaded", timeout=40_000)

            # Aguarda o conteúdo aparecer no DOM (sites não-Platts)
            wait_sel = cfg.get("wait_for")
            if wait_sel and not cfg.get("use_platts_flow"):
                try:
                    page.wait_for_selector(wait_sel, timeout=20_000)
                except Exception:
                    page.wait_for_timeout(4_000)
            elif not cfg.get("use_platts_flow"):
                try:
                    page.wait_for_load_state("networkidle", timeout=12_000)
                except Exception:
                    page.wait_for_timeout(4_000)

            # ── Título ────────────────────────────────────────────────────────
            for sel in cfg.get("title", []):
                try:
                    el = page.query_selector(sel)
                    if el:
                        t = el.inner_text().strip()
                        if t:
                            title = t
                            break
                except Exception:
                    pass
            if not title:
                title = page.title().strip()

            # ── Corpo ─────────────────────────────────────────────────────────
            if cfg.get("use_platts_flow"):
                # Platts: innerText dos containers via JSON separado.
                # NÃO combinar com \n\n antes de processar — o \n\n entre
                # highlights e body ativa o modo errado em innertext_to_html
                # e colapsa todos os parágrafos num único bloco.
                # Scroll para ativar lazy-loading antes de capturar imagens
                try:
                    page.evaluate("""(function() {
                        var el = document.querySelector('.newsSection-body');
                        if (!el) return;
                        var h = el.scrollHeight;
                        window.scrollTo(0, Math.floor(h / 3));
                        window.scrollTo(0, Math.floor(h * 2 / 3));
                        window.scrollTo(0, h);
                        window.scrollTo(0, 0);
                    })()""")
                    page.wait_for_timeout(1_200)
                except Exception:
                    pass

                # Aguarda imagens lazy-load renderizarem (naturalWidth > 0)
                try:
                    page.wait_for_function(
                        "(function() {"
                        "  var imgs = document.querySelectorAll('.newsSection-body img');"
                        "  if (!imgs.length) return true;"
                        "  for (var i=0; i<imgs.length; i++) {"
                        "    if (imgs[i].naturalWidth > 0) return true;"
                        "  }"
                        "  return false;"
                        "})",
                        timeout=5_000,
                    )
                except Exception:
                    pass

                # DOM walker: retorna items em ordem (texto + slots de imagem)
                # Imagens na posição correta, sem hover artifacts
                from .html_utils import PLATTS_DOM_WALK_JS, platts_dom_items_to_html
                from urllib.parse import urlparse as _urlparse
                import json as _json
                try:
                    _walk_data = _json.loads(page.evaluate(PLATTS_DOM_WALK_JS))
                except Exception:
                    _walk_data = {"items": [], "hl": ""}

                # ── Validação de URL ────────────────────────────────────────────
                # Verifica que o articleID no DOM corresponde ao artigo solicitado.
                # Protege contra race condition do Angular SPA onde o DOM ainda
                # mostra o artigo anterior enquanto o router carrega o novo.
                _dom_url = _walk_data.get("url", "")
                # Fragment: "platts/insightsArticle?articleID=UUID&insightsType=..."
                _target_fragment = _urlparse(url).fragment
                _article_id = ""
                _frag_qs = _target_fragment.split("?", 1)[-1] if "?" in _target_fragment else ""
                for _part in _frag_qs.split("&"):
                    if _part.lower().startswith("articleid="):
                        _article_id = _part.split("=", 1)[-1].strip()
                        break
                if _article_id and _dom_url and _article_id.lower() not in _dom_url.lower():
                    log.warning(
                        "playwright_reader: Platts URL mismatch — esperado articleID=%s "
                        "dom_url=%s — corpo descartado (SPA ainda carregando?)",
                        _article_id, _dom_url[-80:],
                    )
                    _walk_data = {"items": [], "hl": ""}

                _text_items = [it for it in _walk_data.get("items", []) if it.get("t") != "img"]
                _bdy_len    = sum(len(it.get("v", "")) for it in _text_items)

                if _bdy_len > 80:
                    body_html = platts_dom_items_to_html(_walk_data, page)
            else:
                # Outros sites: innerHTML + article_to_safe_html
                raw_parts: list[str] = []

                for sel in cfg.get("highlights", []):
                    try:
                        el = page.query_selector(sel)
                        if el:
                            inner = el.inner_html().strip()
                            if inner:
                                raw_parts.append(inner)
                                break
                    except Exception:
                        pass

                for sel in cfg.get("body", []):
                    try:
                        el = page.query_selector(sel)
                        if el:
                            inner = el.inner_html().strip()
                            if inner and len(inner) > 100:
                                raw_parts.append(inner)
                                break
                    except Exception:
                        pass

                if raw_parts:
                    body_html = article_to_safe_html("\n".join(raw_parts))

            # Fallback genérico: extrai <p> de containers semânticos.
            # ATENÇÃO: Para sites Angular/SPA (use_platts_flow), este fallback
            # costuma capturar fragmentos de UI (menus, breadcrumbs) e produzir
            # conteúdo inútil — desabilitado para Platts.
            if not body_html or len(body_html) < 80:
                if cfg.get("use_platts_flow"):
                    # Platts Angular: sem fallback genérico — melhor mostrar
                    # "conteúdo não disponível" do que texto de UI fragmentado.
                    log.debug("playwright_reader: Platts sem conteúdo no DOM — sem fallback genérico")
                else:
                    els = page.query_selector_all(
                        "article p, main p, [role='main'] p, .content p"
                    )
                    paras = [
                        el.inner_text().strip()
                        for el in els
                        if len(el.inner_text().strip()) > 40
                    ][:40]
                    if paras:
                        body_html = "\n".join(
                            f"<p>{_html_mod.escape(p)}</p>" for p in paras
                        )

        except Exception as e:
            log.warning("playwright_reader erro em %s: %s", url, e)
        finally:
            page.close()
            browser.close()

    return title, body_html


def fetch_article(url: str) -> tuple[str, str]:
    """Busca artigo com Playwright. Retorna (titulo, corpo_html).

    corpo_html é HTML seguro (tags <p>, <img>, <h3>, etc.) pronto para
    renderização no leitor. String vazia se falhar.
    """
    domain = _norm_domain(urlparse(url).netloc)

    result: list[tuple[str, str]] = [("", "")]
    err: list[Exception | None] = [None]

    def _run():
        try:
            result[0] = _fetch_worker(url, domain)
        except Exception as e:
            err[0] = e

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout=90)  # homepage(12s) + goto(12s) + body-wait(18s) + overhead

    if err[0]:
        raise err[0]
    return result[0]


def needs_playwright(url: str) -> bool:
    """True se a URL precisa de Playwright para renderizar."""
    domain = _norm_domain(urlparse(url).netloc)
    return domain in PLAYWRIGHT_DOMAINS
