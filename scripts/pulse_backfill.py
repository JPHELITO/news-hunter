"""
pulse_backfill.py — semeia pulse_snapshot com o histórico que ainda dá para recuperar.

POR QUE ISTO É URGENTE
----------------------
O Yahoo só entrega barras HORÁRIAS dos últimos 730 dias. Cada dia que passa, um dia de
janela de treino desaparece para sempre. A captura diária (pulse_daily.yml) só constrói
histórico daqui para frente — este script é a única chance de recuperar o passado.
Rodar UMA vez, o quanto antes. É idempotente: rodar de novo só reescreve o mesmo dado.

A ARMADILHA QUE ESTE CÓDIGO EVITA
---------------------------------
O Yahoo carimba a barra horária no INÍCIO do intervalo. A barra `ts=12:00 UTC` fecha às
13:00 UTC — que é exatamente a abertura da B3. Usá-la seria look-ahead: o "preço de antes
da abertura" na verdade já é o preço DA abertura. No estudo, esse detalhe sozinho inflava
o IC em 12% (0,328 contra os 0,290 reais). Por isso só entram barras cujo FECHAMENTO
(ts + 1h) seja <= o corte, e existe um assert para garantir.

Uso:
    python scripts/pulse_backfill.py --dry-run     # conta e mostra, não grava
    python scripts/pulse_backfill.py               # grava
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

from hunter.prices import HEADERS, _YAHOO_HOSTS, _supa_upsert       # noqa: E402
from hunter.pulse_snapshot import (CUTS, PREMARKET_SYMBOLS,         # noqa: E402
                                   SNAPSHOT_SYMBOLS)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("pulse_backfill")

CALENDARIO = "VALE3.SA"     # de quem herdamos o calendário de pregões da B3
LOTE = 500                  # linhas por upsert


def _yahoo(symbol: str, params: str) -> dict | None:
    """GET no endpoint de chart, com rotação de host e backoff (mesma política do prices.py)."""
    for tentativa in range(4):
        host = _YAHOO_HOSTS[tentativa % len(_YAHOO_HOSTS)]
        try:
            r = requests.get(f"{host}/v8/finance/chart/{symbol}?{params}",
                             headers=HEADERS, timeout=30)
            if r.status_code in (429, 401, 403):
                time.sleep(1.5 * (tentativa + 1))
                continue
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if tentativa == 3:
                log.warning("  %s falhou: %s", symbol, e)
            time.sleep(1.0 * (tentativa + 1))
    return None


def barras_horarias(symbol: str) -> list[tuple[int, float]]:
    """
    [(instante UTC em que a barra FECHOU, preço), ...] dos últimos 730 dias.

    Para os símbolos de pré-mercado o Yahoo só devolve as barras estendidas com
    `includePrePost=true` — e devolve MESMO no histórico (medido em 2026-08-26: VALE tem
    288 barras de pré-mercado em 60 dias com o flag e ZERO sem ele). É por isso que os
    ADRs podem entrar no modelo já treinados, em vez de esperar meses de coleta.
    """
    pre = "true" if symbol in PREMARKET_SYMBOLS else "false"
    js = _yahoo(symbol, f"range=730d&interval=1h&includePrePost={pre}")
    if not js:
        return []
    try:
        res = js["chart"]["result"][0]
    except (KeyError, IndexError, TypeError):
        return []
    ts = res.get("timestamp") or []
    closes = (res.get("indicators", {}).get("quote") or [{}])[0].get("close") or []
    # +3600: o carimbo é o INÍCIO da barra; o dado só existe quando ela fecha.
    return [(int(t) + 3600, float(c)) for t, c in zip(ts, closes) if c is not None]


def pregoes_b3() -> list[str]:
    """Datas em que a B3 realmente negociou, nos últimos 2 anos."""
    js = _yahoo(CALENDARIO, "range=2y&interval=1d")
    if not js:
        raise RuntimeError("não consegui o calendário de pregões da B3")
    res = js["chart"]["result"][0]
    ts = res.get("timestamp") or []
    closes = (res.get("indicators", {}).get("quote") or [{}])[0].get("close") or []
    off = res["meta"].get("gmtoffset", -10800)
    return sorted({
        datetime.fromtimestamp(t + off, timezone.utc).date().isoformat()
        for t, c in zip(ts, closes) if c is not None
    })


def preco_no_corte(barras: list[tuple[int, float]], corte_epoch: int,
                   max_idade_h: float = 30.0) -> float | None:
    """Último fechamento de barra conhecido no instante do corte. None se velho demais."""
    melhor = None
    for fecha_em, preco in barras:
        if fecha_em > corte_epoch:          # barras vêm em ordem crescente
            break
        melhor = (fecha_em, preco)
    if melhor is None:
        return None
    assert melhor[0] <= corte_epoch, "look-ahead no backfill"
    if (corte_epoch - melhor[0]) / 3600 > max_idade_h:
        return None
    return melhor[1]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="conta e mostra, não grava")
    args = ap.parse_args()

    log.info("baixando calendário de pregões da B3 ...")
    sessoes = pregoes_b3()
    log.info("  %d pregões: %s a %s", len(sessoes), sessoes[0], sessoes[-1])

    log.info("baixando barras horárias de %d instrumentos ...", len(SNAPSHOT_SYMBOLS))
    series: dict[str, list[tuple[int, float]]] = {}
    for i, sym in enumerate(SNAPSHOT_SYMBOLS, 1):
        b = barras_horarias(sym)
        series[sym] = b
        primeira = (datetime.fromtimestamp(b[0][0], timezone.utc).date().isoformat()
                    if b else "—")
        log.info("  [%2d/%d] %-12s %6d barras  desde %s",
                 i, len(SNAPSHOT_SYMBOLS), sym, len(b), primeira)
        time.sleep(0.4)

    rows, por_corte, sem_dado = [], {c: set() for c in CUTS}, {}
    for sessao in sessoes:
        dia = datetime.fromisoformat(sessao).replace(tzinfo=timezone.utc)
        for corte, hora_utc in CUTS.items():
            corte_epoch = int((dia + timedelta(hours=hora_utc)).timestamp())
            for sym, barras in series.items():
                p = preco_no_corte(barras, corte_epoch)
                if p is None:
                    sem_dado[sym] = sem_dado.get(sym, 0) + 1
                    continue
                rows.append({"session_date": sessao, "symbol": sym, "cut": corte,
                             "price": round(p, 6),
                             "captured_at": datetime.fromtimestamp(corte_epoch,
                                                                   timezone.utc).isoformat()})
                por_corte[corte].add(sessao)

    log.info("")
    log.info("=== RESUMO ===")
    for corte in CUTS:
        log.info("  corte %s (%02d:00 UTC / %02d:00 BRT): %d pregões com dado",
                 corte, CUTS[corte], CUTS[corte] - 3, len(por_corte[corte]))
    log.info("  linhas a gravar: %d", len(rows))
    if sem_dado:
        piores = sorted(sem_dado.items(), key=lambda kv: -kv[1])[:8]
        log.info("  instrumentos com mais buracos (esperado p/ feriado e histórico curto):")
        for sym, n in piores:
            log.info("      %-12s %d ausências", sym, n)

    if args.dry_run:
        log.info("dry-run: nada gravado.")
        return 0
    if not rows:
        log.error("nada para gravar — abortando.")
        return 1

    gravadas = 0
    for i in range(0, len(rows), LOTE):
        gravadas += _supa_upsert("pulse_snapshot", rows[i:i + LOTE])
        log.info("  gravadas %d/%d", gravadas, len(rows))
    log.info("pronto: %d linhas em pulse_snapshot", gravadas)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
