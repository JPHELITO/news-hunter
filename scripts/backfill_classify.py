"""
One-shot: classifica todos os news_articles que ainda não foram classificados.

Roda em paralelo via requests ao Supabase REST API.
Pode ser executado várias vezes — apenas reprocessa rows com sector IS NULL.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

# Carrega .env do projeto
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

import requests
from hunter.classify import classify

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

PAGE_SIZE = 500
SUPA_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPA_KEY = os.environ["SUPABASE_SERVICE_KEY"]
H = {
    "apikey": SUPA_KEY,
    "Authorization": f"Bearer {SUPA_KEY}",
    "Content-Type": "application/json",
}


def fetch_unclassified(offset: int = 0) -> list[dict]:
    """Busca artigos sem classificação (sector IS NULL)."""
    url = (
        f"{SUPA_URL}/rest/v1/news_articles"
        f"?select=url,title,snippet&sector=is.null"
        f"&order=found_at.desc&limit={PAGE_SIZE}&offset={offset}"
    )
    r = requests.get(url, headers=H, timeout=30)
    r.raise_for_status()
    return r.json()


def update_classification(url: str, cls: dict) -> bool:
    """Atualiza um artigo com sua classificação."""
    endpoint = f"{SUPA_URL}/rest/v1/news_articles?url=eq.{requests.utils.quote(url, safe='')}"
    body = {
        "sector":    cls["sector"],
        "news_type": cls["news_type"],
        "sentiment": cls["sentiment"],
        "tickers":   cls["tickers"],
    }
    r = requests.patch(
        endpoint, json=body,
        headers={**H, "Prefer": "return=minimal"},
        timeout=15,
    )
    return r.ok


def main():
    total = 0
    while True:
        batch = fetch_unclassified()  # sempre offset 0 — após PATCH eles saem do filtro
        if not batch:
            break
        log.info("Processando batch de %d artigos…", len(batch))
        for art in batch:
            cls = classify(art.get("title", ""), art.get("snippet", ""))
            if update_classification(art["url"], cls):
                total += 1
        log.info("Total acumulado: %d", total)
    log.info("=== Backfill concluído: %d artigos classificados ===", total)


if __name__ == "__main__":
    main()
