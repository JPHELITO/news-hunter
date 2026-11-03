"""intraday.json (HOT, conteúdo gzip com nome .json, ≤ 150 KB bruto) — PLANO Apêndice B.3.

`series[ticker] = {prev, pts}` com `pts` = `quotes.intraday.pts` tal qual (`[[epoch_s, fechamento],
…]`, início de cada vela de 5 min; pode ser `[]` — no começo do pregão o Yahoo ainda não tem
negócio do dia). Fonte: a memória do ciclo por cima do último estado, como o live.json.
Papel sem linha nenhuma (nem na memória, nem no estado, nem no banco) fica de fora.
`updatedAt` no topo = o carimbo mais novo das cotações incluídas.
"""
from __future__ import annotations


def build(src) -> dict:
    q = src.quotes()
    series = {}
    stamps = []
    for ticker, _name, _exchange in src.quote_universe:
        r = q.get(ticker)
        if not r:
            continue
        it = r.get("intraday") or {}
        series[ticker] = {"prev": it.get("prev"), "pts": [list(p) for p in it.get("pts") or []]}
        if r.get("updated_at"):
            stamps.append(r["updated_at"])
    return {
        "schemaVersion": 1,
        "generatedAt": src.generated_at,
        "updatedAt": max(stamps) if stamps else None,
        "series": series,
    }
