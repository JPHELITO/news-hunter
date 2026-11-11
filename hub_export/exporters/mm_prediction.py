"""mm-prediction.json (EVENT, texto; medido 9,6 KB) — PLANO, Apêndice G.4.

`{ schemaVersion, generatedAt,
   imports: { updatedAt, from, through, series: { HRC: { Korea: [kg…], China: [] }, CRC: {}, Coated: {} } },
   exports: { updatedAt, from, through, series: {…} } }`

O modelo preditivo (`SMD:2098-2227`, O42): laranja = `imports` (`import_prediction`, a importação
brasileira pelo SECEX vinda da Coreia/China nos SH6 antidumping); preta = `exports`
(`pred_exports`, a exportação registrada na aduana de origem). kg por mês, como guardado.

SÓ OS PARES COM AS DUAS LINHAS ("mesmo conjunto nas duas pontas", memória do usuário): um produto
sai só se existe nas DUAS tabelas, e dentro dele só os países que têm linha nas duas — por isso o
`Wire Rod` (só na laranja: China 82 meses, Coreia 3) não sai; e ele fica de fora por nome também
(G.0: "nunca sai"), mesmo se um dia ganhar a ponta de exportação (teste). Ordem: HRC, CRC, Coated,
Korea, China (a da página), e o que vier de novo depois, em ordem de código Unicode.
Cada bloco tem o seu eixo (`from`/`through` das linhas publicadas) e o seu `updatedAt` (fora do
hash). A flag `pred_model` (fail-open) só esconde a seção no hub: o arquivo sai sempre.
Nunca saem: `value_usd`, o `Wire Rod`, o `updated_at` de cada linha.
"""
from __future__ import annotations

from .sector_common import dense, label, need, ordered, put, span, stamp

TABLES = {"imports": "import_prediction", "exports": "pred_exports"}
COLUMNS = ("period", "product", "country", "volume_kg", "updated_at")
PRODUCTS = ("HRC", "CRC", "Coated")
COUNTRIES = ("Korea", "China")
EXCLUDED = frozenset({"Wire Rod"})      # G.0: nunca sai, nem se um dia ganhar a ponta de exportação


def build(src) -> dict:
    con = src.sectors().db("mm")
    need(con, {t: COLUMNS for t in TABLES.values()}, "mm-prediction")
    rows = {}
    for block, table in TABLES.items():
        got: dict = {}
        for p, prod, ctry, kg, upd in con.execute(
                f"SELECT period, product, country, volume_kg, updated_at FROM {table}"):
            pair = (label(prod, f"mm-prediction ({table})"), label(ctry, f"mm-prediction ({table})"))
            put(got.setdefault(pair, {}), p, (kg, upd), f"mm-prediction ({table})")
        rows[block] = got
    pairs = {pc for pc in set(rows["imports"]) & set(rows["exports"])  # as duas pontas, por par
             if pc[0] not in EXCLUDED}
    products = ordered({p for p, _c in pairs}, PRODUCTS)
    out = {"schemaVersion": 1, "generatedAt": src.generated_at}
    for key in TABLES:
        pts = {pc: v for pc, v in rows[key].items() if pc in pairs}
        first, last = span(p for v in pts.values() for p in v)
        series = {}
        for prod in products:
            series[prod] = {c: dense({p: kg for p, (kg, _u) in pts[(prod, c)].items()}, first, last)
                            for c in ordered({c for p, c in pairs if p == prod}, COUNTRIES)}
        out[key] = {"updatedAt": stamp(u for v in pts.values() for _kg, u in v.values()),
                    "from": first, "through": last, "series": series}
    return out
