"""Comunicados das 4 de cobre de Toronto (Hudbay, Lundin, Capstone, Ero) pela TMX Money — fonte do News Hunter.

Por que existe: cobertura formal desde 2026-11-23. Medido nos 90 dias anteriores: só 15 manchetes do banco citavam
as quatro (quase tudo Mining.com, horas depois). Os comunicados oficiais (resultados, guidance, operação, M&A) não
chegavam: o GlobeNewswire (Hudbay, Ero) e a Business Wire (Capstone) bloqueiam robô (Akamai 403), o site da Capstone
é Cloudflare 403 e o da Lundin não tem RSS. A TMX Money (dona da bolsa de Toronto) junta TODOS os newswires por
ticker, com hora exata, num JSON público — o análogo do asx_announcements.py para a TSX.

De onde vem: POST https://app-money.tmx.com/graphql, query `getNewsForSymbol` (sem chave, sem cookie). Os tipos
têm de ser obrigatórios (`Int!`/`String!`) — com opcionais a resposta é 400. NÃO é API documentada: se mudar de
forma, este coletor devolve [] e loga (nunca derruba o hunt).

Regras:
  • uma chamada por ticker (4), itens das últimas WINDOW_H horas;
  • fica só o item cuja MANCHETE cita a empresa do ticker (classificador de cobertas): a TMX também marca o ticker
    em comunicado de terceiros — propaganda ("Five Metals Producers Trade Higher…") sai; o que a cita fica
    ("Pecoy Copper… Financing Led by Hudbay", "Royalties Inc.… Capstone's Cozamin");
  • o mesmo comunicado chega por dois newswires (Canada Newswire + PR Newswire) → um só (manchete normalizada);
  • fonte curada (`source_name = "TMX"`): pass_through no filter.py e nunca excluída do relatório, como CVM/ASX.
"""
from __future__ import annotations

import html
import logging
import re
from datetime import datetime, timedelta, timezone

import requests

from .fetcher import RawArticle

log = logging.getLogger(__name__)

SOURCE_NAME = "TMX"
WINDOW_H = 72
ENDPOINT = "https://app-money.tmx.com/graphql"
ITEM_URL = "https://money.tmx.com/en/quote/{symbol}/news/{newsid}"
LIMIT = 20                    # as quatro publicam ~0,35 comunicado por dia somadas: 20 por ticker cobrem meses
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Content-Type": "application/json",
}
QUERY = ("query getNewsForSymbol($symbol: String!, $page: Int!, $limit: Int!, $locale: String!) "
         "{ news: getNewsForSymbol(symbol: $symbol, page: $page, limit: $limit, locale: $locale) "
         "{ headline datetime source newsid summary } }")

# ticker da TSX → (token do classificador, nome no título quando a manchete não o traz)
COMPANIES = {
    "HBM": ("HUDBAY", "Hudbay"),
    "LUN": ("LUNDIN MINING", "Lundin Mining"),
    "CS": ("CAPSTONE COPPER", "Capstone Copper"),
    "ERO": ("ERO COPPER", "Ero Copper"),
}


def _clean(s: str | None) -> str:
    return " ".join(html.unescape(s or "").replace("\xa0", " ").split())


def _parse_ts(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d.astimezone(timezone.utc) if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _key(headline: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", headline.lower()).strip()[:90]


def item_to_article(symbol: str, item: dict, now: datetime | None = None,
                    window_h: int = WINDOW_H) -> RawArticle | None:
    """Um item da TMX → RawArticle (ou None: fora da janela, sem id, ou manchete que não cita a empresa)."""
    from .news_take_classifier import detect_covered_companies
    symbol = (symbol or "").upper()
    if symbol not in COMPANIES:
        return None
    headline = _clean(item.get("headline"))
    newsid = str(item.get("newsid") or "").strip()
    if not newsid or len(headline) < 6:
        return None
    now = now or datetime.now(timezone.utc)
    published = _parse_ts(item.get("datetime"))
    if published is None or published < now - timedelta(hours=window_h):
        return None
    token, _name = COMPANIES[symbol]
    if token not in detect_covered_companies(headline):
        return None
    summary = _clean(item.get("summary"))
    via = _clean(item.get("source")).replace(" via QuoteMedia", "")
    snippet = (summary or headline) + (f" ({via})" if via else "")
    return RawArticle(
        url=ITEM_URL.format(symbol=symbol, newsid=newsid), domain="money.tmx.com", source_name=SOURCE_NAME,
        title=headline[:300], snippet=snippet[:400], published_at=published, found_at=now, needs_filter=False,
    )


def _fetch_symbol(symbol: str) -> list[dict]:
    body = {"query": QUERY, "variables": {"symbol": symbol, "page": 1, "limit": LIMIT, "locale": "en"}}
    r = requests.post(ENDPOINT, json=body, headers=HEADERS, timeout=15)
    if r.status_code != 200:
        log.warning("TMX [%s]: HTTP %s", symbol, r.status_code)
        return []
    data = (r.json() or {}).get("data") or {}
    return data.get("news") or []


def collect_tmx_news(window_h: int = WINDOW_H) -> list[RawArticle]:
    """Comunicados recentes das quatro (um por manchete). Nunca levanta."""
    now = datetime.now(timezone.utc)
    out: list[RawArticle] = []
    seen: set[str] = set()
    recebidos = 0
    for symbol in COMPANIES:
        try:
            items = _fetch_symbol(symbol)
        except Exception as e:  # noqa: BLE001
            log.warning("TMX [%s]: leitura falhou: %s", symbol, e)
            continue
        recebidos += len(items)
        for it in items:
            a = item_to_article(symbol, it, now, window_h)
            if a is None:
                continue
            k = _key(a.title)
            if k in seen:
                continue
            seen.add(k)
            out.append(a)
    log.info("TMX: %d comunicados das 4 de cobre (%dh) de %d recebidos", len(out), window_h, recebidos)
    return out
