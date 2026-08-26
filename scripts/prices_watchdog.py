"""Vigia do HISTÓRICO DE PREÇOS (nasceu do apagão da Bolsa de Santiago, 2026-08-26).

Por que existe: a série diária da SGO morreu em 17/07/2026 e o Yahoo passou 27 pregões
devolvendo uma barra por dia com `open=high=low=close` e volume zero — HTTP 200, série
completa, nenhum erro em log nenhum. CMPC, COPEC e CAP viraram linha reta enquanto a CAP
caía 16% e a COPEC subia 7% de verdade; o fechamento gravado chegou a ficar 19% errado.
O problema só apareceu 40 dias depois, e quem viu foi o ANALISTA, no olho, no gráfico.
Não havia número nenhum vigiando isso — exatamente como no incidente dos takes.

Duas perguntas, ambas somente-leitura (zero escrita, zero IA):

  1) A FONTE está viva?   barras fantasma consecutivas na série diária do Yahoo.
     -> teria gritado em 22/07, no 3º pregão, em vez de no 40º dia.
     ⚠️ O alarme é sobre O NOSSO DADO, não sobre a perfeição do Yahoo: corrida confirmada
     cujo estrago JÁ foi consertado no banco entra no relatório e NÃO alarma — senão o
     e-mail se repetiria todo dia enquanto o Yahoo não reescrevesse o passado dele (e ele
     pode nunca reescrever).
  2) A NOSSA série anda?  `quote_history.last_ts` velho demais por papel.
     -> pega o caso oposto: o Yahoo bem e o nosso ingestor parado.

FEED MORTO x PAPEL PARADO — a diferença que evita alarme falso
--------------------------------------------------------------
As duas coisas produzem a MESMA barra (volume 0, OHLC iguais), e confundi-las faria o vigia
chorar lobo toda vez que uma small cap entrasse em leilão. O que separa é a série
INTRADIÁRIA daquelas mesmas sessões — medido nos dois casos reais do universo:

    CMPC/COPEC/CAP  13-25/08  diário morto, intradiário com 7 candles/dia e volume cheio
                              -> FEED MORTO. Houve pregão; o preço existe e foi perdido.
    VMM.AX          13-19/08  diário morto, intradiário com ZERO candles
                              -> PAPEL PARADO (halt na ASX). Não houve negócio nenhum;
                                 não existe fechamento a recuperar, e não há o que consertar.

Só o primeiro caso alarma. O segundo entra no relatório como informação.

Padrão de alarme igual ao scripts/watchdog.py e ao takes_watchdog.py: **exit 1 faz o job
do Actions FALHAR e o GitHub manda e-mail ao dono do repo** — sem SMTP, e SEM e-mail
quando está tudo bem (anti-spam). O relatório completo sai sempre no log do job.

Conserto, quando alarmar: `python scripts/repair_phantom_bars.py --dry-run` mostra o
estrago e `sem --dry-run` reconstrói os fechamentos pela série intradiária.

Uso:  python -m scripts.prices_watchdog [--dias 45] [--only CMPC.SN,CAP.SN]
"""
from __future__ import annotations

import argparse
import os
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

from hunter import quote_history as qh
from hunter.prices import QUOTES_LIST
# A regra de reconstrução mora no script de conserto — o vigia IMPORTA em vez de copiar,
# senão os dois divergem e o vigia passa a acusar (ou perdoar) o que o conserto não faria.
from scripts import repair_phantom_bars as rp

# ── Limiares (ajustáveis por env, p/ calibrar sem mexer em código) ─────────────
# 3 pregões seguidos: no apagão da SGO o padrão já estava formado no 3º dia, e dia sem
# negócio em small cap ilíquida nunca chegou a 3 seguidos (medido em 6 meses de MEI.AX
# e VMM.AX, os dois únicos papéis do universo com fantasma legítima).
RUN_MAX = int(os.environ.get("PRICES_FANTASMA_RUN_MAX", "3"))
DIAS_JANELA = int(os.environ.get("PRICES_JANELA_DIAS", "45"))
# Série nossa parada. 5 dias corridos cobrem fim de semana + 1 feriado sem falso alarme.
SERIE_PARADA_DIAS = int(os.environ.get("PRICES_SERIE_PARADA_DIAS", "5"))
PAUSA_S = float(os.environ.get("PRICES_PAUSA_S", "0.35"))


def _d(ts) -> str:
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")


def corridas_fantasma(res: dict | None) -> list[tuple[int, int, int]]:
    """Série diária crua -> [(epoch_inicio, epoch_fim, n), ...] das corridas de fantasma.

    Só corridas de 2 ou mais entram na lista; quem julga o tamanho é quem chama."""
    if not res:
        return []
    ts = res.get("timestamp") or []
    q = ((res.get("indicators", {}) or {}).get("quote") or [{}])[0]
    col = {k: (q.get(k) or [None] * len(ts))
           for k in ("open", "high", "low", "close", "volume")}
    marcas = []
    for i, t in enumerate(ts):
        c = col["close"][i]
        if c is None:
            continue
        marcas.append((int(t), qh.eh_fantasma(col["open"][i], col["high"][i],
                                              col["low"][i], c, col["volume"][i])))
    corridas, ini = [], None
    for i, (t, fantasma) in enumerate(marcas):
        if fantasma and ini is None:
            ini = i
        elif not fantasma and ini is not None:
            corridas.append((marcas[ini][0], marcas[i - 1][0], i - ini))
            ini = None
    if ini is not None:
        corridas.append((marcas[ini][0], marcas[-1][0], len(marcas) - ini))
    return [c for c in corridas if c[2] >= 2]


def intradiario(qsym: str, ini: int, fim: int) -> tuple[int, list[list]]:
    """(candles COM NEGÓCIO entre as duas sessões, série intradiária inteira).

    Serve a duas perguntas de uma vez, com UMA requisição: o contador é o desempate entre
    feed morto e papel parado (ver docstring do módulo), e a série é o que permite saber o
    fechamento REAL de cada dia carimbado. Só é chamada para corrida já suspeita — custa
    uma requisição por incidente, não por papel.
    """
    res = qh._chart(qsym, {"range": "730d", "interval": "1h"}, timeout=60)
    if not res:
        return 0, []
    ts = res.get("timestamp") or []
    q = ((res.get("indicators", {}) or {}).get("quote") or [{}])[0]
    closes = q.get("close") or []
    vols = q.get("volume") or [None] * len(ts)
    serie = [[int(t), round(float(closes[i]), 4)] for i, t in enumerate(ts)
             if i < len(closes) and closes[i] is not None]
    negocios = sum(1 for i, t in enumerate(ts)
                   if ini <= t < fim + 86400 and i < len(closes)
                   and closes[i] is not None and (vols[i] or 0) > 0)
    return negocios, serie


def pontos_fantasma(res: dict | None, ini: int, fim: int) -> dict[int, float]:
    """{dia_epoch//86400: close} das barras fantasma daquela corrida (série CRUA do Yahoo)."""
    if not res:
        return {}
    ts = res.get("timestamp") or []
    q = ((res.get("indicators", {}) or {}).get("quote") or [{}])[0]
    col = {k: (q.get(k) or [None] * len(ts))
           for k in ("open", "high", "low", "close", "volume")}
    out = {}
    for i, t in enumerate(ts):
        c = col["close"][i]
        if c is None or not (ini <= t <= fim):
            continue
        if qh.eh_fantasma(col["open"][i], col["high"][i], col["low"][i], c, col["volume"][i]):
            out[int(t) // 86400] = round(float(c), 4)
    return out


def nao_reparados(ticker: str, fantasmas: dict[int, float], intra: list[list]) -> int:
    """Quantos daqueles dias ainda estão ERRADOS na nossa série guardada.

    ⚠️ Compara com o fechamento RECONSTRUÍDO pelo intradiário, **não** com o valor
    carimbado — às vezes o carimbo calha de ser o preço certo (medido: a CMPC em 22/07
    fechou exatamente nos 1.070 que o feed vinha repetindo). Comparar com o carimbo
    marcaria esse dia como estragado para sempre e o vigia mandaria e-mail todo dia sobre
    um número correto. Usa a MESMA regra do conserto (`fechamento_da_sessao`), então o que
    o vigia chama de errado é exatamente o que o conserto mudaria.

    Dia sem fonte intradiária, ou ausente da nossa série, não conta como estrago.
    """
    if not fantasmas or not intra:
        return 0
    nossa = {int(t) // 86400: (int(t), v) for t, v in (qh.load_series(ticker) or [])}
    sujos = 0
    for d in fantasmas:
        if d not in nossa:
            continue
        epoch, guardado = nossa[d]
        real = rp.fechamento_da_sessao(intra, epoch)
        if real is not None and abs(guardado - round(float(real), 4)) > 1e-6:
            sujos += 1
    return sujos


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dias", type=int, default=DIAS_JANELA, help="janela olhada no Yahoo")
    ap.add_argument("--only", default="", help="tickers separados por virgula")
    args = ap.parse_args()

    only = {t.strip() for t in args.only.split(",") if t.strip()}
    alvo = [(tk, qsym) for tk, _n, _s, _e, _p, qsym in QUOTES_LIST if not only or tk in only]
    problemas: list[str] = []

    # ── 1) A fonte está viva? ────────────────────────────────────────────────
    print("1) Barras fantasma na fonte (Yahoo) — janela de %d dias, alarme a partir de "
          "%d pregoes seguidos" % (args.dias, RUN_MAX))
    sem_resposta = []
    for i, (tk, qsym) in enumerate(alvo):
        res = qh._chart(qsym, {"range": "%dd" % args.dias, "interval": "1d"}, timeout=20)
        if not res:
            sem_resposta.append(tk)
        else:
            for ini, fim, n in corridas_fantasma(res):
                if n < RUN_MAX:
                    print("   %-13s %s -> %s  %2d pregao(oes)  [ok — curto demais p/ julgar]"
                          % (tk, _d(ini), _d(fim), n))
                    continue
                # corrida longa: o intradiario diz se houve pregao de verdade
                negocios, intra = intradiario(qsym, ini, fim)
                if negocios == 0:
                    print("   %-13s %s -> %s  %2d pregao(oes)  [papel PARADO — intradiario "
                          "vazio tambem; nao ha fechamento a recuperar]"
                          % (tk, _d(ini), _d(fim), n))
                    continue
                # o estrago ainda esta na NOSSA serie, ou ja consertamos?
                sujos = nao_reparados(tk, pontos_fantasma(res, ini, fim), intra)
                if sujos == 0:
                    print("   %-13s %s -> %s  %2d pregao(oes)  [fonte torta, mas a NOSSA "
                          "serie ja esta consertada]" % (tk, _d(ini), _d(fim), n))
                    continue
                print("   %-13s %s -> %s  %2d pregao(oes)  [ALARME — %d candles "
                      "intradiarios (houve pregao) e %d dia(s) ainda errado(s) no banco]"
                      % (tk, _d(ini), _d(fim), n, negocios, sujos))
                problemas.append(
                    "%s: %d pregoes seguidos de barra fantasma (%s -> %s) MAS o "
                    "intradiario mostra %d candles com negocio — o feed diario parou e o "
                    "grafico virou linha reta sobre um mercado que andou. %d dia(s) ainda "
                    "com o valor carimbado no nosso banco. Conserto: "
                    "python scripts/repair_phantom_bars.py --dry-run --only %s"
                    % (tk, n, _d(ini), _d(fim), negocios, sujos, tk))
        if i < len(alvo) - 1:
            time.sleep(PAUSA_S)
    if sem_resposta:
        print("   (sem resposta do Yahoo: %s)" % ", ".join(sem_resposta))
        if len(sem_resposta) > len(alvo) // 2:
            problemas.append("o Yahoo nao respondeu para %d de %d papeis — bloqueio de IP "
                             "ou API fora." % (len(sem_resposta), len(alvo)))

    # ── 2) A nossa série anda? ───────────────────────────────────────────────
    print("\n2) Nossa serie — ultimo fechamento gravado por papel (alarme com mais de %d "
          "dias)" % SERIE_PARADA_DIAS)
    estado = qh.load_state()
    if estado is None:
        print("   tabela quote_history indisponivel — pulando")
    else:
        corte = datetime.now(timezone.utc) - timedelta(days=SERIE_PARADA_DIAS)
        paradas = []
        for tk, _qsym in alvo:
            st = estado.get(tk)
            if not st:
                paradas.append((tk, "sem serie nenhuma"))
                continue
            last = st.get("last_ts")
            if last and datetime.fromtimestamp(int(last), tz=timezone.utc) < corte:
                paradas.append((tk, "parada em %s" % _d(last)))
        for tk, motivo in paradas:
            print("   %-13s %s" % (tk, motivo))
        if not paradas:
            print("   todas em dia.")
        elif len(paradas) > len(alvo) // 2:
            problemas.append("%d de %d series paradas — o ingestor (update_quote_history) "
                             "provavelmente morreu." % (len(paradas), len(alvo)))
        else:
            for tk, motivo in paradas:
                problemas.append("%s: serie %s." % (tk, motivo))

    # ── Veredicto ────────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    if problemas:
        print("%d PROBLEMA(S):" % len(problemas))
        for p in problemas:
            print("   - %s" % p)
        return 1                    # exit 1 -> job falha -> GitHub manda e-mail
    print("historico de precos saudavel (%d papeis conferidos)." % len(alvo))
    return 0


if __name__ == "__main__":
    sys.exit(main())
