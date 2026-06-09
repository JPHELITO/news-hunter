"""capture_fastmarkets_session.py — dá a PARTIDA na sessão do Fastmarkets (uma vez).

O login do FM exige um fluxo OAuth interativo (não automatizável headless). Este script
abre um Chrome VISÍVEL para você logar normalmente; ao terminar, salva a sessão no STORE
remoto (Supabase: tabela source_sessions), de onde o robô passa a puxá-la e mantê-la viva
continuamente (roll-forward) — então você só precisa fazer isto de novo se um dia ela cair
(você receberá um email do watchdog se isso acontecer).

USO (no terminal, dentro de news-hunter):
    python scripts/capture_fastmarkets_session.py

1. Um Chrome visível abre no dashboard do Fastmarkets.
2. Faça login normalmente (a tela de login do FM aparece).
3. Espere o dashboard "PP News" carregar com as notícias visíveis.
4. Volte ao terminal e pressione ENTER.

Nada de credencial é hardcoded — você digita o login na própria página do Fastmarkets.
Requer .env com SUPABASE_URL e SUPABASE_SERVICE_KEY (para salvar no store).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")   # SUPABASE_URL / SUPABASE_SERVICE_KEY p/ salvar no store
except ImportError:
    pass

from hunter.playwright_session import save_state


def main():
    from playwright.sync_api import sync_playwright

    print("=" * 70)
    print(" PARTIDA DE SESSAO — FASTMARKETS")
    print("=" * 70)
    print("\nAbrindo Chrome... faca login e espere o dashboard 'PP News' carregar.\n")

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=False, channel="chrome")
        except Exception:
            browser = p.chromium.launch(headless=False)

        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1440, "height": 900},
            locale="en-US",
        )
        page = ctx.new_page()
        page.goto("https://dashboard.fastmarkets.com/", wait_until="domcontentloaded")

        print(">>> Faca login no navegador que abriu (se ficar em branco, va em")
        print(">>> dashboard.fastmarkets.com ou fastmarkets.com e clique em Log in).")
        print(">>> Quando o dashboard 'PP News' estiver com as noticias visiveis, volte aqui e")
        input(">>> pressione ENTER para salvar a sessao... ")

        # Salva local + empurra pro store remoto (Supabase) — fonte da verdade do robo.
        save_state(ctx, "fastmarkets")
        browser.close()

    print("\nOK — sessao do Fastmarkets salva no store. O robo vai mante-la viva (roll-forward).")
    print("Se algum dia ela cair (voce recebe email do watchdog), rode este script de novo.")


if __name__ == "__main__":
    main()
