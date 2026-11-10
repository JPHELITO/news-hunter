"""mw-short-<empresa>.json (família, uma por empresa visível, conteúdo gzip com nome .json, ≤ 60 KB
cru hoje) — PLANO, Apêndice F.8.

`{ schemaVersion, generatedAt, company, columns: [refDate, qtyTotal, valueBrl, avgPrice, rateTakerAvg,
   rateTakerMin, rateTakerMax, rateDonorAvg, contractsDay, qtyDay, valueDay, source],
   series: { <papel>: [[…], …] } }`

A série INTEIRA de cada papel da empresa (o "all-time" do gráfico e o histórico/Excel da visão de
empresa), em data crescente; `source` = `bdi_api` | `bdi_pdf`. Papéis: o principal primeiro, depois
A→Z; papel sem linha nenhuma não aparece (a tela monta os chips dos papéis com dado).

Nome pela regra F.0 (`names.file_id`): `mw-short-klbn11.json`. De onde vem a série (cópia publicada
+ janela dos últimos 10 dias, ou a leitura paginada por empresa) e quando cada uma vai: mw.py.
"""
from __future__ import annotations

from .mw_short_interest import FULL_COLUMNS, ticker_order


def build(src, company: str) -> dict:
    by = src.mw().lending_company(company)
    return {"schemaVersion": 1, "generatedAt": src.generated_at, "company": company,
            "columns": list(FULL_COLUMNS),
            "series": {t: [list(r) for r in by[t]] for t in ticker_order(company, by) if by[t]}}
