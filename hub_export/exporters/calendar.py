"""calendar.json (WARM com sonda, conteúdo gzip com nome .json, ≤ 300 KB bruto) — PLANO, Apêndice E.7.

    { updatedAt, timezone: "America/Sao_Paulo", from, to, categories, dataWindows, events }

  categories  TODAS, inclusive as inativas (a agenda mostra todas): {id, name, color, kind,
              sortOrder, active}, por (sortOrder, name, id);
  dataWindows só as ativas: {id, sourceKey, label, fromDay, toDay, lagMonths, note}, por
              (fromDay, label, id);
  events      só os visíveis: {id, title, categoryId, startDate, endDate, allDay, startTime,
              endTime, company, recurrence, origin}; os NÃO recorrentes de 1º/jan do ano anterior
              até o fim do mês de hoje + 18 meses (fim arredondado ao mês: o hash não muda todo
              dia); os recorrentes SEMPRE, com a REGRA (`recurrence` com as chaves do banco —
              exceção ao camelCase para o porte do calendar-lib.js ser 1:1; o exportador não
              expande ocorrências). Horas em "HH:MM" de Brasília (`time` sem fuso no banco).
              `origin` = `source` do banco ("b3" ou null = cadastrado à mão). Por (startDate,
              startTime, id).

SEM TEXTO LIVRE (revisão do orquestrador, 05/11/2026): `location`, `description` e `links` dos
eventos NÃO saem — nem são lidos do banco. São texto digitado pelo time, com links copiados de
e-mails recebidos (rastreio de clique que identifica quem recebeu, confirmação de inscrição do
Itaú). A Home não usa nenhum dos três; eles voltam na Onda 4 (página Calendar), com regra própria
decidida com o usuário. O leitor do hub trata a ausência (`null` / `[]`).
Fora do arquivo também: `updated_by` (uuid de auth.users), `updated_at` de linha, `external_id`,
`ics_seq`, `locked`, eventos ocultos.

Leitura (sources.py): sonda "contagem + max(updated_at)" dos visíveis a cada 15 min numa chamada
só; leitura completa quando ela muda, na partida a frio, quando o intervalo vira o mês e uma vez
por dia depois das 23:45 UTC (o robô da B3 roda às 23:20 e faz upsert/oculta SEM mexer em
`updated_at`; o delete do admin some da contagem). Categorias e janelas de dado: a cada 60 min.
"""
from __future__ import annotations

import calendar as _cal
import re
from datetime import date, datetime, timedelta, timezone

from ..encode import iso_date, num

TIMEZONE = "America/Sao_Paulo"
BRT = timezone(timedelta(hours=-3))          # o Brasil não tem horário de verão desde 2019
CATEGORY_COLS = "id,name,color,kind,sort_order,is_active"
WINDOW_COLS = "id,source_key,label,from_day,to_day,lag_months,note,is_active"
EVENT_COLS = ("id,title,category_id,start_date,end_date,all_day,start_time,end_time,company,"
              "recurrence,source,is_visible")
EVENT_KEYS = ("id", "title", "categoryId", "startDate", "endDate", "allDay", "startTime", "endTime",
              "company", "recurrence", "origin")
CATEGORY_KEYS = ("id", "name", "color", "kind", "sortOrder", "active")
WINDOW_KEYS = ("id", "sourceKey", "label", "fromDay", "toDay", "lagMonths", "note")
RECURRENCE_KEYS = ("freq", "interval", "byweekday", "bymonthday", "bysetpos", "until", "count",
                   "whole_week")
MONTHS_AHEAD = 18
_HHMM = re.compile(r"^(\d{1,2}):(\d{2})")


def _text(value):
    return (value.strip() or None) if isinstance(value, str) else None


def _int(value):
    v = num(value)
    return int(v) if v is not None and float(v).is_integer() else None


def date_range(now: datetime) -> tuple[str, str]:
    """(1º/jan do ano anterior, último dia do mês de hoje + 18 meses), com "hoje" em Brasília."""
    today = now.astimezone(BRT).date()
    m = today.month + MONTHS_AHEAD
    y, m = today.year + (m - 1) // 12, (m - 1) % 12 + 1
    return date(today.year - 1, 1, 1).isoformat(), date(y, m, _cal.monthrange(y, m)[1]).isoformat()


def hhmm(value):
    m = _HHMM.match(value.strip()) if isinstance(value, str) else None
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    return f"{h:02d}:{mi:02d}" if h < 24 and mi < 60 else None


def category(r: dict) -> dict | None:
    if r.get("id") in (None, "") or not _text(r.get("name")):
        return None
    return {"id": str(r["id"]), "name": _text(r["name"]), "color": _text(r.get("color")),
            "kind": _text(r.get("kind")), "sortOrder": _int(r.get("sort_order")),
            "active": r.get("is_active") is not False}


def data_window(r: dict) -> dict | None:
    if r.get("is_active") is False or r.get("id") in (None, "") or not _text(r.get("source_key")):
        return None
    return {"id": str(r["id"]), "sourceKey": _text(r["source_key"]), "label": _text(r.get("label")),
            "fromDay": _int(r.get("from_day")), "toDay": _int(r.get("to_day")),
            "lagMonths": _int(r.get("lag_months")), "note": _text(r.get("note"))}


def _scalar(v) -> bool:
    return v is None or isinstance(v, (str, bool)) or num(v) is not None


def recurrence(value) -> dict | None:
    """Só as 8 chaves do motor do calendar-lib.js, com valor escalar ou lista de escalares."""
    if not isinstance(value, dict):
        return None
    out = {}
    for k in RECURRENCE_KEYS:
        if k not in value:
            continue
        v = value[k]
        if _scalar(v) or (isinstance(v, list) and all(_scalar(x) for x in v)):
            out[k] = v
    return out or None


def event(r: dict) -> dict | None:
    if r.get("is_visible") is not True or r.get("id") in (None, ""):
        return None
    title, start = _text(r.get("title")), iso_date(r.get("start_date"))
    if not title or not start:
        return None
    return {"id": str(r["id"]), "title": title,
            "categoryId": str(r["category_id"]) if r.get("category_id") not in (None, "") else None,
            "startDate": start, "endDate": iso_date(r.get("end_date")),
            "allDay": r["all_day"] if isinstance(r.get("all_day"), bool) else None,
            "startTime": hhmm(r.get("start_time")), "endTime": hhmm(r.get("end_time")),
            "company": _text(r.get("company")), "recurrence": recurrence(r.get("recurrence")),
            "origin": _text(r.get("source"))}


def in_range(ev: dict, first: str, last: str) -> bool:
    """Recorrente: sempre. Senão: cruza [first, last] (fim inclusivo; sem fim = 1 dia)."""
    return ev["recurrence"] is not None or (ev["startDate"] <= last
                                            and (ev["endDate"] or ev["startDate"]) >= first)


def sort_categories(items: list[dict]) -> list[dict]:
    return sorted(items, key=lambda c: (c["sortOrder"] if c["sortOrder"] is not None else 0,
                                        c["name"], c["id"]))


def sort_windows(items: list[dict]) -> list[dict]:
    return sorted(items, key=lambda w: (w["fromDay"] if w["fromDay"] is not None else 0,
                                        w["label"] or "", w["id"]))


def sort_events(items: list[dict]) -> list[dict]:
    return sorted(items, key=lambda e: (e["startDate"], e["startTime"] or "", e["id"]))


def _only(items, keys) -> list[dict]:
    """Campo a campo na montagem também: um cache de estado de uma versão anterior (com chaves que
    deixaram de sair) nunca chega ao arquivo."""
    return [{k: i.get(k) for k in keys} for i in items or [] if isinstance(i, dict)]


def build(src) -> dict:
    cal = src.calendar()
    return {
        "schemaVersion": 1,
        "generatedAt": src.generated_at,
        "updatedAt": cal["updatedAt"],
        "timezone": TIMEZONE,
        "from": cal["from"],
        "to": cal["to"],
        "categories": _only(cal["categories"], CATEGORY_KEYS),
        "dataWindows": _only(cal["windows"], WINDOW_KEYS),
        "events": _only(cal["events"], EVENT_KEYS),
    }
