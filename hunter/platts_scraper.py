"""Scraper Platts (S&P Global) — Fase 1 apenas (headlines) + preços.

Intercepta POST content-bff/v1/search para headlines.
Intercepta JSON responses durante navegação ao workspace para preços IODEX.
Retorna apenas título + snippet + link. Sem Fase 2 (sem corpo completo).
Requer platts_state.json (sessão válida do browser).
"""
from __future__ import annotations

import html
import json
import logging
import re
import threading
from datetime import datetime, timezone
from urllib.parse import quote

from .cookies import get_cookies_dir
from .fetcher import RawArticle

log = logging.getLogger(__name__)

# Símbolos de preço que queremos capturar do workspace Platts
_PRICE_SYMBOLS = {"IODBZ00", "IOPRM00", "IODFE00"}

# Cache de preços preenchido pelo _scrape() como efeito colateral.
# Lido pelo thread principal após join via get_platts_prices().
_platts_prices: dict[str, dict] = {}

_WANTED_TYPES = {"News", "Top News", "Flash", "Market Commentary",
                 "Blog", "Rationale", "Headline Analysis"}

_TIMEOUT = 120  # segundos máximos no thread


def _html_to_text(h: str) -> str:
    text = re.sub(r"<[^>]+>", " ", h)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _parse_date(s: str | None) -> datetime | None:
    if not s or s.startswith("0001"):
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
    except (ValueError, TypeError):
        return None


def _article_url(article_id: str, content_type: str = "News") -> str:
    ct = quote(content_type, safe="")
    return f"https://core.spglobal.com/#platts/insightsArticle?articleID={article_id}&insightsType={ct}"


def _parse_price(text: str) -> float | None:
    """Converte texto de preço para float, lidando com vírgula decimal (103,35)
    e separador de milhar (1.234,56 ou 1,234.56)."""
    if not text:
        return None
    t = text.strip().replace(" ", "")
    # Remove qualquer símbolo de moeda / sufixo
    t = re.sub(r"[^\d.,\-]", "", t)
    if not t:
        return None
    has_comma = "," in t
    has_dot = "." in t
    try:
        if has_comma and has_dot:
            # O último separador é o decimal
            if t.rfind(",") > t.rfind("."):
                t = t.replace(".", "").replace(",", ".")   # 1.234,56 → 1234.56
            else:
                t = t.replace(",", "")                      # 1,234.56 → 1234.56
        elif has_comma:
            # Só vírgula → decimal europeu/brasileiro (103,35 → 103.35)
            t = t.replace(",", ".")
        return float(t)
    except (ValueError, TypeError):
        return None


# JS para ler preços direto da tabela AG-Grid renderizada (DOM).
# Mais robusto que interceptar rede — lê exatamente o que está na tela.
_DOM_PRICE_JS = """
() => {
  const targets = ['IODBZ00','IOPRM00','IODFE00'];
  const out = {};
  let priceColId = null;
  // Descobre o col-id da coluna "Price" pelo cabeçalho
  document.querySelectorAll('.ag-header-cell, [role="columnheader"]').forEach(h => {
    const t = (h.textContent||'').trim().toLowerCase();
    if (!priceColId && (t === 'price' || t === 'bid' || t === 'value' || t === 'last')) {
      priceColId = h.getAttribute('col-id');
    }
  });
  const rows = document.querySelectorAll('.ag-row, [role="row"]');
  rows.forEach(row => {
    let sym = null;
    const cells = row.querySelectorAll('.ag-cell, [role="gridcell"], td');
    cells.forEach(c => {
      const t = (c.textContent||'').trim();
      if (targets.includes(t)) sym = t;
    });
    if (!sym) return;
    let priceText = null;
    if (priceColId) {
      const pc = row.querySelector('[col-id="'+priceColId+'"]');
      if (pc) priceText = (pc.textContent||'').trim();
    }
    if (!priceText) {
      // Fallback: primeira célula numérica da linha
      cells.forEach(c => {
        const t = (c.textContent||'').trim();
        if (!priceText && /^-?\\d{1,4}([.,]\\d{1,3})?$/.test(t)) priceText = t;
      });
    }
    if (priceText) out[sym] = priceText;
  });
  return {prices: out, rowCount: rows.length};
}
"""


def _extract_prices(data, out: dict) -> None:
    """Busca recursivamente preços dos símbolos _PRICE_SYMBOLS em qualquer resposta JSON."""
    if isinstance(data, dict):
        sym = (data.get("symbol") or data.get("Symbol") or
               data.get("code")   or data.get("Code")   or
               data.get("ticker") or data.get("Ticker") or "")
        if sym in _PRICE_SYMBOLS:
            price_val = (
                data.get("price")          or data.get("Price")          or
                data.get("value")          or data.get("Value")          or
                data.get("latestValue")    or data.get("assessedPrice")  or
                data.get("closePrice")     or data.get("settlementPrice") or
                data.get("lastPrice")      or data.get("midPrice")
            )
            if price_val is not None:
                try:
                    out[sym] = {"price": float(price_val)}
                    log.info("platts_prices: %s = %s", sym, price_val)
                except (TypeError, ValueError):
                    pass
        for v in data.values():
            _extract_prices(v, out)
    elif isinstance(data, list):
        for item in data:
            _extract_prices(item, out)


def _scrape() -> list[RawArticle]:
    """Executa em thread. Intercepta headlines + preços IODEX do workspace."""
    from playwright.sync_api import sync_playwright

    state_file = get_cookies_dir() / "platts_state.json"
    if not state_file.exists():
        log.warning("platts_scraper: state file não encontrado em %s", state_file)
        return []

    results: list[RawArticle] = []
    seen_ids: set[str] = set()
    now_utc = datetime.now(timezone.utc)
    price_buf: dict[str, dict] = {}

    def on_response(response):
        url = response.url
        # Headlines
        if "content-bff/v1/search" in url and "image" not in url:
            try:
                data = json.loads(response.body().decode("utf-8", errors="replace"))
                for item in data.get("Items", []):
                    article_id = item.get("Id", "")
                    if not article_id or article_id in seen_ids:
                        continue
                    content_type = item.get("ContentType", "News")
                    if content_type not in _WANTED_TYPES:
                        continue
                    seen_ids.add(article_id)

                    headline = item.get("Headline") or item.get("Name") or ""
                    if not headline:
                        continue

                    summary_html  = item.get("Summary") or ""
                    body_html     = item.get("Body") or ""
                    content_prev  = item.get("Content") or ""
                    snippet = (
                        _html_to_text(summary_html)[:360]
                        or _html_to_text(body_html)[:360]
                        or content_prev[:360]
                    )
                    pub = _parse_date(item.get("UpdatedDate") or item.get("RtpTimestamp"))

                    results.append(RawArticle(
                        url=_article_url(article_id, content_type),
                        domain="core.spglobal.com",
                        source_name="S&P Platts",
                        title=headline,
                        snippet=snippet,
                        published_at=pub,
                        found_at=now_utc,
                        needs_filter=True,
                    ))
            except Exception as e:
                log.debug("platts_scraper headlines parse error: %s", e)
            return

        # Preços — inspeciona toda resposta JSON em busca dos símbolos IODEX
        if response.status != 200:
            return
        ct = response.headers.get("content-type", "")
        if "json" not in ct:
            return
        try:
            data = json.loads(response.body().decode("utf-8", errors="replace"))
            _extract_prices(data, price_buf)
        except Exception:
            pass

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled"],
                channel="chrome",
            )
        except Exception:
            browser = p.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled"],
            )

        ctx = browser.new_context(
            storage_state=str(state_file),
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1440, "height": 900},
            locale="en-US",
        )
        ctx.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        ctx.on("response", on_response)
        page = ctx.new_page()

        try:
            log.info("platts_scraper: carregando core.spglobal.com...")
            page.goto("https://core.spglobal.com/", wait_until="domcontentloaded", timeout=40_000)
            page.wait_for_timeout(6_000)

            # Verifica sessão expirada
            if any(x in page.url.lower() for x in ("login", "signin", "auth")):
                log.warning("platts_scraper: sessão expirada — renovar platts_state.json")
                return []

            # allInsights — News, Flash, Rationale, etc.
            page.evaluate("window.location.hash = '#platts/allInsights'")
            page.wait_for_timeout(18_000)

            # Market Commentary
            try:
                page.evaluate(
                    "window.location.hash = "
                    "'#platts/insightsResult?contentType=Market%20Commentary'"
                )
                page.wait_for_timeout(12_000)
            except Exception:
                pass

            log.info("platts_scraper: %d headlines coletados", len(results))

            # Navega ao workspace com watchlist de Iron Ore para capturar preços IODEX.
            # Método primário: ler a tabela AG-Grid renderizada (DOM).
            # Fallback: interceptação de rede (price_buf já preenchido via on_response).
            try:
                page.evaluate(
                    "window.location.hash = "
                    "'#platts/workspace?workspace=New%20Workspace&type=private'"
                )
                page.wait_for_timeout(12_000)  # tempo para a grid renderizar + dados chegarem

                # Tenta ler do DOM até 3x (a grid pode demorar a popular)
                dom_prices = {}
                row_count = 0
                for attempt in range(3):
                    try:
                        res = page.evaluate(_DOM_PRICE_JS)
                        dom_prices = res.get("prices", {}) if isinstance(res, dict) else {}
                        row_count = res.get("rowCount", 0) if isinstance(res, dict) else 0
                        if dom_prices:
                            break
                        page.wait_for_timeout(4_000)
                    except Exception as e:
                        log.debug("platts_scraper: DOM read attempt %d falhou: %s", attempt, e)
                        page.wait_for_timeout(4_000)

                log.info("platts_scraper: DOM grid rows=%d, símbolos lidos=%s",
                         row_count, list(dom_prices.keys()))

                # Parseia e mescla no price_buf (DOM tem prioridade sobre rede)
                for sym, raw in dom_prices.items():
                    val = _parse_price(raw)
                    if val is not None:
                        price_buf[sym] = {"price": val}
                        log.info("platts_scraper: %s = %s (DOM)", sym, val)

                log.info("platts_scraper: preços finais capturados: %s", list(price_buf.keys()))
            except Exception as e:
                log.warning("platts_scraper: workspace navigation error: %s", e)

        except Exception as e:
            log.warning("platts_scraper: erro na navegação: %s", e)
        finally:
            page.close()
            browser.close()

    # Publica no cache de módulo (lido pelo thread principal após join)
    global _platts_prices
    _platts_prices = price_buf

    return results


def collect_platts_headlines() -> list[RawArticle]:
    """Ponto de entrada — executa em thread com timeout."""
    out: list[RawArticle] = []
    err: list[Exception] = []

    def run():
        try:
            out.extend(_scrape())
        except Exception as e:
            err.append(e)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout=_TIMEOUT)
    if t.is_alive():
        log.warning("platts_scraper: timeout após %ds", _TIMEOUT)
        return []
    if err:
        log.warning("platts_scraper: erro: %s", err[0])
        return []
    return out


def get_platts_prices() -> dict[str, dict]:
    """Retorna preços IODEX capturados durante a última sessão Playwright.

    Deve ser chamado após collect_platts_headlines() completar.
    Chaves: 'IODBZ00' (IODEX 62% CFR China), 'IOPRM00' (65%), 'IODFE00' (58%).
    Valores: {'price': float}.
    """
    return dict(_platts_prices)
