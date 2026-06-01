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
    from hunter.fetcher import fetch_all
    from hunter.filter import filter_articles
    from hunter.sync import push_articles

    log.info("=== News Hunter start ===")

    articles_raw = fetch_all()
    log.info("Fetched: %d artigos brutos", len(articles_raw))

    articles_filtered = filter_articles(articles_raw)
    log.info("Após filtro: %d artigos relevantes", len(articles_filtered))

    pushed = push_articles(articles_filtered)
    log.info("Supabase: %d artigos enviados", pushed)

    log.info("=== News Hunter done ===")


if __name__ == "__main__":
    main()
