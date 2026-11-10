"""Nome de arquivo por identificador (PLANO, Apêndice F.0) — a MESMA regra nos dois lados (no hub:
`historyFileName(ticker)` e `mwShortFileName(company)` em `metadata.ts`).

    file_id(id): aceita só `^[A-Z0-9][A-Z0-9._]*$`, sem `..` e sem `.` no fim; devolve o id em
                 minúsculas com `_` trocado por `-`. Recusado → None (o arquivo não sai; `id_problem`
                 diz por quê, para o log).
    id_of(fid):  a volta — maiúsculas, `-` → `_`. Reversível porque nenhum id aceito tem `-` nem
                 minúscula; um `fid` que não seja exatamente a ida de algum id aceito → None.

Por que assim (pesquisa 2-dados §5.3): o Feeder distingue caixa, o SharePoint não (→ minúsculas);
o hub monta a URL sem codificar (→ o nome tem de ser seguro em URL tal qual); o Feeder recusa `/`,
`\\`, `.`/`..` sozinhos e a sequência `..`. Exemplos: `VALE3.SA` → `vale3.sa`; `KLBN11` → `klbn11`;
`SP500_FUT` → `sp500-fut`; `^BVSP`, `TIO=F`, `@IRON_ORE_62`, `BRK-B` e `A..B` recusados.

Famílias de arquivos por identificador: `history-<fid>.json` (Onda 2, WP2.1B) e
`mw-short-<fid>.json` (Onda 2, WP2.4B) — `family_file`/`family_ident`.
"""
from __future__ import annotations

import re

_ID = re.compile(r"[A-Z0-9][A-Z0-9._]*")
_FID = re.compile(r"[a-z0-9][a-z0-9.\-]*")


def id_problem(ident) -> str | None:
    """Por que o identificador não vira nome de arquivo (None = aceito)."""
    if not isinstance(ident, str) or not ident:
        return "identificador vazio"
    if not _ID.fullmatch(ident):
        bad = sorted({c for c in ident if not (c.isascii() and (c.isupper() or c.isdigit()) or c in "._")})
        if bad:
            return "caractere fora de [A-Z0-9._]: " + " ".join(repr(c) for c in bad)
        return "tem de começar por letra maiúscula ou dígito"
    if ".." in ident:
        return "sequência '..'"
    if ident.endswith("."):
        return "termina em ponto"
    return None


def file_id(ident) -> str | None:
    """O pedaço do nome de arquivo de um identificador (F.0), ou None se recusado."""
    if id_problem(ident) is not None:
        return None
    return ident.lower().replace("_", "-")


def id_of(fid) -> str | None:
    """A volta de `file_id`: o identificador de um pedaço de nome, ou None se não é a ida de nenhum."""
    if not isinstance(fid, str) or not _FID.fullmatch(fid):
        return None
    ident = fid.upper().replace("-", "_")
    return ident if file_id(ident) == fid else None


def family_file(prefix: str, ident) -> str | None:
    """`<prefixo>-<fileId>.json` (ex.: history-vale3.sa.json), ou None se o id é recusado."""
    fid = file_id(ident)
    return f"{prefix}-{fid}.json" if fid else None


def family_ident(prefix: str, name) -> str | None:
    """O identificador de um nome da família (`history-vale3.sa.json` → `VALE3.SA`), ou None."""
    head = prefix + "-"
    if not isinstance(name, str) or not name.startswith(head) or not name.endswith(".json"):
        return None
    return id_of(name[len(head):-len(".json")])
