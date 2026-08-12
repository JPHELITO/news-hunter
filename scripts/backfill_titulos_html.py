# -*- coding: utf-8 -*-
"""Conserta manchetes que ficaram no banco com CÓDIGO DE HTML dentro do título.

"Arauco&rsquo;s Sucuri&uacute; project" -> "Arauco's Sucuriú project"

De onde veio: o `hunter/fastmarkets_scraper.py` mandava o título CRU da API do FM pro
Supabase (o snippet já era decodificado, o título não). O push usa ignore-duplicates, então
corrigir o scraper NÃO reescreve o que já está gravado — daí este backfill, de rodar UMA vez.

Na dashboard a home decodifica sozinha (innerHTML), mas a aba News e a lista de candidatas
do Clipping escapam o texto -> o cliente via "Arauco&rsquo;s" na tela.

Uso (de news-hunter/):
    python scripts/backfill_titulos_html.py            # simulação: mostra o que mudaria
    python scripts/backfill_titulos_html.py --aplicar  # grava

Precisa de SUPABASE_URL + SUPABASE_SERVICE_KEY (.env).
"""
from __future__ import annotations

import argparse
import io
import os
import sys

import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from clipping.build import clean_headline          # noqa: E402  (a MESMA regra do clipping)

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

PAGE = 1000


def _env() -> tuple[str, str]:
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    if not url or not key:
        raise SystemExit("SUPABASE_URL / SUPABASE_SERVICE_KEY ausentes (.env)")
    return url, key


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--aplicar", action="store_true", help="grava (sem isso, só simula)")
    a = ap.parse_args()
    url, key = _env()
    h = {"apikey": key, "Authorization": f"Bearer {key}"}

    rows, offset = [], 0
    while True:
        r = requests.get(f"{url}/rest/v1/news_articles", headers=h, timeout=60,
                         params={"select": "url,title,source_name", "order": "found_at.desc",
                                 "limit": PAGE, "offset": offset})
        r.raise_for_status()
        b = r.json()
        rows += b
        offset += PAGE
        if len(b) < PAGE:
            break

    # a chave da tabela é a URL (não existe coluna id)
    alvos = [(x["url"], x["title"], clean_headline(x["title"] or ""), x.get("source_name"))
             for x in rows]
    alvos = [t for t in alvos if t[2] != t[1]]
    print(f"manchetes lidas: {len(rows)}  |  a corrigir: {len(alvos)}")
    if not alvos:
        return

    por_fonte: dict[str, int] = {}
    for _, _, _, src in alvos:
        por_fonte[src or "?"] = por_fonte.get(src or "?", 0) + 1
    for k, v in sorted(por_fonte.items(), key=lambda kv: -kv[1]):
        print(f"   {v:4d}  {k}")
    print()
    for _, antes, depois, _ in alvos[:5]:
        print(f"   - {antes[:88]}\n   + {depois[:88]}\n")

    if not a.aplicar:
        print("SIMULAÇÃO — nada foi gravado. Rode com --aplicar para valer.")
        return

    ok = err = 0
    for u_art, _, depois, _ in alvos:
        # filtro por params → o requests codifica a URL do artigo (tem ?, &, /)
        r = requests.patch(f"{url}/rest/v1/news_articles",
                           headers={**h, "Content-Type": "application/json",
                                    "Prefer": "return=minimal"},
                           params={"url": f"eq.{u_art}"},
                           json={"title": depois}, timeout=30)
        if r.ok:
            ok += 1
        else:
            err += 1
            print(f"   FALHOU {u_art[:60]}: {r.status_code} {r.text[:120]}")
    print(f"gravados: {ok}  |  falhas: {err}")


if __name__ == "__main__":
    main()
