"""
backfill_quote_history.py — puxa o histórico DIÁRIO completo de cada papel da aba
Market e grava na tabela `quote_history`. Roda UMA vez; depois o hunt-loop só faz
append do fechamento do dia (hunter/prices.py::update_quote_history).

Por que um script separado e não o robô de sempre: são 47 requisições que devolvem
~650 KB cada. É barato (o hunt-loop já faz 47 requisições ao Yahoo a cada 5 minutos),
mas é trabalho de uma vez só — não faz sentido pendurar no ciclo de 5 minutos.

⚠️ NÃO usa `range=max`: o Yahoo rebaixa para barras MENSAIS quando a janela é longa.
   O diário de verdade vem de period1/period2 (ver hunter/quote_history.py).

Uso:
    python scripts/backfill_quote_history.py --dry-run      # mede, não grava
    python scripts/backfill_quote_history.py                # grava quem está faltando
    python scripts/backfill_quote_history.py --force        # re-puxa TODOS
    python scripts/backfill_quote_history.py --only VALE3.SA,GGBR4.SA
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

from hunter import quote_history as qh
from hunter.prices import QUOTES_LIST

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("backfill_quote_history")

PAUSE_S = 0.8      # respiro entre papéis; 47 × 0,8s ≈ 40s a mais no total


def _d(ts) -> str:
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d") if ts else "-"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="mede e mostra, sem gravar")
    ap.add_argument("--force", action="store_true", help="re-puxa mesmo quem já tem série")
    ap.add_argument("--only", default="", help="lista de tickers separados por vírgula")
    ap.add_argument("--pause", type=float, default=PAUSE_S)
    args = ap.parse_args()

    only = {t.strip() for t in args.only.split(",") if t.strip()}
    alvo = [(tk, qsym) for tk, _n, _s, _e, _p, qsym in QUOTES_LIST if not only or tk in only]
    if not alvo:
        log.error("nenhum ticker casou com --only=%s", args.only)
        return 2

    estado = qh.load_state()
    if not args.dry_run and not estado and not qh._env()[0]:
        log.error("SUPABASE_URL/SUPABASE_SERVICE_KEY ausentes - nada a gravar")
        return 2

    feitos = pulados = falhas = 0
    total_pts = 0
    t_ini = time.time()
    log.info("backfill de %d papéis (force=%s, dry-run=%s)", len(alvo), args.force, args.dry_run)

    for i, (ticker, qsym) in enumerate(alvo, 1):
        st = estado.get(ticker)
        if st and not args.force:
            log.info("[%2d/%d] %-12s já tem %s pontos (%s -> %s) - pulando",
                     i, len(alvo), ticker, st.get("points"), _d(st.get("first_ts")), _d(st.get("last_ts")))
            pulados += 1
            continue

        pts = qh.fetch_full(qsym)
        if len(pts) < 2:
            log.warning("[%2d/%d] %-12s sem série utilizável (%d pontos)", i, len(alvo), ticker, len(pts))
            falhas += 1
            time.sleep(args.pause)
            continue

        span = (pts[-1][0] - pts[0][0]) / 86400
        espac = span / max(1, len(pts) - 1)
        # espaçamento ~1,4 dia = diário (fim de semana não tem pregão). Se vier ~30, o
        # Yahoo devolveu mensal — sinal de que alguém trocou period1/period2 por range=max.
        if espac > 5:
            log.warning("[%2d/%d] %-12s espacamento de %.1f dias - NAO e diario, ignorando",
                        i, len(alvo), ticker, espac)
            falhas += 1
            time.sleep(args.pause)
            continue

        total_pts += len(pts)
        log.info("[%2d/%d] %-12s %5d pontos  %s -> %s  (%.2fd)",
                 i, len(alvo), ticker, len(pts), _d(pts[0][0]), _d(pts[-1][0]), espac)

        if not args.dry_run:
            if qh.save(ticker, pts):
                feitos += 1
            else:
                falhas += 1
        else:
            feitos += 1
        time.sleep(args.pause)

    log.info("-" * 60)
    log.info("gravados=%d  pulados=%d  falhas=%d  pontos=%d  em %.0fs",
             feitos, pulados, falhas, total_pts, time.time() - t_ini)
    if args.dry_run:
        log.info("(dry-run: nada foi gravado)")
    return 1 if falhas and not feitos else 0


if __name__ == "__main__":
    raise SystemExit(main())
