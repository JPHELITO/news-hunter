"""Keep-alive das sessões autenticadas do clipping (Valor, Estadão) — "nunca perde o login".

Faz por Valor/Estadão o MESMO que o loop hunt-playwright faz por Platts/FM: puxa a sessão
viva do store (source_sessions no Supabase), abre uma página do site, e regrava a sessão
renovada (roll-forward). Rodar isto periodicamente (workflow keepalive_sessions.yml, cron a
cada ~6h) mantém os cookies do Globo/Zephr sempre frescos → a sessão quase nunca expira.

Rodar de news-hunter/:  python -m clipping.keepalive
Usa SUPABASE_URL + SUPABASE_SERVICE_KEY (store das sessões) e COOKIES_DIR (arquivos de state).
"""
from __future__ import annotations

import logging
import threading

log = logging.getLogger(__name__)

# provider → (URL leve p/ "tocar" a sessão, cookies de auth que precisam sobreviver p/ regravar).
# Só regravamos quando a sessão ainda está viva → nunca "envenenamos" o store com sessão morta.
_TARGETS: dict[str, dict] = {
    "valor":   {"url": "https://valor.globo.com/",            "auth_cookies": ("GLBID",)},
    "estadao": {"url": "https://www.estadao.com.br/economia/", "auth_cookies": ()},
}

_LOGIN_HOSTS = ("id.globo.com", "contas.globo.com", "login.globo.com", "acesso.estadao.com.br")


def _session_alive(state: dict, names: tuple[str, ...]) -> bool:
    """True se o storage_state ainda parece logado. Com `names`, exige que ao menos um
    cookie de auth continue presente (server não fez logout); sem `names`, basta ter cookies."""
    cookies = state.get("cookies") or []
    if not names:
        return bool(cookies)
    allnames = " ".join((c.get("name") or "") for c in cookies).upper()
    return any(n.upper() in allnames for n in names)


_VALOR_LINKS_JS = (
    "() => [...new Set([...document.querySelectorAll('a[href*=\"/noticia/\"]')]"
    ".map(a => a.href.split('#')[0]))]"
)


def _valor_cropped(page, href: str):
    """Abre a matéria e diz se o paywall escondeu o texto (True), se veio liberada (False) ou se
    não deu para ler (None).
    ⚠️ A presença do cookie GLBID NÃO garante sessão viva. ⚠️ E olhar a página "na hora" também
    não: o Valor manda o texto inteiro e o script do paywall o esconde depois (até 7 s, medido em
    18/11/2026 — antes disso esta checagem dizia "liberada" até SEM cookie). Ver `valor_paywall`."""
    from .valor_paywall import wait_cropped
    try:
        page.goto(href, wait_until="domcontentloaded", timeout=30_000)
    except Exception:
        return None
    return wait_cropped(page)


def _valor_dead_reason(page) -> str | None:
    """Motivo de a sessão do Valor estar MORTA, ou None. `page` está na home.

    Testa as 2 primeiras matérias da home e só declara morta se AS DUAS vierem cortadas: uma só
    cortada pode ser seção à parte; uma liberada pode ser matéria grátis. Na dúvida NÃO mata — o
    keep-alive deixaria de rolar a sessão boa pra frente e mandaria alarme falso.
    (O cabeçalho "não logado" NÃO entra: com a sessão boa ele aparece às vezes sem cortar nada.)"""
    try:
        links = [h for h in (page.evaluate(_VALOR_LINKS_JS) or []) if "/noticia/" in h][:2]
    except Exception:
        links = []
    if not links:
        log.warning("keepalive: valor — não achei matéria na home p/ testar; não julgo a sessão")
        return None
    res = [_valor_cropped(page, h) for h in links]
    cortadas = sum(r is True for r in res)
    if cortadas == len(res):
        return (f"{cortadas} de {len(res)} matérias vieram CORTADAS (paywall) — "
                "a sessão não destrava o conteúdo pago")
    if cortadas:
        log.warning("keepalive: valor — %d de %d matérias cortadas (inconclusivo; não mato a sessão)",
                    cortadas, len(res))
    return None


def _report_health(provider: str, dead: bool) -> None:
    """Grava o status da sessão em source_health (login_failed=dead). O watchdog lê isso e
    manda e-mail do GitHub quando cai (mesmo canal do Platts/FM). Best-effort."""
    try:
        from hunter.sync import record_source_health
        record_source_health(provider, login_failed=dead)
    except Exception as e:
        log.debug("keepalive: record_source_health(%s) indisponível: %s", provider, e)


def _touch(provider: str, url: str, auth_cookies: tuple[str, ...]) -> bool:
    """Puxa a sessão do store, carrega `url` e regrava a versão renovada (roll-forward).
    Retorna True se rolou pra frente. NÃO regrava se caiu no login ou perdeu o cookie de auth."""
    from playwright.sync_api import sync_playwright
    from hunter import playwright_session as ps

    ps.pull_session(provider)                      # store remoto → arquivo local de state
    if not ps.state_path(provider).exists():
        log.warning("keepalive: %s sem state — rode o login local ou sete %s_STATE_JSON",
                    provider, provider.upper())
        return False

    with sync_playwright() as p:
        browser = ps.launch_browser(p)
        ctx = ps.new_context(browser, provider)    # usa o state file do provider (storage_state)
        page = ctx.new_page()
        rolled = False
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=40_000)
            try:
                page.wait_for_load_state("networkidle", timeout=8_000)
            except Exception:
                page.wait_for_timeout(2_500)
            state = ctx.storage_state()
            dead_reason = None
            if ps.is_login_page(page, _LOGIN_HOSTS):
                dead_reason = "caiu em login"
            elif not _session_alive(state, auth_cookies):
                dead_reason = "perdeu o cookie de auth"
            elif provider == "valor":
                # GLBID presente MAS as matérias vêm cortadas pelo paywall → sessão morta.
                dead_reason = _valor_dead_reason(page)
            if dead_reason:
                log.warning("keepalive: %s SESSÃO MORTA — %s (não regravo)", provider, dead_reason)
                _report_health(provider, dead=True)     # source_health → watchdog manda e-mail
            else:
                ps.save_state(ctx, provider)             # roll-forward: store recebe a versão renovada
                rolled = True
                _report_health(provider, dead=False)     # sessão viva → limpa o alerta
                log.info("keepalive: %s OK — sessão viva, rolada pra frente", provider)
        except Exception as e:
            log.warning("keepalive: %s erro: %s", provider, e)
        finally:
            try:
                page.close()
            except Exception:
                pass
            try:
                browser.close()
            except Exception:
                pass
    return rolled


def keepalive_all() -> int:
    """Toca todas as sessões-alvo (cada uma numa thread com timeout). Retorna quantas rolaram."""
    n_ok = 0
    for prov, cfg in _TARGETS.items():
        result: list[bool] = [False]

        def _run(prov=prov, cfg=cfg, result=result):
            result[0] = _touch(prov, cfg["url"], cfg["auth_cookies"])

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        # 150 s: o Valor agora abre a home + 2 matérias e espera o paywall decidir em cada uma
        # (até ~9 s por matéria). Workflow tem 10 min no total.
        t.join(timeout=150)
        if t.is_alive():
            log.warning("keepalive: %s timeout (150s)", prov)
        elif result[0]:
            n_ok += 1
    return n_ok


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    n = keepalive_all()
    print(f"keepalive concluído — {n}/{len(_TARGETS)} sessões roladas pra frente")


if __name__ == "__main__":
    main()
