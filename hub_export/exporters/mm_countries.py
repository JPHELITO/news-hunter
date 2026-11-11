"""mm-countries-<dir>-<tab>.json — conjunto FIXO de 8 (dir = imp | exp; tab = total | flat | long |
semi), EVENT, conteúdo gzip com nome .json; medido 105,5–734,0 KB cru / 35,7–273,2 KB gz — PLANO,
Apêndice G.5 (O45).

`{ schemaVersion, generatedAt, updatedAt, direction, tab, from, through,
   countries: ["Afeganistão", "África do Sul", …],
   categories: { flat: [ [<país>, [ [<início>, [kt…]], … ]], … ], long: [], semi: [] } }`

O gráfico "by Country" de Imports/Exports (`SMD:1752-1939`) trabalha em mês/trimestre/ano, com janela
livre de meses e top-5 recalculado na janela → a série MENSAL × PAÍS × CATEGORIA inteira, uma aba de
produto por arquivo (a aba Total pede só os três "pais"; cada aba leva o seu pai, o botão "Total" da
aba). Só volume (kt), como guardado — a mesma linha do `SELECT period,country,volume_ktons FROM
secex_country WHERE direction=? AND category=? ORDER BY period,country` da página (`SMD:1091`).
  countries   o dicionário: os países com linha nas categorias do arquivo, em ordem de código Unicode
              (= a binária do SQLite, a da página nos empates do ranking: iterar na ordem dada, SEM
              localeCompare); nomes em português, como o MDIC publica (a tradução é do hub, O39);
  categories  na ordem de `SCTY_CATS` (`SMD:1085-1087`); cada série = `[índice do país, corridas]`
              (sector_common: trecho novo depois de mais de 3 meses sem linha; `null` = mês sem
              linha; `0` = linha com zero); categoria sem linha = `[]`.
`from`/`through` = o primeiro e o último mês da TABELA (o mesmo eixo nos 8); `updatedAt` = o maior
`updated_at` das linhas do arquivo (fora do hash). Nunca saem: `revenue_usd_mn` e o `updated_at` de
cada linha. Decodificados (`expand_runs`), os 8 devolvem as 274.963 linhas do `SELECT` da página, com
os mesmos bits de float (teste com o banco de fixture; rodada manual com o banco real).
"""
from __future__ import annotations

from ..sectors import COUNTRY_TABS
from .sector_common import encode_runs, label, month_index, need, span, stamp

COLUMNS = ("period", "direction", "category", "country", "volume_ktons", "updated_at")


def build(src, direction: str, tab: str) -> dict:
    if direction not in ("imp", "exp") or tab not in COUNTRY_TABS:
        raise ValueError(f"mm-countries: combinação desconhecida {direction}/{tab}")
    con = src.sectors().db("mm")
    need(con, {"secex_country": COLUMNS}, f"mm-countries-{direction}-{tab}")
    first, last = con.execute("SELECT MIN(period), MAX(period) FROM secex_country").fetchone()
    first, last = span(x for x in (first, last) if x is not None)
    base = month_index(first) if first else 0
    by_cat: dict[str, dict[str, dict[int, tuple]]] = {}
    stamps: set = set()
    month: dict[str, int] = {}                # período → índice (358 distintos em 274 mil linhas)
    for cat in COUNTRY_TABS[tab]:
        pts: dict[str, dict[int, tuple]] = {}
        # a chave primária (direction, category, period, country) garante uma linha por país × mês
        for p, ctry, vol, upd in con.execute(
                "SELECT period, country, volume_ktons, updated_at FROM secex_country "
                "WHERE direction=? AND category=? ORDER BY period, country", (direction, cat)):
            stamps.add(upd)
            if vol is None:
                continue                      # a página não cria a chave (`mergeOriginRows`)
            i = month.get(p)
            if i is None:
                i = month[p] = month_index(p) - base
            row = pts.get(ctry)
            if row is None:
                row = pts[label(ctry, "mm-countries (país)")] = {}
            row[i] = (vol,)
        by_cat[cat] = pts
    names = sorted({c for pts in by_cat.values() for c in pts})
    idx = {n: i for i, n in enumerate(names)}
    categories = {cat: [[idx[c], encode_runs(pts[c], 1)] for c in sorted(pts, key=idx.__getitem__)]
                  for cat, pts in by_cat.items()}
    return {"schemaVersion": 1, "generatedAt": src.generated_at, "updatedAt": stamp(stamps),
            "direction": direction, "tab": tab, "from": first, "through": last,
            "countries": names, "categories": categories}
