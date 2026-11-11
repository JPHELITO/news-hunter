"""pp-paper.json (EVENT, conteúdo gzip com nome .json; medido 28,2 KB cru / 6,7 KB gz) — PLANO,
Apêndice G.9.

`{ schemaVersion, generatedAt, from, through,
   production:    { total, packaging, pw, newsprint, cardboard, other },
   domesticSales: { … }, exports: { … }, imports: { … }, apparentConsumption: [kt…] }`

Paper → Fundamentals — IBÁ (`iba_paper`, o boletim DadosPapel): os 4 fluxos × 6 tipos de papel e o
consumo aparente, em kt, arrays mensais (`null` = mês sem linha ou valor NULL). As quatro pontas
estão aqui para o hub fazer a penetração de importação de um tipo sobre o consumo aparente DO PRÓPRIO
tipo (U8, erro 2). Período repetido no banco (a tabela não tem chave) → o arquivo falha sem publicar.
Sem `updatedAt` (vale o `committedAt` do pp-index.json). Nunca saem: as 7 colunas `exprev_*`
("inconsistente na fonte"), a tabela `iba_pulp` e a `company_q` (código morto na página desde 11/06).
"""
from __future__ import annotations

from .sector_common import dense, need, put, span

GRADES = ("total", "packaging", "pw", "newsprint", "cardboard", "other")
FLOWS = {"production": "prod", "domesticSales": "dom", "exports": "exp", "imports": "imp"}
COLUMNS = [f"{prefix}_{g}" for prefix in FLOWS.values() for g in GRADES] + ["app_cons"]


def build(src) -> dict:
    con = src.sectors().db("pp")
    need(con, {"iba_paper": ["period", *COLUMNS]}, "pp-paper")
    vals = {c: {} for c in COLUMNS}
    seen: dict = {}
    for row in con.execute(f"SELECT period, {', '.join(COLUMNS)} FROM iba_paper"):
        put(seen, row[0], True, "pp-paper (iba_paper)")
        for c, v in zip(COLUMNS, row[1:]):
            vals[c][row[0]] = v
    first, last = span(seen)
    out = {"schemaVersion": 1, "generatedAt": src.generated_at, "from": first, "through": last}
    for key, prefix in FLOWS.items():
        out[key] = {g: dense(vals[f"{prefix}_{g}"], first, last) for g in GRADES}
    out["apparentConsumption"] = dense(vals["app_cons"], first, last)
    return out
