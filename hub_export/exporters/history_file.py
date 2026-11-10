"""history-<id>.json (família, conteúdo gzip com nome .json, ≤ 300 KB bruto) — PLANO, Apêndice F.1.

`{ schemaVersion, generatedAt, ticker, updatedAt, firstTs, lastTs, points, lastSplitTs, pts }`:
o histórico diário COMPLETO de um papel do universo da Market (`quote_history.daily` tal qual:
`[[epoch, fechamento], …]` crescente, 1 ponto por dia UTC, epoch = abertura do pregão, fechamento
ajustado por split, não por dividendo). `updatedAt` = `daily_updated_at` do papel (fora do hash);
`firstTs`/`lastTs`/`points` = as pontas e o tamanho da série; `lastSplitTs` = o último split já
refletido (epoch) ou null. Sem a ponta viva (é do hub, pelo live.json).

Nome pela regra F.0 (`names.file_id`): `history-vale3.sa.json`. Quais papéis vão em cada execução,
de onde vem a série (cópia publicada + série leve, ou a leitura inteira) e o ritmo: history.py.
"""
from __future__ import annotations


def build(src, ticker: str) -> dict:
    s = src.history().series(ticker)
    pts = s["pts"]
    return {
        "schemaVersion": 1,
        "generatedAt": src.generated_at,
        "ticker": ticker,
        "updatedAt": s.get("updatedAt"),
        "firstTs": pts[0][0],
        "lastTs": pts[-1][0],
        "points": len(pts),
        "lastSplitTs": s.get("lastSplitTs"),
        "pts": pts,
    }


def after(src, ticker: str, obj: dict, action: str) -> None:
    """Depois do envio (ou da decisão de não enviar): o registro do que está publicado."""
    src.history().after(ticker, obj, action)
