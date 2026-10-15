"""Push de artigos para o Supabase via REST API."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

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

# Memória do push_take_fields: o que ESTE job do Actions já gravou, por notícia.
# Medido em 2026-10-15: numa rodada de 283 regravações, 282 eram idênticas ao banco e
# NENHUMA mudava nada (a outra era notícia nova, que o INSERT já grava completa). Eram
# ~100 mil PATCHes por dia, ~95% de todas as chamadas ao Supabase — o que estourava o
# Log Ingestion. Com a memória, só vai PATCH quando o pacote de take muda. Mais de 6h =
# memória de outro job (o job dura 5h55): ignora e regrava tudo, como antes.
TAKE_CACHE_MAX_AGE_S = 6 * 3600
TAKE_CACHE_FILE = "news_hunter_take_cache.json"


def take_cache_path() -> Path | None:
    """Arquivo da memória de takes — SÓ no GitHub Actions, dentro do RUNNER_TEMP (que o
    runner esvazia a cada job). Fora do Actions devolve None = regrava tudo (o de sempre),
    então rodar à mão no computador do analista segue idêntico."""
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return None
    return Path(os.environ.get("RUNNER_TEMP") or tempfile.gettempdir()) / TAKE_CACHE_FILE


def _take_digest(take_data: dict) -> str:
    """Impressão digital do pacote de take que iria no PATCH."""
    raw = json.dumps(take_data, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _read_take_cache(path: Path) -> dict[str, str]:
    """Memória gravada por rodadas anteriores deste job. Qualquer problema = vazia (regrava tudo)."""
    try:
        if time.time() - path.stat().st_mtime > TAKE_CACHE_MAX_AGE_S:
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_take_cache(path: Path, cache: dict[str, str]) -> None:
    try:
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(cache), encoding="utf-8")
        os.replace(tmp, path)          # troca atômica: arquivo pela metade nunca é lido
    except Exception as e:
        log.debug("push_take_fields: memória não gravada: %s", e)


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


def push_take_fields(articles: list[dict], cache_path: Path | None = None) -> int:
    """PATCH take classification fields nos artigos já inseridos no Supabase.

    Usa requests PATCH individuais em paralelo (um por artigo).

    `cache_path` (só o hunt.py passa, e só no Actions — ver `take_cache_path`) é a memória
    do que ESTE job já gravou: notícia cujo pacote de take é igual ao da última gravação
    bem-sucedida não ganha PATCH de novo. Sem memória (None, arquivo ausente, ilegível ou
    com mais de 6h) = regrava tudo, exatamente como antes. PATCH que falha não entra na
    memória → é tentado de novo na rodada seguinte, como antes. URL repetida na mesma
    rodada também vai sempre (dois pacotes para a mesma linha: não dá para lembrar qual
    ficou no banco).

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

    def _pacote(art: dict) -> tuple[str, dict]:
        return art.get("url", ""), {k: art.get(k) for k in _TAKE_FIELDS if k in art}

    pendentes = articles
    memoria: dict[str, str] = {}
    if cache_path is not None:
        memoria = _read_take_cache(cache_path)
        vezes: dict[str, int] = {}
        for art in articles:
            u = art.get("url", "")
            vezes[u] = vezes.get(u, 0) + 1
        pendentes = []
        for art in articles:
            u, td = _pacote(art)
            if not (u and td and vezes[u] == 1 and memoria.get(u) == _take_digest(td)):
                pendentes.append(art)

    count = 0
    migration_needed = False
    results = []
    if pendentes:
        with ThreadPoolExecutor(max_workers=10) as ex:
            results = list(ex.map(_patch_one, pendentes))

    for res in results:
        if res is None:
            migration_needed = True
        elif res:
            count += 1

    if cache_path is not None:
        # A memória nova guarda SÓ as notícias desta rodada (a janela de 72h anda sozinha):
        # as puladas seguem iguais ao banco, e as gravadas com sucesso passam a valer.
        foram = {id(a) for a in pendentes}
        nova = {art["url"]: memoria[art["url"]] for art in articles if id(art) not in foram}
        for art, res in zip(pendentes, results):
            u, td = _pacote(art)
            if res is True and u and td and vezes[u] == 1:
                nova[u] = _take_digest(td)
        _write_take_cache(cache_path, nova)

    if migration_needed:
        log.warning(
            "push_take_fields: colunas de take não existem no Supabase. "
            "Rode o SQL de migração descrito na docstring desta função."
        )
    elif cache_path is not None:
        log.info("push_take_fields: %d/%d artigos atualizados com take fields "
                 "(%d sem mudança desde a última gravação deste job — não regravados)",
                 count, len(articles), len(articles) - len(pendentes))
    else:
        log.info("push_take_fields: %d/%d artigos atualizados com take fields", count, len(articles))
    return count
