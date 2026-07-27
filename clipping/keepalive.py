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
            if ps.is_login_page(page, _LOGIN_HOSTS):
                log.warning("keepalive: %s caiu em login — sessão expirada (não regravo)", provider)
            elif not _session_alive(state, auth_cookies):
                log.warning("keepalive: %s perdeu o cookie de auth — não regravo (evita poison)", provider)
            else:
                ps.save_state(ctx, provider)        # roll-forward: store recebe a versão renovada
                rolled = True
                log.info("keepalive: %s OK — sessão rolada pra frente", provider)
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
        t.join(timeout=90)
        if t.is_alive():
            log.warning("keepalive: %s timeout (90s)", prov)
        elif result[0]:
            n_ok += 1
    return n_ok


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    n = keepalive_all()
    print(f"keepalive concluído — {n}/{len(_TARGETS)} sessões roladas pra frente")


if __name__ == "__main__":
    main()
