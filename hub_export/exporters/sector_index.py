"""mm-index.json e pp-index.json (EVENT, texto, < 1 KB) — PLANO, Apêndice G.1, G.6 e G.11.

`{ schemaVersion, generatedAt,
   source: { blob, commit, committedAt },
   dataThrough: { secex: "2026-10", iabr: "2026-09", inda: "2026-09", customs: "2026-09" },
   raw: [ { table: "secex_imports", rows: 5643 }, … ] }`

O índice de cada setor:
  source       o banco de origem: `blob` = SHA do arquivo no repositório do site (o marcador da
               partida a frio do exportador), `commit`/`committedAt` = o último commit que o tocou
               (UTC). Opacos: nunca a URL do repositório. Sem rede (`HUB_EXPORT_SECTORS_DB_DIR` no
               --out), `commit`/`committedAt` saem `null`. No P&P, `committedAt` é o carimbo do dado
               (as tabelas não têm `updated_at`, T15);
  dataThrough  o "Data through" da barra de cada página (`_asOfBadge`): `MAX(period)` de cada fonte,
               como o site (aço `SMD:1548-1549`: SECEX = `secex_imports`, IABr = `iabr_production`,
               INDA = `inda_distribution`, Customs KR/CN = `pred_exports`; P&P `PPD:1188-1191`: SECEX =
               `secex_pulp_port`, Ibá = `iba_paper`, Empapel = `empapel`, GACC = `gacc_woodchips` com
               `volume_bdmt > 0`). É o mês do DADO — o hub mede "desatualizado" por ele, nunca pelo
               `generatedAt` (G.13). Fonte sem linha → `null`;
  raw          a base bruta (G.11): as tabelas da lista `RAW_TABLES` da página que existem no banco,
               na ordem dela, com a contagem de linhas (o painel "⤓ Data" mostra "N rows" antes de
               baixar).
Vai POR ÚLTIMO, só com a fila do blob vazia (sectors.py): muda — e vai — a cada blob novo, mesmo sem
dado novo (~2×/mês), porque é ele que poupa o download na partida a frio.
"""
from __future__ import annotations

from ..sectors import INDEX, RAW_TABLES
from .sector_common import month_index, month_label, need, tables

DATA_THROUGH = {
    "mm": (("secex", "secex_imports", ""), ("iabr", "iabr_production", ""),
           ("inda", "inda_distribution", ""), ("customs", "pred_exports", "")),
    "pp": (("secex", "secex_pulp_port", ""), ("iba", "iba_paper", ""), ("empapel", "empapel", ""),
           ("gacc", "gacc_woodchips", " WHERE volume_bdmt > 0")),
}


def build(src, sector: str) -> dict:
    store = src.sectors()
    info = store.source(sector)                 # (no envio, roda o plano: a fila fica conhecida)
    store.index_guard(sector)
    con = store.db(sector)
    spec = {}
    for _key, table, where in DATA_THROUGH[sector]:
        spec.setdefault(table, ["period"] + (["volume_bdmt"] if where else []))
    need(con, spec, INDEX[sector][:-5])
    through = {}
    for key, table, where in DATA_THROUGH[sector]:
        last = con.execute(f"SELECT MAX(period) FROM {table}{where}").fetchone()[0]
        through[key] = month_label(month_index(last)) if last is not None else None
    have = tables(con)
    raw = [{"table": t, "rows": con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]}
           for t in RAW_TABLES[sector] if t in have]
    return {"schemaVersion": 1, "generatedAt": src.generated_at,
            "source": {"blob": info["blob"], "commit": info.get("commit"),
                       "committedAt": info.get("committedAt")},
            "dataThrough": through, "raw": raw}
