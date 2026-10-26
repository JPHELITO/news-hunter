"""Anúncios da bolsa australiana (ASX) da Viridis e dos pares de terras raras como FONTE do News Hunter.

Por que existe: a Viridis (VMM.AX, coberta desde 2026-10-23) divulga tudo na ASX, que publica à noite
no horário de Brasília. Sem esta fonte o anúncio só chegava quando a imprensa brasileira escrevia sobre
ele — medido em 26/10/2026: o estudo de viabilidade + a captação de US$ 120 mi saíram na ASX às 20:50
de 19/08 e entraram no banco às 10:17 do dia seguinte (13 h depois); o primeiro pedido de equipamento,
~40 h depois; e dois anúncios de pares (Arafura, 11/10; Brazilian Rare Earths, 25/10) nunca chegaram.

De onde vem: o JSON público por trás de asx.com.au (asx.api.markitdigital.com). Uma chamada traz as
sete empresas juntas. NÃO é API documentada — se mudar de forma, este coletor devolve [] e loga, como
os outros (nunca derruba o hunt).

Regras:
  • só o que a própria ASX marca como SENSÍVEL A PREÇO (`isPriceSensitive`). Cerca de 70% do que as
    empresas protocolam é papelada (Appendix 2A/3B/3G, aviso de acionista, juros de diretor) — medido
    em 116 dias: 51 sensíveis a preço para as sete, ~0,4 por dia.
  • `symbol` tem de ser uma das sete: aviso de índice da S&P lista 100+ tickers e viria junto.
  • o nome da empresa vai na frente do título quando ele não o cita ("VMM Receives Approval for R$77.5M
    BNDES Financing Facility" → "Viridis: VMM Receives…"), no padrão dos comunicados da CVM — é o que faz
    o classificador marcar a coberta e o filtro de setor achar a mineradora.
  • fonte curada (`source_name = "ASX"`): aceita tudo (pass_through no filter.py) e nunca é excluída do
    relatório (news_take_classifier._ALWAYS_INCLUDE_SOURCES), como a CVM.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import requests

from .fetcher import RawArticle

log = logging.getLogger(__name__)

SOURCE_NAME = "ASX"
WINDOW_H = 72                 # anúncios dos últimos 3 dias (dedup por URL cuida do resto)
ENDPOINT = "https://asx.api.markitdigital.com/asx-research/1.0/markets/announcements"
PDF_URL = "https://cdn-api.markitdigital.com/apiman-gateway/ASX/asx-research/1.0/file/{key}"
ITEMS_PER_PAGE = 50           # as sete juntas publicam ~2 anúncios por dia útil: 50 cobrem semanas
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
}

# ticker da ASX → (nome no título, xid da Markit). O xid é o id interno da ASX para a empresa e não
# muda; consulta: asx.api.markitdigital.com/asx-research/1.0/companies/<ticker>/header → data.xid.
COMPANIES = {
    "VMM": ("Viridis", "692151730"),
    "LYC": ("Lynas", "194790"),
    "ILU": ("Iluka", "165620"),
    "ARU": ("Arafura", "48403"),
    "MEI": ("Meteoric Resources", "199097"),
    "BRE": ("Brazilian Rare Earths", "845047734"),
    "SGQ": ("St George Mining", "25989504"),
}


def _parse_ts(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def announcement_to_article(item: dict, now: datetime | None = None,
                            window_h: int = WINDOW_H) -> RawArticle | None:
    """Um item do JSON da ASX → RawArticle (ou None se não é das sete, não é sensível a preço,
    não tem documento ou está fora da janela)."""
    symbol = (item.get("symbol") or "").strip().upper()
    if symbol not in COMPANIES or not item.get("isPriceSensitive"):
        return None
    key = (item.get("documentKey") or "").strip()
    headline = " ".join((item.get("headline") or "").split())
    if not key or len(headline) < 6:
        return None
    now = now or datetime.now(timezone.utc)
    published = _parse_ts(item.get("date"))
    if published and published < now - timedelta(hours=window_h):
        return None
    name = COMPANIES[symbol][0]
    title = headline if name.lower() in headline.lower() else f"{name}: {headline}"
    kinds = ", ".join(t for t in (item.get("announcementTypes") or []) if isinstance(t, str))
    snippet = f"ASX price-sensitive announcement · {name} (ASX: {symbol})." + (f" {kinds}." if kinds else "")
    return RawArticle(
        url=PDF_URL.format(key=key), domain="asx.com.au", source_name=SOURCE_NAME, title=title[:300],
        snippet=snippet[:400], published_at=published, found_at=now, needs_filter=False,
    )


def collect_asx_announcements(window_h: int = WINDOW_H) -> list[RawArticle]:
    """Lê os anúncios recentes das sete empresas e devolve RawArticles. Nunca levanta."""
    params = {"xids": ",".join(x for _, x in COMPANIES.values()), "itemsPerPage": ITEMS_PER_PAGE}
    try:
        r = requests.get(ENDPOINT, params=params, headers=HEADERS, timeout=15)
        if r.status_code != 200:
            log.warning("ASX: HTTP %s nos anúncios", r.status_code)
            return []
        items = ((r.json() or {}).get("data") or {}).get("items") or []
    except Exception as e:  # noqa: BLE001
        log.warning("ASX: leitura falhou: %s", e)
        return []
    now = datetime.now(timezone.utc)
    out = [a for a in (announcement_to_article(it, now, window_h) for it in items) if a]
    log.info("ASX: %d anúncios sensíveis a preço (%dh) de %d recebidos", len(out), window_h, len(items))
    return out
