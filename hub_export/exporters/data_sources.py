"""data-sources.json (COLD com sonda em `checked_at`, texto) — PLANO, Apêndice H.4.

    { schemaVersion, generatedAt, updatedAt, checkedAt,
      sources: [{ id, label, description, expectedCadence, sector, kind, manual, state, statusText,
                  lastPeriod, nextExpected, checkedAt }] }

O frescor das 17 fontes da página Data do site, como o robô `status_digest.py --publish` grava (1×/dia, com
3–9 h de atraso do cron do GitHub). ORIGEM: a RPC `get_data_source_status()` (chave de serviço), na ordem
dela (setor steel → pulp → live, depois o rótulo).

CAMPO A CAMPO: `id` (= `key` do registro do site; a palavra `key` é proibida), `label`, `description`
(`client_desc`) e `expectedCadence` (`client_cadence`) — os textos do cliente, em inglês, com e-mail/telefone
mascarados (freetext.clean_text) —, `sector` (steel|pulp|live), `kind` (= `cadence`: monthly|live), `manual`
(= `auto` falso: a etiqueta "manual"), `state` (green|amber|red|grey), `statusText` (o texto do robô: FOTO
da hora da checagem — "live ( 4 min ago)" —, o hub só o usa nas mensais), `lastPeriod`/`nextExpected`
("2026-10" nas mensais; nas live, o carimbo da checagem e "contínuo", como gravados) e `checkedAt` (ISO).
`checkedAt`/`updatedAt` do envelope = o maior `checked_at` ("checked X ago"). O `how_pulled` (bastidor:
como o dado entra) e o `updated_at` (a hora da INSERÇÃO; nunca muda) NUNCA saem — nem são pedidos.

FLAG `data_page` (fail-closed no dado, O50): desligada → `sources: []`, nada lido.
SONDA (pages.py): contagem + `max(checked_at)` da tabela; o `checkedAt` + o número de fontes da cópia
publicada são o marcador da partida a frio (os dois DENTRO do hash).
"""
from __future__ import annotations

from ..encode import iso_utc
from ..freetext import clean_text

COLS = ("key,label,client_desc,client_cadence,sector,cadence,auto,state,status_text,last_period,"
        "next_expected,checked_at")
SOURCE_KEYS = ("id", "label", "description", "expectedCadence", "sector", "kind", "manual", "state",
               "statusText", "lastPeriod", "nextExpected", "checkedAt")
STATES = ("green", "amber", "red", "grey")
FORBIDDEN = frozenset({"how_pulled", "howPulled", "updated_by", "updatedBy", "updated_at", "auto"})


def _text(v) -> str | None:
    return (v.strip() or None) if isinstance(v, str) else None


def source(r: dict) -> dict | None:
    """Uma linha da RPC → a fonte (sem `key` = sem identidade: fica de fora)."""
    sid = _text(r.get("key"))
    if not sid:
        return None
    state = _text(r.get("state"))
    return {"id": sid, "label": clean_text(r.get("label")) or sid,
            "description": clean_text(r.get("client_desc")),
            "expectedCadence": clean_text(r.get("client_cadence")),
            "sector": _text(r.get("sector")), "kind": _text(r.get("cadence")),
            "manual": r.get("auto") is False, "state": state if state in STATES else "grey",
            "statusText": _text(r.get("status_text")), "lastPeriod": _text(r.get("last_period")),
            "nextExpected": _text(r.get("next_expected")), "checkedAt": iso_utc(r.get("checked_at"))}


def sources(rows: list[dict]) -> list[dict]:
    """As linhas da RPC, na ordem da RPC."""
    return [s for s in (source(r) for r in rows if isinstance(r, dict)) if s]


def checked_at(items: list[dict]) -> str | None:
    stamps = [s["checkedAt"] for s in items if s.get("checkedAt")]
    return max(stamps) if stamps else None


def _pick(item, keys) -> dict:
    return {k: item.get(k) for k in keys} if isinstance(item, dict) else {k: None for k in keys}


def from_published(doc: dict) -> list[dict]:
    """As fontes de uma cópia publicada (a semente da partida a frio), campo a campo."""
    return [_pick(s, SOURCE_KEYS) for s in doc.get("sources") or [] if isinstance(s, dict)
            and _text(s.get("id"))]


def build(src) -> dict:
    d = src.data_sources()
    return {
        "schemaVersion": 1,
        "generatedAt": src.generated_at,
        "updatedAt": d["checkedAt"],
        "checkedAt": d["checkedAt"],
        "sources": [_pick(s, SOURCE_KEYS) for s in d.get("sources") or []],
    }
