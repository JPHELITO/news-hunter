"""mw-buybacks.json (leitura completa 2×/dia, conteúdo gzip com nome .json) — PLANO, Apêndice F.9.

`{ schemaVersion, generatedAt, items: [{ programId, company, decidedOn, expiresOn, status, operation,
   reason, purpose, qtyOn, qtyPn, qtyCircOn, qtyCircPn, authorizedUsd, brokers }] }`

Todos os programas de recompra das empresas visíveis (a RPC `get_mw_buybacks` já tira a oculta), na
ordem da RPC (`decided_on` decrescente com nulos no fim, `program_id` decrescente). Os textos da CVM vão
crus, em português (a tela traduz os conjuntos fechados: status, tipo, finalidade); `reason` e
`purpose` são texto livre da companhia → e-mails e telefones mascarados (U6). `brokers` = os nomes
completos das corretoras (a tela mostra a 1ª palavra; o Excel, o nome inteiro).

O `updated_at` desta tabela é a hora da INSERÇÃO (o upsert do robô não o atualiza): não há sonda que
sirva — a leitura é completa (~9 KB) nas duas marcas do dia (mw.py). NUNCA saem `company_name`
(redundante) e `updated_at`.
"""
from __future__ import annotations

from ..contacts import mask_contacts
from ..encode import iso_date, num

COLS = ("program_id,company,decided_on,expires_on,status,operation,reason,purpose,qty_on,qty_pn,"
        "qty_circ_on,qty_circ_pn,authorized_usd,brokers")
ORDER = "decided_on.desc.nullslast,program_id.desc"   # = a da RPC (program_id é a chave)
FORBIDDEN = frozenset({"cnpj", "cvm_code", "b3_code", "companyName", "company_name", "updated_at"})


def _text(v) -> str | None:
    return (v.strip() or None) if isinstance(v, str) else None


def item(r: dict) -> dict | None:
    pid = num(r.get("program_id"))
    co = _text(r.get("company"))
    if pid is None or not float(pid).is_integer() or not co:
        return None
    br = r.get("brokers")
    return {"programId": int(pid), "company": co, "decidedOn": iso_date(r.get("decided_on")),
            "expiresOn": iso_date(r.get("expires_on")), "status": _text(r.get("status")),
            "operation": _text(r.get("operation")),
            "reason": mask_contacts(_text(r.get("reason"))),
            "purpose": mask_contacts(_text(r.get("purpose"))),
            "qtyOn": num(r.get("qty_on")), "qtyPn": num(r.get("qty_pn")),
            "qtyCircOn": num(r.get("qty_circ_on")), "qtyCircPn": num(r.get("qty_circ_pn")),
            "authorizedUsd": num(r.get("authorized_usd")),
            "brokers": [b.strip() for b in br if isinstance(b, str) and b.strip()]
            if isinstance(br, list) else []}


def publish(it: dict) -> dict:
    """O item como vai ao arquivo — a máscara de novo (idempotente: a semente da cópia publicada
    passa pela regra de hoje)."""
    return {**it, "reason": mask_contacts(it.get("reason")), "purpose": mask_contacts(it.get("purpose"))}


def build(src) -> dict:
    data = src.mw().buybacks()
    return {"schemaVersion": 1, "generatedAt": src.generated_at,
            "items": [publish(it) for it in data["items"]]}
