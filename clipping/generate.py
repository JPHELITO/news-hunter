"""Orquestra o clipping: payload (job) → itens (com corpo + tradução) → .docx + .eml.

Rode de news-hunter/:
  python -m clipping.generate --payload sel.json               # job completo
  python -m clipping.generate --url https://... --take + --sector SM   # 1 item (teste)
  (--no-fetch usa o corpo que já vier no payload, sem raspar — para testes offline)

build_from_payload(payload, d, fetch) devolve {docx, eml, docx_name, eml_name, items, errors}.
É a função que a Fase 3 (workflow do Actions) vai chamar por job.
"""
from __future__ import annotations

import argparse
import json
import logging
from datetime import date
from pathlib import Path

from .build import (
    ClippingItem, build_docx, detect_sector, _BILINGUAL_DOMAINS, _translate_to_english,
)
from .bodies import fetch_body, norm_domain, get_stored_body, store_body
from .eml import build_eml_bytes, build_html

log = logging.getLogger(__name__)

_VALID_SECTORS = {"SM", "PP", "NR"}
_LANG = {"valor.globo.com": "Portuguese", "www.estadao.com.br": "Portuguese",
         "www.elfinanciero.com.mx": "Spanish"}


def _fetch_and_translate(url: str, dom: str, title: str) -> tuple[str, str, str]:
    """Raspa o corpo + traduz (bilíngue) → (body_html, translated_title, translated_body).
    Usado pela geração (fallback ao vivo) E pelo aquecedor (clipping/warm_bodies.py)."""
    body = fetch_body(url) or ""
    tt = tb = ""
    if body and dom in _BILINGUAL_DOMAINS:
        try:
            _tt, _tb = _translate_to_english(title, body, _LANG.get(dom, "Portuguese"))
            if _tt:
                tt, tb = _tt, _tb
        except Exception as e:
            log.warning("clipping: tradução falhou (%s): %s", url, e)
    return body, tt, tb


def _to_item(row: dict, fetch: bool, errors: list) -> ClippingItem:
    url = row["url"]
    dom = norm_domain(url)
    take = row.get("take") or "="
    sector = row.get("sector") if row.get("sector") in _VALID_SECTORS else ""
    title = row.get("title") or url
    src = row.get("source_name") or dom
    body = row.get("body") or ""          # 1) corpo colado no payload (item 8) tem prioridade
    tt = tb = ""
    if not body:                          # 2) corpo já guardado pelo aquecedor → INSTANTÂNEO
        stored = get_stored_body(url)
        if stored:
            body = stored.get("body") or ""
            tt   = stored.get("translated_title") or ""
            tb   = stored.get("translated_body") or ""
    if fetch and not body:                # 3) fallback: raspa ao vivo E guarda p/ a próxima vez
        body, tt, tb = _fetch_and_translate(url, dom, title)
        if body:
            store_body(url, title, src, body, tt, tb)
        else:
            errors.append((url, "corpo não obtido"))
    it = ClippingItem(url=url, title=title, source_name=src, body=body,
                      matched_keywords=[], domain=dom, take=take,
                      sector=(sector or detect_sector(dom, [], title)))
    # tradução: usa a guardada; senão, corpo veio do payload (colado) e é bilíngue → traduz agora
    if not tt and body and dom in _BILINGUAL_DOMAINS:
        try:
            _tt, _tb = _translate_to_english(title, body, _LANG.get(dom, "Portuguese"))
            if _tt:
                tt, tb = _tt, _tb
        except Exception as e:
            log.warning("clipping: tradução falhou (%s): %s", url, e)
    if tt:
        it.translated_title, it.translated_body = tt, tb
    return it


def build_from_payload(payload: list[dict], d: date | None = None, fetch: bool = True,
                       config: dict | None = None) -> dict:
    d = d or date.today()
    rows = sorted(payload, key=lambda r: r.get("pos", 0))
    errors: list = []
    items = [_to_item(r, fetch, errors) for r in rows]
    docx = build_docx(items, d, config)
    docx_name = f"clipping_{d.strftime('%Y%m%d')}.docx"
    eml = build_eml_bytes(items, d, docx_bytes=docx, docx_name=docx_name, config=config)
    html = build_html(items, d, config)                       # prévia inline (mesmo HTML do e-mail)
    return {"docx": docx, "eml": eml, "docx_name": docx_name,
            "eml_name": f"clipping_{d.strftime('%Y%m%d')}.eml",
            "html": html, "html_name": f"clipping_{d.strftime('%Y%m%d')}.html",
            "items": items, "errors": errors}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--payload")
    ap.add_argument("--url")
    ap.add_argument("--take", default="=")
    ap.add_argument("--sector", default="")
    ap.add_argument("--title", default="")
    ap.add_argument("--source", default="")
    ap.add_argument("--no-fetch", action="store_true")
    ap.add_argument("--config", help="JSON com intro/recent_publications/earnings_review/analysts")
    ap.add_argument("--date")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent / "out"))
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if a.payload:
        payload = json.loads(Path(a.payload).read_text(encoding="utf-8"))
    elif a.url:
        payload = [{"url": a.url, "take": a.take, "sector": a.sector,
                    "title": a.title, "source_name": a.source, "pos": 0}]
    else:
        ap.error("use --payload ou --url")

    d = date.fromisoformat(a.date) if a.date else date.today()
    _cfg = json.loads(Path(a.config).read_text(encoding="utf-8")) if a.config else None
    res = build_from_payload(payload, d, fetch=not a.no_fetch, config=_cfg)
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    (out / res["docx_name"]).write_bytes(res["docx"])
    (out / res["eml_name"]).write_bytes(res["eml"])
    print(f"OK -> {out / res['docx_name']}  ({len(res['docx'])} bytes)")
    print(f"OK -> {out / res['eml_name']}  ({len(res['eml'])} bytes)")
    if res["errors"]:
        print("avisos:", res["errors"])


if __name__ == "__main__":
    main()
