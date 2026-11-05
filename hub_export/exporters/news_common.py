"""O item de notícia do contrato (PLANO, Apêndice E.1), montado CAMPO A CAMPO de uma linha de
`news_articles` — comum ao `news-latest.json` e aos `news-week-*.json`.

    id  url  title  source  domain  primary  publishedAt  foundAt  take  sector  desk
    companies  topics  snippet  reason(só no news-latest)

• `url` passa pelo filtro de URL; link recusado = o item sai (não há notícia sem link).
• `source`/`desk`/`primary` saem RESOLVIDOS (regras do site em news_reference).
• `take` é o da IA (`take_llm`); `null` = pendente. O take do robô nunca é publicado.
• `snippet`: sem tags, espaços normalizados, até 200 caracteres; `null` se vazio ou de FONTE PAGA.
• datas normalizadas aqui (ISO UTC `Z`), antes de qualquer filtro ou comparação.

FONTES PAGAS (decisão DEFINITIVA do usuário, 05/11/2026: "só o título"): notícia de fonte
coletada COM ASSINATURA vai só com o título, `snippet: null`. São as dos raspadores com login
(hunter/platts_scraper.py e hunter/fastmarkets_scraper.py, únicos que usam
`playwright_session.load_credentials`): `source_name` "S&P Platts" (domínio core.spglobal.com) e
"Fastmarkets" (dashboard.fastmarkets.com). "Fastmarkets Insights" é RSS aberto (sources.py:79-81)
e fica com o trecho. `HIDE_PAID_SNIPPETS` existe SÓ para o caso de o usuário mudar de ideia —
o padrão é ligado.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone

from ..encode import iso_utc
from ..urls import url_problem
from .news_reference import desk, is_primary, label

HIDE_PAID_SNIPPETS = True
PAID_SOURCE_NAMES = frozenset({"S&P Platts", "Fastmarkets"})
PAID_DOMAINS = frozenset({"core.spglobal.com", "dashboard.fastmarkets.com"})

SNIPPET_MAX = 200
TAKES = ("+", "-", "=", "no take", "review")
ITEM_KEYS = ("id", "url", "title", "source", "domain", "primary", "publishedAt", "foundAt", "take",
             "sector", "desk", "companies", "topics", "snippet", "reason")

# colunas pedidas ao banco (nunca '*'; nada fora da lista branca do contrato além das datas)
NEWS_COLS = ("url,domain,source_name,title,snippet,published_at,found_at,take_llm,take_reason,"
             "sector,take_covered_companies,take_topics")
INCREMENTAL_COLS = NEWS_COLS + ",take_llm_at"
# varredura: só o que MUDA depois da inserção (o resto é congelado pelo INSERT ignore-duplicates)
SWEEP_COLS = ("url,published_at,found_at,take_llm,take_covered_companies,take_topics,sector,"
              "take_reason")

WEIRD_AHEAD = timedelta(hours=1)        # publishedAt > foundAt + 1 h → data esquisita
WEIRD_BEHIND = timedelta(days=8)        # publishedAt < foundAt − 8 dias → data esquisita
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def news_id(url: str) -> str:
    """sha1(url)[:12]: chave curta e estável (React, junção latest × semanas)."""
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]


def is_paid(source_name, domain) -> bool:
    return ((isinstance(source_name, str) and source_name in PAID_SOURCE_NAMES)
            or (isinstance(domain, str) and domain.lower() in PAID_DOMAINS))


def clean_snippet(value):
    """Tags viram espaço, espaços em sequência viram um, corta em 200 e tira a sobra das pontas."""
    if not isinstance(value, str):
        return None
    text = _WS.sub(" ", _TAG.sub(" ", value)).strip()[:SNIPPET_MAX].rstrip()
    return text or None


def _text(value):
    return (value.strip() or None) if isinstance(value, str) else None


def split_list(value) -> list[str]:
    """"VALE; CSN" → ["VALE", "CSN"]: separado por `;`, sem espaços nas pontas, na ordem, sem
    pedaço vazio (lista do banco também é aceita)."""
    if isinstance(value, str):
        parts = value.split(";")
    elif isinstance(value, list):
        parts = [v for v in value if isinstance(v, str)]
    else:
        return []
    return [p.strip() for p in parts if p.strip()]


def take(value):
    v = value.strip() if isinstance(value, str) else None
    return v if v in TAKES else None


def _domain(value):
    if not isinstance(value, str) or not value.strip():
        return None
    d = value.strip().lower()
    return d[4:] if d.startswith("www.") else d


def item_from_row(row: dict) -> tuple[dict | None, str | None]:
    """(item, None) ou (None, motivo) — motivo curto para a estatística do log."""
    url = row.get("url")
    prob = url_problem(url)
    if prob:
        return None, f"url {prob}"
    title = _text(row.get("title"))
    if not title:
        return None, "sem título"
    found = iso_utc(row.get("found_at"))
    if not found:
        return None, "sem found_at"
    sn = row.get("source_name") if isinstance(row.get("source_name"), str) else None
    dom = _domain(row.get("domain"))
    src = label(sn, dom)
    sector = _text(row.get("sector"))
    url = url.strip()
    item = {
        "id": news_id(url),
        "url": url,
        "title": title,
        "source": src,
        "domain": dom,
        "primary": is_primary(sn),
        "publishedAt": iso_utc(row.get("published_at")),
        "foundAt": found,
        "take": take(row.get("take_llm")),
        "sector": sector,
        "desk": desk(src, sector),
        "companies": split_list(row.get("take_covered_companies")),
        "topics": split_list(row.get("take_topics")),
        "snippet": None if (HIDE_PAID_SNIPPETS and is_paid(sn, dom))
        else clean_snippet(row.get("snippet")),
        "reason": _text(row.get("take_reason")),
    }
    return item, None


def apply_mutable(item: dict, row: dict) -> bool:
    """Aplica a um item os campos que MUDAM depois da inserção (varredura: take da IA, empresas,
    temas, setor, motivo; o setor exibido é recalculado pelo rótulo). True se algo mudou."""
    sector = _text(row.get("sector"))
    new = {"take": take(row.get("take_llm")), "sector": sector, "desk": desk(item["source"], sector),
           "companies": split_list(row.get("take_covered_companies")),
           "topics": split_list(row.get("take_topics")), "reason": _text(row.get("take_reason"))}
    changed = any(item.get(k) != v for k, v in new.items())
    item.update(new)
    return changed


def from_published(obj) -> dict | None:
    """Um item de uma cópia PUBLICADA (semente da partida a frio) de volta à forma do cache.
    É o nosso próprio formato: confere o mínimo e recompõe na ordem do contrato."""
    if not isinstance(obj, dict) or url_problem(obj.get("url")) or not _text(obj.get("title")):
        return None
    found = iso_utc(obj.get("foundAt"))
    if not found or not isinstance(obj.get("source"), str):
        return None
    url = obj["url"].strip()
    out = {k: obj.get(k) for k in ITEM_KEYS}
    out.update(id=news_id(url), url=url, foundAt=found, publishedAt=iso_utc(obj.get("publishedAt")),
               primary=obj.get("primary") is True, take=take(obj.get("take")),
               desk=obj.get("desk") if obj.get("desk") in ("sm", "pp", "nr") else "nr",
               companies=split_list(obj.get("companies")), topics=split_list(obj.get("topics")),
               snippet=_text(obj.get("snippet")), reason=_text(obj.get("reason")),
               domain=_domain(obj.get("domain")), sector=_text(obj.get("sector")))
    return out


def week_item(item: dict) -> dict:
    """O item como vai nos arquivos semanais: igual, SEM `reason` (a News não usa)."""
    return {k: item[k] for k in ITEM_KEYS if k != "reason"}


# ── datas de referência ────────────────────────────────────────────────────────────────────────
def _dt(iso: str) -> datetime:
    return datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def is_weird(item: dict) -> bool:
    """publishedAt mais de 1 h DEPOIS do foundAt, ou mais de 8 dias ANTES (Apêndice E.3)."""
    pub = item.get("publishedAt")
    if not pub:
        return False
    p, f = _dt(pub), _dt(item["foundAt"])
    return p > f + WEIRD_AHEAD or p < f - WEIRD_BEHIND


def plain_iso(item: dict) -> str:
    """Data efetiva do contrato: `publishedAt ?? foundAt`."""
    return item.get("publishedAt") or item["foundAt"]


def bucket_iso(item: dict) -> str:
    """A data que põe a notícia na sua semana (e na janela do news-latest): a efetiva, mas a data
    esquisita cai no `foundAt`."""
    return item["foundAt"] if is_weird(item) else plain_iso(item)


def bucket_dt(item: dict) -> datetime:
    return _dt(bucket_iso(item))


def sort_latest(items: list[dict]) -> list[dict]:
    """`foundAt` decrescente, desempate por `id`."""
    return sorted(sorted(items, key=lambda i: i["id"]), key=lambda i: i["foundAt"], reverse=True)


def sort_week(items: list[dict]) -> list[dict]:
    """Data de referência decrescente, desempate por `id`."""
    return sorted(sorted(items, key=lambda i: i["id"]), key=bucket_iso, reverse=True)
