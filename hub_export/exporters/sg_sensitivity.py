"""sg-sensitivity.json (COLD com sonda, conteúdo gzip com nome .json) — PLANO, Apêndice H.2.

    { schemaVersion, generatedAt, updatedAt, source: {stamp, rows, rule},
      companies: [{ code, order, currency, modelDate, sourceFile, modelPrice, mktcapModel, mktcapModelNav,
                    assumptions,
                    sheets: [{ name, analysis, tables: [{ id, metric, year, title, pairId, colLabel, rowLabel,
                                                          colLevels, rowLevels, values }] }] }] }

As grades 9×9 que o analista publica no `_Sensitivity.xlsx` de cada modelo e que a aba Sensitivity do site
DESENHA — 72 em 26/11/2026 (16 empresas; a Vale com 3 análises). Uma linha por empresa, na ordem da RPC
(`display_order`, id).

ORIGEM: a RPC `get_stock_guide_sensitivity_tables()` com a chave de serviço (visão de cliente: a empresa
oculta já vem de fora), pedida só com `definition->>kind = sens2d` (a RPC devolve também os 2 exemplos
fictícios de junho, no formato antigo — medido em 13/11: o filtro funciona sobre o resultado da RPC); o
`kind` é conferido de novo aqui. A grade 6-D, a malha antiga e os drivers não são lidos.

QUAIS GRADES (porte de `_s2Sheets`, stock-guide.html:810-833, e de `_s2Analyses`/`_s2AnaView`, 1282-1296,
origin/main a92c19f):
  • o conjunto POR EMPRESA vale antes de tudo (`_S2_SHOW_EMPRESA`, :816-820, pelo código da empresa:
    `definition.ticker`, ou o `import_ticker` quando ele falta, em maiúsculas e sem aparar, como o site): o
    COBRE (Hudbay, Lundin, Capstone, Ero) mostra EBITDA, FCF, FCF yield e P/NAV — SEM o EV/EBITDA, que o
    analista escondeu em 25/11 (U18 = a; a grade continua guardada no banco e não sai);
  • as outras: só EBITDA, EV/EBITDA, FCF e FCF yield; empresa sem nenhuma dessas quatro (a desenvolvedora:
    Aclara, Viridis) → DCF upside e P/NAV. Margem e dividend yield nunca (as 3 grades guardadas e escondidas —
    o dividend yield da Vale e o upside/P-NAV da Aura — ficam fora);
  • "2027 de preferência": se alguma grade mostrável é de 2027, as de outro ano saem; a SEM ano no título
    ("FCF Ex-Prepayments" da CMIN, o P/NAV de longo prazo do cobre) fica. O ano compara como o
    `Number(x.year) === 2027` do JavaScript;
  • empresa com ANÁLISES (a Vale: `sheets[].analysis.key` = fx, freight, copper): o corte é feito análise por
    análise, como a tela (cada análise é uma "vista" com as abas dela e o código da empresa); aba sem
    `analysis` numa empresa que tem análises nunca é desenhada e não sai. Aba sem nenhuma grade mostrável sai.
CAMPO A CAMPO: da empresa e de cada análise, os campos do modelo que a tela usa — `currency`, `modelDate`,
`sourceFile` (o cabeçalho do painel), `modelPrice` e `mktcapModel` (a reescrita ao vivo dos múltiplos),
`mktcapModelNav` (a base do P/NAV do cobre, do quadro "Implicit Cu Prices": milhões, como o banco guarda; a
trava de ±10 % é do hub, como `_s2Ctx`, :1094-1099), `assumptions` `[{label, value}]` (o carimbo
"base case"); `null` = ausente (a análise vale por cima da empresa só onde não é null, como `_s2AnaView`). A
chave da análise (`key`, palavra proibida) sai como `id`. Títulos, rótulos e níveis como o analista escreveu
(memória "não reescrever siglas"). Nunca saem: `kind`, `panel`, `import_ticker`, `shares_mn`, `corner`, as
notas internas (`title_note`, `title_original`, `label_note`, `mktcap_note`, `assumptions_note`), os consertos
à mão do cobre (`model_price_original`, `model_price_note`, `tables_omitted`, `mktcap_model_nav_note`),
`updated_by` e as grades não desenhadas (o registro proíbe as chaves).

`source` = o MARCADOR da partida a frio — a sonda (contagem e maior `updated_at` das linhas `sens2d` da
tabela) + `rule`, a versão da regra de LEITURA (o corte acima e os campos tirados do banco) — DENTRO do hash;
`updatedAt` = o carimbo (fora). A regra muda sem o banco mudar: sem a versão, a cópia publicada velha bateria
com a sonda e serviria de semente (e o estado do runner, de dado guardado) até a leitura de segurança de
segunda-feira. Mudou o corte ou um campo tirado do banco → suba `RULE`: o primeiro ciclo do arquivo relê.
FLAGS no dado (O50): `stock_guide` ou `stock_guide_sensitivity` desligada → `companies: []`, nada lido.
"""
from __future__ import annotations

import json
import re

from ..encode import num

COLS = "id,display_order,definition"
KIND = "sens2d"
KIND_FILTER = {"definition->>kind": f"eq.{KIND}"}
# o corte da tela (stock-guide.html, origin/main a92c19f)
SHOW = frozenset({"ebitda", "ev_ebitda", "fcf", "fcf_yield"})        # _S2_SHOW, :810
SHOW_DEV = frozenset({"upside", "p_nav"})                            # _S2_SHOW_DEV, :815
SHOW_COPPER = frozenset({"ebitda", "fcf", "fcf_yield", "p_nav"})     # _S2_SHOW_COBRE, :819
SHOW_COMPANY = {"HBM": SHOW_COPPER, "LUN": SHOW_COPPER,              # _S2_SHOW_EMPRESA, :820 — a MESMA
                "CS": SHOW_COPPER, "ERO": SHOW_COPPER}               # lista mora no site e no hub: uma 5ª
                                                                     # empresa muda nos três (e sobe RULE)
SHOW_YEAR = 2027                                                     # _S2_ANO, :821
# versão da regra de leitura, no marcador `source` (ver o docstring): 1 = a da Onda 4 (sem o campo no
# arquivo); 2 = o conjunto por empresa (cobre) + `mktcapModelNav` (WP5.1B, 26/11/2026)
RULE = 2
COMPANY_KEYS = ("code", "order", "currency", "modelDate", "sourceFile", "modelPrice", "mktcapModel",
                "mktcapModelNav", "assumptions", "sheets")
META_KEYS = ("currency", "modelDate", "sourceFile", "modelPrice", "mktcapModel", "mktcapModelNav",
             "assumptions")
ANALYSIS_KEYS = ("id", "label") + META_KEYS
SHEET_KEYS = ("name", "analysis", "tables")
TABLE_KEYS = ("id", "metric", "year", "title", "pairId", "colLabel", "rowLabel", "colLevels", "rowLevels",
              "values")
ASSUMPTION_KEYS = ("label", "value")
SOURCE_KEYS = ("stamp", "rows", "rule")
FORBIDDEN = frozenset({"kind", "panel", "import_ticker", "importTicker", "shares_mn", "sharesMn", "corner",
                       "title_note", "titleNote", "title_original", "titleOriginal", "label_note",
                       "labelNote", "mktcap_note", "mktcapNote", "assumptions_note", "assumptionsNote",
                       "note", "updated_by", "updatedBy", "drivers", "grid", "grid6", "base",
                       # os consertos à mão do cobre (25/11; nomes conferidos no banco em 26/11): o preço do
                       # D12, a grade omitida e a nota da base do P/NAV da Ero
                       "model_price_original", "modelPriceOriginal", "model_price_note", "modelPriceNote",
                       "tables_omitted", "tablesOmitted", "mktcap_model_nav_note", "mktcapModelNavNote"})
_NUM_TEXT = re.compile(r"[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?")


# ── os valores ──────────────────────────────────────────────────────────────────────────────────
def _raw_text(v) -> str | None:
    """Texto como o analista escreveu (espaços inclusive); vazio ou não-texto → None."""
    return v if isinstance(v, str) and v.strip() else None


def _number(v):
    if isinstance(v, str):
        s = v.strip()
        return float(s) if _NUM_TEXT.fullmatch(s) else None
    return num(v)


def _js_number(v) -> float | None:
    """`Number(v)` do JavaScript nos casos que importam ao corte do ano (None = NaN)."""
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        x = float(v)
        return x if x == x else None
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return 0.0
        return float(s) if _NUM_TEXT.fullmatch(s) else None
    return None


def _is_year(v) -> bool:
    return v is not None and _js_number(v) == SHOW_YEAR


def _year(v) -> int | None:
    x = _js_number(v) if v is not None else None
    return int(x) if x is not None and x.is_integer() else None


def _js_string(v) -> str | None:
    """`String(v)` do JavaScript para chave/identificador (None continua None)."""
    if v is None:
        return None
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, str):
        return v
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    if isinstance(v, (int, float)):
        return repr(v)
    return json.dumps(v, ensure_ascii=False, sort_keys=True)


def _js_truthy(v) -> bool:
    """A verdade do JavaScript (`a || b`): falsos são null, false, 0, NaN e ""; lista e objeto, mesmo
    vazios, são verdadeiros."""
    if v is None or isinstance(v, bool):
        return bool(v)
    if isinstance(v, (int, float)):
        return v == v and v != 0
    if isinstance(v, str):
        return v != ""
    return True


def show_key(definition: dict) -> str:
    """O código com que a tela procura o conjunto da empresa: `String((d&&(d.ticker||d.import_ticker))||'')
    .toUpperCase()` (stock-guide.html:825) — sem aparar, como lá."""
    if not isinstance(definition, dict):
        return ""
    v = definition.get("ticker")
    if not _js_truthy(v):
        v = definition.get("import_ticker")
    return (_js_string(v) or "").upper() if _js_truthy(v) else ""


def _levels(v):
    return [_number(x) for x in v] if isinstance(v, list) else None


def _values(v) -> list:
    if not isinstance(v, list):
        return []
    return [[_number(c) for c in row] if isinstance(row, list) else None for row in v]


def _assumptions(v):
    """`[{label, value}]`; item sem rótulo não serve ao carimbo e sai. Lista ausente = None (a análise
    não vale por cima da empresa); lista vazia fica vazia (vale, como no site)."""
    if not isinstance(v, list):
        return None
    out = []
    for a in v:
        if isinstance(a, dict) and _raw_text(a.get("label")):
            out.append({"label": a["label"], "value": _number(a.get("value"))})
    return out


def _meta(m: dict) -> dict:
    return {"currency": _raw_text(m.get("currency")), "modelDate": _raw_text(m.get("model_date")),
            "sourceFile": _raw_text(m.get("source_file")), "modelPrice": _number(m.get("model_price")),
            "mktcapModel": _number(m.get("mktcap_model")),
            "mktcapModelNav": _number(m.get("mktcap_model_nav")),
            "assumptions": _assumptions(m.get("assumptions"))}


def _table(x: dict) -> dict:
    return {"id": _js_string(x.get("id")), "metric": x.get("metric"), "year": _year(x.get("year")),
            "title": _raw_text(x.get("title")), "pairId": _js_string(x.get("pairId")),
            "colLabel": _raw_text(x.get("colLabel")), "rowLabel": _raw_text(x.get("rowLabel")),
            "colLevels": _levels(x.get("colLevels")) or [], "rowLevels": _levels(x.get("rowLevels")),
            "values": _values(x.get("values"))}


# ── o porte do corte da tela ─────────────────────────────────────────────────────────────────────
def _tables(sh: dict) -> list[dict]:
    t = sh.get("tables")
    return [x for x in t if isinstance(x, dict)] if isinstance(t, list) else []


def _metric(x: dict):
    m = x.get("metric")
    return m if isinstance(m, str) else None


def drawn(sheets: list[dict], code: str = "") -> list[tuple[dict, list[dict]]]:
    """Porte de `_s2Sheets` (stock-guide.html:822-833): (aba, as grades que a tela desenha), na ordem, sem
    as abas vazias. `code` = `show_key` da empresa: o conjunto dela (`_S2_SHOW_EMPRESA`) vale antes do
    padrão (:825-826)."""
    show = SHOW_COMPANY.get(code)
    if show is None:
        padrao = any(_metric(x) in SHOW for sh in sheets for x in _tables(sh))
        show = SHOW if padrao else SHOW_DEV
    shs = [(sh, [x for x in _tables(sh) if _metric(x) in show]) for sh in sheets]
    if any(_is_year(x.get("year")) for _, ts in shs for x in ts):
        shs = [(sh, [x for x in ts if x.get("year") is None or _is_year(x.get("year"))]) for sh, ts in shs]
    return [(sh, ts) for sh, ts in shs if ts]


def _analysis_key(sh: dict) -> str | None:
    a = sh.get("analysis")
    if not isinstance(a, dict) or a.get("key") is None:
        return None
    return _js_string(a["key"])


def drawn_sheets(definition: dict) -> list[dict]:
    """As abas que a tela desenha de uma empresa — análise por análise quando há análises (`_s2Analyses`;
    a vista de cada uma, `_s2AnaView`, guarda o código da empresa: o conjunto dela vale ali também)."""
    raw = definition.get("sheets")
    sheets = [sh for sh in raw if isinstance(sh, dict)] if isinstance(raw, list) else []
    code = show_key(definition)
    keys: list[str] = []
    for sh in sheets:
        k = _analysis_key(sh)
        if k is not None and k not in keys:
            keys.append(k)
    if keys:
        keep: dict[int, list[dict]] = {}
        for k in keys:
            for sh, ts in drawn([s for s in sheets if _analysis_key(s) == k], code):
                keep[id(sh)] = ts
        pairs = [(sh, keep[id(sh)]) for sh in sheets if id(sh) in keep]
    else:
        pairs = drawn(sheets, code)
    out = []
    for sh, ts in pairs:
        a = sh.get("analysis") if keys else None
        out.append({"name": _raw_text(sh.get("name")),
                    "analysis": ({"id": _analysis_key(sh), "label": _raw_text(a.get("label")), **_meta(a)}
                                 if isinstance(a, dict) and _analysis_key(sh) is not None else None),
                    "tables": [_table(x) for x in ts]})
    return out


def company(row: dict) -> dict | None:
    """Uma linha `sens2d` da RPC → a empresa (só com as grades desenhadas); outra coisa → None."""
    d = row.get("definition")
    if not isinstance(d, dict) or d.get("kind") != KIND:
        return None
    code = d.get("ticker").strip() if isinstance(d.get("ticker"), str) and d["ticker"].strip() else None
    if not code:
        return None
    order = _number(row.get("display_order"))
    return {"code": code, "order": int(order) if order is not None and float(order).is_integer() else None,
            **_meta(d), "sheets": drawn_sheets(d)}


def companies(rows: list[dict]) -> list[dict]:
    """As linhas da RPC, na ordem dela (`display_order`, id) → as empresas `sens2d`."""
    def key(r):
        o, i = _number(r.get("display_order")), _number(r.get("id"))
        return (o if o is not None else 0, i if i is not None else 0)
    return [c for c in (company(r) for r in sorted((r for r in rows if isinstance(r, dict)), key=key)) if c]


def tables_count(items: list[dict]) -> int:
    return sum(len(sh.get("tables") or []) for c in items for sh in c.get("sheets") or [])


# ── a montagem (campo a campo de novo: as empresas podem vir do estado ou da cópia publicada) ──────
def _pick(item, keys) -> dict:
    return {k: item.get(k) for k in keys} if isinstance(item, dict) else {k: None for k in keys}


def _assum_out(v):
    return [_pick(a, ASSUMPTION_KEYS) for a in v if isinstance(a, dict)] if isinstance(v, list) else None


def _company_out(c: dict) -> dict:
    out = _pick(c, COMPANY_KEYS)
    out["assumptions"] = _assum_out(c.get("assumptions"))
    sheets = []
    for sh in c.get("sheets") or []:
        if not isinstance(sh, dict):
            continue
        s = _pick(sh, SHEET_KEYS)
        if isinstance(sh.get("analysis"), dict):
            s["analysis"] = _pick(sh["analysis"], ANALYSIS_KEYS)
            s["analysis"]["assumptions"] = _assum_out(sh["analysis"].get("assumptions"))
        else:
            s["analysis"] = None
        s["tables"] = [_pick(t, TABLE_KEYS) for t in sh.get("tables") or [] if isinstance(t, dict)]
        sheets.append(s)
    out["sheets"] = sheets
    return out


def from_published(doc: dict) -> list[dict]:
    """As empresas de uma cópia publicada (a semente da partida a frio), campo a campo."""
    return [_company_out(c) for c in doc.get("companies") or [] if isinstance(c, dict)
            and isinstance(c.get("code"), str) and c["code"].strip()]


def build(src) -> dict:
    d = src.sensitivity()
    return {
        "schemaVersion": 1,
        "generatedAt": src.generated_at,
        "updatedAt": d["updatedAt"],
        "source": _pick(d["source"], SOURCE_KEYS),
        "companies": [_company_out(c) for c in d.get("companies") or []],
    }
