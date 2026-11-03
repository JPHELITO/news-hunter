"""live.json (HOT + pulsação de 15 min, ≤ 80 KB) — PLANO Apêndice B.2.

quotes       TODOS os instrumentos do QUOTES_LIST, na ordem dele (inclusive os que nenhum grupo
             do heatmap usa); sem `pts` (a curva do dia vai no intraday.json). `prevClose` =
             `intraday.prev`; `tradedAt` = `intraday.traded_at`; `session` só quando válida.
             Papel que faltou na memória do ciclo sai com o último valor conhecido (como no banco).
commodities  todas as linhas da tabela, sem série diária, faixa de 12 m ou fonte (Onda 2);
             `spark` é a cópia do `commodities.spark` do banco, campo a campo.
macro        os indicadores do update_macro (Yahoo e BCB), na ordem dele.
Ausente = `null`. Nunca 0, "" ou a hora de agora no lugar de um dado que não veio.
"""
from __future__ import annotations

from ..encode import iso_date, num

SPARK_WINDOWS = ("d", "w", "m", "ytd", "y")


def _latest(items: list[dict]) -> str | None:
    stamps = [i["updatedAt"] for i in items if i.get("updatedAt")]
    return max(stamps) if stamps else None


def _spark(sp) -> dict | None:
    """O risquinho do carrossel copiado do banco, só com as chaves conhecidas (formato v2 do
    prices.commodity_periods): `asof`, `v`, `ph` e as janelas d/w/m/ytd/y com `c`, `s`, `b`.
    Janela com algum ponto inválido em `s` fica de fora inteira (`b` aponta para um índice de
    `s`: tirar um ponto do meio deslocaria o destaque)."""
    if not isinstance(sp, dict):
        return None
    out: dict = {"asof": iso_date(sp.get("asof"))}
    if num(sp.get("v")) is not None:
        out["v"] = num(sp.get("v"))
    if num(sp.get("ph")) is not None:
        out["ph"] = num(sp.get("ph"))
    for w in SPARK_WINDOWS:
        win = sp.get(w)
        if not isinstance(win, dict) or not isinstance(win.get("s"), list):
            continue
        s = win["s"]
        if any(num(x) is None for x in s):
            continue
        out[w] = {"c": num(win.get("c")), "s": list(s), "b": num(win.get("b"))}
    return out


def build(src) -> dict:
    q = src.quotes()
    quotes = []
    for ticker, name, exchange in src.quote_universe:
        r = q.get(ticker) or {}
        it = r.get("intraday") or {}
        quotes.append({
            "ticker": ticker,
            "name": name,
            "exchange": exchange,
            "price": r.get("price"),
            "changePct": r.get("change_pct"),
            "changeAbs": r.get("change_abs"),
            "prevClose": it.get("prev"),
            "tradedAt": it.get("traded_at"),
            "session": it.get("session"),
            "updatedAt": r.get("updated_at"),
        })

    commodities = []
    for r in src.commodities():
        commodities.append({
            "code": r["code"],
            "name": r.get("name"),
            "unit": r.get("unit"),
            "price": r.get("price"),
            "changePct": r.get("change_pct"),
            "assessedAt": r.get("assessed_at"),
            "updatedAt": r.get("updated_at"),
            "spark": _spark(r.get("spark")),
        })

    m = src.macro()
    macro = []
    for code, name, unit in src.macro_universe:
        r = m.get(code) or {}
        macro.append({
            "code": code,
            "name": name,
            "unit": unit,
            "value": r.get("value"),
            "changePct": r.get("change_pct"),
            "updatedAt": r.get("updated_at"),
        })

    return {
        "schemaVersion": 1,
        "generatedAt": src.generated_at,
        "quotes": {"updatedAt": _latest(quotes), "items": quotes},
        "commodities": {"updatedAt": _latest(commodities), "items": commodities},
        "macro": {"updatedAt": _latest(macro), "items": macro},
    }
