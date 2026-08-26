"""
pulse_check.py — o Market Pulse está acertando AO VIVO?

Compara o que foi publicado em `pulse_daily` com o gap de abertura que de fato aconteceu.
É a régua da Fase 3: só ligar a flag para o cliente depois que os números ao vivo
confirmarem o que o holdout prometeu.

REFERÊNCIA (holdout de 205 pregões, corte 09):
    IC 0,264 · acerto direcional 58,6% · spread Q5−Q1 de 1,02 pp/dia

CRITÉRIO DE LIBERAÇÃO: IC agregado > 0,15 e acerto > 55%.
Se ficar abaixo, o suspeito nº1 é a CAPTURA (horário, feriado, símbolo faltando),
não o modelo — confira `pulse_snapshot` antes de mexer em qualquer peso.

⚠️ QUANTOS PREGÕES ANTES DE ACREDITAR NO NÚMERO. Com n pregões, o desvio-padrão do IC
sob a hipótese "não há sinal nenhum" é ~1/raiz(n): com 10 pregões isso dá ±0,33, ou seja,
um IC de −0,34 sai do puro acaso uma vez a cada seis janelas. Para afirmar que um modelo
quebrou são precisos ~35 pregões (n > (1,96/IC)²). Abaixo disso o resultado é
direcionalmente informativo e nada mais — não desligue empresa nem re-treine por causa
de uma quinzena ruim.

Uso:
    python scripts/pulse_check.py --dias 20
    python scripts/pulse_check.py --dias 60 --cut 09
"""
from __future__ import annotations

import argparse
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

from hunter.prices import HEADERS, _YAHOO_HOSTS        # noqa: E402
from hunter.pulse_score import _supa_get               # noqa: E402
from hunter.pulse_snapshot import COMPANIES            # noqa: E402


def _spearman(xs: list[float], ys: list[float]) -> float:
    """Spearman sem numpy: Pearson sobre os postos (com empates médios)."""
    n = len(xs)
    if n < 3:
        return float("nan")

    def postos(v):
        ordem = sorted(range(n), key=lambda i: v[i])
        r = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and v[ordem[j + 1]] == v[ordem[i]]:
                j += 1
            media = (i + j) / 2.0 + 1
            for k in range(i, j + 1):
                r[ordem[k]] = media
            i = j + 1
        return r

    rx, ry = postos(xs), postos(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = sum((a - mx) ** 2 for a in rx) ** 0.5
    dy = sum((b - my) ** 2 for b in ry) ** 0.5
    return num / (dx * dy) if dx and dy else float("nan")


def gaps_reais(desde: str) -> dict[tuple[str, str], tuple[float, bool]]:
    """
    {(empresa, data): (gap de abertura realizado em %, houve negócio no leilão?)}.

    ⚠️ ABERTURA CARIMBADA NÃO É ERRO DE PREVISÃO. Quando o preço de abertura sai
    EXATAMENTE igual ao fechamento anterior, o leilão não formou preço — não existe gap
    para acertar, e contar esses dias como erro rebaixa o placar de papéis ilíquidos sem
    dizer nada sobre o modelo. Medido em 3 meses (2026-08): RANI3 27,7% dos pregões,
    CMIN3 e KLBN11 16,9%, USIM5 7,7%. Eles saem do acerto e do IC, e são reportados à parte.
    """
    out = {}
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
        res = js["chart"]["result"][0]
        q = res["indicators"]["quote"][0]
        adjs = (res["indicators"].get("adjclose") or [{}])[0].get("adjclose") or q.get("close")
        off = res["meta"].get("gmtoffset", -10800)
        prev = prev_close = None
        for i, ts in enumerate(res["timestamp"]):
            o, c, a = q["open"][i], q["close"][i], adjs[i]
            d = datetime.fromtimestamp(ts + off, timezone.utc).date().isoformat()
            if None not in (o, c, a):
                if prev and d >= desde:
                    fac = a / c
                    g = (o * fac) / prev - 1
                    # o carimbo se detecta no preço CRU: no ajustado, um dia de provento
                    # daria diferente de zero e o filtro passaria batido
                    negociou = abs(o / prev_close - 1) > 1e-9 if prev_close else True
                    if abs(g) < 0.40:
                        out[(sym, d)] = (100 * g, negociou)
                prev, prev_close = a, c
        time.sleep(0.3)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dias", type=int, default=20, help="quantos pregões publicados analisar")
    ap.add_argument("--cut", default=None, help="'07' ou '09' (padrão: os dois, em separado)")
    args = ap.parse_args()

    pub = _supa_get("pulse_daily?status=eq.ok&select=session_date,cut,company,score,"
                    "gap_expected,confidence&order=session_date.desc&limit=5000")
    if not pub:
        print("pulse_daily vazio — nada publicado ainda.")
        return 1
    datas = sorted({r["session_date"] for r in pub}, reverse=True)[:args.dias]
    if not datas:
        return 1
    pub = [r for r in pub if r["session_date"] in datas]
    if args.cut:
        pub = [r for r in pub if r["cut"] == args.cut]

    print(f"janela: {min(datas)} a {max(datas)}  ({len(datas)} pregões publicados)")
    print("buscando os gaps realizados no Yahoo ...")
    reais = gaps_reais(min(datas))

    linhas, empates = [], 0
    for r in pub:
        real = reais.get((r["company"], r["session_date"]))
        if real is None or r.get("score") is None:
            continue
        y, negociou = real
        if not negociou:
            empates += 1          # leilão sem negócio: não há gap para acertar
            continue
        linhas.append((r["cut"], r["company"], float(r["score"]),
                       float(r["gap_expected"]), y, r.get("confidence")))
    if not linhas:
        print("nenhuma previsão casou com um gap realizado — confira as datas.")
        return 1
    if empates:
        print(f"({empates} previsões fora da conta: abertura carimbada no fechamento "
              f"anterior, ou seja, leilão sem negócio)")

    for cut in sorted({l[0] for l in linhas}):
        sub = [l for l in linhas if l[0] == cut]
        sc = [l[2] for l in sub]; yy = [l[4] for l in sub]
        ic = _spearman(sc, yy)
        acerto = sum(1 for l in sub if (l[2] > 0) == (l[4] > 0)) / len(sub)
        erro = sum(abs(l[3] - l[4]) for l in sub) / len(sub)
        erro0 = sum(abs(l[4]) for l in sub) / len(sub)
        # spread entre o quintil mais alto e o mais baixo do score
        ordenado = sorted(sub, key=lambda l: l[2])
        k = max(1, len(ordenado) // 5)
        spread = (sum(l[4] for l in ordenado[-k:]) / k) - (sum(l[4] for l in ordenado[:k]) / k)

        print()
        print("=" * 74)
        print(f"CORTE {cut}  ({int(cut):02d}:00 BRT)   n = {len(sub)} previsões")
        print("=" * 74)
        print(f"  IC (Spearman)          {ic:+.3f}      (holdout: +0,264 | mínimo p/ liberar: 0,15)")
        print(f"  acerto direcional      {100*acerto:.1f}%      (holdout: 58,6% | mínimo: 55%)")
        print(f"  spread topo−base       {spread:+.3f} pp/dia  (holdout: +1,02)")
        print(f"  erro médio             {erro:.3f} pp   vs {erro0:.3f} pp de prever zero "
              f"({100*(1-erro/erro0):+.0f}%)")
        ok = (ic > 0.15) and (acerto > 0.55)
        print(f"\n  VEREDITO: {'PODE LIGAR a flag para o cliente' if ok else 'AINDA NÃO — investigar a captura antes'}")

        por_emp = defaultdict(list)
        for l in sub:
            por_emp[l[1]].append(l)
        print(f"\n  {'empresa':<12}{'n':>4}{'IC':>9}{'acerto':>9}{'erro (pp)':>12}")
        for emp, ls in sorted(por_emp.items(), key=lambda kv: -len(kv[1])):
            if len(ls) < 5:
                continue
            i2 = _spearman([l[2] for l in ls], [l[4] for l in ls])
            a2 = sum(1 for l in ls if (l[2] > 0) == (l[4] > 0)) / len(ls)
            e2 = sum(abs(l[3] - l[4]) for l in ls) / len(ls)
            print(f"  {emp:<12}{len(ls):>4}{i2:>+9.3f}{100*a2:>8.1f}%{e2:>12.3f}")

        conf = [l for l in sub if l[5] is not None]
        if len(conf) >= 20:
            alta = [l for l in conf if float(l[5]) >= 60]
            baixa = [l for l in conf if float(l[5]) < 52]
            print("\n  confiança declarada x acerto real:")
            for nome, g in (("confiança >= 60%", alta), ("confiança < 52%", baixa)):
                if g:
                    a = sum(1 for l in g if (l[2] > 0) == (l[4] > 0)) / len(g)
                    print(f"    {nome:<20} {100*a:.1f}% de acerto  (n={len(g)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
