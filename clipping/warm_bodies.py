"""Aquecedor de corpos do clipping — 100% À PARTE do news hunter.

LÊ os candidatos recentes (as 6 fontes do clipping) do news_articles (SÓ LEITURA — nunca escreve
lá) e, para cada um SEM corpo guardado, raspa o corpo (reusando os scrapers do clipping) + traduz e
GUARDA em clipping_bodies. Depois: clicar na headline = corpo já pronto (instantâneo); Gerar = reusa
(Word rápido). NÃO toca no pipeline do hunter (hunt.py/coleta/IA); o único recurso compartilhado é a
sessão keep-alive (Platts/FM), que este processo só ROLA PRA FRENTE (nunca invalida).

Rodar de news-hunter/:  python -m clipping.warm_bodies [--budget 900] [--limit 80] [--cooldown 4]
Usa SUPABASE_URL + SUPABASE_SERVICE_KEY e COOKIES_DIR (sessões), como o run_jobs.
"""
from __future__ import annotations

import argparse
import logging
import os
import time
from datetime import datetime, timezone
from urllib.parse import quote

import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

from .bodies import get_stored_body, norm_domain, store_body
from .generate import _fetch_and_translate

log = logging.getLogger(__name__)

# As 5 fontes do clipping (idênticas ao admin_get_clipping_candidates; Estadão removido 2026-07-31).
_SOURCES = ["S&P Platts", "Fastmarkets", "Valor Econômico", "Mining.com", "Portal Celulose"]


def _env() -> tuple[str, str]:
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    if not url or not key:
        raise RuntimeError("SUPABASE_URL / SUPABASE_SERVICE_KEY ausentes")
    return url, key


def _candidates(url: str, key: str, limit: int) -> list[dict]:
    """Candidatos recentes das 6 fontes — SÓ LEITURA do news_articles (não interfere no hunter)."""
    src_in = ",".join(f'"{s}"' for s in _SOURCES)
    r = requests.get(
        f"{url}/rest/v1/news_articles"
        f"?select=url,title,source_name&source_name=in.({src_in})"
        f"&include_in_report=not.is.false&order=found_at.desc&limit={limit}",
        headers={"apikey": key, "Authorization": f"Bearer {key}"}, timeout=30)
    r.raise_for_status()
    return r.json()


def _skip(url: str, su: str, key: str, cooldown_h: float) -> bool:
    """Pula se já tem corpo bom (char_len>80) OU se foi tentado há < cooldown_h (evita re-raspar
    os vazios toda rodada)."""
    try:
        r = requests.get(
            f"{su}/rest/v1/clipping_bodies?url=eq.{quote(url, safe='')}&select=char_len,fetched_at&limit=1",
            headers={"apikey": key, "Authorization": f"Bearer {key}"}, timeout=15)
        if r.ok and r.json():
            row = r.json()[0]
            if (row.get("char_len") or 0) > 80:
                return True
            fa = row.get("fetched_at")
            if fa:
                try:
                    age_h = (datetime.now(timezone.utc)
                             - datetime.fromisoformat(fa.replace("Z", "+00:00"))).total_seconds() / 3600
                    if age_h < cooldown_h:
                        return True
                except Exception:
                    pass
    except Exception as e:
        log.debug("_skip(%s): %s", url, e)
    return False


def warm(budget: int = 900, limit: int = 80, cooldown_h: float = 4.0) -> dict:
    su, key = _env()
    rows = _candidates(su, key, limit)
    log.info("aquecedor: %d candidatos recentes das 6 fontes", len(rows))
    t0 = time.time()
    n_ok = n_skip = n_empty = 0
    for row in rows:
        if time.time() - t0 > budget:
            log.info("aquecedor: budget de %ds esgotado", budget)
            break
        u = (row.get("url") or "").strip()
        if not u or _skip(u, su, key, cooldown_h):
            n_skip += 1
            continue
        title = row.get("title") or u
        src = row.get("source_name") or ""
        try:
            body, tt, tb = _fetch_and_translate(u, norm_domain(u), title)
        except Exception as e:
            log.warning("aquecedor: erro em %s: %s", u, e)
            body, tt, tb = "", "", ""
        if body:
            store_body(u, title, src, body, tt, tb, status="ok")
            n_ok += 1
            log.info("aquecedor: OK (%d chars) %s", len(body), u[-70:])
        else:
            store_body(u, title, src, "", status="empty")   # marca (cooldown) p/ não martelar
            n_empty += 1
    return {"ok": n_ok, "skip": n_skip, "empty": n_empty, "total": len(rows)}


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=int, default=900, help="teto de tempo (s)")
    ap.add_argument("--limit", type=int, default=80, help="quantos candidatos recentes considerar")
    ap.add_argument("--cooldown", type=float, default=4.0, help="horas p/ re-tentar um corpo vazio")
    a = ap.parse_args()
    res = warm(budget=a.budget, limit=a.limit, cooldown_h=a.cooldown)
    print(f"aquecedor: {res['ok']} guardados, {res['skip']} pulados, {res['empty']} sem corpo "
          f"(de {res['total']} candidatos)")


if __name__ == "__main__":
    main()
