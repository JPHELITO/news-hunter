"""news-digest.json (HOT, conteúdo gzip com nome .json, ≤ 80 KB bruto) — PLANO, Apêndice E.5.

O "resumo da última hora" (robô hunter/digest.py → tabela `news_digest`): as últimas 48 janelas,
INCLUSIVE as `quiet`, em `slotStart` decrescente:
    { slotStart, slotEnd, kind: hour|overnight, status: ok|list|quiet, summary, createdAt,
      items: [{ url, title, source, take, tickers, at }] }
Lido da TABELA (a RPC `get_news_digest` exige usuário logado: com a service key devolveria 0
linhas), campo a campo, sem `model`/`tokens_*` e sem `items[].score` (ranking interno). Cada
janela é gravada uma vez e não muda: o exportador só lê nas janelas de gravação (sources.py).

FAIL-CLOSED: com a flag `home_digest` desligada (ou ausente) o arquivo sai com `windows: []` —
o arquivo é público e o texto é de IA em avaliação (decisão O3). `updatedAt` = o maior
`createdAt` publicado.
"""
from __future__ import annotations

from ..encode import iso_utc
from ..urls import safe_url
from .news_common import take

KEEP = 48
KINDS = ("hour", "overnight")
STATUSES = ("ok", "list", "quiet")


def _text(value):
    return (value.strip() or None) if isinstance(value, str) else None


def _item(obj) -> dict | None:
    if not isinstance(obj, dict) or not _text(obj.get("title")):
        return None
    tickers = obj.get("tickers")
    return {"url": safe_url(obj.get("url")),
            "title": _text(obj["title"]),
            "source": _text(obj.get("source")),
            "take": take(obj.get("take")),
            "tickers": [t.strip() for t in tickers if isinstance(t, str) and t.strip()]
            if isinstance(tickers, list) else [],
            "at": iso_utc(obj.get("at"))}


def window(slot_start, slot_end, kind, status, summary, created_at, items) -> dict | None:
    """Uma janela no formato do contrato, ou None se a linha não é reconhecível."""
    start = iso_utc(slot_start)
    if not start or kind not in KINDS or status not in STATUSES:
        return None
    return {"slotStart": start, "slotEnd": iso_utc(slot_end), "kind": kind, "status": status,
            "summary": _text(summary), "createdAt": iso_utc(created_at),
            "items": [i for i in (_item(o) for o in items or []) if i]
            if isinstance(items, list) else []}


def from_row(r: dict) -> dict | None:
    """Linha da tabela `news_digest` (nomes do banco)."""
    return window(r.get("slot_start"), r.get("slot_end"), r.get("kind"), r.get("status"),
                  r.get("summary"), r.get("created_at"), r.get("items"))


def from_published(w) -> dict | None:
    """Janela da cópia publicada (a semente da partida a frio)."""
    if not isinstance(w, dict):
        return None
    return window(w.get("slotStart"), w.get("slotEnd"), w.get("kind"), w.get("status"),
                  w.get("summary"), w.get("createdAt"), w.get("items"))


def build(src) -> dict:
    windows = src.digest_windows() if src.digest_enabled() else []
    return {
        "schemaVersion": 1,
        "generatedAt": src.generated_at,
        "updatedAt": max((w["createdAt"] for w in windows if w.get("createdAt")), default=None),
        "windows": windows,
    }
