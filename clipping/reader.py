"""Leitor de artigos via Playwright para SPAs autenticadas (Platts, Fastmarkets).

Usa os arquivos platts_state.json / fastmarkets_state.json salvos pelo
news_generator/login.py — mesmos cookies, mesmos seletores CSS.

Diferente do leitor antigo (inner_text → texto plano), este usa innerHTML +
article_to_safe_html para preservar a estrutura de parágrafos e imagens,
produzindo o mesmo formato HTML seguro que o scraper em lote (Phase 2).

⚡ Platts tem CAMINHO RÁPIDO por API (sem navegador): fetch_article lê o corpo
direto de content-bff/v2/search/article/<id> (o mesmo JSON que o SPA usa p/ montar
a página). É robusto a mudança de layout e muito mais rápido; cai para o fluxo
Playwright (DOM + screenshot) se falhar / token expirado / conteúdo rico
(tabela/imagem/Analysis, que precisa de screenshot fiel). Ver _platts_body_via_api.
"""
from __future__ import annotations

import base64
import html as _html_mod
import json
import logging
import re
import threading
import time
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
                        // Corpo NOVO do Valor: o texto real são os <p>/<h2>/<li> DENTRO de
                        // '.mc-article-body' (envoltos por '.paywall' quando logado — NÃO pular paywall!).
                        // Pula só os widgets de verdade (scripts, "Valor One", relacionados, ads,
                        // "matérias migradas") → corpo limpo, sem ruído. Preserva negrito/links (innerHTML).
                        function mcBody() {
                            const root = document.querySelector('.mc-article-body');
                            if (!root) return '';
                            // Parágrafos REAIS do Valor têm a classe 'content-text__container'. Promos
                            // ("Valor Empresas 360") são h3/blockquote de OUTRA classe → ficam de fora.
                            // Fallback genérico (pulando widgets) p/ eventual layout antigo.
                            let nodes = Array.from(root.querySelectorAll(
                                'p.content-text__container, .mc-column.content-text > h2,'
                                + ' .mc-column.content-text > h3, .mc-column.content-text ul li,'
                                + ' .mc-column.content-text ol li, .mc-column.content-text blockquote'));
                            if (!nodes.length) {
                                const SKIP = 'script,style,figure,aside,.mc-column.entities,.gtm-div-conteudo,'
                                    + '[class*="valor-one"],[class*="empresas-360"],[class*="related"],'
                                    + '[class*="mais-recente"],[class*="recomend"],[class*="newsletter"],'
                                    + '[class*="banner"],[class*="advertising"],[class*="materias-migradas"],'
                                    + '[class*="chartbeat"],[class*="social"]';
                                nodes = Array.from(root.querySelectorAll('p, h2, h3, li, blockquote'))
                                             .filter(el => !el.closest(SKIP));
                            }
                            // promos que o Valor injeta como parágrafo/box no meio do texto (denylist de frase)
                            const PROMO = /Valor Empresas 360|Confira os resultados e indicadores|Valor PRO|assine o valor|receba as newsletters/i;
                            const parts = [];
                            for (const el of nodes) {
                                const clone = el.cloneNode(true);
                                // remove hovercards de cotação (<ins>Cotação de X</ins>) e tooltips inline
                                clone.querySelectorAll('ins,[class*="tooltip"],[class*="hover-card"]')
                                     .forEach(n => n.remove());
                                const t = (clone.textContent || '').replace(/\\s+/g, ' ').trim();
                                if (!t) continue;
                                if (PROMO.test(t)) continue;
                                const tag = el.tagName === 'LI' ? 'li'
                                          : (el.tagName[0] === 'H' ? el.tagName.toLowerCase()
                                          : (el.tagName === 'BLOCKQUOTE' ? 'blockquote' : 'p'));
                                parts.push('<' + tag + '>' + clone.innerHTML.trim() + '</' + tag + '>');
                            }
                            return parts.join('\\n');
                        }
                        // ⚠️ Sinal REAL de "não destrava" = BARREIRA de assinatura VISÍVEL. NÃO usar
                        // '.mc-article-body.cropped' (essa classe fica em TODA matéria, logado ou não →
                        // falso-positivo, testado ao vivo). O que distingue é '.paywall.hide-all-content'
                        // (classe só presente quando o conteúdo está escondido) OU '.wall.protected-content'
                        // com altura > 0 (a barreira "Já é assinante? Faça o seu login" aparecendo).
                        let cropped = false;
                        if (document.querySelector('.paywall.hide-all-content')) cropped = true;
                        const pw = document.querySelector('.wall.protected-content');
                        if (pw && pw.offsetHeight > 0 && getComputedStyle(pw).display !== 'none') cropped = true;
                        return JSON.stringify({
                            cropped:      cropped,
                            subtitle:     getHtml('.content-head__subtitle'),
                            mc_body:      mcBody(),
                            content_text: getHtml('.content-text'),
                            wall:         getHtml('.wall'),
                        });
                    }""")
                    _dv = _json.loads(dom_json)
                    _cropped = _dv.get("cropped", False)
                    _parts: list[str] = []
                    # Sessão logada + artigo INTEIRO (não recortado) → monta o corpo. A estrutura NOVA
                    # do Valor (Falkor) põe o corpo em '.mc-article-body' (é lá que está o texto de
                    # verdade — provado ao vivo: 38 parágrafos). Cai p/ '.content-text'+'.wall' só se
                    # o '.mc-article-body' não existir (matérias no layout antigo). Começa pelo subtítulo.
                    if not _cropped:
                        _sub = _dv.get("subtitle") or ""
                        if _sub and not _looks_like_sub_screen(_sub):
                            _parts.append(_sub)
                        _mc = _dv.get("mc_body") or ""
                        if _mc and not _looks_like_sub_screen(_mc):
                            _parts.append(_mc)
                        elif not _mc:
                            _ct = _dv.get("content_text") or ""
                            _wl = _dv.get("wall") or ""
                            if _ct and not _looks_like_sub_screen(_ct):
                                _parts.append(_ct)
                            if _wl and not _looks_like_sub_screen(_wl):
                                _parts.append(_wl)
                    if _parts:
                        body_html = article_to_safe_html("\n".join(_parts))
                        # Sessão OK — limpa alerta anterior se existir
                        try:
                            from .store import clear_session_alert
                            clear_session_alert("valor")
                        except Exception:
                            pass
                    elif _cropped:
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


# ── Platts: caminho rápido por API content-bff/v2 (sem navegador) ─────────────
# A página do Core monta o artigo a partir deste JSON; ler a fonte é robusto a
# mudança de layout (foi o que quebrou as headlines) e dispensa render/screenshot.
_PLATTS_API_BASE = "https://api.platts.com/platts-platform"
# appkey PÚBLICA do SPA do Core (vai no bundle JS do cliente — não é segredo, como a
# anon key do Supabase). Se a Platts trocar, o caminho cai no fluxo DOM (fallback).
_PLATTS_APPKEY = "NrjDvgdFBxQQPJxoiLhR"

# Cache do access token em processo POR FONTE (TTL do JWT ~60min): evita re-puxar a
# sessão a cada artigo no aquecedor em lote. Renova sozinho perto do vencimento.
_tok_cache: dict = {"platts":      {"tok": None, "exp": 0.0},
                    "fastmarkets": {"tok": None, "exp": 0.0}}


def _extract_okta_token(state: dict) -> str | None:
    """Access token Okta guardado no storage_state (localStorage 'okta-token-storage')."""
    def _find(o):
        if isinstance(o, dict):
            at = o.get("accessToken")
            if isinstance(at, str) and at.startswith("eyJ"):
                return at
            for v in o.values():
                r = _find(v)
                if r:
                    return r
        elif isinstance(o, list):
            for v in o:
                r = _find(v)
                if r:
                    return r
        return None
    for origin in (state or {}).get("origins", []):
        for kv in origin.get("localStorage", []):
            if kv.get("name") == "okta-token-storage":
                try:
                    return _find(json.loads(kv.get("value") or "{}"))
                except Exception:
                    pass
    return None


def _extract_fm_token(state: dict) -> str | None:
    """Access token OAuth do Fastmarkets (localStorage 'oidc.user:…' → access_token)."""
    for origin in (state or {}).get("origins", []):
        for kv in origin.get("localStorage", []):
            if (kv.get("name") or "").startswith("oidc.user:"):
                try:
                    at = json.loads(kv.get("value") or "{}").get("access_token")
                    if isinstance(at, str) and at.startswith("eyJ"):
                        return at
                except Exception:
                    pass
    return None


def _jwt_exp(tok: str) -> float | None:
    """Epoch de expiração do JWT (claim exp), sem verificar assinatura. None se não decodificar."""
    try:
        payload = tok.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        exp = json.loads(base64.urlsafe_b64decode(payload)).get("exp")
        return float(exp) if exp else None
    except Exception:
        return None


def _cached_access_token(provider: str, state_filename: str, extract_fn) -> str | None:
    """Token válido p/ a API da fonte (do state file rolado-pra-frente) ou None.
    None = ausente/expirado → o chamador cai no fluxo DOM (que renova via browser)."""
    cache = _tok_cache[provider]
    now = time.time()
    if cache["tok"] and now < cache["exp"] - 60:
        return cache["tok"]
    try:
        from hunter import playwright_session as ps
        ps.pull_session(provider)               # traz a sessão viva do store p/ o state file
    except Exception as e:
        log.debug("reader: pull_session(%s) p/ token: %s", provider, e)
    sp = _cookies_dir() / state_filename
    if not sp.exists():
        return None
    try:
        state = json.loads(sp.read_text(encoding="utf-8"))
    except Exception:
        return None
    tok = extract_fn(state)
    if not tok:
        return None
    exp = _jwt_exp(tok)
    if exp and now >= exp - 60:                  # expirado/quase → força fallback DOM
        return None
    cache["tok"] = tok
    cache["exp"] = exp or (now + 300.0)          # sem exp legível → cacheia 5min
    return tok


def _platts_access_token() -> str | None:
    return _cached_access_token("platts", "platts_state.json", _extract_okta_token)


def _fm_access_token() -> str | None:
    return _cached_access_token("fastmarkets", "fastmarkets_state.json", _extract_fm_token)


# Fração MÍNIMA do texto bruto que o sanitizador tem que preservar num corpo de API.
# O Body/content da API é corpo PURO (sem menu, anúncio ou "leia também") → o
# sanitizador não pode comer pedaço grande; se comeu, é BUG dele. Medido em 397
# artigos reais Platts/FM: o pior caso legítimo preserva 0,857 (p1 = 0,897) — os
# dois artigos decapitados pelo bug do "Also read"/"See also" ficavam em ~0,35.
# Abaixo do piso: NÃO entrega matéria pela metade — grita no log e cai no DOM.
_MIN_SANITIZE_KEEP = 0.80


def _sanitize_ok(raw_body: str, safe: str, url: str, source: str) -> bool:
    """False se o sanitizador comeu parte grande demais do corpo (→ cair no DOM)."""
    from .html_utils import plain_text
    raw_len = len(plain_text(raw_body))
    if raw_len < 200:                       # corpo curto: proporção não é sinal confiável
        return True
    keep = len(plain_text(safe)) / raw_len
    if keep < _MIN_SANITIZE_KEEP:
        log.warning("clipping: %s — sanitizador preservou só %.0f%% do corpo da API "
                    "(%d de %d chars) em %s → caindo p/ o DOM",
                    source, keep * 100, len(plain_text(safe)), raw_len, url[-60:])
        return False
    return True


def _parse_platts_article(url: str) -> tuple[str | None, str]:
    """(articleID, insightsType) do fragmento #platts/insightsArticle?articleID=…&insightsType=…"""
    from urllib.parse import unquote
    frag = urlparse(url).fragment
    qs = frag.split("?", 1)[-1] if "?" in frag else ""
    d = {}
    for part in qs.split("&"):
        if "=" in part:
            k, v = part.split("=", 1)
            d[k.lower()] = v
    aid = d.get("articleid")
    typ = unquote(d.get("insightstype", "News")) if d.get("insightstype") else "News"
    return (aid or None), typ


def _platts_body_via_api(url: str) -> tuple[str, str]:
    """Corpo do artigo Platts pela API content-bff/v2 (sem navegador). ('','') se não der
    → o chamador cai no fluxo Playwright (DOM + screenshot). Conteúdo rico
    (tabela/imagem/Analysis) também devolve ('','') de propósito, p/ o DOM captar fiel."""
    aid, typ = _parse_platts_article(url)
    if not aid:
        return "", ""
    tok = _platts_access_token()
    if not tok:
        return "", ""
    import requests
    from urllib.parse import quote
    api = (f"{_PLATTS_API_BASE}/content-bff/v2/search/article/{quote(aid)}"
           f"?relatedArticlesPerPage=5&newsType={quote(typ)}")
    try:
        r = requests.get(api, headers={"Authorization": f"Bearer {tok}",
                                       "appkey": _PLATTS_APPKEY,
                                       "Accept": "application/json"}, timeout=15)
    except Exception as e:
        log.debug("reader: Platts API erro de rede: %s", e)
        return "", ""
    if r.status_code != 200:
        log.debug("reader: Platts API HTTP %s (%s)", r.status_code, aid)
        _tok_cache["platts"]["tok"] = None       # pode ser token vencido → força re-pull na próxima
        return "", ""
    try:
        j = r.json()
    except Exception:
        return "", ""
    body  = j.get("Body") or ""
    title = j.get("Headline") or j.get("Name") or ""
    ctype = (j.get("ContentType") or "").lower()
    low   = body.lower()
    # Rico (tabela/imagem) ou Analysis → deixa o fluxo DOM cuidar (screenshot fiel).
    if not body or "<table" in low or "<img" in low or "analysis" in ctype:
        return "", ""
    from .html_utils import article_to_safe_html
    safe = article_to_safe_html(body)            # mesmo sanitizador das outras fontes
    if len(safe) < 80 or not _sanitize_ok(body, safe, url, "Platts"):
        return "", ""
    return title, safe


# ── Fastmarkets: caminho rápido por API news/v3/articles (sem navegador) ───────
# A página /a/<id> monta o artigo a partir deste JSON. Corpo = summary (LEAD) + content
# (o DOM junta os dois no .content-container → validado: texto IDÊNTICO, jaccard 1.000).
# Imagens já vêm como <img src="https://…"> (mesma URL do DOM, baixadas depois) — sem
# screenshot. Sem appkey (só Bearer). Cai no DOM se falhar/token vencido.
def _fm_body_via_api(url: str) -> tuple[str, str]:
    """Corpo do artigo Fastmarkets pela API news/v3/articles (sem navegador). ('','') se não der
    → o chamador cai no fluxo Playwright (DOM)."""
    if "/a/" not in url:
        return "", ""
    aid = url.rstrip("/").split("/a/")[-1].split("/")[0].split("?")[0]
    if not aid:
        return "", ""
    tok = _fm_access_token()
    if not tok:
        return "", ""
    import requests
    api = f"https://api.fastmarkets.com/news/v3/articles?ids={aid}"
    try:
        r = requests.get(api, headers={"Authorization": f"Bearer {tok}",
                                       "Accept": "application/json",
                                       "Origin": "https://dashboard.fastmarkets.com",
                                       "Referer": "https://dashboard.fastmarkets.com/"}, timeout=15)
    except Exception as e:
        log.debug("reader: FM API erro de rede: %s", e)
        return "", ""
    if r.status_code != 200:
        log.debug("reader: FM API HTTP %s (%s)", r.status_code, aid)
        _tok_cache["fastmarkets"]["tok"] = None  # pode ser token vencido → re-pull na próxima
        return "", ""
    try:
        arts = (r.json() or {}).get("articles") or []
    except Exception:
        return "", ""
    if not arts:
        return "", ""
    art     = arts[0]
    summary = art.get("summary") or ""           # LEAD — o DOM o inclui no topo do corpo
    content = art.get("content") or ""
    title   = art.get("title") or ""
    if not (summary or content):
        return "", ""
    from .html_utils import article_to_safe_html
    raw  = (summary + "\n" + content).strip()
    safe = article_to_safe_html(raw)
    if len(safe) < 80 or not _sanitize_ok(raw, safe, url, "Fastmarkets"):
        return "", ""
    return title, safe


def fetch_article(url: str) -> tuple[str, str]:
    """Busca artigo. Retorna (titulo, corpo_html).

    Platts/Fastmarkets: tentam a API primeiro (rápido, robusto); caem p/ Playwright
    (DOM + screenshot) se falhar. Demais fontes: Playwright direto.
    corpo_html é HTML seguro (tags <p>, <img>, <h3>, etc.). String vazia se falhar.
    """
    domain = _norm_domain(urlparse(url).netloc)

    # ── Caminho rápido por API (sem navegador): Platts e Fastmarkets ──────────
    _api_fast = {"core.spglobal.com": _platts_body_via_api,
                 "dashboard.fastmarkets.com": _fm_body_via_api}.get(domain)
    if _api_fast:
        try:
            t, b = _api_fast(url)
            if b:
                log.debug("reader: %s corpo via API (%d chars) %s", domain, len(b), url[-50:])
                return t, b
        except Exception as e:
            log.debug("reader: %s API path falhou (%s) — caindo p/ DOM", domain, e)

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
