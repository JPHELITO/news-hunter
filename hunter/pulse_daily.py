"""
Market Pulse v2 — entrypoint da rodada diária. Roda no pulse_daily.yml, 2x por dia útil.

    python -m hunter.pulse_daily                 # corte deduzido da hora UTC do run
    python -m hunter.pulse_daily --cut 09        # força o corte
    python -m hunter.pulse_daily --cut 09 --dry-run   # imprime a tabela, não grava

Sequência: tira a foto do mundo -> pontua as cobertas -> grava. Se a foto falhar, NÃO
pontua: publicar um pulse com metade dos instrumentos seria pior que não publicar.

O corte "18" (21:00 UTC) é a ÂNCORA do overnight — ele SÓ TIRA A FOTO, não pontua: é o
retrato do mundo no instante em que a B3 fechou, e serve de ponto de partida para a
variação overnight que os cortes das 07h e 09h medem no dia seguinte.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

from . import pulse_outcome, pulse_score, pulse_sina, pulse_snapshot

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("pulse_daily")

# Abaixo desta FRAÇÃO dos instrumentos o dia está capenga demais para publicar (feriado em
# cadeia, Yahoo bloqueando). Fração e não número fixo: a lista de instrumentos cresce, e um
# piso absoluto envelhece em silêncio — passaria a aceitar metade da foto sem ninguém notar.
MIN_FRACAO_INSTRUMENTOS = 0.75


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cut", choices=list(pulse_snapshot.CUTS),
                    help="corte a processar (padrão: deduzido da hora UTC)")
    ap.add_argument("--dry-run", action="store_true", help="não grava nada")
    ap.add_argument("--skip-capture", action="store_true",
                    help="só repontua com o que já está no banco")
    ap.add_argument("--so-placar", action="store_true",
                    help="não captura nem pontua: só fecha o placar (previsto x realizado)")
    args = ap.parse_args()

    # Fecha o placar e sai. Roda logo depois da abertura da B3, porque o preço de abertura
    # já é definitivo assim que o leilão termina — e o Yahoo o publica em minutos (conferido
    # contra a API oficial da B3 em 26/08: diferença 0,0000 nas nove cobertas). Sem isto o
    # cliente só veria o ✓/✗ à noite, quando o dia já não interessa.
    if args.so_placar:
        try:
            pulse_outcome.preencher(dry_run=args.dry_run)
            return 0
        except Exception as e:
            log.error("placar falhou: %s", e)
            return 1

    cut = args.cut or pulse_snapshot.cut_agora()
    log.info("=== Market Pulse — corte %s (%02d:00 BRT) ===", cut, pulse_snapshot.CUTS[cut] - 3)

    total = len(pulse_snapshot.SNAPSHOT_SYMBOLS)
    minimo = int(total * MIN_FRACAO_INSTRUMENTOS)

    # ⚠️ TRAVA ANTI-LOOK-AHEAD. Um corte de manhã só pode ser capturado ANTES de a B3 abrir.
    # Se o Actions atrasar a rodada (acontece), `cut_agora()` ainda devolveria '09' às 13h
    # UTC e gravaríamos, com o rótulo das 09:00, uma foto tirada DEPOIS da abertura — e essa
    # linha entraria no treino como se fosse pré-abertura, envenenando o histórico em
    # silêncio. Melhor perder a rodada do dia do que corromper a série.
    agora_utc = datetime.now(timezone.utc)
    if (cut in pulse_snapshot.CUTS_SCORE and not args.skip_capture
            and agora_utc.hour >= pulse_snapshot.B3_OPEN_UTC):
        log.error("são %02d:%02d UTC e a B3 abre às %02d:00 — a foto do corte %s seria "
                  "pós-abertura. Não vou capturar (use --skip-capture para só repontuar).",
                  agora_utc.hour, agora_utc.minute, pulse_snapshot.B3_OPEN_UTC, cut)
        return 1

    if not args.skip_capture:
        try:
            precos = pulse_snapshot.capture(cut, dry_run=args.dry_run)
        except Exception as e:
            log.error("captura falhou: %s", e)
            return 1
        if len(precos) < minimo:
            log.error("só %d de %d instrumentos capturados (mínimo %d) — não vou pontuar.",
                      len(precos), total, minimo)
            return 1

        # Minério de Cingapura e futuros da China. Ainda não entram no modelo (ver o
        # cabeçalho de pulse_sina.py) — estão acumulando histórico ao vivo. Falha aqui
        # NUNCA derruba a rodada: são dados extras, não o vetor do modelo.
        try:
            pulse_sina.capture(cut, pulse_snapshot.sessao_hoje(), dry_run=args.dry_run)
        except Exception as e:
            log.warning("coletor sina falhou (ignorado): %s", e)

    # A âncora do fechamento não pontua: ela é o ponto de partida da janela overnight
    # que os cortes da manhã seguinte vão medir. É também a hora certa de fechar o placar
    # do dia: o pregão acabou, então o preço de abertura já é definitivo.
    if cut == pulse_snapshot.CUT_BASE:
        try:
            pulse_outcome.preencher(dry_run=args.dry_run)
        except Exception as e:
            log.warning("placar do dia falhou (ignorado): %s", e)
        log.info("corte %s é a âncora do fechamento — foto tirada, sem pontuação.", cut)
        return 0

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
