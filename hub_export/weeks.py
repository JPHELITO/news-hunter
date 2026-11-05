"""Semanas ISO dos arquivos semanais de notícias (PLANO, Apêndice E.3).

Rótulo `AAAA-wNN` com o ANO ISO e a semana com 2 dígitos, sempre em minúsculas (o SharePoint não
distingue caixa): 2026 tem 53 semanas e a `2026-w53` vai de 28/12/2026 a 03/01/2027. A semana de
uma notícia é a da sua data de referência EM UTC (o hub carrega as semanas do intervalo pedido
com 1 dia de folga em cada ponta, porque o dia da tela é local).

Rótulos comparam como texto na ordem do tempo (`"2026-w09" < "2026-w45" < "2027-w01"`).
"""
from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta, timezone

PREFIX = "news-week"
FIRST_WEEK = "2026-w23"          # o feed começa em 02/06/2026 (CLAUDE.md:2512-2513)
_LABEL = re.compile(r"^(\d{4})-w(\d{2})$")
_FILE = re.compile(r"^news-week-(\d{4}-w\d{2})\.json$")


def parse(label: str) -> tuple[int, int] | None:
    """(ano ISO, semana) de um rótulo válido; None se o rótulo não existe (ex.: 2025-w53)."""
    m = _LABEL.match(label or "")
    if not m:
        return None
    y, w = int(m.group(1)), int(m.group(2))
    try:
        date.fromisocalendar(y, w, 1)
    except ValueError:
        return None
    return y, w


def label_of(when: datetime | date) -> str:
    """A semana ISO (em UTC) de um instante, ou de uma data."""
    if isinstance(when, datetime):
        when = when.astimezone(timezone.utc).date()
    y, w, _ = when.isocalendar()
    return f"{y}-w{w:02d}"


def monday(label: str) -> date:
    y, w = parse(label) or (None, None)
    if y is None:
        raise ValueError(f"semana inválida: {label}")
    return date.fromisocalendar(y, w, 1)


def bounds(label: str) -> tuple[str, str]:
    """(`from`, `to`) = segunda e domingo, `AAAA-MM-DD`."""
    m = monday(label)
    return m.isoformat(), (m + timedelta(days=6)).isoformat()


def start(label: str) -> datetime:
    """Segunda 00:00 UTC (início, inclusivo)."""
    return datetime.combine(monday(label), time(0), tzinfo=timezone.utc)


def end(label: str) -> datetime:
    """A segunda seguinte, 00:00 UTC (fim, exclusivo)."""
    return start(label) + timedelta(days=7)


def shift(label: str, n: int) -> str:
    return label_of(monday(label) + timedelta(days=7 * n))


def span(first: str, last: str) -> list[str]:
    """Os rótulos de `first` a `last`, inclusive, em ordem crescente ([] se first > last)."""
    out, cur = [], first
    while cur <= last:
        out.append(cur)
        cur = shift(cur, 1)
    return out


def file_name(label: str) -> str:
    return f"{PREFIX}-{label}.json"


def label_from_file(name: str) -> str | None:
    """O rótulo de um nome de arquivo semanal válido (`news-week-2026-w45.json`), ou None."""
    m = _FILE.match(name or "")
    return m.group(1) if m and parse(m.group(1)) else None
