"""Push de artigos para o Supabase via REST API."""
from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor

import requests

from .config import SUPABASE_TABLE

log = logging.getLogger(__name__)

BATCH_SIZE = 100  # máximo por request
RUNS_TABLE   = "hunter_runs"
HEALTH_TABLE = "source_health"

# Campos do news_take_classifier que existem na tabela news_articles
# (migration SQL já rodada). O push_articles inclui todos eles no INSERT;
# push_take_fields faz PATCH para atualizar artigos já existentes no banco.
_TAKE_FIELDS = frozenset({
    "take", "take_reason", "take_sector", "take_region",
    "take_topics", "take_covered_companies", "take_confidence",
    "take_matched_rules", "include_in_report", "exclusion_reason",
})


def record_run(articles_new: int) -> bool:
    """Registra um run do hunter na tabela hunter_runs.

    Chamado SEMPRE, mesmo quando o run não trouxe novidades — é o
    timestamp que o dashboard usa para mostrar "sincronizado há X min".

    Também limpa runs antigos (>24h) para manter a tabela enxuta: ~288 runs/dia
    (loop de 5 min) + ~48 (hunt-playwright de 30 min) ≈ 340 linhas no pico de 24h.
    """
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    if not url or not key:
        return False
    h = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    try:
        # Insere o run atual
        resp = requests.post(
            f"{url}/rest/v1/{RUNS_TABLE}",
            json={"articles_new": articles_new},
            headers={**h, "Prefer": "return=minimal"},
            timeout=10,
        )
        if not resp.ok:
            log.warning("hunter_runs insert: status %s | %s",
                        resp.status_code, resp.text[:200])
            return False
        log.info("hunter_runs: registro OK (articles_new=%d)", articles_new)

        # Limpa runs antigos (> 24h) — uma vez a cada 12 chamadas (~1h)
        from datetime import datetime, timedelta, timezone
        import random
        if random.randint(0, 11) == 0:
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
            from urllib.parse import quote
            requests.delete(
                f"{url}/rest/v1/{RUNS_TABLE}?ran_at=lt.{quote(cutoff)}",
                headers=h, timeout=10,
            )
        return True
    except Exception as e:
        log.warning("hunter_runs: falhou: %s", e)
        return False


def record_source_health(source: str, login_failed: bool, detail: dict | None = None) -> bool:
    """Upsert do "sinal de vida" de uma fonte Playwright na tabela source_health.

    Chamado a cada run (--playwright) para Platts e Fastmarkets. Permite ao watchdog
    distinguir "fonte quieta" (sessão viva, sem notícia nova) de "fonte morta".

    - last_attempt = agora (sempre).
    - last_ok      = agora SÓ se login_failed=False. Quando o login falha, last_ok NÃO é
      enviado → o valor anterior é preservado (merge-duplicates) e vai ficando "velho",
      que é exatamente o sinal que o watchdog usa para alertar.

    Idempotente por source (on_conflict=source). Se a tabela ainda não existir, loga um
    aviso e retorna False sem quebrar o run.

    SQL p/ criar a tabela (rodar UMA vez no Supabase SQL Editor):
    ──────────────────────────────────────────────────────────────
    create table if not exists source_health (
      source        text primary key,
      last_ok       timestamptz,
      last_attempt  timestamptz not null default now(),
      login_failed  boolean     not null default false,
      detail        jsonb
    );
    ──────────────────────────────────────────────────────────────
    """
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    if not url or not key:
        return False
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    row: dict = {"source": source, "last_attempt": now, "login_failed": login_failed}
    if not login_failed:
        row["last_ok"] = now
    if detail is not None:
        row["detail"] = detail
    try:
        r = requests.post(
            f"{url}/rest/v1/{HEALTH_TABLE}?on_conflict=source",
            json=[row],
            headers={
                "apikey": key,
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "Prefer": "resolution=merge-duplicates,return=minimal",
            },
            timeout=10,
        )
        if r.ok:
            log.info("source_health[%s]: login_failed=%s", source, login_failed)
            return True
        if r.status_code in (404, 400) and (
            "source_health" in r.text or "does not exist" in r.text or "relation" in r.text
        ):
            log.warning(
                "source_health: tabela ausente — rode o SQL da docstring de "
                "record_source_health no Supabase SQL Editor."
            )
        else:
            log.warning("source_health[%s] erro %s: %s", source, r.status_code, r.text[:200])
        return False
    except Exception as e:
        log.warning("source_health[%s] exceção: %s", source, e)
        return False


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
        batch = list(articles[i : i + BATCH_SIZE])
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


def push_take_fields(articles: list[dict]) -> int:
    """PATCH take classification fields nos artigos já inseridos no Supabase.

    Usa requests PATCH individuais em paralelo (um por artigo).
    Se as colunas ainda não existirem no schema (antes da migration SQL),
    loga um aviso e retorna 0 sem quebrar nada.

    SQL de migração necessário (rodar uma vez no Supabase SQL Editor):
    ──────────────────────────────────────────────────────────────────
    ALTER TABLE news_articles
      ADD COLUMN IF NOT EXISTS take                   text,
      ADD COLUMN IF NOT EXISTS take_reason            text,
      ADD COLUMN IF NOT EXISTS take_sector            text,
      ADD COLUMN IF NOT EXISTS take_region            text,
      ADD COLUMN IF NOT EXISTS take_topics            text,
      ADD COLUMN IF NOT EXISTS take_covered_companies text,
      ADD COLUMN IF NOT EXISTS take_confidence        float8,
      ADD COLUMN IF NOT EXISTS take_matched_rules     text,
      ADD COLUMN IF NOT EXISTS include_in_report      boolean,
      ADD COLUMN IF NOT EXISTS exclusion_reason       text;
    ──────────────────────────────────────────────────────────────────
    """
    supa_url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    if not supa_url or not key:
        return 0
    if not articles:
        return 0

    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }

    def _patch_one(art: dict) -> bool:
        article_url = art.get("url", "")
        if not article_url:
            return False
        take_data = {k: art.get(k) for k in _TAKE_FIELDS if k in art}
        if not take_data:
            return False
        endpoint = (
            f"{supa_url}/rest/v1/{SUPABASE_TABLE}"
            f"?url=eq.{requests.utils.quote(article_url, safe='')}"
        )
        try:
            r = requests.patch(endpoint, json=take_data, headers=headers, timeout=10)
            if not r.ok and r.status_code == 400 and "does not exist" in r.text:
                return None  # sinaliza: migration SQL ainda não foi rodada
            return r.ok
        except Exception as e:
            log.debug("push_take_fields patch falhou: %s", e)
            return False

    count = 0
    migration_needed = False
    with ThreadPoolExecutor(max_workers=10) as ex:
        results = list(ex.map(_patch_one, articles))

    for res in results:
        if res is None:
            migration_needed = True
        elif res:
            count += 1

    if migration_needed:
        log.warning(
            "push_take_fields: colunas de take não existem no Supabase. "
            "Rode o SQL de migração descrito na docstring desta função."
        )
    else:
        log.info("push_take_fields: %d/%d artigos atualizados com take fields", count, len(articles))
    return count
