"""A base bruta "⤓ Data" — `mm-raw-<tabela>.json` ×11 e `pp-raw-<tabela>.json` ×8 (EVENT, conteúdo
gzip com nome .json, lidos só no clique) — PLANO, Apêndice G.11 (decisão U7 do usuário: manter).

`{ schemaVersion, generatedAt, table: "secex_country", columns: ["period", …],
   data: [[/* coluna 1 */], [/* coluna 2 */], …], dict: { "<coluna>": ["Afeganistão", …] } }`

UMA TABELA INTEIRA POR ARQUIVO, "exactly as they are stored": todas as colunas e linhas do `SELECT *`
do botão do site, NA ORDEM DELE (`_tableAOA`: `SMD:1644-1650`, `PPD:1257-1262` — a ordem natural
do banco; `secex_country` por `period, direction, category, country`). Nome do arquivo: a tabela em
minúsculas com hífen (`mm-raw-secex-country.json`). É a EXCEÇÃO DECLARADA à lista de campos que
nunca saem (G.0): aqui sai tudo o que o botão do site entrega (os dois bancos já são públicos no
GitHub) — `updated_at`, `revenue_usd_mn`, `value_usd`, `iba_pulp`, `company_q`… A rede de baixo
continua: coluna com nome proibido (e-mail, token, chave…) faz o arquivo FALHAR, e BLOB/infinito
também (não viram JSON sem perda).

FORMA, SEM PERDA E COMPACTA (por coluna):
  columns  os nomes, na ordem do `SELECT *`;
  data     uma lista por coluna, um valor por linha: número (inteiro fica inteiro, REAL fica REAL),
           texto ou `null` — exatamente o que o SQLite devolve;
  dict     coluna de TEXTO REPETIDO (todas as linhas com texto, sem NULL, e no máximo metade de
           valores distintos) sai como ÍNDICES (inteiros a partir de 0) em `dict[coluna]`, a lista dos
           valores distintos em ordem de código Unicode. Volta: `dict[coluna][índice]`. Coluna fora do
           `dict` vai crua. `dict` vazio = nenhuma coluna codificada.
O hub reconstrói as linhas idênticas às do site (`[cabeçalho, ...linhas]` para o Excel/CSV do painel);
o teste de ida e volta compara com o `SELECT *` (bit a bit nos REAL).
"""
from __future__ import annotations

from ..encode import FORBIDDEN_KEYS
from ..sectors import RAW_TABLES
from .sector_common import SchemaError, tables

ORDERS = {"secex_country": " ORDER BY period,direction,category,country"}
CHUNK = 20_000                     # linhas por bloco (o secex_country tem 274 mil)
_SCALARS = (int, float, type(None))


class _Column:
    """Os valores de uma coluna, bloco a bloco. Enquanto ela for só TEXTO, cada valor é guardado
    pelo objeto único do seu texto (o dicionário `pool`), não uma cópia por linha."""

    __slots__ = ("values", "pool", "table")

    def __init__(self, table: str):
        self.values: list = []
        self.pool: dict | None = {}
        self.table = table

    def extend(self, block: tuple) -> None:
        kinds = set(map(type, block))
        if self.pool is not None and kinds == {str}:
            self.values.extend(map(self.pool.setdefault, block, block))
            return
        if not kinds <= {str, *_SCALARS}:
            raise SchemaError(f"base bruta {self.table}: valor que não vira JSON sem perda "
                              f"({', '.join(sorted(t.__name__ for t in kinds - {str, *_SCALARS}))})")
        # (REAL infinito: o encode.to_json_bytes recusa — `allow_nan=False` — e o arquivo falha)
        self.pool = None                         # não é (só) texto: vai cru
        self.values.extend(block)

    def finish(self) -> tuple[list, list | None]:
        """(valores, dicionário ou None): texto repetido (distintos ≤ metade das linhas) → índices."""
        if self.pool is None or not self.values or len(self.pool) * 2 > len(self.values):
            return self.values, None
        order = sorted(self.pool)
        rank = {v: i for i, v in enumerate(order)}
        return list(map(rank.__getitem__, self.values)), order


def build(src, sector: str, table: str) -> dict:
    if table not in RAW_TABLES.get(sector, ()):
        raise ValueError(f"base bruta: {sector}/{table} fora da lista RAW_TABLES")
    con = src.sectors().db(sector)
    if table not in tables(con):
        raise SchemaError(f"base bruta: a tabela {table} não existe no banco")
    cur = con.execute(f'SELECT * FROM "{table}"' + ORDERS.get(table, ""))
    cols = [d[0] for d in cur.description]
    bad = [c for c in cols if str(c).lower() in FORBIDDEN_KEYS]
    if bad:
        raise SchemaError(f"base bruta {table}: coluna de nome proibido num arquivo público "
                          f"({', '.join(bad)})")
    acc = [_Column(table) for _ in cols]
    while True:
        rows = cur.fetchmany(CHUNK)
        if not rows:
            break
        for col, block in zip(acc, zip(*rows)):
            col.extend(block)
    data, dicts = [], {}
    for name, col in zip(cols, acc):
        values, order = col.finish()
        data.append(values)
        if order is not None:
            dicts[name] = order
    return {"schemaVersion": 1, "generatedAt": src.generated_at, "table": table, "columns": cols,
            "data": data, "dict": dicts}


def rows_of(obj: dict) -> list[tuple]:
    """A volta (a mesma conta do hub): as linhas `SELECT *` de um arquivo da base bruta."""
    cols = obj["columns"]
    data = [[obj["dict"][c][i] for i in col] if c in obj["dict"] else col
            for c, col in zip(cols, obj["data"])]
    return list(zip(*data)) if data else []
