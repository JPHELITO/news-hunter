"""News Hunter — runner principal (GitHub Actions + local)."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

# Carrega .env se existir (desenvolvimento local)
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("hunt")


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--playwright", action="store_true",
                        help="Inclui scrapers Playwright (Platts + Fastmarkets)")
    args = parser.parse_args()

    from hunter.fetcher import fetch_all
    from hunter.filter import filter_articles
    from hunter.sync import push_articles, push_take_fields, record_run

    log.info("=== News Hunter start (playwright=%s) ===", args.playwright)

    # RSS + requests (sempre)
    articles_raw = fetch_all()
    log.info("RSS: %d artigos brutos", len(articles_raw))

    # Playwright: Platts + Fastmarkets (opcional)
    platts_prices: dict = {}
    if args.playwright:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from hunter.platts_scraper import collect_platts_headlines, get_platts_prices
        from hunter.fastmarkets_scraper import collect_fastmarkets_headlines

        pw_results = []
        with ThreadPoolExecutor(max_workers=2) as ex:
            futures = {
                ex.submit(collect_platts_headlines):    "Platts",
                ex.submit(collect_fastmarkets_headlines): "Fastmarkets",
            }
            for fut in as_completed(futures):
                name = futures[fut]
                try:
                    items = fut.result()
                    log.info("%s: %d headlines", name, len(items))
                    pw_results.extend(items)
                except Exception as e:
                    log.warning("%s scraper falhou: %s", name, e)

        # Captura preços IODEX coletados como side-effect da sessão Platts
        platts_prices = get_platts_prices()
        log.info("Platts prices capturados: %s", list(platts_prices.keys()))

        articles_raw.extend(pw_results)
        log.info("Total com Playwright: %d artigos brutos", len(articles_raw))

    articles_filtered = filter_articles(articles_raw)
    log.info("Após filtro: %d artigos relevantes", len(articles_filtered))

    # Classifica sector/sentiment/tickers (módulo existente)
    from hunter.classify import classify_article_dict
    for art in articles_filtered:
        classify_article_dict(art)
    log.info("Classificação básica aplicada em %d artigos", len(articles_filtered))

    # Classifica take/region/topics/covered_companies (novo classificador de mercado)
    from hunter.news_take_classifier import classify_article_take
    for art in articles_filtered:
        classify_article_take(art)
    log.info("Take classificado em %d artigos", len(articles_filtered))

    pushed = push_articles(articles_filtered)
    log.info("Supabase: %d artigos enviados", pushed)

    # Registra o run independente de ter trazido novidades —
    # o dashboard usa este timestamp para "sincronizado há X min".
    record_run(pushed)

    # PATCH take fields separadamente (colunas não fazem parte do INSERT principal
    # enquanto a migration SQL não for rodada no Supabase).
    push_take_fields(articles_filtered)

    # Atualiza cotações, commodities e indicadores macro
    try:
        from hunter.prices import (update_quotes, update_commodities, update_macro,
                                    update_platts_commodities, update_quote_history)
    except Exception as e:
        log.warning("Preços: import falhou: %s", e)
    else:
        # Cada provedor isolado: uma falha (ex.: Yahoo fora do ar) NÃO derruba os
        # demais, e o log diz exatamente qual quebrou.
        def _safe(name, fn, *a):
            try:
                return fn(*a)
            except Exception as e:
                log.warning("Preços[%s] falhou: %s", name, e)
                return None
        q = _safe("quotes", update_quotes)
        h = _safe("quote_history", update_quote_history)  # série diária (auto-throttle ~1x/dia)
        c = _safe("commodities", update_commodities)       # Copper + Gold (Yahoo)
        m = _safe("macro", update_macro)
        if platts_prices:  # Iron Ore 61%, HRC China, Rebar Turkey, Met Coal
            _safe("platts_commodities", update_platts_commodities, platts_prices)
        log.info("Preços: quotes=%s (hist=%s), commodities=%s, macro=%s", q, h, c, m)

    # Sinal de vida das fontes Playwright → o watchdog lê isto para alertar (email do
    # GitHub) se Platts/Fastmarkets ficarem fora do ar por expiração de sessão.
    if args.playwright:
        try:
            from hunter.platts_scraper import get_platts_health
            from hunter.fastmarkets_scraper import get_fastmarkets_health
            from hunter.sync import record_source_health
            healths = {
                "platts":      get_platts_health(),
                "fastmarkets": get_fastmarkets_health(),
            }
            for src, hh in healths.items():
                record_source_health(src, login_failed=bool(hh.get("login_failed")))
            failed = [s for s, hh in healths.items() if hh.get("login_failed")]
            if failed:
                log.warning("SESSAO FORA DO AR (login falhou): %s", ", ".join(failed))
        except Exception as e:
            log.warning("source_health: falha ao registrar: %s", e)

    log.info("=== News Hunter done ===")


if __name__ == "__main__":
    main()
