"""
Market Pulse — o que de fato aconteceu na abertura.

POR QUE ISTO EXISTE
-------------------
Previsão sem placar é opinião. O produto "At the Open" mostra, todo dia, o que dissemos
ontem ao lado do que o mercado fez — e um histórico de acerto que o cliente pode conferir.
Nenhum concorrente gratuito faz isso para a B3, e é o jeito mais barato de construir
autoridade: os dados já estão gravados, só faltava fechar o ciclo.

Roda no corte das 18h, quando o pregão já acabou e o preço de abertura do dia é definitivo.

DUAS REGRAS QUE VÊM DA AUDITORIA
--------------------------------
1. O gap é ajustado por PROVENTOS. No ex-dividendo o preço cai por construção, e contar
   isso como "o mercado derrubou a ação" seria mentir sobre o modelo.
2. Abertura EXATAMENTE igual ao fechamento anterior = leilão sem negócio: não houve gap
   para acertar. Fica registrado como `traded=false` e sai da conta de acerto. Sem isso,
   papéis ilíquidos (RANI3 abre assim em 27,7% dos pregões) teriam um teto artificial.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone

import requests

from .prices import HEADERS, _YAHOO_HOSTS
from .pulse_score import _supa_get
from .pulse_snapshot import COMPANIES

log = logging.getLogger(__name__)


def gaps_realizados(desde: str) -> dict[tuple[str, str], tuple[float, bool]]:
    """{(empresa, data): (gap de abertura em %, houve negócio no leilão?)}."""
    out: dict[tuple[str, str], tuple[float, bool]] = {}
    for sym in COMPANIES:
        js = None
        for t in range(4):
            host = _YAHOO_HOSTS[t % len(_YAHOO_HOSTS)]
            try:
                r = requests.get(f"{host}/v8/finance/chart/{sym}?range=6mo&interval=1d"
                                 "&events=div,split", headers=HEADERS, timeout=30)
                if r.status_code in (429, 401, 403):
                    time.sleep(1.5 * (t + 1)); continue
                r.raise_for_status(); js = r.json(); break
            except Exception:
                time.sleep(1.0 * (t + 1))
        if not js:
            continue
        try:
            res = js["chart"]["result"][0]
            q = res["indicators"]["quote"][0]
        except (KeyError, IndexError, TypeError):
            continue
        adjs = (res["indicators"].get("adjclose") or [{}])[0].get("adjclose") or q.get("close")
        off = res["meta"].get("gmtoffset", -10800)
        prev = prev_close = None
        for i, ts in enumerate(res.get("timestamp") or []):
            o, c, a = q["open"][i], q["close"][i], adjs[i]
            d = datetime.fromtimestamp(ts + off, timezone.utc).date().isoformat()
            if None in (o, c, a):
                continue
            if prev and d >= desde:
                g = (o * (a / c)) / prev - 1
                # o carimbo se detecta no preço CRU: no ajustado, um dia de provento daria
                # diferente de zero e o filtro passaria batido
                negociou = abs(o / prev_close - 1) > 1e-9 if prev_close else True
                if abs(g) < 0.40:
                    out[(sym, d)] = (100 * g, negociou)
            prev, prev_close = a, c
        time.sleep(0.3)
    return out


def preencher(dias: int = 10, dry_run: bool = False) -> int:
    """
    Completa `gap_actual` nas linhas de pulse_daily que ainda não o têm.

    ⚠️ DEGRADA LIMPO. Se a coluna `gap_actual` ainda não existir no banco (o SQL de
    `admin/supabase_market_pulse.sql` não foi rodado), o PATCH devolve erro e a função
    apenas avisa — o pulse do dia seguinte continua saindo normalmente. O placar é um
    ganho de produto, não pode derrubar a previsão.
    """
    desde = (datetime.now(timezone.utc) - timedelta(days=dias + 5)).date().isoformat()
    try:
        pend = _supa_get(f"pulse_daily?status=eq.ok&gap_actual=is.null"
                         f"&session_date=gte.{desde}"
                         f"&select=session_date,cut,company&limit=2000")
    except Exception as e:
        log.warning("pulse_outcome: não consegui ler as pendências (%s). "
                    "A coluna gap_actual existe? Rode admin/supabase_market_pulse.sql.", e)
        return 0
    if not pend:
        log.info("pulse_outcome: nada pendente.")
        return 0

    datas = sorted({r["session_date"] for r in pend})
    reais = gaps_realizados(min(datas))

    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    headers = {"apikey": key, "Authorization": f"Bearer {key}",
               "Content-Type": "application/json", "Prefer": "return=minimal"}

    gravadas, sem_dado = 0, 0
    for r in pend:
        real = reais.get((r["company"], r["session_date"]))
        if real is None:
            sem_dado += 1
            continue
        gap, negociou = real
        if dry_run:
            gravadas += 1
            continue
        try:
            resp = requests.patch(
                f"{url}/rest/v1/pulse_daily"
                f"?session_date=eq.{r['session_date']}&cut=eq.{r['cut']}"
                f"&company=eq.{r['company']}",
                headers=headers,
                json={"gap_actual": round(gap, 6), "traded": bool(negociou)}, timeout=30)
            if resp.status_code >= 400:
                log.warning("pulse_outcome: PATCH recusado (%s) — %s. "
                            "Rode admin/supabase_market_pulse.sql para criar as colunas.",
                            resp.status_code, resp.text[:160])
                return gravadas
            gravadas += 1
        except Exception as e:
            log.warning("pulse_outcome: falha ao gravar %s/%s: %s",
                        r["company"], r["session_date"], e)
            return gravadas

    log.info("pulse_outcome: %d realizados gravados%s%s", gravadas,
             f", {sem_dado} sem cotação ainda" if sem_dado else "",
             " (dry-run)" if dry_run else "")
    return gravadas
