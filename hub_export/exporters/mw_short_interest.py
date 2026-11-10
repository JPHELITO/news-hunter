"""mw-short-interest.json (COLD com sonda, conteúdo gzip com nome .json) — PLANO, Apêndice F.7.

`{ schemaVersion, generatedAt, from, columns: ["refDate", "qtyTotal", "rateTakerAvg"],
   series: { <papel>: { company, rows: [[refDate, qtyTotal, rateTakerAvg], …] } } }`

A visão "Todas" do Short interest: os últimos 100 dias de CADA papel (a tela usa 90 — a tendência e o
"vs. 3-month range" contam do "hoje" do navegador), em data crescente. `from` = o 1º dia do recorte =
a maior data do aluguel − 100 dias (ancorado no DADO, não no relógio: o arquivo só muda quando sai um
pregão novo — e sempre cobre os 90 dias que a tela pede, porque a maior data nunca passa de hoje).
Papéis na ordem das empresas (`display_order`), o papel principal da empresa primeiro, depois A→Z.

A série vem do MESMO cache das famílias `mw-short-<empresa>.json` (mw.py): a RPC
`get_mw_short_interest` ordena por data CRESCENTE e, acima de 1.000 linhas, o PostgREST cortaria os
dias mais NOVOS — o exportador lê paginado (defeito M2 do site, latente).
"""
from __future__ import annotations

from datetime import date, timedelta

from ..encode import iso_date, num

# o que se pede à RPC (uma linha por papel × dia; `company` e `ticker` só agrupam)
COLS = ("ticker,company,ref_date,qty_total,value_brl,avg_price,rate_taker_avg,rate_taker_min,"
        "rate_taker_max,rate_donor_avg,contracts_day,qty_day,value_day,source")
ORDER = "ref_date.asc,ticker.asc"                     # = a da RPC; (papel, dia) é a chave do banco
# as colunas da série inteira (a família, F.8) — a 1ª é sempre a data
FULL_COLUMNS = ["refDate", "qtyTotal", "valueBrl", "avgPrice", "rateTakerAvg", "rateTakerMin",
                "rateTakerMax", "rateDonorAvg", "contractsDay", "qtyDay", "valueDay", "source"]
COLUMNS = ["refDate", "qtyTotal", "rateTakerAvg"]
_PICK = [FULL_COLUMNS.index(c) for c in COLUMNS]
OVERVIEW_DAYS = 100
FORBIDDEN = frozenset({"cnpj", "cvm_code", "b3_code", "qtyRegistro", "qty_registro", "qtyD0",
                       "qty_d0", "qtyD1", "qty_d1", "updated_at"})


def _text(v) -> str | None:
    return (v.strip() or None) if isinstance(v, str) else None


def row(r: dict) -> tuple[str, str, list] | None:
    """Uma linha da RPC → (papel, empresa, linha nas colunas de FULL_COLUMNS); None sem data/papel."""
    t, co, day = _text(r.get("ticker")), _text(r.get("company")), iso_date(r.get("ref_date"))
    if not t or not co or not day:
        return None
    return t, co, [day, num(r.get("qty_total")), num(r.get("value_brl")), num(r.get("avg_price")),
                   num(r.get("rate_taker_avg")), num(r.get("rate_taker_min")),
                   num(r.get("rate_taker_max")), num(r.get("rate_donor_avg")),
                   num(r.get("contracts_day")), num(r.get("qty_day")), num(r.get("value_day")),
                   _text(r.get("source"))]


def ticker_order(company: str, tickers) -> list[str]:
    """O papel principal da empresa primeiro, depois A→Z (a ordem dos chips do site)."""
    rest = sorted(t for t in tickers if t != company)
    return ([company] if company in tickers else []) + rest


def overview_from(last_day: str | None) -> str | None:
    if not last_day:
        return None
    return (date.fromisoformat(last_day) - timedelta(days=OVERVIEW_DAYS)).isoformat()


def build(src) -> dict:
    mw = src.mw()
    cos = mw.companies()
    series = mw.lending()                              # empresa → {papel: linhas}
    days = [rows[-1][0] for co in series.values() for rows in co.values() if rows]
    start = overview_from(max(days) if days else None)
    out = {}
    for c in cos:
        by = series.get(c["ticker"]) or {}
        for t in ticker_order(c["ticker"], by):
            rows = [[r[i] for i in _PICK] for r in by[t] if start is None or r[0] >= start]
            if rows:
                out[t] = {"company": c["ticker"], "rows": rows}
    return {"schemaVersion": 1, "generatedAt": src.generated_at, "from": start,
            "columns": list(COLUMNS), "series": out}
