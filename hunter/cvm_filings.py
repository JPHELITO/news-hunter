"""Comunicados oficiais (fatos relevantes, comunicados ao mercado…) como FONTE do News Hunter.

De onde vem: a tabela `mw_filings` do Supabase, que o robô do Market Watch
(IBBA-Research-Dashboard/_shared/market_watch.py) alimenta em tempo real a partir do
Plantão de Notícias da B3 — e que já traz, para cada comunicado da cobertura, o link do
documento na CVM, o título REAL (1ª linha do PDF) e um trecho do texto.

Por que entra pelo hunter (e não direto em news_articles): assim o comunicado passa pelo
MESMO caminho de toda notícia — dedup por URL, classificação de setor, take determinístico
e a fila da IA de takes — e aparece no feed, no clipping e no Market Pulse sem código à parte.

Regras:
  • só o que o Market Watch marcou como `is_newsworthy` (fato relevante, comunicado, aviso
    aos acionistas, participação acionária, esclarecimento CVM/B3, press-release). Atas,
    posições de VM, apresentações e ITR ficam só na aba Filings do Market Watch.
  • fonte curada (`source_name = "CVM"`): aceita tudo (pass_through no filter.py) e nunca é
    excluída do relatório (news_take_classifier._ALWAYS_INCLUDE_SOURCES).
  • setor vem da EMPRESA (mapa abaixo), não da keyword — "Vale informa nova composição do
    Comitê" não tem termo de minério no título e cairia em NR.
  • se a tabela não existir ainda (SQL não rodado) ou o Supabase estiver fora, devolve []
    e loga — nunca derruba o hunt.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import requests

from .fetcher import RawArticle

log = logging.getLogger(__name__)

SOURCE_NAME = "CVM"
WINDOW_H = 72                 # comunicados dos últimos 3 dias (dedup por URL cuida do resto)

# ticker principal (mw_companies) → setor do classificador ('steel' | 'mining' | 'pp')
SECTOR_BY_COMPANY = {
    "VALE3": "mining", "CMIN3": "mining", "BRAP4": "mining", "AURA33": "mining",
    "CSNA3": "steel", "GGBR4": "steel", "GOAU4": "steel", "USIM5": "steel",
    "KLBN11": "pp", "SUZB3": "pp", "RANI3": "pp",
}
NAME_BY_COMPANY = {
    "VALE3": "Vale", "CMIN3": "CSN Mineração", "BRAP4": "Bradespar", "AURA33": "Aura Minerals",
    "CSNA3": "CSN", "GGBR4": "Gerdau", "GOAU4": "Metalúrgica Gerdau", "USIM5": "Usiminas",
    "KLBN11": "Klabin", "SUZB3": "Suzano", "RANI3": "Irani",
}


def _parse_ts(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None


def filing_to_article(row: dict, now: datetime | None = None) -> RawArticle | None:
    """Uma linha de mw_filings → RawArticle (ou None se não tem link/empresa)."""
    url = (row.get("cvm_url") or "").strip()
    company = (row.get("company") or "").strip()
    if not url.startswith("http") or company not in SECTOR_BY_COMPANY:
        return None
    name = NAME_BY_COMPANY[company]
    category = (row.get("category") or "").strip()
    title = (row.get("doc_title") or "").strip() or f"{name} — {category or 'comunicado ao mercado'}"
    # o nome da empresa no título ajuda o filtro de keyword do clipping e a IA (o PDF às vezes
    # abre com "Fato Relevante" seco)
    if name.split()[0].lower() not in title.lower():
        title = f"{name}: {title}"
    excerpt = (row.get("doc_excerpt") or "").strip()
    snippet = f"{category} · {name} ({company})." + (f" {excerpt}" if excerpt else "")
    published = _parse_ts(row.get("published_at"))
    return RawArticle(
        url=url, domain="rad.cvm.gov.br", source_name=SOURCE_NAME, title=title[:300],
        snippet=snippet[:1800], published_at=published, found_at=now or datetime.now(timezone.utc),
        needs_filter=False,
    )


def collect_cvm_filings(window_h: int = WINDOW_H) -> list[RawArticle]:
    """Lê mw_filings (newsworthy, janela recente) e devolve RawArticles. Nunca levanta."""
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    if not url or not key:
        return []
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=window_h)).isoformat()
    try:
        r = requests.get(
            f"{url}/rest/v1/mw_filings?select=id,company,category,doc_title,doc_excerpt,cvm_url,published_at"
            f"&is_newsworthy=eq.true&published_at=gte.{quote(cutoff)}&order=published_at.desc&limit=200",
            headers={"apikey": key, "Authorization": f"Bearer {key}"}, timeout=20)
        if not r.ok:
            log.info("CVM filings: mw_filings indisponível (HTTP %s) — SQL do Market Watch não rodou?", r.status_code)
            return []
        rows = r.json() or []
    except Exception as e:  # noqa: BLE001
        log.warning("CVM filings: leitura falhou: %s", e)
        return []
    now = datetime.now(timezone.utc)
    out = [a for a in (filing_to_article(x, now) for x in rows) if a]
    log.info("CVM filings: %d comunicados newsworthy (%dh)", len(out), window_h)
    return out


def apply_cvm_sector(articles: list[dict]) -> int:
    """Depois do classify_article_dict: força o setor da EMPRESA nos comunicados da CVM.

    A classificação por keyword olha título+resumo; um comunicado de governança da Vale não
    tem termo de minério e cairia em NR. O snippet traz "(TICKER)" — é daí que o setor sai.
    """
    n = 0
    for art in articles:
        if art.get("source_name") != SOURCE_NAME:
            continue
        snip = art.get("snippet") or ""
        for tk, sec in SECTOR_BY_COMPANY.items():
            if f"({tk})" in snip:
                if art.get("sector") != sec:
                    art["sector"] = sec
                    n += 1
                if not art.get("tickers"):
                    art["tickers"] = [tk]
                break
    return n
