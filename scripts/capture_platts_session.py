"""
capture_platts_session.py — Renova a sessão da Platts (core.spglobal.com).

A sessão (platts_state.json) expira periodicamente. Quando isso acontece,
o scraper para de trazer headlines E preços de minério (IODEX).

COMO USAR:
    python scripts/capture_platts_session.py

1. Um Chrome VISÍVEL vai abrir em core.spglobal.com
2. Faça login normalmente (usuário + senha + 2FA se houver)
3. Navegue até ver o workspace com a watchlist de Iron Ore carregada
4. Volte ao terminal e pressione ENTER
5. O script salva a sessão localmente E imprime a string pro GitHub Secret

NADA de credencial é hardcoded — você digita o login na própria página da Platts.
A sessão salva contém apenas cookies/tokens, igual ao que o navegador guarda.
"""
from __future__ import annotations

import base64
import gzip
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hunter.cookies import get_cookies_dir


def main():
    from playwright.sync_api import sync_playwright

    state_file = get_cookies_dir() / "platts_state.json"
    state_file.parent.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print(" RENOVAÇÃO DE SESSÃO — PLATTS (core.spglobal.com)")
    print("=" * 70)
    print("\nAbrindo Chrome... faça login e abra o workspace de Iron Ore.\n")

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
        page.goto("https://core.spglobal.com/", wait_until="domcontentloaded")

        print(">>> Faça login no navegador que abriu.")
        print(">>> Quando o workspace/watchlist estiver visível, volte aqui e")
        input(">>> pressione ENTER para salvar a sessão... ")

        # Salva o storage state (cookies + localStorage)
        ctx.storage_state(path=str(state_file))
        browser.close()

    size = state_file.stat().st_size
    print(f"\n✓ Sessão salva em: {state_file} ({size:,} bytes)")

    # Gera a string gzip+base64 para o GitHub Secret PLATTS_STATE_JSON
    raw = state_file.read_bytes()
    encoded = base64.b64encode(gzip.compress(raw)).decode("ascii")

    secret_file = state_file.parent / "platts_state_secret.txt"
    secret_file.write_text(encoded, encoding="ascii")

    print(f"✓ String do GitHub Secret salva em: {secret_file}")
    print(f"  ({len(encoded):,} caracteres)\n")
    print("PRÓXIMO PASSO — atualizar o GitHub Secret:")
    print("  1. GitHub → repo news-hunter → Settings → Secrets and variables → Actions")
    print("  2. Edite o secret PLATTS_STATE_JSON")
    print(f"  3. Cole TODO o conteúdo de: {secret_file}")
    print("  4. Salve. O próximo hunt-playwright já usará a sessão nova.\n")
    print("Para testar localmente antes:")
    print("  python -c \"import logging; logging.basicConfig(level=logging.INFO); "
          "from hunter.platts_scraper import collect_platts_headlines, get_platts_prices; "
          "collect_platts_headlines(); print(get_platts_prices())\"")


if __name__ == "__main__":
    main()
