"""glossary.json (EVENT: o repositório do site pela API do GitHub; conteúdo gzip com nome .json) — PLANO,
Apêndice H.5.

    { schemaVersion, generatedAt, updatedAt, source: {blob, commit, committedAt},
      sections: [{ id, title, intro, items: [{ term, unit, freq, source, desc, route }] }] }

ORIGEM: `admin/glossary.json` do repositório PÚBLICO do site (`JPHELITO/IBBA-Research-Dashboard`, `main`),
editado à mão pelo time — a página Data do site o busca na Vercel. Aqui vem pela API do GitHub, conferido
pelo SHA do blob (sitefiles.py; zero Supabase). `source` = o marcador da partida a frio (o blob) e de onde
veio (o último commit que tocou o arquivo e quando) — dentro do hash; `updatedAt` = a data desse commit
(não o `generated` escrito à mão no arquivo).

CAMPO A CAMPO: seção `id`, `title`, `intro`; termo `term` (sem termo, o item sai), `unit`, `freq`, `source`,
`desc` — o texto como o analista escreveu (conteúdo em inglês nas duas línguas, O55), com e-mail/telefone
mascarados e URL pelo filtro (freetext.clean_text; nada disso existe hoje) — e `route`.
`route` = a SEÇÃO DO HUB para o "Open →" do site, pela tabela FECHADA abaixo (T24): os 15 endereços do site
(`where`) e as exceções POR TERMO onde o site aponta a página-mãe (as linhas laranja e preta → o Modelo
preditivo; GACC → Woodchips; Empapel e LTM → Corrugated). Endereço desconhecido → `null` (sem link) e UMA
linha de aviso no log; sem endereço → `null`. O caminho do site (`where`) e a nota do editor (`note`) NUNCA
saem (o registro proíbe as chaves neste arquivo).
"""
from __future__ import annotations

import logging
import re

from ..freetext import clean_text

log = logging.getLogger(__name__)

PATH = "admin/glossary.json"          # no repositório do site
# as rotas do hub que um termo pode abrir (H.5): `""` = Início
ROUTES = frozenset({"", "news", "market", "steel-prices", "steel-domestic", "steel-imports",
                    "steel-prediction", "steel-exports", "pulp-secex", "woodchips", "paper-fundamentals",
                    "corrugated", "stock-guide", "stock-guide-sensitivity", "short-interest", "buybacks",
                    "insiders", "filings"})
_SM = "/Steel and Mining/steel_sm_dashboard.html"
_PP = "/Pulp and Paper/pp_dashboard.html"
# os 15 endereços do site (`where` do `admin/glossary.json`, origin/main cffdadc) → a seção do hub
WHERE_ROUTE = {
    "/index.html": "",
    "/news.html": "news",
    "/market.html": "market",
    _SM + "#prices": "steel-prices",
    _SM + "#domestic": "steel-domestic",
    _SM + "#imports": "steel-imports",
    _SM + "#exports": "steel-exports",
    _PP + "#pulp": "pulp-secex",
    _PP + "#paper": "paper-fundamentals",
    "/stock-guide.html#comp": "stock-guide",
    "/stock-guide.html#sens": "stock-guide-sensitivity",
    "/market-watch.html": "short-interest",
    "/market-watch.html?tab=buybacks": "buybacks",
    "/market-watch.html?tab=insiders": "insiders",
    "/market-watch.html?tab=filings": "filings",
}
# onde o site aponta a página-mãe e o hub tem a seção do próprio termo (o termo como está no arquivo,
# comparado sem diferença de caixa e de espaços); só vale para termo que o site já leva a algum lugar
TERM_ROUTE = {
    "Import model — orange line": "steel-prediction",
    "Customs-of-origin exports — black line (Korea, China)": "steel-prediction",
    "China woodchip imports (GACC)": "woodchips",
    "Corrugated shipments (Empapel / IBPO)": "corrugated",
    "LTM shipments": "corrugated",
}
SECTION_KEYS = ("id", "title", "intro", "items")
ITEM_KEYS = ("term", "unit", "freq", "source", "desc", "route")
SOURCE_KEYS = ("blob", "commit", "committedAt")
FORBIDDEN = frozenset({"where", "note", "generated"})
_SPACES = re.compile(r"\s+")


def _norm(term: str) -> str:
    return _SPACES.sub(" ", term).strip().casefold()


_TERMS = {_norm(t): r for t, r in TERM_ROUTE.items()}


def _text(v) -> str | None:
    return (v.strip() or None) if isinstance(v, str) else None


def route(term, where, unknown: list | None = None) -> str | None:
    """A seção do hub do termo: pelo endereço do site e, onde o site aponta a página-mãe, pelo termo."""
    w = _text(where)
    if w is None:
        return None
    if w not in WHERE_ROUTE:
        if unknown is not None and w not in unknown:
            unknown.append(w)
        return None
    if isinstance(term, str) and _norm(term) in _TERMS:
        return _TERMS[_norm(term)]
    return WHERE_ROUTE[w]


def sections(doc: dict) -> list[dict]:
    """O arquivo do site (já lido como JSON) → as seções do arquivo público. Endereço desconhecido: uma
    linha de aviso (sem a nota do editor, sem o resto do arquivo)."""
    unknown: list[str] = []
    out = []
    for s in doc.get("sections") or [] if isinstance(doc, dict) else []:
        if not isinstance(s, dict) or not _text(s.get("id")):
            continue
        items = []
        for it in s.get("items") or []:
            if not isinstance(it, dict) or not clean_text(it.get("term")):
                continue
            items.append({"term": clean_text(it.get("term")), "unit": clean_text(it.get("unit")),
                          "freq": clean_text(it.get("freq")), "source": clean_text(it.get("source")),
                          "desc": clean_text(it.get("desc")),
                          "route": route(it.get("term"), it.get("where"), unknown)})
        out.append({"id": _text(s["id"]), "title": clean_text(s.get("title")) or _text(s["id"]),
                    "intro": clean_text(s.get("intro")), "items": items})
    if unknown:
        log.warning("glossary.json: endereço do site sem seção no hub (sai sem link): %s",
                    ", ".join(unknown[:5]) + ("…" if len(unknown) > 5 else ""))
    return out


def _pick(item, keys) -> dict:
    return {k: item.get(k) for k in keys} if isinstance(item, dict) else {k: None for k in keys}


def _sections_out(items) -> list[dict]:
    out = []
    for s in items or []:
        if not isinstance(s, dict):
            continue
        sec = _pick(s, SECTION_KEYS)
        sec["items"] = [_pick(i, ITEM_KEYS) for i in s.get("items") or [] if isinstance(i, dict)]
        out.append(sec)
    return out


def build(src) -> dict:
    g = src.sitefiles().glossary()
    source = _pick(g["source"], SOURCE_KEYS)
    return {
        "schemaVersion": 1,
        "generatedAt": src.generated_at,
        "updatedAt": source.get("committedAt"),
        "source": source,
        "sections": _sections_out(g["sections"]),
    }
