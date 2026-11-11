"""pp-pulp-exports.json (EVENT, conteúdo gzip com nome .json; medido 314,8 KB cru / 109,7 KB gz) —
PLANO, Apêndice G.7.

`{ schemaVersion, generatedAt, from, through,
   workingDays: [dias úteis do Brasil, um por mês],
   ports: { names: ["Barcarena", …], series: [ [<porto>, [ [<início>, [kt…], [US$ mi…]] ]] ] },
   destinations: { countries: ["Algeria", …],
                   products: { hardwood: [ [<país>, [corridas]] ], softwood: [], ukp: [], dissolving: [],
                               other: [] } } }`

Pulp → Exports — SECEX inteiro (`PPD:1166-1305`, `1325-1631`): por PORTO (`secex_pulp_port`, os KPIs,
volume, YoY, receita, preço, por dia útil, sazonalidade e o empilhado por porto) e por DESTINO ×
PRODUTO (`secex_pulp_country`). Corridas de 2 métricas (volume kt, receita US$ mi, como guardadas).
Total do mês = soma dos portos (`secexTotals`); "Total" por destino = os produtos todos, inclusive
`other`, que não tem botão. Os dicionários (`ports.names`, `destinations.countries`) em ordem de
código Unicode; os produtos: hardwood, softwood, ukp, dissolving e other sempre (`[]` sem linha),
mais o que o robô gravar de novo (em ordem de código Unicode).
`workingDays` = `calendar` (seg–sex menos feriados) no mesmo eixo; `null` = mês sem linha.
Porto vazio no banco vira "—" (como a página, `r.port||'—'`). Sem `updatedAt`: as tabelas do P&P
não têm carimbo — vale o `source.committedAt` do pp-index.json (T15).
"""
from __future__ import annotations

from .sector_common import encode_runs, label, month_index, need, ordered, put, span

PORT_COLUMNS = ("period", "port", "volume_ktons", "revenue_usd_mn")
DEST_COLUMNS = ("period", "country", "product", "volume_ktons", "revenue_usd_mn")
PRODUCTS = ("hardwood", "softwood", "ukp", "dissolving", "other")


def _series(pts: dict[str, dict[int, tuple]], names: list[str]) -> list:
    idx = {n: i for i, n in enumerate(names)}
    return [[idx[n], encode_runs(pts[n], 2)] for n in sorted(pts, key=idx.__getitem__)]


def build(src) -> dict:
    con = src.sectors().db("pp")
    need(con, {"secex_pulp_port": PORT_COLUMNS, "secex_pulp_country": DEST_COLUMNS,
               "calendar": ("period", "working_days")}, "pp-pulp-exports")
    ports = [(p, port if port else "—", v, r) for p, port, v, r in con.execute(
        "SELECT period, port, volume_ktons, revenue_usd_mn FROM secex_pulp_port")]
    dest = list(con.execute("SELECT period, country, product, volume_ktons, revenue_usd_mn "
                            "FROM secex_pulp_country"))
    first, last = span([r[0] for r in ports] + [r[0] for r in dest])
    base = month_index(first) if first else 0
    by_port: dict = {}
    for p, port, v, r in ports:
        put(by_port.setdefault(port, {}), month_index(p) - base, (v, r), f"pp-pulp-exports ({port})")
    by_prod: dict = {}
    for p, ctry, prod, v, r in dest:
        prod, ctry = label(prod, "pp-pulp-exports (produto)"), label(ctry, "pp-pulp-exports (país)")
        put(by_prod.setdefault(prod, {}).setdefault(ctry, {}), month_index(p) - base, (v, r),
            f"pp-pulp-exports ({ctry}/{prod})")
    work: dict = {}
    for p, wd in con.execute("SELECT period, working_days FROM calendar"):
        if first and month_index(first) <= month_index(p) <= month_index(last):
            put(work, p, wd, "pp-pulp-exports (calendar)")
    wd_arr = [None] * (month_index(last) - base + 1 if first else 0)
    for p, wd in work.items():
        wd_arr[month_index(p) - base] = wd
    port_names = sorted(by_port)
    countries = sorted({c for per in by_prod.values() for c in per})
    products = {prod: _series(by_prod.get(prod, {}), countries)       # os 5 sempre (+ os novos)
                for prod in ordered(set(by_prod) | set(PRODUCTS), PRODUCTS)}
    return {"schemaVersion": 1, "generatedAt": src.generated_at, "from": first, "through": last,
            "workingDays": wd_arr,
            "ports": {"names": port_names, "series": _series(by_port, port_names)},
            "destinations": {"countries": countries, "products": products}}
