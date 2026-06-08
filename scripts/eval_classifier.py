"""
eval_classifier.py — avalia o classificador determinístico contra o gabarito
manual (data/labeled/golden_takes.csv) e imprime SÓ o que importa: acurácia,
matriz de confusão e os casos de divergência.

Economia de tokens: nenhuma manchete "certa" é impressa — só agregados e erros.
O loop de ajuste vê apenas as discordâncias (poucas), não o dataset inteiro.

Uso:
    python scripts/eval_classifier.py
    python scripts/eval_classifier.py --csv outro.csv --max-errors 80
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hunter.news_take_classifier import classify_take  # noqa: E402

DEFAULT_CSV = Path(__file__).resolve().parent.parent / "data" / "labeled" / "golden_takes.csv"
TAKES = ("+", "-", "=")


def _truthy(s: str) -> bool:
    return str(s).strip().lower() in ("true", "1", "sim", "yes", "y", "t")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=str(DEFAULT_CSV))
    ap.add_argument("--max-errors", type=int, default=60,
                    help="máximo de divergências listadas (evita estourar saída)")
    args = ap.parse_args()

    csv_path = Path(args.csv)
    if not csv_path.exists():
        print(f"Gabarito não encontrado: {csv_path}\n"
              f"Rode antes: python scripts/extract_labeled_takes.py")
        return 1

    with csv_path.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        print("Gabarito vazio.")
        return 1

    n = 0
    take_hits = 0
    incl_hits = 0
    incl_total = 0
    confusion: Counter = Counter()        # (gold, pred) -> contagem
    take_errors: list[tuple] = []
    incl_errors: list[tuple] = []

    for r in rows:
        gold = (r.get("gold_take") or "").strip()
        if gold not in TAKES:
            continue
        headline = (r.get("headline") or "").strip()
        if not headline:
            continue
        n += 1
        res = classify_take(headline, {"source_name": r.get("source") or r.get("source_file", "")})
        pred = res["take"]
        # "review" conta como divergência de take (não é +/-/=)
        pred_norm = pred if pred in TAKES else "review"
        confusion[(gold, pred_norm)] += 1
        if pred_norm == gold:
            take_hits += 1
        elif len(take_errors) < args.max_errors:
            take_errors.append((gold, pred_norm, "; ".join(res["matched_rules"]), headline))

        # include/exclude (se o gabarito tiver a coluna preenchida)
        if r.get("gold_include", "").strip():
            incl_total += 1
            gold_inc = _truthy(r["gold_include"])
            if res["include_in_report"] == gold_inc:
                incl_hits += 1
            elif len(incl_errors) < args.max_errors:
                incl_errors.append((gold_inc, res["include_in_report"],
                                    res.get("exclusion_reason"), headline))

    # ── Relatório ─────────────────────────────────────────────────────────────
    print(f"=== Avaliação do classificador vs gabarito ({csv_path.name}) ===")
    print(f"Manchetes avaliadas: {n}")
    if n:
        print(f"Acurácia de TAKE: {take_hits}/{n} = {take_hits / n:.1%}")
    if incl_total:
        print(f"Acurácia de INCLUDE/EXCLUDE: {incl_hits}/{incl_total} = {incl_hits / incl_total:.1%}")

    print("\n-- Matriz de confusão (linha=gabarito, coluna=previsto) --")
    cols = TAKES + ("review",)
    print("        " + "".join(f"{c:>8}" for c in cols))
    for g in TAKES:
        line = "".join(f"{confusion.get((g, c), 0):>8}" for c in cols)
        print(f"  {g:>4}  {line}")

    if take_errors:
        print(f"\n-- Divergências de TAKE ({len(take_errors)} mostradas) --")
        print(f"  {'gold':>4} {'pred':>6}  regras / manchete")
        for gold, pred, rules, head in take_errors:
            print(f"  {gold:>4} {pred:>6}  [{rules}] {head[:80]}")

    if incl_errors:
        print(f"\n-- Divergências de INCLUDE/EXCLUDE ({len(incl_errors)} mostradas) --")
        for gi, pi, reason, head in incl_errors:
            print(f"  gold_include={gi!s:>5} pred={pi!s:>5} reason={reason} :: {head[:70]}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
