"""O comum aos arquivos dos setores (Onda 3 — PLANO, Apêndice G.0): meses, arrays mensais, a
codificação em CORRIDAS, a conferência de esquema e o carimbo do dado.

MESES. `from`/`through` em "AAAA-MM"; um array mensal tem um valor por mês de `from` a `through`
(índice 0 = `from`). `null` = mês sem linha no banco (ou com o valor NULL); `0` = linha com zero.
Nunca se preenche mês ausente.

CORRIDAS (`mm-countries-*`, `pp-pulp-exports`). Uma série (um país, um porto) vira
`[índice no dicionário, [corrida, corrida, …]]`; cada corrida é `[início, [métrica 1…], [métrica 2…]]`
com `início` = índice do mês desde o `from` do arquivo. Um trecho novo começa depois de MAIS DE 3
meses sem linha (3 meses vazios ficam dentro do trecho, como `null`); dentro do trecho, `null` = mês
sem linha. Mês cujas métricas são todas NULL no banco é igual a mês sem linha (a página não cria a
chave: `mergeOriginRows`/`aggPeriodPort`). `expand_runs` é a volta (o hub tem o mesmo, testado).

ESQUEMA. Cada arquivo confere, antes de ler, as tabelas e colunas de que precisa (`PRAGMA
table_info` × a lista do construtor): tabela ou coluna que sumiu do banco do site → aquele arquivo
FALHA sem publicar (SchemaError, com o nome do que falta) e os outros seguem.
"""
from __future__ import annotations

import re
from typing import Iterable

from ..encode import iso_utc

GAP = 3                                  # mais que isto de meses sem linha → corrida nova
_MONTH = re.compile(r"(\d{4})-(0[1-9]|1[0-2])")


class SchemaError(ValueError):
    """O banco do site não tem a tabela/coluna que o arquivo precisa (ou tem um dado impossível)."""


# ── esquema ──────────────────────────────────────────────────────────────────────────────────
def tables(con) -> set[str]:
    return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def columns(con, table: str) -> list[str]:
    return [r[1] for r in con.execute(f'PRAGMA table_info("{table}")')]


def need(con, spec: dict[str, Iterable[str]], what: str) -> None:
    """Confere tabelas e colunas (`{tabela: [colunas]}`); o que faltar → SchemaError."""
    have = tables(con)
    for table, cols in spec.items():
        if table not in have:
            raise SchemaError(f"{what}: a tabela {table} não existe no banco")
        present = set(columns(con, table))
        missing = [c for c in cols if c not in present]
        if missing:
            raise SchemaError(f"{what}: falta(m) a(s) coluna(s) {', '.join(missing)} em {table}")


# ── meses ────────────────────────────────────────────────────────────────────────────────────
_INDEX: dict[str, int] = {}


def month_index(period) -> int:
    """"AAAA-MM" → número do mês (ano×12 + mês−1). Outro formato → SchemaError."""
    got = _INDEX.get(period) if isinstance(period, str) else None
    if got is not None:
        return got
    m = _MONTH.fullmatch(period) if isinstance(period, str) else None
    if not m:
        raise SchemaError(f"período fora do formato AAAA-MM: {period!r}")
    got = _INDEX[period] = int(m.group(1)) * 12 + int(m.group(2)) - 1
    return got


def month_label(index: int) -> str:
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def span(periods: Iterable[str]) -> tuple[str | None, str | None]:
    """(primeiro, último) "AAAA-MM" de uma coleção (None, None se vazia)."""
    idx = [month_index(p) for p in periods]
    if not idx:
        return None, None
    return month_label(min(idx)), month_label(max(idx))


def length(first: str | None, last: str | None) -> int:
    if first is None or last is None:
        return 0
    return month_index(last) - month_index(first) + 1


def dense(points: dict[str, object], first: str | None, last: str | None) -> list:
    """{período: valor} → array mensal de `first` a `last` (None onde não há)."""
    out = [None] * length(first, last)
    if out:
        base = month_index(first)
        for p, v in points.items():
            out[month_index(p) - base] = v
    return out


def stamp(values: Iterable) -> str | None:
    """O maior `updated_at` (o robô grava `datetime.utcnow().isoformat()`, sem fuso = UTC) → `…Z`.
    Normaliza só os DISTINTOS (são poucos: um por rodada do robô; 274 mil linhas, 10 carimbos)."""
    got = [iso_utc(v) for v in set(values)]
    got = [v for v in got if v]
    return max(got) if got else None


def label(value, what: str) -> str:
    """Um nome (país, porto, produto, fibra) como veio do banco — vazio/NULL → SchemaError (o nome
    é chave de dicionário no arquivo)."""
    if not isinstance(value, str) or not value:
        raise SchemaError(f"{what}: linha sem nome ({value!r})")
    return value


def put(target: dict, key, value, what: str) -> None:
    """Grava `target[key] = value`; chave repetida no banco (tabela sem chave primária) → erro: o
    arquivo não sai com um dos dois valores escolhido em silêncio."""
    if key in target:
        raise SchemaError(f"{what}: linha repetida no banco para {key}")
    target[key] = value


# ── corridas ─────────────────────────────────────────────────────────────────────────────────
def encode_runs(points: dict[int, tuple], metrics: int, gap: int = GAP) -> list:
    """{índice do mês: (métrica 1, …)} → [[início, [m1…], [m2…]], …]. Mês com todas as métricas
    None não entra (é "sem linha")."""
    none = (None,) * metrics
    idx = sorted(i for i, vals in points.items() if vals != none)
    out = []
    if not idx:
        return out
    get = points.get
    start = prev = idx[0]
    for i in idx[1:] + [None]:
        if i is None or i - prev - 1 > gap:
            rows = [get(j, none) for j in range(start, prev + 1)]
            out.append([start] + [list(col) for col in zip(*rows)])
            start = i
        if i is not None:
            prev = i
    return out


def expand_runs(runs: list) -> list[tuple[int, tuple]]:
    """A volta: [(índice do mês, (m1, …)), …] só dos meses com ao menos uma métrica (= as linhas)."""
    out = []
    for run in runs:
        start, arrays = run[0], run[1:]
        for k in range(len(arrays[0]) if arrays else 0):
            vals = tuple(a[k] for a in arrays)
            if any(v is not None for v in vals):
                out.append((start + k, vals))
    return out


def ordered(found: Iterable[str], preferred: Iterable[str] = ()) -> list[str]:
    """Os nomes achados: primeiro os da ordem preferida (que existirem), depois os outros em ordem de
    código Unicode (= a binária do SQLite)."""
    found = set(found)
    head = [x for x in preferred if x in found]
    return head + sorted(found - set(head))
