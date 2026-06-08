"""
extract_labeled_takes.py — converte os PDFs de gabarito (data/labeled/*.pdf) em
um CSV estruturado (data/labeled/golden_takes.csv) para avaliar o classificador.

Princípio de economia de tokens: o conteúdo dos PDFs fica em DISCO. O assistente
nunca lê os PDFs — só o stdout resumido deste script e do eval.

Uso:
    python scripts/extract_labeled_takes.py                 # extrai todos os PDFs
    python scripts/extract_labeled_takes.py --inspect X.pdf # mostra estrutura de 1 PDF
    python scripts/extract_labeled_takes.py --out outro.csv # CSV de saída custom

Layout esperado (confirmado): texto selecionável, com símbolo de take (+/-/=)
em coluna/posição ao lado da manchete. O parser tenta TABELA primeiro; se não
houver, cai para leitura linha-a-linha.
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

import pdfplumber

LABELED_DIR = Path(__file__).resolve().parent.parent / "data" / "labeled"
DEFAULT_OUT = LABELED_DIR / "golden_takes.csv"

# ── Normalização do take → "+", "-", "=" ──────────────────────────────────────
_POS = {"+", "▲", "↑", "⬆", "up", "alta", "positivo", "positive", "pos", "p"}
_NEG = {"-", "−", "–", "—", "▼", "↓", "⬇", "down", "baixa", "negativo", "negative", "neg", "n"}
_NEU = {"=", "→", "≈", "0", "neutro", "neutral", "flat", "neu", "e"}


def normalize_take(raw: str) -> str | None:
    """Converte um token bruto de take para '+', '-' ou '=' (ou None se irreconhecível)."""
    if raw is None:
        return None
    t = raw.strip().lower()
    if not t:
        return None
    # tenta o token inteiro e o primeiro caractere
    for cand in (t, t[0]):
        if cand in _POS:
            return "+"
        if cand in _NEG:
            return "-"
        if cand in _NEU:
            return "="
    return None


# Linha-a-linha: take como símbolo isolado no início OU fim da linha.
_LINE_RE = re.compile(
    r"^\s*(?P<lead>[+\-−–—=▲▼↑↓→])?\s*(?P<text>.+?)\s*(?P<trail>[+\-−–—=▲▼↑↓→])?\s*$"
)


def _looks_like_take_col(cells: list[str]) -> int:
    """Heurística: quantas células desta coluna parecem um take."""
    return sum(1 for c in cells if c and normalize_take(c) is not None and len(c.strip()) <= 3)


def _rows_from_tables(page) -> list[tuple[str, str]]:
    """Extrai (headline, raw_take) de tabelas detectadas na página."""
    out: list[tuple[str, str]] = []
    for table in page.extract_tables() or []:
        if not table or len(table) < 2:
            continue
        cols = list(zip(*[[(c or "").strip() for c in row] for row in table]))
        if len(cols) < 2:
            continue
        # coluna de take = a que mais parece take; headline = a de texto mais longo
        take_idx = max(range(len(cols)), key=lambda i: _looks_like_take_col(list(cols[i])))
        head_idx = max(range(len(cols)),
                       key=lambda i: sum(len(c) for c in cols[i]) if i != take_idx else -1)
        for row in table:
            cells = [(c or "").strip() for c in row]
            if max(take_idx, head_idx) >= len(cells):
                continue
            headline, raw = cells[head_idx], cells[take_idx]
            if headline and normalize_take(raw) is not None:
                out.append((headline, raw))
    return out


def _rows_from_lines(text: str) -> list[tuple[str, str]]:
    """Fallback: take como símbolo no início/fim de cada linha."""
    out: list[tuple[str, str]] = []
    for line in text.splitlines():
        if len(line.strip()) < 8:
            continue
        m = _LINE_RE.match(line)
        if not m:
            continue
        raw = m.group("lead") or m.group("trail")
        head = m.group("text")
        if raw and head and normalize_take(raw) is not None and len(head) >= 8:
            out.append((head.strip(), raw))
    return out


def extract_pdf(path: Path) -> list[dict]:
    rows: list[dict] = []
    with pdfplumber.open(str(path)) as pdf:
        for page in pdf.pages:
            pairs = _rows_from_tables(page)
            if not pairs:
                pairs = _rows_from_lines(page.extract_text() or "")
            for headline, raw in pairs:
                rows.append({
                    "source_file": path.name,
                    "headline": re.sub(r"\s+", " ", headline).strip(),
                    "gold_take": normalize_take(raw),
                    "gold_include": "true",
                    "raw_take": raw.strip(),
                    "notes": "",
                })
    return rows


def inspect(path: Path) -> None:
    """Mostra a estrutura de UM pdf (saída curta) para calibrar o parser."""
    with pdfplumber.open(str(path)) as pdf:
        print(f"== {path.name} :: {len(pdf.pages)} página(s) ==")
        for pno, page in enumerate(pdf.pages[:2], 1):
            tables = page.extract_tables() or []
            print(f"\n-- página {pno}: {len(tables)} tabela(s) --")
            for ti, t in enumerate(tables[:2]):
                print(f"  tabela {ti}: {len(t)} linhas x {len(t[0]) if t else 0} colunas")
                for row in t[:4]:
                    print("   ", [(c or "")[:40] for c in row])
            text = page.extract_text() or ""
            print("  primeiras linhas de texto:")
            for ln in text.splitlines()[:12]:
                print("    |", ln[:90])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--inspect", metavar="PDF", help="mostra estrutura de 1 PDF e sai")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="CSV de saída")
    args = ap.parse_args()

    if args.inspect:
        inspect(Path(args.inspect))
        return 0

    pdfs = sorted(LABELED_DIR.glob("*.pdf"))
    if not pdfs:
        print(f"Nenhum PDF em {LABELED_DIR}. Solte os PDFs lá e rode de novo.")
        return 1

    all_rows: list[dict] = []
    for p in pdfs:
        rows = extract_pdf(p)
        print(f"  {p.name}: {len(rows)} manchetes")
        all_rows.extend(rows)

    out_path = Path(args.out)
    with out_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["source_file", "headline", "gold_take",
                                          "gold_include", "raw_take", "notes"])
        w.writeheader()
        w.writerows(all_rows)

    dist = {t: sum(1 for r in all_rows if r["gold_take"] == t) for t in ("+", "-", "=")}
    print(f"\nTotal: {len(all_rows)} manchetes de {len(pdfs)} PDF(s) → {out_path}")
    print(f"Distribuição de takes: + {dist['+']} | - {dist['-']} | = {dist['=']}")
    if not all_rows:
        print("⚠️  0 linhas extraídas — rode com --inspect num PDF p/ eu calibrar o parser.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
