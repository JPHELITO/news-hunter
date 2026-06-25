# -*- coding: utf-8 -*-
"""Passo SHADOW da IA de takes (FASE 3 — LLM_TAKES_PLAN.md §4/§5). Roda PÓS-push.

- GATED: só roda com LLM_TAKES_ENABLED=1 E GITHUB_ACTIONS=true (evita run local
  acidental disparar chamadas + a corrida entre os 2 workflows — habilitar só no hunt-loop).
- Grava SÓ take_llm* (a dashboard segue 100% determinística — zero risco client-facing).
- Fila = só pendentes (take_llm IS NULL), cap de tentativas (artigo-veneno), mesma janela
  do feed (published_at 48h ou null), novos primeiro. Orçamento de tempo por run.
- Racional vai p/ llm_take_log (RLS fechada, backstage). Tudo falha gracioso.
"""
from __future__ import annotations

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import requests

from . import llm_take
from .article_body import fetch_body

log = logging.getLogger(__name__)

BATCH = int(os.environ.get("LLM_SHADOW_BATCH", "30"))
TIME_BUDGET_S = int(os.environ.get("LLM_SHADOW_BUDGET_S", "180"))
CATCHUP = int(os.environ.get("LLM_SHADOW_CATCHUP", "8"))   # itens >48h recuperados/rodada (mata a perda permanente)
MAX_ATTEMPTS = 8
QUEUE_AGE_ALARM_H = 2
WINDOW_H = 48


def _enabled() -> bool:
    return os.environ.get("LLM_TAKES_ENABLED") == "1" and os.environ.get("GITHUB_ACTIONS") == "true"


def _supa():
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    if not (url and key):
        return None, None
    return url, {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def run_llm_shadow() -> None:
    """Classifica em sombra os incluídos sem take_llm. COBERTAS primeiro (as 13 nunca
    ficam sem take); recupera a cauda >48h (catch-up, mais-antigo-primeiro); pré-busca
    corpos em PARALELO (tira a rede do caminho crítico — a IA segue serial/throttlada);
    disjuntor por rodada (não desperdiça orçamento em provedor com cota estourada).
    Não-fatal."""
    if not _enabled():
        return
    url, H = _supa()
    if not url:
        return
    if not llm_take.CHAIN:
        log.warning("LLM shadow: nenhum provedor com chave — pulando")
        return
    llm_take.reset_run_skips()   # zera o disjuntor de provedor por rodada

    cutoff = (datetime.now(timezone.utc) - timedelta(hours=WINDOW_H)).isoformat()
    win = (f"&or=(published_at.gte.{quote(cutoff)},"
           f"and(published_at.is.null,found_at.gte.{quote(cutoff)}))")
    base = (f"{url}/rest/v1/news_articles?select=url,title,source_name"
            f"&include_in_report=eq.true&take_llm=is.null&take_llm_attempts=lt.{MAX_ATTEMPTS}")

    def _q(extra, order="found_at.desc", lim=BATCH):
        try:
            r = requests.get(f"{base}{extra}&order={order}&limit={lim}", headers=H, timeout=30)
        except Exception as e:
            log.warning("LLM shadow: query exceção: %s", e)
            return None
        if not r.ok:
            if r.status_code in (400, 404) and ("take_llm" in r.text or "does not exist" in r.text):
                log.warning("LLM shadow: colunas take_llm ausentes — rode scripts/llm_shadow_migration.sql")
            else:
                log.warning("LLM shadow: query %s: %s", r.status_code, r.text[:160])
            return None
        return r.json()

    general = _q(win)
    if general is None:
        return                                                            # erro já logado
    covered = _q(win + "&take_covered_companies=not.is.null") or []        # as 13 cobertas → prioridade
    catchup = _q(f"&found_at=lt.{quote(cutoff)}", order="found_at.asc", lim=CATCHUP) or []  # cauda >48h

    seen, pend = set(), []
    for group in (covered, general, catchup):                             # COBERTAS, depois janela, depois cauda
        for a in group:
            u = a.get("url")
            if u and u not in seen:
                seen.add(u); pend.append(a)
    pend = pend[:BATCH + CATCHUP]                                          # bound da pré-busca
    if not pend:
        _check_queue_age(url, H, cutoff)
        return

    bodies = {}                                                           # pré-busca de corpos em PARALELO
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(fetch_body, a["url"], a.get("source_name")): a["url"]
                for a in pend if a.get("url")}
        for f in as_completed(futs):
            try:
                bodies[futs[f]] = (f.result() or (None,))[0]
            except Exception:
                bodies[futs[f]] = None

    t0 = time.time()
    done = 0
    for a in pend:
        if time.time() - t0 > TIME_BUDGET_S:
            log.info("LLM shadow: orçamento de %ds atingido — resto na próxima rodada", TIME_BUDGET_S)
            break
        url_a, title = a.get("url"), a.get("title", "")
        if not url_a:
            continue
        res = llm_take.classify(title, source=None, body=(bodies.get(url_a) or None))   # Decisão G: SEM fonte
        if res is None:
            _bump_attempt(url, H, url_a)
            continue
        _save(url, H, url_a, res)
        done += 1

    log.info("LLM shadow: %d/%d classificados (cobertas=%d catch-up=%d chain=%s attempts=%s)",
             done, len(pend), len(covered), len(catchup), llm_take.CHAIN, llm_take.chain_status()["attempts"])
    _check_queue_age(url, H, cutoff)


def _save(url, H, url_a, res) -> None:
    ep = f"{url}/rest/v1/news_articles?url=eq.{quote(url_a, safe='')}"
    patch = {"take_llm": res["take"], "take_llm_model": res["model"],
             "take_llm_at": datetime.now(timezone.utc).isoformat()}
    try:
        requests.patch(ep, json=patch, headers={**H, "Prefer": "return=minimal"}, timeout=10)
        requests.post(  # racional BACKSTAGE (RLS fechada)
            f"{url}/rest/v1/llm_take_log?on_conflict=url",
            json=[{"url": url_a, "reason": res["reason"], "model": res["model"], "attempts": 1}],
            headers={**H, "Prefer": "resolution=merge-duplicates,return=minimal"}, timeout=10)
    except Exception as e:
        log.debug("LLM shadow: save falhou: %s", e)


def _bump_attempt(url, H, url_a) -> None:
    """Incrementa take_llm_attempts (GET+PATCH; PostgREST não faz col=col+1 sem RPC).
    Volume é baixo (~0-5 novos/run), então 2 chamadas é aceitável."""
    ep = f"{url}/rest/v1/news_articles?url=eq.{quote(url_a, safe='')}"
    try:
        g = requests.get(ep + "&select=take_llm_attempts", headers=H, timeout=10)
        cur = (g.json()[0].get("take_llm_attempts") or 0) if (g.ok and g.json()) else 0
        requests.patch(ep, json={"take_llm_attempts": cur + 1},
                       headers={**H, "Prefer": "return=minimal"}, timeout=10)
    except Exception:
        pass


def _check_queue_age(url, H, cutoff) -> None:
    """Alarme NO PRÓPRIO LOOP (roda a cada 5 min) se há pendente há >2h — não depende
    do watchdog (cron horário atrasado). Job loga WARNING; escalar p/ falha é opcional."""
    old = (datetime.now(timezone.utc) - timedelta(hours=QUEUE_AGE_ALARM_H)).isoformat()
    q = (f"{url}/rest/v1/news_articles?select=url&include_in_report=eq.true&take_llm=is.null"
         f"&take_llm_attempts=lt.{MAX_ATTEMPTS}&found_at=lt.{quote(old)}"
         f"&or=(published_at.gte.{quote(cutoff)},and(published_at.is.null,found_at.gte.{quote(cutoff)}))")
    try:
        r = requests.get(q, headers={**H, "Prefer": "count=exact", "Range": "0-4"}, timeout=15)
        cr = r.headers.get("content-range", "")
        n = int(cr.split("/")[-1]) if "/" in cr and cr.split("/")[-1].isdigit() else 0
        if n > 0:
            log.warning("LLM shadow: %d notícia(s) pendente(s) há >%dh — provedores podem estar fora",
                        n, QUEUE_AGE_ALARM_H)
    except Exception:
        pass
