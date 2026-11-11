"""pp-woodchips.json (EVENT, conteúdo gzip com nome .json; medido 48,7 KB cru / 17,3 KB gz) — PLANO,
Apêndice G.8.

`{ schemaVersion, generatedAt, from, through,
   suppliers: ["Australia", "Brazil", …, "Others", "Total", …, "Vietnam"],
   fibres: { HW: { <fornecedor>: { volume: [M BDMT…], revenue: [US$ mi…] }, … }, SW: {…} } }`

Pulp → Woodchips — GACC (`gacc_woodchips`, a alfândega da China): por fibra (HW/SW) e fornecedor
(os 10 nomeados + `Others` + `Total`, como o robô grava), volume e receita como guardados, em
arrays mensais. `from` = o 1º mês com volume > 0 em qualquer linha (o corte de `PPD:1180-1182`,
feito aqui: a tabela já teve 2008-2014 com volume 0), `through` = o último mês da tabela.
`suppliers` vem do DADO, em ordem de código Unicode (G.0: é a ordem do banco; o botão e o padrão
Vietnam + Australia são constantes do hub). Todo fornecedor aparece em toda fibra (arrays de `null`
se não houver linha). `Total` = soma exata dos outros (conferido no banco); "Others" do gráfico =
Total − selecionados é conta do hub. Sem `updatedAt` (vale o `committedAt` do pp-index.json).
"""
from __future__ import annotations

from .sector_common import dense, label, month_index, need, put, span

COLUMNS = ("period", "fibre", "country", "volume_bdmt", "revenue_usd_mn")


def build(src) -> dict:
    con = src.sectors().db("pp")
    need(con, {"gacc_woodchips": COLUMNS}, "pp-woodchips")
    rows = list(con.execute("SELECT period, fibre, country, volume_bdmt, revenue_usd_mn "
                            "FROM gacc_woodchips"))
    positive = [r[0] for r in rows if isinstance(r[3], (int, float)) and r[3] > 0]
    cut = min(positive, key=month_index) if positive else None
    if cut is not None:
        rows = [r for r in rows if month_index(r[0]) >= month_index(cut)]
    first, last = span(r[0] for r in rows)
    got: dict = {}
    for p, fibre, ctry, vol, rev in rows:
        key = (label(fibre, "pp-woodchips (fibra)"), label(ctry, "pp-woodchips (fornecedor)"))
        put(got.setdefault(key, {}), p, (vol, rev), f"pp-woodchips ({fibre}/{ctry})")
    suppliers = sorted({c for _f, c in got})
    fibres = {}
    for fibre in sorted({f for f, _c in got}):
        fibres[fibre] = {}
        for c in suppliers:
            pts = got.get((fibre, c), {})
            fibres[fibre][c] = {"volume": dense({p: v for p, (v, _r) in pts.items()}, first, last),
                                "revenue": dense({p: r for p, (_v, r) in pts.items()}, first, last)}
    return {"schemaVersion": 1, "generatedAt": src.generated_at, "from": first, "through": last,
            "suppliers": suppliers, "fibres": fibres}
