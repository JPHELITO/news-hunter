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
    from hunter.sync import push_articles, record_run

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

    # Atualiza cotações, commodities e indicadores macro
    try:
        from hunter.prices import update_quotes, update_commodities, update_macro, update_iron_ore_platts
        q = update_quotes()
        c = update_commodities()
        m = update_macro()
        # Sobrescreve IRON_ORE com Platts IODEX real quando disponível
        if platts_prices:
            update_iron_ore_platts(platts_prices)
        log.info("Preços: quotes=%d, commodities=%d, macro=%d", q, c, m)
    except Exception as e:
        log.warning("Atualização de preços falhou: %s", e)

    log.info("=== News Hunter done ===")


if __name__ == "__main__":
    main()
