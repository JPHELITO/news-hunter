"""commodities-daily.json (COLD com sonda, conteúdo gzip com nome .json, ≤ 150 KB bruto) — PLANO,
Apêndice F.4.

`{ schemaVersion, generatedAt, series: { <code>: { updatedAt, unit, source, pts } } }` — a série
diária em NÚMEROS só das commodities de FONTE LIVRE: minério 62% do Trading Economics (decisão U5 do
usuário, 09/11) e cobre e ouro do Yahoo. `pts` = `commodities.daily` tal qual (`[[epoch, valor], …]`
crescente; minério ~10 anos, cobre e ouro ~5); `updatedAt` = `daily_updated_at` (fora do hash);
`source` diz a fonte de cada série. Na ordem da lista abaixo; código que sumiu do banco não sai.

⚠️ LISTA BRANCA DE CÓDIGOS (D1/R1 + `COMM_NUMEROS_LIVRES` da market.html): todo o resto de
`commodities.daily` é assessment PAGO (Platts, Fastmarkets: aço, carvão, minério 61%, celulose…) que
o robô acumula dia a dia — a série em números NUNCA sai ("só não deixe que o cliente tenha acesso ao
histórico em si, os números", o analista em 31/08/2026). Nem é pedida ao banco (`code=in.(…)` só com
os 3), e a montagem ignora qualquer outro código que chegue. `commodity_history` (a série paga
privada) nunca é lida. Código novo aqui = decisão do usuário + o teste que fixa esta lista.
"""
from __future__ import annotations

# código → fonte (o rótulo vai no arquivo). NÃO acrescentar código pago.
FREE_SERIES = {
    "IRON_ORE_62": "Trading Economics",
    "COPPER": "Yahoo Finance",
    "GOLD": "Yahoo Finance",
}


def build(src) -> dict:
    rows = src.commodities_daily()
    series = {}
    for code, source in FREE_SERIES.items():
        r = rows.get(code)
        if not r:
            continue
        series[code] = {"updatedAt": r.get("at"), "unit": r.get("unit"), "source": source,
                        "pts": [list(p) for p in r.get("pts") or []]}
    return {"schemaVersion": 1, "generatedAt": src.generated_at, "series": series}
