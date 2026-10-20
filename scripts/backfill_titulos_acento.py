# -*- coding: utf-8 -*-
"""Conserta manchetes que ficaram no banco com ACENTO ESTRAGADO (UTF-8 lido como Latin-1).

"SÃ¶dra Establishing Commercial Sales Office in Massachusetts" -> "Södra Establishing ..."

De onde veio: a PaperAge manda a página sem charset no cabeçalho e o `hunter/html_scrapers.py`
lia com o `.text` do requests, que nesse caso chuta ISO-8859-1 (15 manchetes, 25/06 a
20/10/2026). O push usa ignore-duplicates, então consertar o scraper NÃO reescreve o que já
está gravado — daí este backfill, de rodar UMA vez, depois que o conserto estiver no ar.

Gasta pouco do Supabase: o filtro roda NO SERVIDOR (regex na coluna title) e só voltam as
linhas suspeitas (~5 KB em 20/10/2026), em vez de baixar a tabela inteira. Quem decide O QUE
muda é o `clean_headline` do clipping (a MESMA regra do Word/e-mail): o filtro do servidor é
mais largo de propósito e também traz títulos certos ("Comitê" + espaço fixo), que ficam como
estão — e aparecem na lista, para conferência.

Uso (de news-hunter/):
    python scripts/backfill_titulos_acento.py            # simulação: lista o que mudaria
    python scripts/backfill_titulos_acento.py --aplicar  # grava e confere de novo

Precisa de SUPABASE_URL + SUPABASE_SERVICE_KEY (.env).
"""
from __future__ import annotations

import argparse
import io
import os
import re
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
# Regex do Postgres: 1º byte de UTF-8 visto como Latin-1 (Â..ô) + um byte de continuação
# (U+0080–U+00BF). Mais largo que a regra do clean_headline, de propósito.
SUSPEITO = "[\xc2-\xf4][\x80-\xbf]"


def _env() -> tuple[str, str]:
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    if not url or not key:
        raise SystemExit("SUPABASE_URL / SUPABASE_SERVICE_KEY ausentes (.env)")
    return url, key


def _suspeitas(url: str, h: dict) -> list[dict]:
    rows, offset = [], 0
    while True:
        r = requests.get(f"{url}/rest/v1/news_articles", headers=h, timeout=60,
                         params={"select": "url,title,source_name,found_at",
                                 "title": f"match.{SUSPEITO}", "order": "found_at.desc",
                                 "limit": PAGE, "offset": offset})
        r.raise_for_status()
        b = r.json()
        rows += b
        offset += PAGE
        if len(b) < PAGE:
            return rows


def _a_corrigir(rows: list[dict]) -> list[tuple[dict, str]]:
    pares = [(x, clean_headline(x["title"] or "")) for x in rows]
    return [(x, depois) for x, depois in pares if depois != x["title"]]


def _visivel(t: str) -> str:
    """Mostra os caracteres invisíveis do estrago: "PaulÃ<AD>nia", "Ã<84>Ã¤nekoski"."""
    return re.sub(r"[\x00-\x1f\x7f-\x9f\xad]", lambda m: f"<{ord(m.group(0)):02X}>", t)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--aplicar", action="store_true", help="grava (sem isso, só simula)")
    a = ap.parse_args()
    url, key = _env()
    h = {"apikey": key, "Authorization": f"Bearer {key}"}

    rows = _suspeitas(url, h)
    alvos = _a_corrigir(rows)
    mudam = {x["url"] for x, _ in alvos}
    ficam = [x for x in rows if x["url"] not in mudam]
    print(f"suspeitas (filtro do servidor): {len(rows)}  |  a corrigir: {len(alvos)}"
          f"  |  certas, ficam como estão: {len(ficam)}\n")
    for x, depois in alvos:
        print(f"   {x['found_at'][:10]}  {x.get('source_name') or '?'}")
        print(f"   - {_visivel(x['title'])}\n   + {depois}\n")
    for x in ficam:
        print(f"   {x['found_at'][:10]}  {x.get('source_name') or '?'}  (fica)\n   = {_visivel(x['title'])}\n")

    if not a.aplicar:
        print("SIMULAÇÃO — nada foi gravado. Rode com --aplicar para valer.")
        return
    if not alvos:
        return

    ok = err = 0
    for x, depois in alvos:
        # filtro por params → o requests codifica a URL do artigo (tem ?, &, /)
        r = requests.patch(f"{url}/rest/v1/news_articles",
                           headers={**h, "Content-Type": "application/json",
                                    "Prefer": "return=minimal"},
                           params={"url": f"eq.{x['url']}"},
                           json={"title": depois}, timeout=30)
        if r.ok:
            ok += 1
        else:
            err += 1
            print(f"   FALHOU {x['url'][:60]}: {r.status_code} {r.text[:120]}")
    print(f"gravados: {ok}  |  falhas: {err}")
    print(f"conferência no banco: {len(_a_corrigir(_suspeitas(url, h)))} pendentes")


if __name__ == "__main__":
    main()
