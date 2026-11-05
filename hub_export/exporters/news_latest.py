"""news-latest.json (HOT, conteúdo gzip com nome .json, ≤ 300 KB bruto) — PLANO, Apêndice E.2.

Um arquivo só serve o feed da Home, o Market Pulse, o filtro do resumo e o "mais novo" da News:
as incluídas com data de referência ≥ `windowStart` OU achadas nas últimas 48 h (a descoberta
tardia). `windowStart` = o mais antigo entre agora − 48 h e o início do Pulse (17:00 de Brasília
do último dia útil antes de hoje — na segunda, ~79 h), ARREDONDADO PARA BAIXO À HORA CHEIA: assim
o arquivo muda no máximo uma vez por hora só porque a janela andou. O corte das 48 h do `foundAt`
também é arredondado à hora (é um pouco mais largo que o contrato, nunca mais estreito).
Data esquisita (publicação mais de 1 h depois de achada, ou mais de 8 dias antes) conta pelo
`foundAt` — sem isso, um `published_at` no futuro deixaria a notícia presa na janela.

Itens no formato do Apêndice E.1 (com `reason`), em `foundAt` decrescente, desempate por `id`.
`updatedAt` = o maior `foundAt` (o hash ignora `updatedAt`). Sem `lastRunAt`: o "synced X min
ago" do feed usa o `generatedAt` do live.json (decisão O2, Apêndice E.9).
"""
from __future__ import annotations


def build(src) -> dict:
    store = src.news()
    items = store.latest_items()
    return {
        "schemaVersion": 1,
        "generatedAt": src.generated_at,
        "updatedAt": max((i["foundAt"] for i in items), default=None),
        "windowStart": store.window_start,
        "items": items,
    }
