"""config.json (WARM, ≤ 20 KB) — PLANO Apêndice B.1.

flags       do RPC `get_dashboard_flags` → `{chave: ligada}`. Para cada chave `k`, se existir
            `hub_k`, vale o `hub_k` (assim o hub pode se separar do site sem código: basta criar
            a flag `hub_…` no /admin); as `hub_*` não aparecem no arquivo.
exchanges   mapa constante (reference.EXCHANGES) para cada bolsa do QUOTES_LIST.
heatmap     grupos constantes do site + a configuração do /admin aplicada como o
            `applyHeatmapConfig` do index.html: oculto sai de TODOS os grupos e vai para
            `hidden`; `coverage` = linhas `in_coverage` por `sort_order` (e símbolo), só os
            papéis conhecidos; sem nenhuma → o grupo constante. O grupo `coverage` recebe essa lista.
commodityGroups  do RPC `get_commodity_groups`, só os visíveis, por `sort_order`.
"""
from __future__ import annotations

import logging

from hunter import prices

from ..encode import FORBIDDEN_KEYS
from .reference import (BENCH_BY_EXCH, BENCH_LABEL, EXCHANGES, FALLBACK_TZ, G_COVERAGE,
                        HEATMAP_GROUPS, HM_MAX)

log = logging.getLogger(__name__)


def _flags(rows: list[dict]) -> dict[str, bool]:
    base: dict[str, bool] = {}
    hub: dict[str, bool] = {}
    for r in rows:
        k, v = r.get("key"), r.get("enabled")
        if not isinstance(k, str) or not k or not isinstance(v, bool):
            continue
        if k.startswith("hub_"):
            hub[k[4:]] = v
        else:
            base[k] = v
    out = {}
    for k, v in base.items():
        # a chave da flag é o ÚNICO nome de campo que vem do /admin: nome proibido no arquivo
        # público (FORBIDDEN_KEYS) fica de fora em vez de barrar o config.json inteiro
        if k.lower() in FORBIDDEN_KEYS:
            log.warning("config: flag %r tem nome proibido em arquivo público — fica de fora", k)
            continue
        out[k] = hub.get(k, v)
    return out


def _exchanges() -> dict[str, dict]:
    present = []
    for q in prices.QUOTES_LIST:
        if q[3] not in present:
            present.append(q[3])
    out = {code: dict(EXCHANGES[code]) for code in EXCHANGES if code in present}
    for code in present:
        if code not in out:
            log.warning("config: bolsa %s sem entrada em reference.EXCHANGES", code)
            out[code] = {"name": code, "tz": FALLBACK_TZ, "currency": None}
    return out


def _admin_order(r: dict):
    so = r.get("sort_order")
    return (so if isinstance(so, int) and not isinstance(so, bool) else 0, str(r.get("symbol") or ""))


def _heatmap(admin_rows: list[dict]) -> dict:
    ordered = sorted((r for r in admin_rows if isinstance(r.get("symbol"), str) and r["symbol"]),
                     key=_admin_order)
    hidden: list[str] = []
    for r in ordered:
        if r.get("is_hidden") is True and r["symbol"] not in hidden:
            hidden.append(r["symbol"])
    hid = set(hidden)
    known = {t for _id, _pt, _en, tickers in HEATMAP_GROUPS for t in tickers} - hid
    coverage: list[str] = []
    for r in ordered:
        if r.get("in_coverage") is True and r["symbol"] in known and r["symbol"] not in coverage:
            coverage.append(r["symbol"])
    if not coverage:
        coverage = [t for t in G_COVERAGE if t not in hid]
    groups = []
    for gid, pt, en, tickers in HEATMAP_GROUPS:
        lst = coverage if gid == "coverage" else [t for t in tickers if t not in hid]
        groups.append({"id": gid, "label": {"pt-br": pt, "en-us": en}, "tickers": list(lst)})
    return {"max": HM_MAX, "groups": groups, "coverage": list(coverage), "hidden": hidden,
            "benchmarkByExchange": dict(BENCH_BY_EXCH), "benchmarkLabels": dict(BENCH_LABEL)}


def _commodity_groups(rows: list[dict]) -> list[dict]:
    def order(r):
        so = r.get("sort_order")
        return (so if isinstance(so, int) and not isinstance(so, bool) else 0, str(r.get("title") or ""))
    out = []
    for r in sorted((r for r in rows if r.get("is_visible") is True), key=order):
        title, codes = r.get("title"), r.get("codes")
        if not isinstance(title, str) or not title:
            continue
        out.append({"id": r.get("id"), "title": title,
                    "codes": [c for c in codes if isinstance(c, str) and c]
                    if isinstance(codes, list) else []})
    return out


def build(src) -> dict:
    return {
        "schemaVersion": 1,
        "generatedAt": src.generated_at,
        "flags": _flags(src.flags()),
        "exchanges": _exchanges(),
        "heatmap": _heatmap(src.heatmap_admin()),
        "commodityGroups": _commodity_groups(src.commodity_groups()),
    }
