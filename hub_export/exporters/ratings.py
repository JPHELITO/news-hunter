"""ratings.json (COLD com sonda, conteúdo gzip com nome .json) — PLANO, Apêndice F.5.

`{ schemaVersion, generatedAt, updatedAt, companies: [{ code, name, symbol, events: [{ date, rating,
targetPrice, currency, listing }] }] }` — o histórico OP/MP/UP/NR + preço-alvo das empresas cobertas
(as etiquetas do gráfico, o "Since last update" e o "Our rating" da Market).

Origem: a RPC `get_rating_history()` (a empresa OCULTA no Stock Guide já sai de fora; a tabela nunca
é lida inteira). Campo a campo: `code` = a chave do Stock Guide (`VALE`, `AUGO`, `ARA`… — a palavra
`key` é proibida em arquivo público); `name` = `company_name`; `symbol` = `yahoo_symbol`; eventos em
data crescente; `rating` ∈ OP/MP/UP/NR (outra coisa vira null); `targetPrice` > 0 ou null; `currency`
= o código ISO como gravado ou null; `listing` = `coalesce(listing, yahoo_symbol)`. NUNCA saem
`note` (texto livre da importação da Bloomberg, a tela não mostra), `source`, ids e carimbos de
usuário — nem são pedidos à RPC (`select` explícito), e o registro proíbe as chaves neste arquivo.

`updatedAt` = o maior `updated_at` do histórico (a sonda; fora do hash). A "última atualização" que
a Market mostra é o ÚLTIMO EVENTO de cada empresa (o hub calcula), não a data E2 do Stock Guide.

FLAG `market_ratings` (fail-closed, como no site, com a regra `hub_`): desligada → `companies: []`
sem ler nada — o arquivo é público e a RPC só devolve linhas com a flag ligada (decisão U4).
"""
from __future__ import annotations

import re

from ..encode import iso_date, num

COLS = "ticker,company_name,yahoo_symbol,effective_date,rating,target_price,currency,listing"
ORDER = "ticker.asc,effective_date.asc"          # = a ordem da RPC; (ticker, data) é única no banco
RATINGS = ("OP", "MP", "UP", "NR")
FORBIDDEN = frozenset({"note", "source", "id", "createdAt", "created_at", "updatedBy", "updated_by",
                       "createdBy", "created_by"})
_CCY = re.compile(r"[A-Za-z]{3}")


def _text(v) -> str | None:
    return (v.strip() or None) if isinstance(v, str) else None


def companies(rows: list[dict]) -> list[dict]:
    """As linhas da RPC (uma por evento) → empresas na ordem do código, eventos em data crescente.
    Evento sem data válida fica de fora; empresa sem evento nenhum, também."""
    by: dict[str, dict] = {}
    for r in rows:
        code = _text(r.get("ticker"))
        day = iso_date(r.get("effective_date"))
        if not code or not day:
            continue
        co = by.setdefault(code, {"code": code, "name": None, "symbol": None, "events": []})
        co["name"] = co["name"] or _text(r.get("company_name"))
        co["symbol"] = co["symbol"] or _text(r.get("yahoo_symbol"))
        rating = _text(r.get("rating"))
        price = num(r.get("target_price"))
        ccy = _text(r.get("currency"))
        co["events"].append({
            "date": day,
            "rating": rating.upper() if rating and rating.upper() in RATINGS else None,
            "targetPrice": price if price is not None and price > 0 else None,
            "currency": ccy if ccy and _CCY.fullmatch(ccy) else None,
            "listing": _text(r.get("listing")) or _text(r.get("yahoo_symbol")),
        })
    out = []
    for code in sorted(by):
        co = by[code]
        co["events"].sort(key=lambda e: e["date"])
        if co["events"]:
            out.append(co)
    return out


def build(src) -> dict:
    data = src.ratings()
    return {
        "schemaVersion": 1,
        "generatedAt": src.generated_at,
        "updatedAt": data["updatedAt"],
        "companies": data["companies"],
    }
