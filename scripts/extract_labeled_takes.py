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


# Formato real dos relatórios "NEWS - <data>.pdf": texto corrido, um item por
# manchete na seção "Sector Headlines":
#   • STEEL & MINING - <manchete (pode quebrar em várias linhas)> [Fonte] (take)
# O take vem entre parênteses no fim; a fonte em colchetes logo antes dele.
_HEADLINE_RE = re.compile(
    r"(?P<sector>STEEL\s*&\s*MINING|PULP\s*&\s*PAPER)\s*-\s*"
    r"(?P<headline>.+?)\s*"
    r"\[(?P<source>[^\]]+)\]\s*"
    r"\(\s*(?P<take>[+\-−–—=])\s*\)",
    re.DOTALL | re.IGNORECASE,
)

_SECTOR_LABEL = {"steel & mining": "steel_mining", "pulp & paper": "pulp_paper"}


def _rows_from_pattern(text: str, source_file: str) -> list[dict]:
    """Extrai manchetes do padrão 'SETOR - headline [fonte] (take)'."""
    rows: list[dict] = []
    seen: set[str] = set()
    for m in _HEADLINE_RE.finditer(text):
        headline = re.sub(r"\s+", " ", m.group("headline")).strip()
        take = normalize_take(m.group("take"))
        if not headline or take is None:
            continue
        key = headline.lower()
        if key in seen:        # dedup dentro do mesmo PDF
            continue
        seen.add(key)
        sector_raw = re.sub(r"\s+", " ", m.group("sector")).strip().lower()
        rows.append({
            "source_file": source_file,
            "sector": _SECTOR_LABEL.get(sector_raw, sector_raw),
            "source": m.group("source").strip(),
            "headline": headline,
            "gold_take": take,
            "gold_include": "true",   # PDFs são o relatório final → tudo incluído
            "raw_take": m.group("take"),
            "notes": "",
        })
    return rows


def extract_pdf(path: Path) -> list[dict]:
    parts: list[str] = []
    with pdfplumber.open(str(path)) as pdf:
        for page in pdf.pages:
            parts.append(page.extract_text() or "")
    return _rows_from_pattern("\n".join(parts), path.name)


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
        w = csv.DictWriter(f, fieldnames=["source_file", "sector", "source", "headline",
                                          "gold_take", "gold_include", "raw_take", "notes"])
        w.writeheader()
        w.writerows(all_rows)

    dist = {t: sum(1 for r in all_rows if r["gold_take"] == t) for t in ("+", "-", "=")}
    print(f"\nTotal: {len(all_rows)} manchetes de {len(pdfs)} PDF(s) -> {out_path}")
    print(f"Distribuicao de takes: + {dist['+']} | - {dist['-']} | = {dist['=']}")
    if not all_rows:
        print("[!] 0 linhas extraidas -- rode com --inspect num PDF p/ calibrar o parser.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
