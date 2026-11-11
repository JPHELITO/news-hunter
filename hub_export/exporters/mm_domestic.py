"""mm-domestic.json (EVENT, conteúdo gzip com nome .json; medido 27,9 KB cru / 12,5 KB gz) — PLANO,
Apêndice G.3.

`{ schemaVersion, generatedAt,
   iabr: { updatedAt, from, through,
           production:    { crudeSteel, flat, long, semi },
           consumption:   { total, flat, long },
           domesticSales: { total, flat, long, semi, slabs, billets } },
   inda: { updatedAt, from, through, inventories, invMonths, purchases, sales, salesLtm, salesMa3 } }`

O "Domestic Market" da página (`SMD:1531-1545`): IABr (Aço Brasil) e INDA, em kt (`invMonths` em
meses de estoque). Os três quadros do IABr dividem UM eixo (`from`/`through` = o menor e o maior mês
das 3 tabelas), o INDA tem o seu. Não existe consumo aparente de semiacabados (sem `consumption.semi`).
`flat_incl`/`long_incl` (placa em Flat, tarugo em Long) e a penetração são do hub (`SMD:1541-1544`,
`2323-2339`), sobre `slabs`/`billets` daqui. `updatedAt` = o maior `updated_at` das linhas (fora do
hash). Nunca saem: `iabr_exports`, `iabr_imports`, `slabs`/`billets`/`pig_iron` da PRODUÇÃO,
`inv_yoy`, `hist_avg` e `working_days` do INDA.
"""
from __future__ import annotations

from .sector_common import dense, need, put, span, stamp

IABR = {"production": ("iabr_production", {"crude_steel": "crudeSteel", "flat": "flat",
                                           "long_prod": "long", "semi": "semi"}),
        "consumption": ("iabr_consumption", {"total": "total", "flat": "flat", "long_prod": "long"}),
        "domesticSales": ("iabr_domestic_sales", {"total": "total", "flat": "flat",
                                                  "long_prod": "long", "semi": "semi",
                                                  "slabs": "slabs", "billets": "billets"})}
INDA = ("inda_distribution", {"inventories": "inventories", "inv_months": "invMonths",
                              "purchases": "purchases", "sales": "sales", "sales_ltm": "salesLtm",
                              "sales_ma3": "salesMa3"})


def _read(con, table: str, cols: dict) -> tuple[dict, list]:
    """{coluna do banco: {período: valor}} e os carimbos das linhas."""
    out = {c: {} for c in cols}
    seen: dict = {}
    stamps = []
    sql = f"SELECT period, {', '.join(cols)}, updated_at FROM {table}"
    for row in con.execute(sql):
        put(seen, row[0], True, f"mm-domestic ({table})")
        for c, v in zip(cols, row[1:-1]):
            out[c][row[0]] = v
        stamps.append(row[-1])
    return out, stamps


def build(src) -> dict:
    con = src.sectors().db("mm")
    spec = {t: ["period", *cols, "updated_at"] for t, cols in IABR.values()}
    spec[INDA[0]] = ["period", *INDA[1], "updated_at"]
    need(con, spec, "mm-domestic")
    blocks, stamps, periods = {}, [], []
    for key, (table, cols) in IABR.items():
        vals, st = _read(con, table, cols)
        blocks[key] = (cols, vals)
        stamps += st
        for v in vals.values():
            periods += list(v)
    first, last = span(periods)
    iabr = {"updatedAt": stamp(stamps), "from": first, "through": last}
    for key, (cols, vals) in blocks.items():
        iabr[key] = {name: dense(vals[c], first, last) for c, name in cols.items()}
    table, cols = INDA
    vals, st = _read(con, table, cols)
    nf, nl = span(p for v in vals.values() for p in v)
    inda = {"updatedAt": stamp(st), "from": nf, "through": nl}
    for c, name in cols.items():
        inda[name] = dense(vals[c], nf, nl)
    return {"schemaVersion": 1, "generatedAt": src.generated_at, "iabr": iabr, "inda": inda}
