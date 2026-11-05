"""news-week-<ano ISO>-w<NN>.json (COLD, conteúdo gzip, ≤ 1 MB bruto) — PLANO, Apêndice E.3.

Uma FAMÍLIA de arquivos (um por semana ISO em UTC, nome em minúsculas: `news-week-2026-w45.json`;
a `2026-w53` vai de 28/12/2026 a 03/01/2027). Itens = as incluídas cuja data de referência cai
na semana (data esquisita → a do `foundAt`), no formato do E.1 SEM `reason`, em data de
referência decrescente, desempate por `id`. Envelope: `updatedAt` (o maior `foundAt`), `week`,
`from`/`to` (segunda e domingo). Semana sem notícia sai com `items: []` (o índice fica contíguo).

Quais semanas vão em cada execução (news.py): a anterior e a corrente, montadas do cache, só
quando mudaram; as antigas com correção pendente (sobre a cópia publicada); a carga inicial aos
poucos; e as pedidas pelo nome (`--only news-week-2026-w30`, relidas do banco).
"""
from __future__ import annotations

from .. import weeks as W


def build(src, label: str) -> dict:
    items = src.news().week_items(label)
    first, last = W.bounds(label)
    return {
        "schemaVersion": 1,
        "generatedAt": src.generated_at,
        "updatedAt": max((i["foundAt"] for i in items), default=None),
        "week": label,
        "from": first,
        "to": last,
        "items": items,
    }


def after(src, label: str, obj: dict, action: str) -> None:
    """Depois do envio (ou da decisão de não enviar): o registro das semanas publicadas."""
    src.news().after_week(label, obj["items"], obj.get("updatedAt"), action)
