"""
pulse_pooling.py — o painel pooled melhora, ou é complexidade sem retorno?

A PERGUNTA
----------
Hoje treinamos NOVE ridges independentes, um por empresa, com ~350 pregões cada. A
literatura de previsão de retornos (Bollerslev et al. 2018; Gu, Kelly & Xiu 2020) diz que,
com amostra pequena e relações parecidas entre os ativos, um PAINEL empilhado costuma
vencer os modelos ativo-a-ativo: são ~3.000 observações em vez de 350, e os nomes fracos
são puxados para o comportamento médio do setor em vez de sobreajustar o próprio ruído.

Aqui o painel supõe BETA COMUM e escala própria: o alvo de cada empresa é padronizado pelo
desvio-padrão dela (gap/σ), o ridge aprende um vetor de pesos só, e a previsão volta para
a escala da empresa multiplicando por σ. É a forma mais simples de pooling — se nem ela
ajudar, versões mais elaboradas dificilmente ajudariam.

O que medimos, sempre no MESMO walk-forward:
    per-name   os nove ridges de hoje
    painel     um ridge só, empilhado
    média      (per-name + painel) / 2 — a combinação que a literatura de forecast
               (Timmermann) mostra ser difícil de bater

CRITÉRIO: só adotar se a média vencer o per-name na maioria das empresas E na média geral.
Complexidade que não paga não entra.

Uso:
    python scripts/pulse_pooling.py --cut 09
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

from hunter.pulse_snapshot import COMPANIES, CUTS_SCORE            # noqa: E402
from pulse_train import (ALPHA, features_do_corte,                 # noqa: E402
                         gaps_de_abertura, walk_forward_conjunto)

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("pulse_pooling")


def _ic(a, b) -> float:
    m = ~(pd.isna(a) | pd.isna(b))
    if m.sum() < 30:
        return float("nan")
    return float(stats.spearmanr(np.asarray(a)[m], np.asarray(b)[m])[0])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cut", choices=list(CUTS_SCORE), default="09")
    ap.add_argument("--alpha", type=float, default=ALPHA)
    args = ap.parse_args()

    print("baixando o alvo ...")
    G = gaps_de_abertura()
    X = features_do_corte(args.cut, janela="overnight")
    if X.empty:
        print("sem features — rode scripts/pulse_backfill.py")
        return 1
    print(f"corte {args.cut}: {X.shape[0]} pregões x {X.shape[1]} instrumentos")

    R = walk_forward_conjunto(X, G, args.alpha)
    if R.empty:
        print("nada medido.")
        return 1

    tab = []
    for emp, g in R.groupby("empresa"):
        tab.append({"empresa": emp, "n": len(g),
                    "per-name": _ic(g.per_name, g.y),
                    "painel": _ic(g.painel, g.y),
                    "média": _ic(g.pred, g.y)})     # `pred` É a média — o que vai ao ar
    df = pd.DataFrame(tab).set_index("empresa")
    df = df.reindex([c for c in COMPANIES if c in df.index])

    print()
    print("=" * 72)
    print(f"IC FORA DA AMOSTRA — corte {args.cut}")
    print("=" * 72)
    print(df.round(3).to_string(na_rep="  —"))
    print("-" * 72)
    for c in ("per-name", "painel", "média"):
        print(f"  média geral {c:<10} {df[c].mean():+.3f}")

    ganho = (df["média"] - df["per-name"]).dropna()
    venceu = int((ganho > 0).sum())
    print()
    print(f"VEREDITO: a média bate o per-name em {venceu} de {len(ganho)} empresas · "
          f"ganho médio {ganho.mean():+.3f}")
    print("  → ADOTAR" if venceu > len(ganho) / 2 and ganho.mean() > 0.005
          else "  → NÃO ADOTAR (complexidade sem retorno)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
