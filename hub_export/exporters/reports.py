"""reports.json (COLD = 60 min, como a Home do site; texto, ≤ 20 KB) — PLANO, Apêndice E.6.

`items: [{ id, name, sector: SM|PP|NR, link, date, companies }]`, NA ORDEM DA RPC
`get_recent_reports_v3({p_limit: 20})` (data mais nova primeiro; sem data no fim). A tabela de
origem (`clipping_config`) guarda também telefone e e-mail dos analistas, o rascunho do weekly e
a mensagem do clipping: NUNCA é lida — só a RPC, que devolve os 5 campos (conferido em 05/11 com
`--check`: a service key executa a RPC, HTTP 200).

`id` = sha1(link, ou o nome em minúsculas)[:12] — a mesma chave do `distinct` da RPC. `link`
passa pelo filtro de URL (os links são digitados pelo admin: SharePoint, Storage…); recusado →
null (a Home mostra o nome sem link). `date` = `AAAA-MM-DD` ou null. `companies` = os tickers de
tela marcados no Clipping ([] sem marcação).
"""
from __future__ import annotations

import hashlib

from ..encode import iso_date
from ..urls import safe_url

SECTORS = ("SM", "PP", "NR")


def _id(key: str, used: set) -> str:
    rid = hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]
    n = 1
    while rid in used:                    # dois itens com a mesma chave (link não-http repetido)
        rid = hashlib.sha1(f"{key}#{n}".encode("utf-8")).hexdigest()[:12]
        n += 1
    used.add(rid)
    return rid


def build(src) -> dict:
    items, used = [], set()
    for r in src.recent_reports():
        name = r.get("name").strip() if isinstance(r.get("name"), str) else ""
        if not name:
            continue
        raw_link = r.get("link").strip() if isinstance(r.get("link"), str) and r["link"].strip() else None
        sector = r.get("sector").strip().upper() if isinstance(r.get("sector"), str) else ""
        comps = r.get("companies")
        items.append({
            "id": _id(raw_link or name.lower(), used),
            "name": name,
            "sector": sector if sector in SECTORS else "SM",       # a regra da própria RPC
            "link": safe_url(raw_link),
            "date": iso_date(r.get("date")),
            "companies": [c.strip() for c in comps if isinstance(c, str) and c.strip()]
            if isinstance(comps, list) else [],
        })
    return {"schemaVersion": 1, "generatedAt": src.generated_at, "items": items}
