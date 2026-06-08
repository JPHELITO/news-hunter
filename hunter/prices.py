"""
Coleta de preços de cotações, commodities e indicadores macro.

Provedores (todos gratuitos, sem auth):
  Brapi.dev  → tickers brasileiros (.SA)
  Yahoo      → tickers internacionais + commodities + US yield
  BCB        → indicadores macro brasileiros (SELIC, IPCA, CDI, GDP)

Cada função update_* faz GET → parse → upsert no Supabase.
Chamadas a partir de hunt.py uma vez por iteração do hunt-loop.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import Iterable

import requests


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

log = logging.getLogger(__name__)

# Yahoo bloqueia/rate-limita IPs de datacenter (ex: GitHub Actions) com 429/401.
# Rotacionamos entre os dois hosts e fazemos retry com backoff.
_YAHOO_HOSTS = [
    "https://query1.finance.yahoo.com",
    "https://query2.finance.yahoo.com",
]
_YAHOO_RETRIES = 3

# ───────────────────────────────────────────────────────────────────────────
# Listas de instrumentos
# ───────────────────────────────────────────────────────────────────────────
# Quotes: 14 tickers (mistura B3 / NYSE / BCS / BMV)
QUOTES_LIST = [
    # (ticker_supabase, name, sector, exchange, provider, query_symbol)
    # Usamos Yahoo para todos (.SA para tickers brasileiros). Brapi tem rate-limit
    # severo no token público; Yahoo v8 chart endpoint é estável e gratuito.
    ("VALE3.SA",    "Vale",            "mining", "B3",   "yahoo", "VALE3.SA"),
    ("CSNA3.SA",    "CSN",             "steel",  "B3",   "yahoo", "CSNA3.SA"),
    ("CMIN3.SA",    "CSN Mineração",   "mining", "B3",   "yahoo", "CMIN3.SA"),
    ("GGBR4.SA",    "Gerdau",          "steel",  "B3",   "yahoo", "GGBR4.SA"),
    ("USIM5.SA",    "Usiminas",        "steel",  "B3",   "yahoo", "USIM5.SA"),
    ("KLBN11.SA",   "Klabin",          "pp",     "B3",   "yahoo", "KLBN11.SA"),
    ("SUZB3.SA",    "Suzano",          "pp",     "B3",   "yahoo", "SUZB3.SA"),
    ("RANI3.SA",    "Irani",           "pp",     "B3",   "yahoo", "RANI3.SA"),
    ("AURA33.SA",   "Aura Minerals",   "mining", "B3",   "yahoo", "AURA33.SA"),
    ("SCCO",        "Southern Copper", "mining", "NYSE", "yahoo", "SCCO"),
    ("TX",          "Ternium",         "steel",  "NYSE", "yahoo", "TX"),
    ("CMPC.SN",     "CMPC",            "pp",     "BCS",  "yahoo", "CMPC.SN"),
    ("COPEC.SN",    "Copec",           "mining", "BCS",  "yahoo", "COPEC.SN"),
    ("GMEXICOB.MX", "Grupo México",    "mining", "BMV",  "yahoo", "GMEXICOB.MX"),
]

# Commodities Yahoo — só Copper e Gold (benchmark global, atualiza a cada 5min).
# As 4 de aço/minério vêm do Platts (PLATTS_COMMODITIES, hunt-playwright 30min).
COMMODITIES_LIST = [
    # (code, name, unit, query_symbol)
    ("COPPER",    "Copper",  "USD/lb",  "HG=F"),
    ("GOLD",      "Gold",    "USD/oz",  "GC=F"),
]

# Commodities Platts — capturadas da watchlist do workspace (símbolo → meta).
# code = chave na tabela commodities; o scraper devolve {símbolo: {'price': float}}.
PLATTS_COMMODITIES = {
    # platts_symbol: (code, name, unit)
    "IODBZ00": ("IRON_ORE",     "Iron Ore 61%",    "USD/dmt"),
    "STHRZ02": ("HRC_CHINA",    "HRC China",       "USD/t"),
    "STCBM00": ("REBAR_TURKEY", "Rebar Turkey",    "USD/t"),
    "PLVHA00": ("MET_COAL",     "Asian Met Coal",  "USD/t"),
}

# Ordem de exibição no dashboard (códigos)
COMMODITIES_ORDER = ["IRON_ORE", "HRC_CHINA", "REBAR_TURKEY", "MET_COAL", "COPPER", "GOLD"]

# Macro Indicators
MACRO_YAHOO = [
    # (code, name, unit, query_symbol)
    ("USD_BRL", "USD/BRL",      "R$", "USDBRL=X"),
    ("US10Y",   "US 10Y Yield", "%",  "^TNX"),
]
MACRO_BCB = [
    # (code, name, unit, BCB_SERIES_ID)
    ("SELIC",    "SELIC Target", "%", 432),    # Meta SELIC
    ("CDI",      "CDI Rate",     "%", 12),     # CDI diário
    ("IPCA_12M", "IPCA 12m",     "%", 13522),  # IPCA acumulado 12m
    ("PIB_QOQ",  "BR GDP QoQ",   "%", 22099),  # PIB trimestral
]


# ───────────────────────────────────────────────────────────────────────────
# Provider clients
# ───────────────────────────────────────────────────────────────────────────
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json",
}


def _fetch_brapi_one(ticker: str) -> dict | None:
    """Brapi v2 — single ticker, token=demo (público)."""
    url = f"https://brapi.dev/api/quote/{ticker}?token=demo"
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        r.raise_for_status()
        data = r.json()
        results = data.get("results") or []
        if not results:
            return None
        q = results[0]
        return {
            "price":      q.get("regularMarketPrice"),
            "change_abs": q.get("regularMarketChange"),
            "change_pct": q.get("regularMarketChangePercent"),
        }
    except Exception as e:
        log.debug("brapi %s falhou: %s", ticker, e)
        return None


def fetch_brapi(tickers: Iterable[str]) -> dict[str, dict]:
    """Brapi em paralelo (uma req por ticker — batch dá 401)."""
    from concurrent.futures import ThreadPoolExecutor
    tickers = list(tickers)
    if not tickers:
        return {}
    out = {}
    with ThreadPoolExecutor(max_workers=6) as ex:
        results = list(ex.map(_fetch_brapi_one, tickers))
    for t, data in zip(tickers, results):
        if data and data.get("price") is not None:
            out[t] = data
    return out


def _fetch_yahoo_chart(symbol: str) -> dict | None:
    """Yahoo v8 chart endpoint — não exige auth. Retorna price + change.

    Resiliente a bloqueio de IP (GitHub Actions): rotaciona hosts query1/query2,
    faz retry com backoff em 429/401/erros de rede.
    """
    last_status = None
    last_err = None
    for attempt in range(_YAHOO_RETRIES):
        host = _YAHOO_HOSTS[attempt % len(_YAHOO_HOSTS)]
        url = f"{host}/v8/finance/chart/{symbol}"
        try:
            r = requests.get(url, headers=HEADERS, timeout=10)
            last_status = r.status_code
            if r.status_code in (429, 401, 403):
                # Rate-limited / bloqueado → backoff e tenta outro host
                time.sleep(1.2 * (attempt + 1))
                continue
            r.raise_for_status()
            data = r.json()
            results = data.get("chart", {}).get("result") or []
            if not results:
                return None
            meta = results[0].get("meta", {})
            price = meta.get("regularMarketPrice")
            prev = meta.get("chartPreviousClose") or meta.get("previousClose")
            if price is None:
                return None
            change_abs = None
            change_pct = None
            if prev not in (None, 0):
                change_abs = round(price - prev, 4)
                change_pct = round((price - prev) / abs(prev) * 100, 3)
            return {
                "price": price,
                "change_abs": change_abs,
                "change_pct": change_pct,
            }
        except Exception as e:
            last_err = e
            time.sleep(0.5 * (attempt + 1))
    log.warning("yahoo chart %s falhou após %d tentativas (status=%s, err=%s)",
                symbol, _YAHOO_RETRIES, last_status, last_err)
    return None


def fetch_yahoo(symbols: Iterable[str]) -> dict[str, dict]:
    """Busca múltiplos símbolos via Yahoo chart endpoint (em paralelo)."""
    from concurrent.futures import ThreadPoolExecutor
    symbols = list(symbols)
    out = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(_fetch_yahoo_chart, symbols))
    for sym, data in zip(symbols, results):
        if data:
            out[sym] = data
    return out


def fetch_bcb(series_id: int) -> dict | None:
    """BCB SGS API — último valor de uma série."""
    url = f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.{series_id}/dados/ultimos/2?formato=json"
    try:
        r = requests.get(url, headers=HEADERS, timeout=8)
        r.raise_for_status()
        rows = r.json()
        if not rows:
            return None
        last = rows[-1]
        prev = rows[-2] if len(rows) >= 2 else None
        try:
            value = float(last["valor"].replace(",", "."))
        except Exception:
            return None
        change_pct = None
        if prev:
            try:
                prev_val = float(prev["valor"].replace(",", "."))
                if prev_val != 0:
                    change_pct = round((value - prev_val) / abs(prev_val) * 100, 3)
            except Exception:
                pass
        return {"value": value, "change_pct": change_pct}
    except Exception as e:
        log.warning("bcb série %d falhou: %s", series_id, e)
        return None


# ───────────────────────────────────────────────────────────────────────────
# Supabase upsert helper
# ───────────────────────────────────────────────────────────────────────────
def _supa_upsert(table: str, rows: list[dict]) -> int:
    """Upsert via REST API com Prefer: resolution=merge-duplicates."""
    if not rows:
        return 0
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    if not url or not key:
        log.warning("Supabase URL/KEY ausente — upsert pulado")
        return 0
    try:
        r = requests.post(
            f"{url}/rest/v1/{table}",
            json=rows,
            headers={
                "apikey": key,
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "Prefer": "resolution=merge-duplicates,return=minimal",
            },
            timeout=15,
        )
        if r.ok:
            log.info("Supabase %s: %d rows upserted", table, len(rows))
            return len(rows)
        log.warning("Supabase %s erro %s: %s", table, r.status_code, r.text[:200])
        return 0
    except Exception as e:
        log.warning("Supabase %s exceção: %s", table, e)
        return 0


# ───────────────────────────────────────────────────────────────────────────
# Public update functions
# ───────────────────────────────────────────────────────────────────────────
def update_quotes() -> int:
    """Atualiza cotações das 14 empresas via Yahoo. Retorna número de rows."""
    yahoo_syms = [q[5] for q in QUOTES_LIST]
    data = fetch_yahoo(yahoo_syms)
    rows = []
    for ticker, name, sector, exchange, _, qsym in QUOTES_LIST:
        d = data.get(qsym)
        if not d or d.get("price") is None:
            continue
        rows.append({
            "ticker":     ticker,
            "name":       name,
            "sector":     sector,
            "exchange":   exchange,
            "price":      d.get("price"),
            "change_pct": d.get("change_pct"),
            "change_abs": d.get("change_abs"),
            "updated_at": _now_iso(),
        })
    return _supa_upsert("quotes", rows)


def update_commodities() -> int:
    """Atualiza preços de commodities. Retorna número de rows escritas."""
    syms = [c[3] for c in COMMODITIES_LIST]
    data = fetch_yahoo(syms)
    rows = []
    for code, name, unit, qsym in COMMODITIES_LIST:
        d = data.get(qsym)
        if not d or d.get("price") is None:
            continue
        rows.append({
            "code":       code,
            "name":       name,
            "unit":       unit,
            "price":      d.get("price"),
            "change_pct": d.get("change_pct"),
            "updated_at": _now_iso(),
        })
    # As 4 commodities Platts (Iron Ore 61%, HRC China, Rebar Turkey, Met Coal)
    # são geridas por update_platts_commodities (hunt-playwright 30min) — não
    # tocadas aqui. Se o Platts cair, o último valor persiste até renovar a sessão.
    return _supa_upsert("commodities", rows)


def update_platts_commodities(platts_prices: dict) -> int:
    """Grava as commodities Platts (IODBZ00/STHRZ02/STCBM00/PLVHA00) na tabela.

    Chamado quando --playwright capturou preços (hunt-playwright, 30 min).
    Mapeia cada símbolo → (code, name, unit) via PLATTS_COMMODITIES.
    Símbolos ausentes são pulados (mantém valor anterior).
    """
    rows = []
    for symbol, (code, name, unit) in PLATTS_COMMODITIES.items():
        d = platts_prices.get(symbol)
        if not d or d.get("price") is None:
            log.info("platts: %s (%s) não capturado — mantém valor atual", symbol, name)
            continue
        rows.append({
            "code":       code,
            "name":       name,
            "unit":       unit,
            "price":      d["price"],
            "change_pct": d.get("change_pct"),
            "updated_at": _now_iso(),
        })
    if rows:
        log.info("platts commodities: %s", {r["name"]: r["price"] for r in rows})
    return _supa_upsert("commodities", rows)




def update_macro() -> int:
    """Atualiza indicadores macro. Retorna número de rows escritas."""
    rows = []
    # Yahoo
    yahoo_syms = [m[3] for m in MACRO_YAHOO]
    yahoo_data = fetch_yahoo(yahoo_syms)
    for code, name, unit, qsym in MACRO_YAHOO:
        d = yahoo_data.get(qsym)
        if not d or d.get("price") is None:
            continue
        rows.append({
            "code":       code,
            "name":       name,
            "unit":       unit,
            "value":      d.get("price"),
            "change_pct": d.get("change_pct"),
            "updated_at": _now_iso(),
        })
    # BCB
    for code, name, unit, series_id in MACRO_BCB:
        d = fetch_bcb(series_id)
        if not d:
            continue
        rows.append({
            "code":       code,
            "name":       name,
            "unit":       unit,
            "value":      d["value"],
            "change_pct": d.get("change_pct"),
            "updated_at": _now_iso(),
        })
    return _supa_upsert("macro_indicators", rows)


# ───────────────────────────────────────────────────────────────────────────
# CLI test
# ───────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    from pathlib import Path
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).parent.parent / ".env")
    except ImportError:
        pass
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    print(f"Quotes:      {update_quotes()} rows")
    print(f"Commodities: {update_commodities()} rows")
    print(f"Macro:       {update_macro()} rows")
