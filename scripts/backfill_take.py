"""
backfill_take.py — Classifica (take) todos os artigos que ainda têm take IS NULL.

Roda em paralelo: busca artigos sem take → classifica → PATCH no Supabase.
Pode ser executado várias vezes sem risco — só reprocessa rows com take IS NULL.

Uso:
    python scripts/backfill_take.py
    python scripts/backfill_take.py --dry-run   # mostra resultados sem salvar
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

import requests
from hunter.news_take_classifier import classify_take

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

PAGE_SIZE  = 200
MAX_WORKERS = 10

SUPA_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPA_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
H = {
    "apikey": SUPA_URL and SUPA_KEY and SUPA_KEY or "",
    "Authorization": f"Bearer {SUPA_KEY}",
    "Content-Type": "application/json",
}


def _headers():
    return {
        "apikey": SUPA_KEY,
        "Authorization": f"Bearer {SUPA_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }


def fetch_batch(offset: int) -> list[dict]:
    """Busca artigos sem take classificado."""
    url = (
        f"{SUPA_URL}/rest/v1/news_articles"
        f"?select=url,title,snippet,source_name"
        f"&take=is.null"
        f"&order=found_at.desc"
        f"&limit={PAGE_SIZE}&offset={offset}"
    )
    r = requests.get(url, headers=_headers(), timeout=30)
    r.raise_for_status()
    return r.json()


def patch_take(art: dict, dry_run: bool) -> bool:
    """Classifica e faz PATCH de um artigo."""
    text = " ".join(filter(None, [
        art.get("title", ""),
        art.get("snippet", ""),
        art.get("source_name", ""),
    ]))
    meta = {"source_name": art.get("source_name", "")}
    result = classify_take(text, meta)

    take_data = {
        "take":                   result["take"],
        "take_reason":            result["take_reason"],
        "take_sector":            result["sector"],
        "take_region":            result["region"],
        "take_topics":            "; ".join(result["normalized_topics"]),
        "take_covered_companies": "; ".join(result["covered_companies_mentioned"]),
        "take_confidence":        result["confidence"],
        "take_matched_rules":     "; ".join(result["matched_rules"]),
        "include_in_report":      result["include_in_report"],
        "exclusion_reason":       result["exclusion_reason"],
    }

    if dry_run:
        log.info("[DRY] %s → take=%s (%.2f) %s",
                 art["url"][:60], result["take"], result["confidence"],
                 result["take_reason"][:80])
        return True

    endpoint = (
        f"{SUPA_URL}/rest/v1/news_articles"
        f"?url=eq.{requests.utils.quote(art['url'], safe='')}"
    )
    r = requests.patch(endpoint, json=take_data, headers=_headers(), timeout=15)
    if not r.ok:
        log.warning("PATCH falhou %s: %s %s", art["url"][:60], r.status_code, r.text[:120])
    return r.ok


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Classifica mas não salva no Supabase")
    args = parser.parse_args()

    if not SUPA_URL or not SUPA_KEY:
        log.error("SUPABASE_URL e SUPABASE_SERVICE_KEY precisam estar no .env")
        sys.exit(1)

    total = 0
    offset = 0

    while True:
        batch = fetch_batch(offset)
        if not batch:
            break

        log.info("Batch offset=%d — %d artigos a classificar…", offset, len(batch))

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            futures = {ex.submit(patch_take, art, args.dry_run): art for art in batch}
            for fut in as_completed(futures):
                if fut.result():
                    total += 1

        log.info("Acumulado: %d artigos", total)

        if args.dry_run:
            # Em dry-run, offset não avança (artigos não foram atualizados)
            offset += len(batch)
            if len(batch) < PAGE_SIZE:
                break
        else:
            # Com PATCH feito, os artigos saem do filtro take=is.null
            # → sempre busca offset=0 até não restar nada
            if len(batch) < PAGE_SIZE:
                break

    log.info("=== Backfill take concluído: %d artigos classificados ===", total)


if __name__ == "__main__":
    main()
