"""Prova local do motor de Word (Fase 2).

Roda a partir de news-hunter/:  python -m clipping._localtest
Gera clipping/out/clipping_localtest.docx com 2 itens (um S&M, um P&P bilíngue),
usando corpo de amostra — valida o estilo da casa SEM depender de scraping/rede.
"""
from datetime import date
from pathlib import Path

from .build import ClippingItem, build_docx

items = [
    ClippingItem(
        url="https://core.spglobal.com/x/1",
        title="Iron ore prices rise on firmer China steel demand",
        source_name="S&P Platts",
        body=(
            "<p>Iron ore prices climbed on Wednesday as Chinese mills restocked ahead "
            "of the peak construction season.</p>"
            "<p>Analysts said the 62% Fe benchmark could test $110/t if demand holds "
            "through the quarter.</p>"
        ),
        matched_keywords=["iron ore", "china"],
        domain="core.spglobal.com",
        take="+",
        sector="SM",
    ),
    ClippingItem(
        url="https://valor.globo.com/x/2",
        title="Suzano anuncia expansão de capacidade em celulose",
        source_name="Valor Econômico",
        body="<p>A Suzano anunciou nesta terça-feira um novo projeto de expansão de celulose.</p>",
        matched_keywords=["suzano", "celulose"],
        domain="valor.globo.com",
        take="=",
        sector="PP",
        translated_title="Suzano announces pulp capacity expansion",
        translated_body="<p>Suzano announced on Tuesday a new pulp capacity expansion project.</p>",
    ),
]

out = Path(__file__).resolve().parent / "out" / "clipping_localtest.docx"
out.parent.mkdir(parents=True, exist_ok=True)
data = build_docx(items, date(2026, 7, 27))
out.write_bytes(data)
print(f"OK -> {out}  ({len(data)} bytes, {len(items)} itens)")
