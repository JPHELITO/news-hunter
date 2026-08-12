"""
Market Pulse v2 — entrypoint da rodada diária. Roda no pulse_daily.yml, 2x por dia útil.

    python -m hunter.pulse_daily                 # corte deduzido da hora UTC do run
    python -m hunter.pulse_daily --cut 09        # força o corte
    python -m hunter.pulse_daily --cut 09 --dry-run   # imprime a tabela, não grava

Sequência: tira a foto do mundo -> pontua as cobertas -> grava. Se a foto falhar, NÃO
pontua: publicar um pulse com metade dos instrumentos seria pior que não publicar.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

from . import pulse_score, pulse_snapshot

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("pulse_daily")

# Abaixo disto o dia está capenga demais para publicar (feriado em cadeia, Yahoo bloqueando).
MIN_INSTRUMENTOS = 18


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cut", choices=list(pulse_snapshot.CUTS),
                    help="corte a processar (padrão: deduzido da hora UTC)")
    ap.add_argument("--dry-run", action="store_true", help="não grava nada")
    ap.add_argument("--skip-capture", action="store_true",
                    help="só repontua com o que já está no banco")
    args = ap.parse_args()

    cut = args.cut or pulse_snapshot.cut_agora()
    log.info("=== Market Pulse — corte %s (%02d:00 BRT) ===", cut, pulse_snapshot.CUTS[cut] - 3)

    if not args.skip_capture:
        try:
            precos = pulse_snapshot.capture(cut, dry_run=args.dry_run)
        except Exception as e:
            log.error("captura falhou: %s", e)
            return 1
        if len(precos) < MIN_INSTRUMENTOS:
            log.error("só %d de %d instrumentos capturados (mínimo %d) — não vou pontuar.",
                      len(precos), len(pulse_snapshot.SNAPSHOT_SYMBOLS), MIN_INSTRUMENTOS)
            return 1

    if args.dry_run and not args.skip_capture:
        log.info("dry-run: a foto não foi gravada, então a pontuação usaria dado velho. "
                 "Para testar a pontuação: --skip-capture --dry-run")
        return 0

    try:
        pulse_score.score(cut, dry_run=args.dry_run)
    except Exception as e:
        log.error("pontuação falhou: %s", e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
