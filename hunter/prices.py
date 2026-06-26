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
# Quotes: ~47 instrumentos, agrupados como o "painel de cotações" do heatmap
# (Cobertura IBBA + Steel / Iron Ore / Gold / Copper / Rare Earths / P&P / Index).
# Cobertura IBBA + IBOV = default; os demais entram pelo filtro. Tudo via Yahoo.
QUOTES_LIST = [
    # (ticker_supabase, name, sector, exchange, provider, query_symbol)
    # Setores finos: steel / iron_ore / gold / copper / rare_earths / pp / index.
    # ── Cobertura IBBA (default do heatmap) ──────────────────────────────
    ("IBOV",        "Ibovespa",          "index",       "B3",    "yahoo", "^BVSP"),
    ("VALE3.SA",    "Vale",              "iron_ore",    "B3",    "yahoo", "VALE3.SA"),
    ("CSNA3.SA",    "CSN",               "steel",       "B3",    "yahoo", "CSNA3.SA"),
    ("CMIN3.SA",    "CSN Mineração",     "iron_ore",    "B3",    "yahoo", "CMIN3.SA"),
    ("GGBR4.SA",    "Gerdau",            "steel",       "B3",    "yahoo", "GGBR4.SA"),
    ("USIM5.SA",    "Usiminas",          "steel",       "B3",    "yahoo", "USIM5.SA"),
    ("KLBN11.SA",   "Klabin",            "pp",          "B3",    "yahoo", "KLBN11.SA"),
    ("SUZB3.SA",    "Suzano",            "pp",          "B3",    "yahoo", "SUZB3.SA"),
    ("RANI3.SA",    "Irani",             "pp",          "B3",    "yahoo", "RANI3.SA"),
    ("AURA33.SA",   "Aura Minerals",     "gold",        "B3",    "yahoo", "AURA33.SA"),
    ("SCCO",        "Southern Copper",   "copper",      "NYSE",  "yahoo", "SCCO"),
    ("TX",          "Ternium",           "steel",       "NYSE",  "yahoo", "TX"),
    ("CMPC.SN",     "CMPC",              "pp",          "BCS",   "yahoo", "CMPC.SN"),
    ("COPEC.SN",    "Copec",             "pp",          "BCS",   "yahoo", "COPEC.SN"),
    ("GMEXICOB.MX", "Grupo México",      "copper",      "BMV",   "yahoo", "GMEXICOB.MX"),
    # ── Steel ────────────────────────────────────────────────────────────
    ("MT",          "ArcelorMittal",     "steel",       "NYSE",  "yahoo", "MT"),
    ("NUE",         "Nucor",             "steel",       "NYSE",  "yahoo", "NUE"),
    ("CMC",         "Commercial Metals", "steel",       "NYSE",  "yahoo", "CMC"),
    ("GOAU4.SA",    "Metalúrgica Gerdau","steel",       "B3",    "yahoo", "GOAU4.SA"),
    ("STLD",        "Steel Dynamics",    "steel",       "NASDAQ","yahoo", "STLD"),
    # ── Iron ore ─────────────────────────────────────────────────────────
    ("VALE",        "Vale (NYSE)",       "iron_ore",    "NYSE",  "yahoo", "VALE"),
    ("FMG.AX",      "Fortescue",         "iron_ore",    "ASX",   "yahoo", "FMG.AX"),
    ("RIO",         "Rio Tinto",         "iron_ore",    "NYSE",  "yahoo", "RIO"),
    ("BHP",         "BHP Group",         "iron_ore",    "NYSE",  "yahoo", "BHP"),
    ("AAL.L",       "Anglo American",    "iron_ore",    "LSE",   "yahoo", "AAL.L"),
    ("CAP.SN",      "CAP S.A.",          "iron_ore",    "BCS",   "yahoo", "CAP.SN"),
    ("BRAP3.SA",    "Bradespar",         "iron_ore",    "B3",    "yahoo", "BRAP3.SA"),
    # ── Gold ─────────────────────────────────────────────────────────────
    ("AUGO",        "Aura Minerals (Nasdaq)", "gold",   "NASDAQ","yahoo", "AUGO"),
    ("ARIS",        "Aris Mining",       "gold",        "NYSE",  "yahoo", "ARIS"),
    ("BVN",         "Buenaventura",      "gold",        "NYSE",  "yahoo", "BVN"),
    ("AEM",         "Agnico Eagle",      "gold",        "NYSE",  "yahoo", "AEM"),
    ("B",           "Barrick Mining",    "gold",        "NYSE",  "yahoo", "B"),
    ("HOC.L",       "Hochschild Mining", "gold",        "LSE",   "yahoo", "HOC.L"),
    # ── Copper ───────────────────────────────────────────────────────────
    ("ERO",         "Ero Copper",        "copper",      "NYSE",  "yahoo", "ERO"),
    ("CS.TO",       "Capstone Copper",   "copper",      "TSX",   "yahoo", "CS.TO"),
    ("HBM",         "Hudbay Minerals",   "copper",      "NYSE",  "yahoo", "HBM"),
    # ── Rare earths ──────────────────────────────────────────────────────
    ("MEI.AX",      "Meteoric Resources","rare_earths", "ASX",   "yahoo", "MEI.AX"),
    ("VMM.AX",      "Viridis Mining",    "rare_earths", "ASX",   "yahoo", "VMM.AX"),
    ("ARA.TO",      "Aclara Resources",  "rare_earths", "TSX",   "yahoo", "ARA.TO"),
    # ── Pulp & Paper ─────────────────────────────────────────────────────
    ("IP",          "Intl Paper",        "pp",          "NYSE",  "yahoo", "IP"),
    ("UPM.HE",      "UPM-Kymmene",       "pp",          "HEL",   "yahoo", "UPM.HE"),
    ("SW",          "Smurfit WestRock",  "pp",          "NYSE",  "yahoo", "SW"),
    ("STERV.HE",    "Stora Enso",        "pp",          "HEL",   "yahoo", "STERV.HE"),
    # ── Índices ──────────────────────────────────────────────────────────
    ("SPX",         "S&P 500",           "index",       "US",    "yahoo", "^GSPC"),
    ("NASDAQ",      "Nasdaq Composite",  "index",       "US",    "yahoo", "^IXIC"),
    ("MATB11.SA",   "IMAT Materiais (B3)","index",      "B3",    "yahoo", "MATB11.SA"),
    ("GDX",         "Gold Miners ETF",   "index",       "US",    "yahoo", "GDX"),
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
    # platts_symbol: (code, name, unit) — watchlist 'Dashboard' do Platts (curada).
    # Os 4 core mantêm o code amigável (usado p/ ordenar no front). Demais: code = símbolo.
    "IODBZ00": ("IRON_ORE",     "IO Fines 61%",            "USD/dmt"),
    "STHRZ02": ("HRC_CHINA",    "HRC China",               "USD/mt"),
    "STCBM00": ("REBAR_TURKEY", "Rebar Turkey",            "USD/mt"),
    "PLVHA00": ("MET_COAL",     "HCC FOB AUS Premium",     "USD/mt"),
    # Iron ore — grades / diff
    "IOPRM00": ("IOPRM00",      "IO Fines 65%",            "USD/dmt"),
    "IODFE00": ("IODFE00",      "IO Fines 58%",            "USD/dmt"),
    "IOMGD00": ("IOMGD00",      "Diff 60/63.5 Fe",         "USD/dmt"),
    # Iron ore — marcas / blends
    "IOPBQ00": ("IOPBQ00",      "Pilbara Blend Fines",     "USD/dmt"),
    "IOBBA00": ("IOBBA00",      "Brazilian Blend Fines",   "USD/dmt"),
    "IONHA00": ("IONHA00",      "Newman High Grade Fines", "USD/dmt"),
    "IOMAA00": ("IOMAA00",      "Mining Area C Fines",     "USD/dmt"),
    "IOJBA00": ("IOJBA00",      "Jimblebar Fines",         "USD/dmt"),
    # Pellet premium + frete
    "IOBFC04": ("IOBFC04",      "Pellet Premium",          "USD/dmt"),
    "IOFBC00": ("IOFBC00",      "Freight Brazil-China",    "USD/wmt"),
    "IOFAC00": ("IOFAC00",      "Freight Australia-China", "USD/wmt"),
    # Forwards (curva 62% Fe)
    "TSIPQ01": ("TSIPQ01",      "FW +1Q",                  "USD/dmt"),
    "TSIPQ02": ("TSIPQ02",      "FW +2Q",                  "USD/dmt"),
    "TSIPQ03": ("TSIPQ03",      "FW +3Q",                  "USD/dmt"),
    "TSIPY01": ("TSIPY01",      "FW +1y",                  "USD/dmt"),
    # Coal
    "HCCAU00": ("HCCAU00",      "HCC Low Vol",             "USD/mt"),
}

# Ordem de exibição no dashboard (códigos)
COMMODITIES_ORDER = [
    "IRON_ORE", "IOPRM00", "IODFE00", "IOMGD00",
    "IOPBQ00", "IOBBA00", "IONHA00", "IOMAA00", "IOJBA00",
    "IOBFC04", "IOFBC00", "IOFAC00",
    "TSIPQ01", "TSIPQ02", "TSIPQ03", "TSIPY01",
    "HRC_CHINA", "REBAR_TURKEY", "MET_COAL", "HCCAU00",
    "COPPER", "GOLD",
]

# Macro Indicators
MACRO_YAHOO = [
    # (code, name, unit, query_symbol)
    ("USD_BRL", "USD/BRL",      "R$", "USDBRL=X"),
    ("USD_CAD", "USD/CAD",      "C$", "USDCAD=X"),   # câmbio dos exportadores de commodity (Canadá)
    ("USD_AUD", "USD/AUD",      "A$", "USDAUD=X"),   # Austrália
    ("USD_EUR", "USD/EUR",      "€",  "USDEUR=X"),   # Europa
    ("USD_CNY", "USD/CNY",      "¥",  "USDCNY=X"),   # China (driver de minério/aço)
    ("USD_MXN", "USD/MXN",      "MX$", "USDMXN=X"),  # México (Grupo México negocia em MXN)
    ("USD_CLP", "USD/CLP",      "CLP$","USDCLP=X"),  # Chile (CMPC/Copec negociam em CLP)
    ("USD_PEN", "USD/PEN",      "S/",  "USDPEN=X"),  # Peru (cobertura futura)
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


def _fetch_yahoo_chart(symbol: str, range_: str | None = None,
                       interval: str | None = None) -> dict | None:
    """Yahoo v8 chart endpoint — não exige auth. Retorna price + change.

    Se range_/interval forem passados (ex.: '1d'/'5m', '1y'/'1d'), inclui também a série
    histórica: 'series' = [[epoch, close], ...] (closes não-nulos) e 'prev' (close anterior).

    Resiliente a bloqueio de IP (GitHub Actions): rotaciona hosts query1/query2,
    faz retry com backoff em 429/401/erros de rede.
    """
    params = f"?range={range_}&interval={interval}" if (range_ and interval) else ""
    last_status = None
    last_err = None
    for attempt in range(_YAHOO_RETRIES):
        host = _YAHOO_HOSTS[attempt % len(_YAHOO_HOSTS)]
        url = f"{host}/v8/finance/chart/{symbol}{params}"
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
            res0 = results[0]
            meta = res0.get("meta", {})
            price = meta.get("regularMarketPrice")
            prev = meta.get("chartPreviousClose") or meta.get("previousClose")
            if price is None:
                return None
            change_abs = None
            change_pct = None
            if prev not in (None, 0):
                change_abs = round(price - prev, 4)
                change_pct = round((price - prev) / abs(prev) * 100, 3)
            out = {
                "price": price,
                "change_abs": change_abs,
                "change_pct": change_pct,
            }
            if range_ and interval:
                ts = res0.get("timestamp") or []
                quote = (res0.get("indicators", {}).get("quote") or [{}])[0]
                closes = quote.get("close") or []
                out["series"] = [[int(t), round(float(c), 4)]
                                 for t, c in zip(ts, closes) if c is not None]
                out["prev"] = prev
            return out
        except Exception as e:
            last_err = e
            time.sleep(0.5 * (attempt + 1))
    log.warning("yahoo chart %s falhou após %d tentativas (status=%s, err=%s)",
                symbol, _YAHOO_RETRIES, last_status, last_err)
    return None


def fetch_yahoo(symbols: Iterable[str], range_: str | None = None,
                interval: str | None = None) -> dict[str, dict]:
    """Busca múltiplos símbolos via Yahoo chart endpoint (em paralelo).
    range_/interval opcionais → inclui a série histórica em cada resultado."""
    from concurrent.futures import ThreadPoolExecutor
    symbols = list(symbols)
    out = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(lambda s: _fetch_yahoo_chart(s, range_, interval), symbols))
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
    """Atualiza cotações das 14 empresas via Yahoo. Retorna número de rows.

    Pede range=1d&interval=5m: a MESMA chamada traz o preço atual (meta) E a série
    intradiária do dia (sparkline) — zero chamadas extras. change_pct/abs seguem do meta.
    """
    yahoo_syms = [q[5] for q in QUOTES_LIST]
    data = fetch_yahoo(yahoo_syms, range_="1d", interval="5m")
    rows = []
    for ticker, name, sector, exchange, _, qsym in QUOTES_LIST:
        d = data.get(qsym)
        if not d or d.get("price") is None:
            continue
        intraday = {"prev": d.get("prev"), "pts": d["series"]} if d.get("series") else None
        rows.append({
            "ticker":     ticker,
            "name":       name,
            "sector":     sector,
            "exchange":   exchange,
            "price":      d.get("price"),
            "change_pct": d.get("change_pct"),
            "change_abs": d.get("change_abs"),
            "intraday":   intraday,
            "updated_at": _now_iso(),
        })
    return _supa_upsert("quotes", rows)


def update_quote_history(max_age_hours: float = 18.0) -> int:
    """Atualiza a série DIÁRIA (~1 ano) de cada empresa — base p/ WoW/MoM/YoY e o gráfico
    do modal. Auto-throttled: só rebusca tickers cujo `daily` está ausente ou mais velho que
    max_age_hours. Na maioria dos ciclos não faz nada; ~1×/dia rebusca os 14. Retorna nº rows.
    """
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    if not url or not key:
        return 0
    # 1 GET: descobre quais tickers precisam de refresh do diário
    try:
        r = requests.get(
            f"{url}/rest/v1/quotes?select=ticker,daily_updated_at",
            headers={"apikey": key, "Authorization": f"Bearer {key}"}, timeout=15)
        existing = {row["ticker"]: row.get("daily_updated_at") for row in r.json()} if r.ok else {}
    except Exception as e:
        log.warning("quote_history: leitura de estado falhou: %s", e)
        existing = {}

    now = datetime.now(timezone.utc)
    stale = []
    for ticker, name, sector, exchange, _, qsym in QUOTES_LIST:
        du = existing.get(ticker)
        old = True
        if du:
            try:
                age_h = (now - datetime.fromisoformat(du.replace("Z", "+00:00"))).total_seconds() / 3600
                old = age_h > max_age_hours
            except Exception:
                old = True
        if old:
            stale.append((ticker, qsym))
    if not stale:
        return 0

    syms = [qsym for _, qsym in stale]
    daily_data = fetch_yahoo(syms, range_="1y",  interval="1d")   # granular (último ano)
    hist_data  = fetch_yahoo(syms, range_="max", interval="1d")   # mensal (histórico completo)
    # PATCH (não upsert): a linha do ticker já existe (update_quotes roda antes). Upsert
    # parcial tentaria INSERT com name/price nulos (NOT NULL) → 23502. PATCH só altera daily.
    h = {"apikey": key, "Authorization": f"Bearer {key}",
         "Content-Type": "application/json", "Prefer": "return=minimal"}
    n = 0
    for ticker, qsym in stale:
        d1 = (daily_data.get(qsym) or {}).get("series") or []     # ~1 ano diário
        dm = (hist_data.get(qsym) or {}).get("series") or []      # histórico mensal (range=max)
        if not d1 and not dm:
            continue
        # Série única: mensal antigo (< início do diário) + diário do último ano.
        # Cobre WoW/MoM/YoY/YTD (cauda diária) e Max (série inteira) com 1 só campo.
        merged = ([p for p in dm if p[0] < d1[0][0]] + d1) if d1 else dm
        try:
            r = requests.patch(
                f"{url}/rest/v1/quotes?ticker=eq.{ticker}",
                json={"daily": merged, "daily_updated_at": _now_iso()},
                headers=h, timeout=15)
            if r.ok:
                n += 1
            else:
                log.warning("quote_history PATCH %s erro %s: %s", ticker, r.status_code, r.text[:150])
        except Exception as e:
            log.warning("quote_history PATCH %s exceção: %s", ticker, e)
    if n:
        log.info("quote_history: %d séries (mensal+diário) atualizadas", n)
    return n


# Commodities cujo HISTÓRICO vem do Yahoo:
#   IRON_ORE → TIO=F (SGX "Iron Ore 62% Fe CFR China (TSI)" — mesma família do Platts 62%; o preço
#     ao vivo do tile segue Platts, mas a SÉRIE do spread usa o TSI, que casa quase 1:1 em variação %).
#   COPPER/GOLD → HG=F/GC=F (o live delas também é Yahoo → série totalmente consistente).
# As demais Platts (HRC China, rebar, met coal) não têm proxy bom no Yahoo → ACUMULAM pra frente.
COMMODITY_HISTORY_YF = {"IRON_ORE": "TIO=F", "COPPER": "HG=F", "GOLD": "GC=F"}


def update_commodity_history(max_age_hours: float = 18.0) -> int:
    """Mantém `commodities.daily` — a série diária p/ o SPREAD ação×commodity da aba Market.

    - IRON_ORE/COPPER/GOLD: histórico completo via Yahoo (iron ore = TIO=F TSI 62%; copper/gold =
      HG=F/GC=F) — mensal range=max + cauda diária 1y. O spread ação×minério já nasce funcionando.
    - Demais (Platts: HRC China, rebar, met coal): SEM API de histórico → ACUMULA pra frente,
      fazendo append do assessment do dia (dedup por data). A série cresce a partir do deploy.
    Auto-throttled por `daily_updated_at` (~1×/dia). Espelha o padrão PATCH de update_quote_history.
    Retorna nº de séries atualizadas.
    """
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    if not url or not key:
        return 0
    try:
        r = requests.get(
            f"{url}/rest/v1/commodities?select=code,price,assessed_at,daily,daily_updated_at",
            headers={"apikey": key, "Authorization": f"Bearer {key}"}, timeout=15)
        rows = r.json() if r.ok else []
    except Exception as e:
        log.warning("commodity_history: leitura de estado falhou: %s", e)
        return 0

    now = datetime.now(timezone.utc)
    stale = []
    for row in rows:
        du = row.get("daily_updated_at")
        old = True
        if du:
            try:
                age_h = (now - datetime.fromisoformat(du.replace("Z", "+00:00"))).total_seconds() / 3600
                old = age_h > max_age_hours
            except Exception:
                old = True
        if old:
            stale.append(row)
    if not stale:
        return 0

    # backfill Yahoo das que têm ticker (cobre/ouro) — 1 par de chamadas
    yf_syms = [COMMODITY_HISTORY_YF[r["code"]] for r in stale if r.get("code") in COMMODITY_HISTORY_YF]
    daily_yf = fetch_yahoo(yf_syms, range_="1y",  interval="1d") if yf_syms else {}
    hist_yf  = fetch_yahoo(yf_syms, range_="max", interval="1d") if yf_syms else {}

    h = {"apikey": key, "Authorization": f"Bearer {key}",
         "Content-Type": "application/json", "Prefer": "return=minimal"}
    n = 0
    for row in stale:
        code = row.get("code")
        if code in COMMODITY_HISTORY_YF:
            yf = COMMODITY_HISTORY_YF[code]
            d1 = (daily_yf.get(yf) or {}).get("series") or []
            dm = (hist_yf.get(yf) or {}).get("series") or []
            if not d1 and not dm:
                continue
            merged = ([p for p in dm if p[0] < d1[0][0]] + d1) if d1 else dm
        else:
            # acumula: append do assessment do dia (Platts não tem histórico)
            price, assessed = row.get("price"), row.get("assessed_at")
            if price is None or not assessed:
                continue
            try:
                ep = int(datetime.fromisoformat(str(assessed)[:10] + "T00:00:00+00:00").timestamp())
            except Exception:
                continue
            daily = [p for p in (row.get("daily") or []) if p and p[0] != ep]
            daily.append([ep, round(float(price), 4)])
            daily.sort(key=lambda p: p[0])
            merged = daily[-1000:]
        try:
            r = requests.patch(
                f"{url}/rest/v1/commodities?code=eq.{code}",
                json={"daily": merged, "daily_updated_at": _now_iso()},
                headers=h, timeout=15)
            if r.ok:
                n += 1
            else:
                log.warning("commodity_history PATCH %s erro %s: %s", code, r.status_code, r.text[:150])
        except Exception as e:
            log.warning("commodity_history PATCH %s exceção: %s", code, e)
    if n:
        log.info("commodity_history: %d séries atualizadas (Yahoo backfill + accrual Platts)", n)
    return n


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
            "assessed_at": _now_iso()[:10],   # Yahoo = ao vivo → data de hoje (front mostra "Live")
        })
    # As 4 commodities Platts (Iron Ore 61%, HRC China, Rebar Turkey, Met Coal)
    # são geridas por update_platts_commodities (hunt-playwright 30min) — não
    # tocadas aqui. Se o Platts cair, o último valor persiste até renovar a sessão.
    return _supa_upsert("commodities", rows)


def update_platts_commodities(platts_prices: dict) -> int:
    """Grava as commodities Platts na tabela — a watchlist 'Dashboard' CURADA.

    Chamado quando --playwright capturou preços (hunt-playwright, 30 min). Itera
    PLATTS_COMMODITIES (os símbolos registrados = a watchlist do analista) e grava
    cada um que foi capturado (preço via feed de rede e/ou DOM). Escopo curado evita
    puxar ruído de outras abas do workspace. Símbolos não capturados são pulados
    (mantém o valor anterior). Para ADICIONAR/REMOVER um indicador: editar
    PLATTS_COMMODITIES aqui + _PRICE_SYMBOLS no platts_scraper.
    """
    rows = []
    for symbol, (code, name, unit) in PLATTS_COMMODITIES.items():
        d = platts_prices.get(symbol)
        if not d or d.get("price") is None:
            log.info("platts: %s (%s) não capturado — mantém valor atual", symbol, name)
            continue
        row = {
            "code":       code,
            "name":       name,
            "unit":       unit,
            "price":      d["price"],
            "change_pct": d.get("change_pct"),
            "updated_at": _now_iso(),
        }
        if d.get("assessed_at"):           # data real do assessment (coluna Assessed Date)
            row["assessed_at"] = d["assessed_at"]
        rows.append(row)
    if rows:
        log.info("platts commodities (%d/%d): %s", len(rows), len(PLATTS_COMMODITIES),
                 {r["name"]: r["price"] for r in rows})
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
