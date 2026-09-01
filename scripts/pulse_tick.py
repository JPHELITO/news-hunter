"""
pulse_tick.py — "está na hora de fotografar o mundo, e ninguém fotografou?"

POR QUE ISTO EXISTE (2026-09-01). O Market Pulse dependia só do `schedule:` do GitHub
Actions, e entre 27/08 e 01/09 o GitHub passou a soltar os crons em horas arbitrárias:
disparos às 00:16, 02:55, 04:32 e 17:14 UTC no lugar das 09:50 e 11:50 combinadas. As
consequências foram duas, e a segunda é a grave:

  - os cortes da manhã do dia 01/09 simplesmente não aconteceram (as duas rodadas que
    dispararam vieram DEPOIS da abertura da B3 e foram barradas, corretamente);
  - as que dispararam de madrugada passaram pela trava antiga (que só olhava o lado
    tarde) e gravaram foto da noite anterior com o rótulo do corte das 07h — inclusive
    SOBRESCREVENDO fotos boas, porque a gravação é upsert por (sessão, corte, símbolo).

O `hunt-loop` já resolveu esse mesmo problema para a busca de notícias: em vez de confiar
no cron, ele roda em CORRENTE contínua e se redispara. Este script pega carona nessa
batida de 5 minutos, que é a coisa mais confiável que temos rodando, e responde uma
pergunta por vez: existe algum corte cuja janela está ABERTA agora e cuja foto ainda não
está no banco? Se existir, imprime o corte no stdout — e quem chama dispara o
`pulse_daily.yml` com aquele corte.

Desenho de propósito:
  - **só imprime, não executa.** O pulse continua rodando no workflow dele, com a
    concurrency dele. Rodar por dentro do hunt-loop poria a foto atrás de uma corrente
    de 5h55 — exatamente o que o cabeçalho do pulse_daily.yml manda evitar.
  - **pergunta ao banco, não a um relógio interno.** Se a foto já existe, não dispara.
    Isso torna o tick idempotente e faz dele um mecanismo de RECUPERAÇÃO: uma janela
    perdida por 20 minutos ainda é recuperada na batida seguinte, dentro da janela.
  - **um corte por vez**, o mais urgente, para nunca disparar duas rodadas juntas.
  - **stdout é só o corte**; todo o resto vai para stderr, para o shell poder capturar.

Uso:
    python -m scripts.pulse_tick          # imprime "07", "09", "18" ou nada
"""
from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

from hunter import pulse_snapshot                                    # noqa: E402

logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                    format="%(asctime)s %(levelname)s pulse_tick: %(message)s")
log = logging.getLogger("pulse_tick")


def _ja_fotografado(session: str, cut: str) -> bool | None:
    """
    Existe foto BOA de (sessão, corte)? None = não consegui perguntar.

    ⚠️ Não basta perguntar se existe LINHA. Em 01/09/2026 um run atrasado gravou a âncora
    do fechamento às 17:21 UTC, com a B3 aberta havia quatro horas: a linha existe e está
    errada. Um tick que só olhasse a existência daria a sessão por resolvida e a âncora
    ruim atravessaria a noite — justamente a foto de que a manhã seguinte depende.
    Então uma foto fora da janela conta como AUSENTE: o tick redispara e a captura nova
    a sobrescreve pelo upsert. É o que torna o conserto automático em vez de manual.
    """
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    if not (url and key):
        log.warning("SUPABASE_URL / SUPABASE_SERVICE_KEY ausentes — não sei responder.")
        return None
    try:
        r = requests.get(
            f"{url}/rest/v1/pulse_snapshot?select=captured_at"
            f"&session_date=eq.{quote(session)}&cut=eq.{quote(cut)}"
            f"&order=captured_at.asc&limit=1",
            headers={"apikey": key, "Authorization": f"Bearer {key}"}, timeout=20)
        r.raise_for_status()
        linhas = r.json()
        if not linhas:
            return False
        quando = datetime.fromisoformat(linhas[0]["captured_at"].replace("Z", "+00:00"))
        motivo = pulse_snapshot.fora_da_janela(cut, quando)
        if motivo:
            log.warning("a foto do corte %s da sessão %s foi tirada %s UTC, FORA DA "
                        "JANELA — vou tratá-la como ausente para que seja refeita (%s).",
                        cut, session, f"{quando:%H:%M}", motivo)
            return False
        return True
    except Exception as e:
        log.warning("consulta ao banco falhou (%s) — não vou disparar no escuro.", e)
        return None


def corte_pendente(agora: datetime | None = None) -> str | None:
    """O corte que deveria estar fotografado agora e não está. None = nada a fazer."""
    agora = agora or datetime.now(timezone.utc)
    session = pulse_snapshot.sessao_hoje()

    # Fim de semana não tem pregão. A data que importa é a da SESSÃO (UTC−3), não a UTC:
    # às 00:30 de segunda em UTC ainda é domingo no Brasil.
    if datetime.fromisoformat(session).weekday() >= 5:
        log.info("sessão %s é fim de semana — nada a fazer.", session)
        return None

    # Do mais tardio para o mais cedo: se duas janelas estiverem abertas (não acontece
    # hoje, mas a lista de cortes pode mudar), a mais recente é a que interessa.
    for cut in sorted(pulse_snapshot.CUTS, key=lambda c: -pulse_snapshot.CUTS[c]):
        if pulse_snapshot.fora_da_janela(cut, agora):
            continue
        ja = _ja_fotografado(session, cut)
        if ja is None:            # banco fora do ar: silêncio é melhor que disparo cego
            return None
        if ja:
            log.info("corte %s da sessão %s já está fotografado.", cut, session)
            return None
        inicio, fim = pulse_snapshot.janela(cut, agora)
        log.info("corte %s da sessão %s SEM foto e a janela está aberta (%s–%s UTC).",
                 cut, session, f"{inicio:%H:%M}", f"{fim:%H:%M}")
        return cut
    log.info("nenhuma janela de corte aberta às %s UTC.", f"{agora:%H:%M}")
    return None


def main() -> int:
    cut = corte_pendente()
    if cut:
        print(cut)                # stdout = contrato com o shell que dispara o workflow
    return 0


if __name__ == "__main__":
    sys.exit(main())
