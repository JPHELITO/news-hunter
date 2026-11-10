"""mw-companies.json (COLD, texto, ~8 KB) — PLANO, Apêndice F.6.

`{ schemaVersion, generatedAt, lendingThrough, companies: [...], shortLatest: [...] }` — as empresas
do Market Watch (as 4 páginas: chips, frescor, visão "Todas") e os FATOS do cabeçalho da Performance
(decisão O21: a Performance lê 1 arquivo pequeno, não 4).

companies[]  na ordem de `display_order` (a da RPC `get_mw_companies()`, que já tira a empresa oculta):
             `ticker`, `name`, `shareClass` (ON|PN|PNA|PNB|UNT|BDR), `lendingTickers`, `quote` (=
             `yahoo_symbol`, o link para a Performance), `sector`, `order`, `capital` e `facts`.
  capital    `get_mw_share_capital()`: `refDate`, `docVersion`, `sharesOn/Pn/Total`, `floatOn/Pn/Total`,
             `pctFloatTotal`, `floatByClass` ({"PNA": …}); `null` quando a empresa não tem.
  facts      porte de `_mwFacts` (market.html, origin/main f636222, linhas 1723-1735), calculado na
             exportação (o "hoje" é o do exportador, em UTC):
             • shortPctFloat / shortAsOf — a linha do `get_mw_short_latest` do papel PRINCIPAL (ou a 1ª
               da empresa) ÷ o free float da CLASSE DA EMPRESA (`_mwClass`: UNT = (ON+PN)/5; ON; PNA/PNB
               pelo `float_by_class`, senão o PN; PN; o resto, o total) × 100, e a data dessa linha;
               os dois `null` quando não dá para calcular (sem aluguel, sem capital, float 0);
             • buybackActive — algum programa com `status` casando /andamento|ativo|active|open/i; sem
               status, `expires_on` ≥ hoje (o site olhava `deadline`/`end_date`, que não existem:
               defeito M7 corrigido);
             • insidersNet12m / insidersTrades12m — os negócios (`move_type` casando /compra|venda/i) com
               `ref_month` ≥ 1º dia do mês de (hoje − 12 meses): Σ(+qtd compra, −qtd venda) e quantos;
               `null` nos dois quando a empresa não tem movimentação nenhuma na janela de 14 meses.
shortLatest[] `get_mw_short_latest()` tal qual (um por papel, a ordem da RPC: empresa, papel).
lendingThrough  a maior `refDate` do shortLatest ("Lending data through …").

NUNCA saem `cnpj`, `cvm_code`, `b3_code`, `is_visible`, `updated_at` (nem são pedidos: `select`
explícito; o registro recusa as chaves neste arquivo).
"""
from __future__ import annotations

import math
import re
from datetime import date, timedelta

from ..encode import iso_date, num

COMPANY_COLS = "ticker,name,share_class,lending_tickers,yahoo_symbol,sector,display_order"
COMPANY_ORDER = "display_order.asc,ticker.asc"
CAPITAL_COLS = ("company,ref_date,doc_version,shares_on,shares_pn,shares_total,float_on,float_pn,"
                "float_total,pct_float_total,float_by_class")
LATEST_COLS = "ticker,company,ref_date,qty_total,value_brl,avg_price,rate_taker_avg,qty_1m,rate_1m,days"
LATEST_ORDER = "company.asc,ticker.asc"
FORBIDDEN = frozenset({"cnpj", "cvmCode", "cvm_code", "b3Code", "b3_code", "isVisible", "is_visible",
                       "updated_at"})
_ACTIVE = re.compile(r"andamento|ativo|active|open", re.I)
_TRADE = re.compile(r"compra|venda", re.I)
_BUY = re.compile(r"compra", re.I)


def _text(v) -> str | None:
    return (v.strip() or None) if isinstance(v, str) else None


def _int(v) -> int | None:
    v = num(v)
    return int(v) if v is not None and float(v).is_integer() else None


def company(r: dict) -> dict | None:
    """Uma linha da `get_mw_companies()` → a empresa do arquivo (sem capital e fatos)."""
    t = _text(r.get("ticker"))
    if not t:
        return None
    lt = r.get("lending_tickers")
    return {"ticker": t, "name": _text(r.get("name")), "shareClass": _text(r.get("share_class")),
            "lendingTickers": [x.strip() for x in lt if isinstance(x, str) and x.strip()]
            if isinstance(lt, list) else [],
            "quote": _text(r.get("yahoo_symbol")), "sector": _text(r.get("sector")),
            "order": _int(r.get("display_order"))}


def capital(r: dict) -> dict:
    """Uma linha da `get_mw_share_capital()` → o `capital` da empresa."""
    fbc = r.get("float_by_class")
    by = {}
    if isinstance(fbc, dict):
        for k in sorted(k for k in fbc if isinstance(k, str) and k.strip()):
            v = num(fbc[k])
            if v is not None:
                by[k.strip()] = v
    return {"refDate": iso_date(r.get("ref_date")), "docVersion": _int(r.get("doc_version")),
            "sharesOn": num(r.get("shares_on")), "sharesPn": num(r.get("shares_pn")),
            "sharesTotal": num(r.get("shares_total")), "floatOn": num(r.get("float_on")),
            "floatPn": num(r.get("float_pn")), "floatTotal": num(r.get("float_total")),
            "pctFloatTotal": num(r.get("pct_float_total")), "floatByClass": by}


def latest(r: dict) -> dict | None:
    """Uma linha da `get_mw_short_latest()` → um item do `shortLatest`."""
    t, co = _text(r.get("ticker")), _text(r.get("company"))
    if not t or not co:
        return None
    return {"ticker": t, "company": co, "refDate": iso_date(r.get("ref_date")),
            "qtyTotal": num(r.get("qty_total")), "valueBrl": num(r.get("value_brl")),
            "avgPrice": num(r.get("avg_price")), "rateTakerAvg": num(r.get("rate_taker_avg")),
            "qty1m": num(r.get("qty_1m")), "rate1m": num(r.get("rate_1m")), "days": _int(r.get("days"))}


def _round(x: float) -> int:
    """O `Math.round` do JavaScript (meio arredonda para cima), não o arredondamento bancário."""
    return math.floor(x + 0.5)


def class_float(co: dict, cap: dict | None):
    """O free float da CLASSE da empresa (`_mwClass` da market.html) — o denominador do % do float."""
    if not cap:
        return None
    cls = co.get("shareClass") or "ON"
    if cls == "UNT":
        return _round(((cap.get("floatOn") or 0) + (cap.get("floatPn") or 0)) / 5) or None
    if cls == "ON":
        return cap.get("floatOn")
    if cls in ("PNA", "PNB"):
        f = (cap.get("floatByClass") or {}).get(cls)
        return f if f is not None else cap.get("floatPn")
    if cls == "PN":
        return cap.get("floatPn")
    return cap.get("floatTotal")


def months_back(day: str, n: int) -> str:
    """O 1º dia do mês de (dia − n meses), `AAAA-MM-01` — transbordando como o `setMonth` do
    JavaScript (29/02/2028 − 12 meses = 01/03/2027)."""
    d = date.fromisoformat(day)
    y, m = d.year, d.month - n
    while m <= 0:
        m += 12
        y -= 1
    cut = date(y, m, 1) + timedelta(days=d.day - 1)
    return f"{cut.year:04d}-{cut.month:02d}-01"


def facts(co: dict, cap: dict | None, latest_rows: list[dict], buybacks: list[dict],
          insider_rows: list[list], today: str) -> dict:
    """Os 4 fatos de uma empresa (regras na docstring do módulo). `insider_rows` = as linhas do
    mw-insiders.json (colunas de mw_insiders.COLUMNS) da empresa; `today` = `AAAA-MM-DD` (UTC)."""
    from .mw_insiders import COLUMNS as IC
    ticker = co["ticker"]
    lat = [r for r in latest_rows if r.get("company") == ticker]
    cur = next((r for r in lat if r.get("ticker") == ticker), lat[0] if lat else None)
    fl = class_float(co, cap)
    pf = None
    if cur is not None and fl and cur.get("qtyTotal") is not None:
        pf = cur["qtyTotal"] / fl * 100

    def active(b) -> bool:
        st = b.get("status") or ""
        if st:
            return bool(_ACTIVE.search(st))
        end = b.get("expiresOn") or ""
        return bool(end) and end[:10] >= today
    progs = [b for b in buybacks if b.get("company") == ticker]

    i_ref, i_move, i_qty = IC.index("refMonth"), IC.index("moveType"), IC.index("qty")
    rows = [r for r in insider_rows if r and r[0] == ticker]
    net = trades = None
    if rows:
        start = months_back(today, 12)
        net = trades = 0
        for r in rows:
            move = r[i_move] or ""
            if not _TRADE.search(move) or (r[i_ref] or "") < start:
                continue
            trades += 1
            net += (1 if _BUY.search(move) else -1) * (r[i_qty] or 0)
    return {"shortPctFloat": pf, "shortAsOf": cur.get("refDate") if pf is not None else None,
            "buybackActive": any(active(b) for b in progs),
            "insidersNet12m": net, "insidersTrades12m": trades}


def build(src) -> dict:
    mw = src.mw()
    cos = mw.companies()
    caps = mw.capital()
    lat = mw.short_latest()
    bbs = mw.buybacks()["items"]
    ins = mw.insiders()["rows"]
    today = src.now.date().isoformat()
    out = []
    for c in cos:
        cap = caps.get(c["ticker"])
        out.append({**c, "capital": cap,
                    "facts": facts(c, cap, lat, bbs, ins, today)})
    dates = [r["refDate"] for r in lat if r.get("refDate")]
    return {"schemaVersion": 1, "generatedAt": src.generated_at,
            "lendingThrough": max(dates) if dates else None,
            "companies": out, "shortLatest": lat}
