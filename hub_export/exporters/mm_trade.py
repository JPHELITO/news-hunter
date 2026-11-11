"""mm-trade.json (EVENT, conteúdo gzip com nome .json; medido 85,7 KB cru / 39,8 KB gz) — PLANO,
Apêndice G.2.

`{ schemaVersion, generatedAt, updatedAt, from, through,
   series: { imp: { total: { volume, revenue, price, yoyVolumePct }, flat, long, semi }, exp: {…} } }`

SECEX aço por categoria, mensal desde 1997: `secex_imports`/`secex_exports`, só as 4 categorias que a
tela desenha (as de `secat()`, `SMD:1281`): total, flat, long, semi. Arrays alinhados de `from` a
`through` (o mesmo eixo nas 8 séries; `null` = mês sem linha).
  volume        kt, como guardado;
  revenue       US$ mi, como guardado — não é desenhado: vai para o hub poder fazer o preço do
                trimestre/ano = Σ receita ÷ Σ volume (U8, erro 1 corrigido);
  price         US$/t como GUARDADO (nunca receita ÷ volume: diferem no arredondamento em 1.455 linhas);
  yoyVolumePct  `yoy_volume` × 100 com 2 casas (= as 4 casas da fração guardada); `null` nos 12
                primeiros meses (o hub calcula quando falta, como `SMD:2023-2026`).
`updatedAt` = o maior `updated_at` das linhas publicadas (fora do hash).
Nunca saem: as 12 subcategorias, `yoy_revenue`, `working_days`, o `updated_at` de cada linha.
"""
from __future__ import annotations

from .sector_common import dense, need, put, span, stamp

TABLES = {"imp": "secex_imports", "exp": "secex_exports"}      # a ordem do contrato
CATEGORIES = ("total", "flat", "long", "semi")
COLUMNS = ("period", "category", "volume_ktons", "revenue_usd_mn", "price_usd_ton", "yoy_volume",
           "updated_at")
METRICS = ("volume", "revenue", "price", "yoyVolumePct")


def _pct(v):
    return None if v is None else round(v * 100, 2)


def build(src) -> dict:
    con = src.sectors().db("mm")
    need(con, {t: COLUMNS for t in TABLES.values()}, "mm-trade")
    marks = ",".join("?" * len(CATEGORIES))
    got: dict = {}
    stamps, periods = [], []
    for d, table in TABLES.items():
        for p, cat, vol, rev, price, yoy, upd in con.execute(
                f"SELECT period, category, volume_ktons, revenue_usd_mn, price_usd_ton, yoy_volume, "
                f"updated_at FROM {table} WHERE category IN ({marks})", CATEGORIES):
            put(got.setdefault((d, cat), {}), p, (vol, rev, price, _pct(yoy)), f"mm-trade ({table})")
            periods.append(p)
            stamps.append(upd)
    first, last = span(periods)
    series = {}
    for d in TABLES:
        series[d] = {}
        for cat in CATEGORIES:
            pts = got.get((d, cat), {})
            series[d][cat] = {m: dense({p: v[i] for p, v in pts.items()}, first, last)
                              for i, m in enumerate(METRICS)}
    return {"schemaVersion": 1, "generatedAt": src.generated_at, "updatedAt": stamp(stamps),
            "from": first, "through": last, "series": series}
