"""quotes-daily.json (COLD, conteúdo gzip com nome .json) — PLANO Apêndice B.4.

`series[ticker] = {updatedAt, pts}` com `pts` = `quotes.daily` tal qual: um ponto por dia UTC nos
últimos 372 dias e o último pregão de cada mês antes disso; epoch = hora de ABERTURA da sessão;
fechamento ajustado por split (a redução é feita no Postgres, `append_quote_history`).
`updatedAt` = `daily_updated_at`. A ponta ao vivo (`[agora, price]`) é o hub que acrescenta.
"""
from __future__ import annotations


def build(src) -> dict:
    d = src.daily()
    series = {}
    for ticker, _name, _exchange in src.quote_universe:
        e = d.get(ticker)
        if not e:
            continue
        series[ticker] = {"updatedAt": e.get("at"), "pts": [list(p) for p in e.get("pts") or []]}
    return {"schemaVersion": 1, "generatedAt": src.generated_at, "series": series}
