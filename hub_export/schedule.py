"""Cadência de cada arquivo (PLANO §2.5) e a pulsação do `live.json`.

Um arquivo está "na vez" quando `agora − último build ≥ cadência` (HOT = todo ciclo do loop).
A pulsação é outra coisa: o `live.json` vai mesmo SEM mudança quando o último envio tem 15 min
ou mais — o selo "aberta/fechada" do heatmap considera o preço velho depois de 30 min sem
`updatedAt` novo, e num pregão parado (ou fim de semana) o hash não muda nunca.

Folga de 60 s nas duas contas: o loop bate a cada ~300 s, mas o fim do hunt.py (onde o
exportador roda) escorrega alguns segundos de um ciclo para o outro. Sem folga, um WARM de
900 s virava 20 min sempre que o terceiro ciclo chegasse 1 s adiantado — e a pulsação de
15 min, somada aos 5–7 min de publicação, encostaria nos 30 min do selo.
"""
from __future__ import annotations

from datetime import datetime, timezone

from .encode import iso_utc

HOT = 0           # todo ciclo (~5 min)
WARM = 900        # 15 min
COLD = 3600       # 60 min
DAILY = 86400     # 1×/dia
PULSE_LIVE = 900  # o live.json vai ao menos a cada 15 min
SLACK_S = 60      # tolerância do relógio do loop (ver docstring)


def _age_s(stamp, now: datetime) -> float | None:
    iso = iso_utc(stamp)
    if iso is None:
        return None
    then = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return (now - then).total_seconds()


def is_due(cadence: int, built_at, now: datetime) -> bool:
    """Na vez? Sem build anterior (ou carimbo ilegível) = sim."""
    if cadence <= 0:
        return True
    age = _age_s(built_at, now)
    return age is None or age >= cadence - SLACK_S


def pulse_due(pulse: int | None, sent_at, now: datetime) -> bool:
    """A pulsação manda reenviar? Só para quem tem pulsação; sem envio conhecido = sim."""
    if not pulse:
        return False
    age = _age_s(sent_at, now)
    return age is None or age >= pulse - SLACK_S
