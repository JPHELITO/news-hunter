"""quotes-daily.json (COLD, conteúdo gzip com nome .json, ≤ 350 KB bruto) — PLANO Apêndice B.4.

`series[ticker] = {updatedAt, pts}` com `pts` = `quotes.daily` cortado nos ÚLTIMOS 400 DIAS
(`ts ≥ agora − 400 dias`): um ponto por dia UTC; epoch = hora de ABERTURA da sessão; fechamento
ajustado por split (a redução é feita no Postgres, `append_quote_history`). `updatedAt` =
`daily_updated_at`. A ponta ao vivo (`[agora, price]`) é o hub que acrescenta.

Por que o corte (decisão de 03/11/2026, medida no WP1.2): o `quotes.daily` traz, antes dos
últimos 372 dias, o último pregão de cada mês desde 1970 — 56% do arquivo (682 KB brutos) e o
heatmap não usa nada disso (o YoY olha 366 dias + a folga da âncora). O histórico longo é do
`history-<TICKER>.json` (Onda 2). O corte é feito AQUI, na montagem: o cache do diário guarda
a série como veio do banco, e o hash enxerga o arquivo já cortado — a janela que anda conta
como mudança no máximo uma vez por dia em cada série.
"""
from __future__ import annotations

WINDOW_DAYS = 400


def build(src) -> dict:
    d = src.daily()
    cutoff = int(src.now.timestamp()) - WINDOW_DAYS * 86400
    series = {}
    for ticker, _name, _exchange in src.quote_universe:
        e = d.get(ticker)
        if not e:
            continue
        series[ticker] = {"updatedAt": e.get("at"),
                          "pts": [list(p) for p in e.get("pts") or [] if p[0] >= cutoff]}
    return {"schemaVersion": 1, "generatedAt": src.generated_at, "series": series}
