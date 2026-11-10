"""mw-insiders.json (COLD com sonda, conteúdo gzip com nome .json) — PLANO, Apêndice F.10.

`{ schemaVersion, generatedAt, updatedAt, from, columns: [company, refMonth, docVersion, entityType,
   entity, groupType, moveType, moveDesc, operation, assetType, assetClass, broker, moveDate, qty,
   unitPrice, volume, isBalance], rows: [[…], …] }`

As movimentações de insiders (CVM, Valores Mobiliários Negociados e Detidos) com `ref_month` ≥ a
exportação − 14 meses — a janela do site (`setMonth(-14)`, market-watch.html:333) —, TODAS as linhas:
a RPC `get_mw_insiders` é lida em páginas de `limit`/`offset` e a soma é conferida com a contagem do
mesmo filtro (o site recebe só 1.000 de ~2.040: defeito M1, corrigido). Em colunas (`columns` +
`rows`: 374 KB cru / 22 KB gzip contra 755 KB em objetos), na ORDEM DA RPC (`ref_month` decrescente,
`move_date` decrescente com nulos no fim, `group_type`, `id`) — o `id` só ordena e NÃO sai.

`from` = o 1º mês da janela (`AAAA-MM-01`: os meses de referência são o dia 1º; a janela anda 1×/mês).
`updatedAt` = o maior `updated_at` das linhas (a hora da última versão inserida pelo robô; fora do
hash) — é com ele e com a contagem que a partida a frio confere a cópia publicada sem reler o banco.
`moveDesc` é texto livre da companhia → e-mails e telefones mascarados (U6); `entity` (empresa do
grupo) e `broker` (corretora) vão como a CVM publicou. Valores crus, em português (a tela traduz
grupo e movimentação).
"""
from __future__ import annotations

from datetime import date, timedelta

from ..contacts import mask_contacts
from ..encode import iso_date, iso_utc, num

COLS = ("company,ref_month,doc_version,entity_type,entity,group_type,move_type,move_desc,operation,"
        "asset_type,asset_class,broker,move_date,qty,unit_price,volume,is_balance,updated_at")
ORDER = "ref_month.desc,move_date.desc.nullslast,group_type.asc,id.asc"   # = a da RPC; `id` desempata
COLUMNS = ["company", "refMonth", "docVersion", "entityType", "entity", "groupType", "moveType",
           "moveDesc", "operation", "assetType", "assetClass", "broker", "moveDate", "qty",
           "unitPrice", "volume", "isBalance"]
I_DESC = COLUMNS.index("moveDesc")
FORBIDDEN = frozenset({"cnpj", "cvm_code", "b3_code", "id", "updated_at"})


def _text(v) -> str | None:
    return (v.strip() or None) if isinstance(v, str) else None


def window_from(today: date) -> str:
    """O 1º mês da janela: `ref_month` ≥ hoje − 14 meses (o mesmo dia, transbordando como o
    `setMonth` do JavaScript: 30/04 − 14 meses = 02/03), e os meses de referência são o dia 1º → o 1º
    dia do mês seguinte ao corte, ou o próprio corte se cair no dia 1º."""
    y, m = today.year, today.month - 14
    while m <= 0:
        m += 12
        y -= 1
    cut = date(y, m, 1) + timedelta(days=today.day - 1)
    if cut.day == 1:
        return cut.isoformat()
    y, m = (cut.year + 1, 1) if cut.month == 12 else (cut.year, cut.month + 1)
    return date(y, m, 1).isoformat()


def row(r: dict) -> list | None:
    """Uma linha da RPC → a linha do arquivo (nas colunas de COLUMNS); None sem empresa ou mês."""
    co, ref = _text(r.get("company")), iso_date(r.get("ref_month"))
    if not co or not ref:
        return None
    dv = num(r.get("doc_version"))
    return [co, ref, int(dv) if dv is not None and float(dv).is_integer() else None,
            _text(r.get("entity_type")), _text(r.get("entity")), _text(r.get("group_type")),
            _text(r.get("move_type")), mask_contacts(_text(r.get("move_desc"))),
            _text(r.get("operation")), _text(r.get("asset_type")), _text(r.get("asset_class")),
            _text(r.get("broker")), iso_date(r.get("move_date")), num(r.get("qty")),
            num(r.get("unit_price")), num(r.get("volume")), r.get("is_balance") is True]


def stamp(rows: list[dict]) -> str | None:
    """O maior `updated_at` das linhas lidas (o `updatedAt` do arquivo)."""
    st = [s for s in (iso_utc(r.get("updated_at")) for r in rows) if s]
    return max(st) if st else None


def publish(r: list) -> list:
    out = list(r)
    out[I_DESC] = mask_contacts(out[I_DESC])
    return out


def build(src) -> dict:
    data = src.mw().insiders()
    return {"schemaVersion": 1, "generatedAt": src.generated_at, "updatedAt": data["updatedAt"],
            "from": data["from"], "columns": list(COLUMNS),
            "rows": [publish(r) for r in data["rows"]]}
