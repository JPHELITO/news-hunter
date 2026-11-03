"""Codificação dos arquivos do hub: JSON compacto, gzip determinístico, hash de conteúdo e a
normalização dos valores que vêm do banco ou da memória do ciclo.

Três regras do contrato moram aqui (PLANO §2.6 e Apêndice B):
  • datas-hora sempre `2026-11-03T18:05:12Z` (UTC, segundos, `Z`) — o banco devolve
    `...12.123456+00:00` e o `_now_iso()` do robô também; tudo passa por `iso_utc`;
  • número que não é número finito vira `None` (nunca `0`, nunca `NaN`, que nem é JSON válido);
  • o hash de "mudou?" ignora `generatedAt` e `updatedAt` em QUALQUER nível: o carimbo anda a
    cada ciclo mesmo com o preço parado, e isso não pode virar um envio.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import math
import re
from datetime import datetime, timezone

# Chaves que não contam para o "mudou?" (são carimbos, não dado).
HASH_IGNORED_KEYS = frozenset({"generatedAt", "updatedAt"})

# Chaves que NUNCA podem aparecer num arquivo público, em nível nenhum (o Mindassets é aberto a
# qualquer cliente). Os construtores já montam campo a campo; isto é a rede embaixo deles.
FORBIDDEN_KEYS = frozenset({"email", "whatsapp", "photo", "phone", "token", "key", "secret",
                            "password", "signed", "_debug", "service"})

_ISO_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2})[T ](\d{2}):(\d{2}):(\d{2})(?:[.,](\d+))?\s*(Z|z|[+-]\d{2}(?::?\d{2})?)?$")
_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")


# ── bytes ──────────────────────────────────────────────────────────────────────────────────────
def to_json_bytes(obj) -> bytes:
    """JSON compacto, UTF-8 cru (sem \\uXXXX), na ordem em que o construtor montou as chaves.

    `allow_nan=False`: um NaN que escapasse viraria o token `NaN`, que não é JSON — o hub
    inteiro deixaria de ler o arquivo. Melhor o arquivo falhar aqui (e o ciclo seguir)."""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def gzip_bytes(data: bytes) -> bytes:
    """gzip determinístico: `mtime=0` (sem relógio no cabeçalho) → mesma entrada, mesmos bytes."""
    return gzip.compress(data, compresslevel=9, mtime=0)


def maybe_gunzip(data: bytes) -> bytes:
    """Descomprime se começar pelos bytes mágicos do gzip (1f 8b); senão devolve como veio.
    É como o hub lê: o nome continua `.json`, quem diz se é gzip são os bytes."""
    return gzip.decompress(data) if data[:2] == b"\x1f\x8b" else data


def _strip_stamps(obj):
    if isinstance(obj, dict):
        return {k: _strip_stamps(v) for k, v in obj.items() if k not in HASH_IGNORED_KEYS}
    if isinstance(obj, (list, tuple)):
        return [_strip_stamps(v) for v in obj]
    return obj


def canonical_bytes(obj) -> bytes:
    """JSON canônico (chaves ordenadas, compacto) SEM os carimbos — a base do hash."""
    return json.dumps(_strip_stamps(obj), ensure_ascii=False, separators=(",", ":"),
                      sort_keys=True, allow_nan=False).encode("utf-8")


def content_hash(obj) -> str:
    """sha256 do conteúdo, ignorando `generatedAt`/`updatedAt` em qualquer nível."""
    return hashlib.sha256(canonical_bytes(obj)).hexdigest()


def forbidden_keys(obj, path: str = "$") -> list[str]:
    """Caminhos das chaves proibidas (FORBIDDEN_KEYS, sem diferenciar maiúsculas) em qualquer
    nível. Lista vazia = pode publicar."""
    found = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            here = f"{path}.{k}"
            if isinstance(k, str) and k.lower() in FORBIDDEN_KEYS:
                found.append(here)
            found.extend(forbidden_keys(v, here))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            if isinstance(v, (dict, list)):
                found.extend(forbidden_keys(v, f"{path}[{i}]"))
    return found


# ── valores ────────────────────────────────────────────────────────────────────────────────────
def iso_utc(value) -> str | None:
    """Data-hora → `AAAA-MM-DDTHH:MM:SSZ` (UTC, segundos truncados). Inválido/ausente → None.

    Aceita o que o banco devolve (`2026-11-03T17:39:11.95015+00:00`, fração de 1 a 9 dígitos),
    o `_now_iso()` do robô, `Z` no fim, e `datetime`. Sem fuso = UTC (é como o robô grava)."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    else:
        m = _ISO_RE.match(str(value).strip())
        if not m:
            return None
        day, hh, mi, ss, _frac, tz = m.groups()
        base = f"{day}T{hh}:{mi}:{ss}"
        if not tz or tz in ("Z", "z"):
            tz = "+00:00"
        elif ":" not in tz:
            tz = f"{tz[:3]}:{tz[3:] or '00'}"
        try:
            dt = datetime.fromisoformat(base + tz)
        except ValueError:
            return None
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def iso_date(value) -> str | None:
    """Data sem hora (`AAAA-MM-DD`). Aceita data pura ou data-hora (fica a parte da data)."""
    if value is None or isinstance(value, bool):
        return None
    m = _DATE_RE.match(str(value).strip())
    if not m:
        return None
    try:
        datetime.strptime(m.group(1), "%Y-%m-%d")
    except ValueError:
        return None
    return m.group(1)


def num(value):
    """Número finito tal qual (int fica int, float fica float); qualquer outra coisa → None.
    `bool` não é número aqui (em Python `True == 1`, e um `true` perdido viraria preço 1)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def epoch_s(value) -> int | None:
    """Epoch em segundos, inteiro e positivo (≤ 0 é "sem dado", como no selo do site)."""
    v = num(value)
    if v is None or v <= 0:
        return None
    return int(v)


def session_pair(value) -> list[int] | None:
    """`[abre, fecha]` do pregão, só se for um par de epochs com fecha > abre — senão None
    (a mesma regra do `_pregao` do index.html)."""
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    a, b = epoch_s(value[0]), epoch_s(value[1])
    if a is None or b is None or b <= a:
        return None
    return [a, b]


def points(value) -> list[list]:
    """Série `[[epoch_s, valor], …]` copiada tal qual, descartando só o par inválido (sem dois
    números finitos) — um NaN solto quebraria o JSON inteiro do lado do hub."""
    out = []
    if not isinstance(value, (list, tuple)):
        return out
    for p in value:
        if isinstance(p, (list, tuple)) and len(p) == 2:
            t, v = num(p[0]), num(p[1])
            if t is not None and v is not None:
                out.append([t, v])
    return out
