"""
pulse_experiment.py — mede as variantes do Market Pulse no MESMO walk-forward antes de
trocar qualquer coisa em produção.

POR QUE ISTO EXISTE
-------------------
Mudança de feature que entra "porque faz sentido" é como o modelo apodrece. A regra da
casa é: instrumento novo ou janela nova só vai para produção se o `ic_oos` SUBIR, medido
contra a configuração que já está no ar, no mesmo período e com a mesma validação.

As variantes:
    base-24h        23 instrumentos, variação de 24h   ← o que estava no ar até 26/08/2026
    base-overnight  23 instrumentos, variação overnight (fechamento de ontem → corte)
    todos-24h       todos os instrumentos, variação de 24h
    todos-overnight todos os instrumentos, variação overnight   ← a proposta

Comparar `base-24h` com `base-overnight` isola o efeito da JANELA; comparar
`base-overnight` com `todos-overnight` isola o efeito dos INSTRUMENTOS novos. Rodar as
quatro é o que permite dizer qual das duas mudanças pagou — e quanto.

Uso:
    python scripts/pulse_experiment.py                  # as 4 variantes, os 2 cortes
    python scripts/pulse_experiment.py --cut 09         # só o corte definitivo (mais rápido)
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

from hunter.pulse_snapshot import COMPANIES, CUTS_SCORE          # noqa: E402
from pulse_train import (ALPHA, SYMBOLS_BASE_V2, features_do_corte,   # noqa: E402
                         gaps_de_abertura, treinar)

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("pulse_experiment")

VARIANTES = [
    ("base-24h",        "24h",       "base"),
    ("base-overnight",  "overnight", "base"),
    ("todos-24h",       "24h",       "todos"),
    ("todos-overnight", "overnight", "todos"),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cut", choices=list(CUTS_SCORE), help="medir só um corte")
    ap.add_argument("--alpha", type=float, default=ALPHA)
    args = ap.parse_args()

    cortes = [args.cut] if args.cut else list(CUTS_SCORE)

    print("baixando o alvo (gap de abertura, sem os leilões que não negociaram) ...")
    G = gaps_de_abertura()

    resultados = []
    for cut in cortes:
        cache = {}
        for nome, janela, symbols in VARIANTES:
            if janela not in cache:
                cache[janela] = features_do_corte(cut, janela=janela)
            X = cache[janela]
            if X.empty:
                print(f"  corte {cut}/{janela}: sem dado — rode scripts/pulse_backfill.py")
                continue
            if symbols == "base":
                X = X.reindex(columns=[s for s in SYMBOLS_BASE_V2 if s in X.columns])
            print(f"  {cut} · {nome:<16} {X.shape[0]:>4} pregões x {X.shape[1]:>2} instrumentos")
            for empresa in COMPANIES:
                if empresa not in G.columns:
                    continue
                m = treinar(X, G[empresa].reindex(X.index), empresa, cut, args.alpha)
                if m:
                    resultados.append({"cut": cut, "variante": nome, "empresa": empresa,
                                       "ic_oos": m["ic_oos"], "n": m["n_train"]})

    if not resultados:
        print("nada medido.")
        return 1

    df = pd.DataFrame(resultados)
    ordem = [v[0] for v in VARIANTES]

    for cut in cortes:
        sub = df[df.cut == cut]
        if sub.empty:
            continue
        tab = sub.pivot_table(index="empresa", columns="variante", values="ic_oos")
        tab = tab.reindex(columns=[c for c in ordem if c in tab.columns])
        tab = tab.reindex([c for c in COMPANIES if c in tab.index])
        print()
        print("=" * 78)
        print(f"IC FORA DA AMOSTRA — corte {cut}")
        print("=" * 78)
        print(tab.round(3).to_string(na_rep="  —"))
        print("-" * 78)
        print("média      " + "".join(f"{tab[c].mean():>16.3f}" for c in tab.columns))
        print("média das que publicam (ic_oos >= 0,20 na configuração atual):")
        fortes = [e for e in tab.index
                  if "todos-overnight" in tab.columns and pd.notna(tab.loc[e, "todos-overnight"])
                  and tab.loc[e, "todos-overnight"] >= 0.20]
        if fortes:
            print("           " + "".join(f"{tab.loc[fortes, c].mean():>16.3f}"
                                          for c in tab.columns))

        if {"base-24h", "todos-overnight"} <= set(tab.columns):
            d = (tab["todos-overnight"] - tab["base-24h"]).dropna()
            print()
            print(f"VEREDITO (todos-overnight − base-24h): média {d.mean():+.3f} · "
                  f"melhora em {int((d > 0).sum())} de {len(d)} empresas")
            piores = d[d < -0.02]
            if len(piores):
                print("  ⚠️ pioraram: " +
                      ", ".join(f"{e} {v:+.3f}" for e, v in piores.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
