"""Push de artigos para o Supabase via REST API."""
from __future__ import annotations

import logging
import os

import requests

from .config import SUPABASE_TABLE

log = logging.getLogger(__name__)

BATCH_SIZE = 100  # máximo por request


def push_articles(articles: list[dict]) -> int:
    """Insere artigos no Supabase, preservando found_at da primeira descoberta.

    CRÍTICO — usa resolution=ignore-duplicates (não merge-duplicates):
      • Artigo NOVO (URL inédita)    → insere, found_at = agora
      • Artigo já existente (URL repetida) → IGNORA por completo
        ↳ found_at original preservado — não "renasce" como recente

    Isso garante que cada notícia tem um único timestamp imutável que
    representa "quando ficou visível na dashboard pela primeira vez".

    Retorna: número de artigos efetivamente inseridos (novos).
    """
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")

    if not url or not key:
        log.warning("SUPABASE_URL ou SUPABASE_SERVICE_KEY não configurados — sync desabilitado")
        return 0

    if not articles:
        return 0

    endpoint = f"{url}/rest/v1/{SUPABASE_TABLE}"
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        # ignore-duplicates: se URL já existe no banco, ESTA linha é ignorada
        # (found_at original preservado). count=exact para saber quantos foram
        # de fato inseridos.
        "Prefer": "resolution=ignore-duplicates,return=minimal,count=exact",
    }

    total_new = 0
    for i in range(0, len(articles), BATCH_SIZE):
        batch = articles[i : i + BATCH_SIZE]
        try:
            resp = requests.post(endpoint, json=batch, headers=headers, timeout=20)
            if resp.ok:
                # Content-Range: "0-N/total" — onde N+1 = inseridos no batch
                cr = resp.headers.get("content-range", "")
                inserted = len(batch)
                if "/" in cr:
                    try:
                        inserted = int(cr.split("/")[-1])
                    except ValueError:
                        pass
                total_new += inserted
                log.info(
                    "Supabase batch %d: %d enviados → %d novos inseridos",
                    i // BATCH_SIZE + 1, len(batch), inserted,
                )
            else:
                log.warning("Supabase error %s: %s", resp.status_code, resp.text[:200])
        except Exception as e:
            log.warning("Supabase request falhou: %s", e)

    return total_new
