"""stock-guide.json (COLD com sonda, conteúdo gzip com nome .json) — PLANO, Apêndice H.1.

    { schemaVersion, generatedAt, updatedAt, source: {stamp, companies, peers}, years: {y1, y2}, footnote,
      companies: [...], peers: [...] }

A Comp Table (16 empresas) e os Global Peers (18) do Stock Guide do site, com os rótulos dos anos — o que
NÃO depende do preço. Os múltiplos ao vivo (EV/EBITDA, P/CE, yields, upside, valor de mercado, agregado
do setor, "n.m.") são conta do hub, com o preço e o câmbio do `live.json` (H.8).

ORIGEM: as RPCs do site com a chave de serviço = a visão de CLIENTE (`is_admin()` é falso para ela):
`get_stock_guide_comps()` (a empresa OCULTA vem só com nome, visibilidade e ordem), `get_stock_guide_config()`
e `get_stock_guide_global_peers()`. Nunca as tabelas (salvo a sonda, pages.py).

CAMPO A CAMPO:
  companies  por (`display_order`, código), a ordem da RPC:
             • visível: `code` (= o ticker do Stock Guide; a palavra `key` é proibida), `name`, `order`,
               `restricted: false`, `sector`, `quote` (= `yahoo_symbol`: o papel do preço ao vivo, casado
               EXATO no hub), `rating` (OP/MP/UP; outra coisa vira null), `targetPrice`, `shares` (milhões),
               `tradeCcy`, `baseCcy`, `fxToBase`, `fxToUsd` (os fatores do modelo, reserva do câmbio ao vivo),
               `modelUrl` e, por ano (`y1`, `y2`), `ebitda`, `netDebt`, `evAdjustment`, `netIncome`,
               `cashEarnings`, `dividends`, `ocf` — números como o banco guarda (milhões na moeda-base);
             • OCULTA (`is_visible` não é `true`): SÓ `code`, `name`, `order` e `restricted: true` (o rodapé
               "Restricted coverage" do site); nenhum número.
  peers      por (`display_order`, nome): `name`, `order`, `country`, `sector`, `quote` e, por ano,
             `evEbitda`, `netDebtEbitda`, `pe`, `pce` (consenso da planilha, instantâneo) e `divYieldPct`
             (o banco guarda FRAÇÃO: × 100, arredondado em 12 casas só contra o ruído binário).
  years      `{y1, y2}` = os rótulos do /admin ("2026E"/"2027E"; null → o hub usa "26E"/"27E", como o site).
  footnote   o rodapé do analista (texto livre: e-mail/telefone mascarados, URL pelo filtro); null = o hub
             escreve o texto padrão.
  modelUrl   CONSTANTE do exportador = o porte do `MODEL_URL` do site (stock-guide.html, `cffdadc`, 18 chaves,
             com as legadas VALE3 e AURA33 e o slug AURA33 da AUGO), procurada pelo código e pelo código sem o
             sufixo de bolsa, como `modelCell`; passa pelo filtro de URL. A coluna `model_url` do banco não é
             lida (a tela do site também não a usa).
  source     o MARCADOR da partida a frio (`{stamp, companies, peers}` = a sonda) — DENTRO do hash: uma nova
             subida idêntica da planilha regrava os `updated_at` e vira 1 envio a mais, e a partida a frio
             seguinte volta a não ler o banco. `updatedAt` = o carimbo (fora do hash).

FLAGS no dado (O50, com a regra `hub_<chave>`): `stock_guide` desligada → `companies: []` e `peers: []`, nada
lido; `stock_guide_peers` explicitamente `false` → `peers: []` (a flag é fail-open no site).
Nunca saem (o registro proíbe as chaves neste arquivo): `updated_by`, `is_visible`, `last_update`, as colunas
que a tela não usa (`capex_*`, `net_revenues_*`, `volumes_*`, `fcfe_*`…), `is_aggregate`, `is_live`,
`mkt_cap_usd`, os drivers e a malha antiga.
"""
from __future__ import annotations

import re

from ..encode import num
from ..freetext import clean_text
from ..urls import safe_url

COMPS_COLS = ("ticker,company_name,is_visible,display_order,sector,yahoo_symbol,shares_outstanding,"
              "target_price,recommendation,trade_ccy,base_ccy,fx_to_base,fx_to_usd,"
              "ebitda_y1,ebitda_y2,net_debt_y1,net_debt_y2,ev_adjustment_y1,ev_adjustment_y2,"
              "net_income_y1,net_income_y2,cash_earnings_y1,cash_earnings_y2,dividends_y1,dividends_y2,"
              "ocf_y1,ocf_y2")
CONFIG_COLS = "y1_label,y2_label,comps_footnote"
PEER_COLS = ("company,display_order,country,sector,yahoo_symbol,ev_ebitda_y1,ev_ebitda_y2,"
             "net_debt_ebitda_y1,net_debt_ebitda_y2,pe_y1,pe_y2,pce_y1,pce_y2,div_yield_y1,div_yield_y2")
YEARS = ("y1", "y2")
YEAR_FIELDS = (("ebitda", "ebitda"), ("netDebt", "net_debt"), ("evAdjustment", "ev_adjustment"),
               ("netIncome", "net_income"), ("cashEarnings", "cash_earnings"), ("dividends", "dividends"),
               ("ocf", "ocf"))
PEER_FIELDS = (("evEbitda", "ev_ebitda"), ("netDebtEbitda", "net_debt_ebitda"), ("pe", "pe"), ("pce", "pce"))
RATINGS = ("OP", "MP", "UP")
COMPANY_KEYS = ("code", "name", "order", "restricted", "sector", "quote", "rating", "targetPrice",
                "shares", "tradeCcy", "baseCcy", "fxToBase", "fxToUsd", "modelUrl", "y1", "y2")
RESTRICTED_KEYS = ("code", "name", "order", "restricted")
YEAR_KEYS = tuple(k for k, _ in YEAR_FIELDS)
PEER_KEYS = ("name", "order", "country", "sector", "quote", "y1", "y2")
PEER_YEAR_KEYS = tuple(k for k, _ in PEER_FIELDS) + ("divYieldPct",)
SOURCE_KEYS = ("stamp", "companies", "peers")
FORBIDDEN = frozenset({"updated_by", "updatedBy", "updated_at", "is_visible", "isVisible", "last_update",
                       "lastUpdate", "model_url", "is_aggregate", "isAggregate", "is_live", "isLive",
                       "mkt_cap_usd", "mktCapUsd", "capex_y1", "capex_y2", "net_revenues_y1",
                       "net_revenues_y2", "volumes_y1", "volumes_y2", "fcfe_y1", "fcfe_y2", "drivers",
                       "assumptions_note", "assumptionsNote"})

# Link do modelo no portal Itaú BBA — o `MODEL_URL` do site (stock-guide.html:451-470, origin/main cffdadc),
# por código; a AUGO usa o slug AURA33 do portal; VALE3 e AURA33 são as chaves legadas (linhas antigas).
PORTAL = "https://www.itau.com.br/itaubba-pt/portal/equity/company/"
MODEL_URL = {code: PORTAL + slug for code, slug in (
    ("VALE", "VALE"), ("VALE3", "VALE"), ("CMIN3", "CMIN3"), ("GMEXICOB", "GMEXICOB"), ("SCCO", "SCCO"),
    ("AUGO", "AURA33"), ("AURA33", "AURA33"), ("CSNA3", "CSNA3"), ("GGBR4", "GGBR4"), ("TX", "TX"),
    ("USIM5", "USIM5"), ("SUZB3", "SUZB3"), ("KLBN11", "KLBN11"), ("RANI3", "RANI3"), ("CMPC", "CMPC"),
    ("COPEC", "COPEC"), ("ARA", "ARA"), ("VMM", "VMM"))}
_SUFFIX = re.compile(r"\.(SA|SN|MX|L|HE|AX|F|TO)$")         # `_short` do site (stock-guide.html:375)
_NUM_TEXT = re.compile(r"[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?")


def _text(v) -> str | None:
    return (v.strip() or None) if isinstance(v, str) else None


def number(v):
    """Número finito tal qual; texto numérico (o PostgREST pode mandar `numeric` como texto — o site
    coage com `toNumOrNull`) vira float; o resto, None."""
    if isinstance(v, str):
        s = v.strip()
        return float(s) if _NUM_TEXT.fullmatch(s) else None
    return num(v)


def _int(v) -> int | None:
    x = number(v)
    return int(x) if x is not None and float(x).is_integer() else None


def model_url(code) -> str | None:
    """O link do modelo do código (pelo código e pelo código sem sufixo de bolsa), pelo filtro de URL."""
    if not isinstance(code, str):
        return None
    u = MODEL_URL.get(code) or MODEL_URL.get(_SUFFIX.sub("", code))
    return safe_url(u) if u else None


def company(r: dict) -> dict | None:
    """Uma linha de `get_stock_guide_comps` → item da Comp Table (oculta = só os 4 campos)."""
    code = _text(r.get("ticker"))
    if not code:
        return None
    base = {"code": code, "name": _text(r.get("company_name")) or code, "order": _int(r.get("display_order"))}
    if r.get("is_visible") is not True:
        return {**base, "restricted": True}
    rating = _text(r.get("recommendation"))
    out = {**base, "restricted": False, "sector": _text(r.get("sector")),
           "quote": _text(r.get("yahoo_symbol")),
           "rating": rating.upper() if rating and rating.upper() in RATINGS else None,
           "targetPrice": number(r.get("target_price")), "shares": number(r.get("shares_outstanding")),
           "tradeCcy": _text(r.get("trade_ccy")), "baseCcy": _text(r.get("base_ccy")),
           "fxToBase": number(r.get("fx_to_base")), "fxToUsd": number(r.get("fx_to_usd")),
           "modelUrl": model_url(code)}
    for y in YEARS:
        out[y] = {k: number(r.get(f"{col}_{y}")) for k, col in YEAR_FIELDS}
    return out


def _pct(frac):
    v = number(frac)
    return None if v is None else round(v * 100, 12)


def peer(r: dict) -> dict | None:
    """Uma linha de `get_stock_guide_global_peers` → par (múltiplos de consenso; dividend yield em pp)."""
    name = _text(r.get("company"))
    if not name:
        return None
    out = {"name": name, "order": _int(r.get("display_order")), "country": _text(r.get("country")),
           "sector": _text(r.get("sector")), "quote": _text(r.get("yahoo_symbol"))}
    for y in YEARS:
        block = {k: number(r.get(f"{col}_{y}")) for k, col in PEER_FIELDS}
        block["divYieldPct"] = _pct(r.get(f"div_yield_{y}"))
        out[y] = block
    return out


def _order_key(item: dict, ident: str):
    o = item.get("order")
    return (o if isinstance(o, int) else 0, item.get(ident) or "")


def data(comps: list[dict], config: list[dict], peers: list[dict]) -> dict:
    """As 3 RPCs → o miolo do arquivo (sem o envelope e sem o marcador)."""
    cfg = next((c for c in config if isinstance(c, dict)), {})
    cos = sorted((c for c in (company(r) for r in comps if isinstance(r, dict)) if c),
                 key=lambda c: _order_key(c, "code"))
    prs = sorted((p for p in (peer(r) for r in peers if isinstance(r, dict)) if p),
                 key=lambda p: _order_key(p, "name"))
    return {"years": {"y1": _text(cfg.get("y1_label")), "y2": _text(cfg.get("y2_label"))},
            "footnote": clean_text(cfg.get("comps_footnote")), "companies": cos, "peers": prs}


def empty_source() -> dict:
    return {"stamp": None, "companies": 0, "peers": 0}


def empty() -> dict:
    """Flag `stock_guide` desligada (fail-closed no dado, O50): nada lido, nada publicado."""
    return {"years": {"y1": None, "y2": None}, "footnote": None, "companies": [], "peers": []}


# ── a montagem (campo a campo de novo: o miolo pode vir do estado ou da cópia publicada) ──────────
def _pick(item, keys) -> dict:
    return {k: item.get(k) for k in keys} if isinstance(item, dict) else {k: None for k in keys}


def _company_out(c: dict) -> dict:
    if c.get("restricted") is not False:
        return {**_pick(c, RESTRICTED_KEYS), "restricted": True}
    out = _pick(c, COMPANY_KEYS)
    for y in YEARS:
        out[y] = _pick(c.get(y), YEAR_KEYS)
    return out


def _peer_out(p: dict) -> dict:
    out = _pick(p, PEER_KEYS)
    for y in YEARS:
        out[y] = _pick(p.get(y), PEER_YEAR_KEYS)
    return out


def from_published(doc: dict) -> dict:
    """O miolo de uma cópia publicada (a semente da partida a frio), campo a campo."""
    yrs = doc.get("years") if isinstance(doc.get("years"), dict) else {}
    return {"years": {"y1": _text(yrs.get("y1")), "y2": _text(yrs.get("y2"))},
            "footnote": _text(doc.get("footnote")),
            "companies": [_company_out(c) for c in doc.get("companies") or [] if isinstance(c, dict)
                          and _text(c.get("code"))],
            "peers": [_peer_out(p) for p in doc.get("peers") or []
                      if isinstance(p, dict) and _text(p.get("name"))]}


def build(src) -> dict:
    d = src.stock_guide()
    years = d.get("years") if isinstance(d.get("years"), dict) else {}
    return {
        "schemaVersion": 1,
        "generatedAt": src.generated_at,
        "updatedAt": d["updatedAt"],
        "source": _pick(d["source"], SOURCE_KEYS),
        "years": {"y1": years.get("y1"), "y2": years.get("y2")},
        "footnote": d.get("footnote"),
        "companies": [_company_out(c) for c in d.get("companies") or []],
        "peers": [_peer_out(p) for p in d.get("peers") or []],
    }
