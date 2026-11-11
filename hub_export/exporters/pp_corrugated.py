"""pp-corrugated.json (EVENT, texto; medido 4,0 KB) — PLANO, Apêndice G.10.

`{ schemaVersion, generatedAt, from, through, shipments: [kt…], workingDays: [], perWorkingDay: [],
   ltm: [kt…] }`

Paper → Corrugated — Empapel (`empapel`): expedição de papelão ondulado (kt), dias úteis do EMPAPEL
(segunda a sábado menos feriados, `update_empapel_news.py:24-25` — diferentes do `workingDays` da
celulose), expedição por dia útil e a soma dos últimos 12 meses, como guardados, em arrays mensais.
O "por dia útil" de trimestre/ano é conta do hub: Σ expedição ÷ Σ dias úteis (U8, erro 1). O último
mês pode ser o preliminar (manchete da Fastmarkets) até o oficial (IBPO) chegar: a tabela não marca
qual (registro do PLANO, Onda 3). Período repetido → o arquivo falha. Sem `updatedAt`.
"""
from __future__ import annotations

from .sector_common import dense, need, put, span

COLUMNS = {"shipments_kton": "shipments", "working_days": "workingDays",
           "exp_per_day": "perWorkingDay", "ltm_shipments": "ltm"}


def build(src) -> dict:
    con = src.sectors().db("pp")
    need(con, {"empapel": ["period", *COLUMNS]}, "pp-corrugated")
    vals = {c: {} for c in COLUMNS}
    seen: dict = {}
    for row in con.execute(f"SELECT period, {', '.join(COLUMNS)} FROM empapel"):
        put(seen, row[0], True, "pp-corrugated (empapel)")
        for c, v in zip(COLUMNS, row[1:]):
            vals[c][row[0]] = v
    first, last = span(seen)
    out = {"schemaVersion": 1, "generatedAt": src.generated_at, "from": first, "through": last}
    for c, name in COLUMNS.items():
        out[name] = dense(vals[c], first, last)
    return out
