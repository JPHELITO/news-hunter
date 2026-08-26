"""
repair_phantom_bars.py — reconstrói os fechamentos que o Yahoo perdeu e gravou como
"barra fantasma" (volume zero + open=high=low=close), usando a série INTRADIÁRIA do
próprio Yahoo, que continuou correta.

O INCIDENTE QUE ISTO VEIO CONSERTAR (2026-08-26, achado pelo usuário no gráfico)
--------------------------------------------------------------------------------
A série DIÁRIA da Bolsa de Santiago (SGO) morreu em 17/07/2026. De 20/07 a 25/08 —
27 pregões — o Yahoo devolveu, com HTTP 200 e sem erro nenhum, uma barra por dia
carimbando o último preço real e volume zero. CMPC, COPEC e CAP viraram linha reta.

Não era mercado parado. Medido contra a série horária e conferido no fechamento
oficial (`meta.previousClose`) de 25/08, que bateu na casa decimal nos três papéis:

    papel      linha reta      real 25/08     erro do gráfico
    CMPC.SN      1.070,0        1.036,0            +3,3%
    COPEC.SN     6.249,6        6.684,8            -6,5%   (subiu 7% "sem sair do lugar")
    CAP.SN       6.313,3        5.556,0           +13,6%   (chegou a 19% no meio)

E não se curava sozinho: a manutenção diária manda uma janela de 7 dias, e o Postgres
costura dia a dia com o ponto novo vencendo — então mesmo que o Yahoo repare o passado,
só os últimos 7 dias seriam corrigidos. Os outros 20 ficariam errados para sempre.

POR QUE A SÉRIE INTRADIÁRIA SERVE DE FONTE
------------------------------------------
São encanamentos diferentes dentro do mesmo Yahoo: o diário quebrou, o intradiário não.
Aferido nos 33 pregões REAIS da janela (o script refaz esta conta antes de gravar, e
recusa o papel se não bater):

    CMPC.SN   33/33 idênticos ao fechamento oficial
    COPEC.SN  32/33 idênticos · erro médio 0,005% · pior 0,16%
    CAP.SN    24/33 idênticos · erro médio 0,065% · pior 0,47%

O resíduo é o LEILÃO DE FECHAMENTO, que não entra no intradiário. Granularidade mais
fina não ajuda: 1h, 30m, 15m e 5m dão exatamente o mesmo número, porque o último candle
do dia é o mesmo negócio em todos. Trocar um erro de 0,07% por um de 3% a 19% é o negócio.

O QUE ELE FAZ, NESTA ORDEM
--------------------------
1. Acha as barras fantasma na série diária CRUA do Yahoo (não na nossa, que agora já
   filtra — ver hunter/quote_history.eh_fantasma).
2. AFERE a fonte: reconstrói os pregões REAIS pelo intradiário e compara com o
   fechamento oficial. Se o erro médio ou o pior caso estourar o limiar, ABORTA aquele
   papel — reconstrução mal-encaixada (fuso errado, sessão cruzando meia-noite) grita aqui.
3. Reconstrói cada dia fantasma pelo ÚLTIMO ponto intradiário dentro da janela
   [epoch da barra, +24h). Casar pela janela da própria barra, e não pela data UTC,
   é o que mantém isto correto em bolsa do outro lado do fuso (ASX cruza meia-noite).
4. Dia fantasma SEM ponto intradiário é REMOVIDO da série — não inventado. Buraco é
   honesto; carimbo é mentira. (Por isso a gravação é `replace`: append não apaga.)
5. Grava com qh.save(), que passa pelo mesmo RPC de sempre — a série leve de
   quotes.daily (a que a home baixa) é regravada como redução da corrigida, no mesmo
   passo, então as duas não divergem.

Uso:
    python scripts/repair_phantom_bars.py --dry-run                  # mede e mostra, não grava
    python scripts/repair_phantom_bars.py --only CMPC.SN,COPEC.SN,CAP.SN
    python scripts/repair_phantom_bars.py                            # varre os 48
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
log = logging.getLogger("repair_phantom_bars")

DIA = 86400

# Limiares da AFERIÇÃO (passo 2). Não são filtro de precisão — são detector de
# reconstrução mal-encaixada. Um mapeamento errado de sessão erra vários por cento na
# média; o leilão de fechamento erra centésimos. Medido: pior caso real = 0,47% (CAP).
AFERICAO_MIN_N = 10      # pregões reais mínimos para confiar na medida
ERRO_MEDIO_MAX = 0.50    # %
ERRO_PIOR_MAX = 5.00     # %


# ─────────────────────────── leitura crua do Yahoo ───────────────────────────
def _colunas(res: dict) -> tuple[list, dict]:
    """`result` do Yahoo -> (timestamps, {campo: coluna}) com colunas do mesmo tamanho."""
    ts = res.get("timestamp") or []
    q = ((res.get("indicators", {}) or {}).get("quote") or [{}])[0]
    col = {k: (q.get(k) or [None] * len(ts))
           for k in ("open", "high", "low", "close", "volume")}
    return ts, col


def separar_barras(res: dict | None) -> tuple[list[list], list[list]]:
    """Série diária crua -> (fantasmas, reais), cada uma [[epoch, close], ...]."""
    if not res:
        return [], []
    ts, col = _colunas(res)
    fantasmas, reais = [], []
    for i, t in enumerate(ts):
        c = col["close"][i]
        if c is None:
            continue
        alvo = fantasmas if qh.eh_fantasma(col["open"][i], col["high"][i],
                                           col["low"][i], c, col["volume"][i]) else reais
        alvo.append([int(t), round(float(c), 4)])
    return fantasmas, reais


def fechamento_da_sessao(intra: list[list], epoch_barra: int) -> float | None:
    """Último ponto intradiário dentro de [epoch da barra, +24h) — a sessão daquela barra.

    Casa pela JANELA DA BARRA, nunca pela data UTC: em bolsa a leste (ASX) a sessão cruza
    a meia-noite UTC e agrupar por data partiria o pregão em dois."""
    dentro = [c for t, c in intra if epoch_barra <= t < epoch_barra + DIA]
    return dentro[-1] if dentro else None


def aferir(reais: list[list], intra: list[list]) -> dict:
    """Reconstrói os pregões REAIS pelo intradiário e mede contra o fechamento oficial."""
    erros = []
    identicos = 0
    for t, oficial in reais:
        recon = fechamento_da_sessao(intra, t)
        if recon is None or not oficial:
            continue
        e = abs(recon / oficial - 1) * 100
        erros.append(e)
        if e < 1e-9:
            identicos += 1
    return {"n": len(erros), "identicos": identicos,
            "medio": (sum(erros) / len(erros)) if erros else 0.0,
            "pior": max(erros) if erros else 0.0}


def _d(ts) -> str:
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")


# ──────────────────────────────── o conserto ─────────────────────────────────
def reconstruir(guardada: list[list], fantasmas: list[list],
                intra: list[list]) -> tuple[list[list], list[list], list[list]]:
    """Série guardada + dias fantasma + intradiário -> (corrigida, trocados, removidos).

    `trocados`  = [[epoch, valor_velho, valor_novo], ...]
    `removidos` = [[epoch, valor_velho], ...]  (fantasma sem fonte: sai da série)
    """
    por_dia = {int(t) // DIA: [int(t), v] for t, v in (guardada or [])}
    dias = sorted(por_dia) if por_dia else []
    span = (dias[0], dias[-1]) if dias else None
    trocados, removidos = [], []
    for t, _velho_yahoo in fantasmas:
        d = int(t) // DIA
        if d not in por_dia:
            # Dia do incidente que nem chegou a entrar na nossa série (medido: a CMPC
            # tinha 2 buracos assim). Vale preencher — mas SÓ dentro do intervalo que já
            # cobrimos: fora dele estaríamos esticando a série para um período que este
            # papel nunca teve, o que é inventar histórico, não consertar.
            if not span or not (span[0] <= d <= span[1]):
                continue
            novo_pt = fechamento_da_sessao(intra, int(t))
            if novo_pt is None:
                continue                   # sem fonte: buraco é honesto, não se preenche
            por_dia[d] = [int(t), round(float(novo_pt), 4)]
            trocados.append([int(t), None, round(float(novo_pt), 4)])
            continue
        velho = por_dia[d][1]
        novo = fechamento_da_sessao(intra, int(t))
        if novo is None:
            removidos.append([por_dia[d][0], velho])
            del por_dia[d]
            continue
        novo = round(float(novo), 4)
        if novo != velho:
            trocados.append([por_dia[d][0], velho, novo])
        por_dia[d] = [por_dia[d][0], novo]
    return [por_dia[k] for k in sorted(por_dia)], trocados, removidos


def tratar(ticker: str, qsym: str, args) -> str:
    """Devolve um veredicto curto: 'limpo' | 'consertado' | 'abortado' | 'erro'."""
    diario = qh._chart(qsym, {"range": "2y", "interval": "1d"})
    if not diario:
        log.warning("%-12s serie diaria nao veio - pulando", ticker)
        return "erro"
    fantasmas, reais = separar_barras(diario)
    if not fantasmas:
        log.info("%-12s limpo (0 barras fantasma em %d pregoes)", ticker, len(reais))
        return "limpo"

    log.warning("%-12s %d BARRA(S) FANTASMA: %s -> %s",
                ticker, len(fantasmas), _d(fantasmas[0][0]), _d(fantasmas[-1][0]))

    intra_res = qh._chart(qsym, {"range": "730d", "interval": "1h"}, timeout=60)
    _ts, _col = _colunas(intra_res or {})
    intra = [[int(t), round(float(c), 4)]
             for t, c in zip(_ts, _col["close"]) if c is not None]
    if not intra:
        log.error("%-12s ABORTADO - sem serie intradiaria para reconstruir", ticker)
        return "abortado"

    # ── trava: a fonte reproduz os pregões que NÃO quebraram? ──
    janela = fantasmas[0][0] - 90 * DIA
    af = aferir([b for b in reais if b[0] >= janela], intra)
    log.info("%-12s afericao: %d pregoes reais - %d identicos - medio %.4f%% - pior %.3f%%",
             ticker, af["n"], af["identicos"], af["medio"], af["pior"])
    if af["n"] < AFERICAO_MIN_N:
        log.error("%-12s ABORTADO - so %d pregoes afericoes (minimo %d)",
                  ticker, af["n"], AFERICAO_MIN_N)
        return "abortado"
    if af["medio"] > ERRO_MEDIO_MAX or af["pior"] > ERRO_PIOR_MAX:
        log.error("%-12s ABORTADO - reconstrucao nao bate (medio %.3f%% > %.2f%% ou "
                  "pior %.3f%% > %.2f%%). Encaixe de sessao suspeito; nada foi gravado.",
                  ticker, af["medio"], ERRO_MEDIO_MAX, af["pior"], ERRO_PIOR_MAX)
        return "abortado"

    guardada = qh.load_series(ticker)
    if not guardada:
        log.error("%-12s ABORTADO - nada guardado no banco (rodar o backfill antes)", ticker)
        return "abortado"

    corrigida, trocados, removidos = reconstruir(guardada, fantasmas, intra)
    if not trocados and not removidos:
        log.info("%-12s nada a fazer - as fantasmas do Yahoo ja estao corretas (ou ausentes) "
                 "na nossa serie", ticker)
        return "limpo"

    print("\n-- %s --  %d fechamento(s) corrigido(s), %d removido(s)"
          % (ticker, len(trocados), len(removidos)))
    print("   %-12s%12s%12s%18s" % ("data", "gravado", "real", "erro do grafico"))
    for t, velho, novo in trocados:
        if velho is None:
            print("   %-12s%12s%12.2f%18s" % (_d(t), "(faltava)", novo, "dia inserido"))
        else:
            print("   %-12s%12.2f%12.2f%17.2f%%" % (_d(t), velho, novo, (velho / novo - 1) * 100))
    for t, velho in removidos:
        print("   %-12s%12.2f%12s%18s" % (_d(t), velho, "REMOVIDO", "sem fonte"))

    if args.dry_run:
        log.info("%-12s --dry-run: nada gravado", ticker)
        return "consertado"

    if qh.save(ticker, corrigida):
        log.info("%-12s GRAVADO - %d pontos na serie (era %d)",
                 ticker, len(corrigida), len(guardada))
        return "consertado"
    log.error("%-12s falhou ao gravar", ticker)
    return "erro"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="mede e mostra, sem gravar")
    ap.add_argument("--only", default="", help="tickers separados por virgula")
    ap.add_argument("--pause", type=float, default=0.8, help="respiro entre papeis")
    args = ap.parse_args()

    only = {t.strip() for t in args.only.split(",") if t.strip()}
    alvo = [(tk, qsym) for tk, _n, _s, _e, _p, qsym in QUOTES_LIST if not only or tk in only]
    if not alvo:
        log.error("nenhum ticker casou com --only=%s", args.only)
        return 2

    placar = {"limpo": 0, "consertado": 0, "abortado": 0, "erro": 0}
    for i, (tk, qsym) in enumerate(alvo):
        try:
            placar[tratar(tk, qsym, args)] += 1
        except Exception as e:
            log.exception("%-12s excecao: %s", tk, e)
            placar["erro"] += 1
        if i < len(alvo) - 1:
            time.sleep(args.pause)

    print("\n" + "=" * 60)
    print("%d papel(is): %d limpo(s) - %d consertado(s) - %d abortado(s) - %d com erro%s"
          % (len(alvo), placar["limpo"], placar["consertado"], placar["abortado"],
             placar["erro"], "   [--dry-run: NADA foi gravado]" if args.dry_run else ""))
    return 1 if (placar["abortado"] or placar["erro"]) else 0


if __name__ == "__main__":
    sys.exit(main())
