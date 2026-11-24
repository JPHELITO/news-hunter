"""Orquestra o clipping: payload (job) → itens (com corpo + tradução) → .docx + .eml.

Rode de news-hunter/:
  python -m clipping.generate --payload sel.json               # job completo
  python -m clipping.generate --url https://... --take + --sector SM   # 1 item (teste)
  (--no-fetch usa o corpo que já vier no payload, sem raspar — para testes offline)

build_from_payload(payload, d, fetch) devolve {docx, eml, docx_name, eml_name, items, errors}.
É a função que a Fase 3 (workflow do Actions) vai chamar por job.
Com whatsapp=True devolve também {whatsapp, whatsapp_name, whatsapp_error}: o HTML do blast de
WhatsApp (clipping/whatsapp.py), feito dos MESMOS itens. Falha nele nunca derruba o Word.
"""
from __future__ import annotations

import argparse
import json
import logging
from datetime import date
from pathlib import Path

from .build import (
    ClippingItem, build_docx, clean_headline, clipping_basename, detect_sector,
    _BILINGUAL_DOMAINS, _translate_to_english,
)
from .bodies import fetch_body, norm_domain, get_stored_body, store_body
from .eml import build_eml_bytes, build_html

log = logging.getLogger(__name__)

_VALID_SECTORS = {"SM", "PP", "NR"}
_LANG = {"valor.globo.com": "Portuguese", "www.estadao.com.br": "Portuguese",
         "portalcelulose.com.br": "Portuguese", "www.elfinanciero.com.mx": "Spanish"}


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
    # título vem do banco e pode ter código de HTML dentro (Fastmarkets) → decodifica ANTES
    # de tudo: entra limpo na tradução, no corpo guardado e no detect_sector.
    title = clean_headline(row.get("title") or url)
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
    # tradução: usa a guardada; senão, traduz agora (corpo colado OU cacheado sem tradução ainda)
    if not tt and body and dom in _BILINGUAL_DOMAINS:
        try:
            _tt, _tb = _translate_to_english(title, body, _LANG.get(dom, "Portuguese"))
            if _tt:
                tt, tb = _tt, _tb
                # PERSISTE a tradução em clipping_bodies → rodar a MESMA notícia 2× não re-traduz
                # (upsert por url; guarda o corpo usado + a tradução).
                store_body(url, title, src, body, tt, tb)
        except Exception as e:
            log.warning("clipping: tradução falhou (%s): %s", url, e)
    if tt:
        it.translated_title, it.translated_body = clean_headline(tt), tb
    return it


def build_from_payload(payload: list[dict], d: date | None = None, fetch: bool = True,
                       config: dict | None = None, whatsapp: bool = False) -> dict:
    d = d or date.today()
    rows = sorted(payload, key=lambda r: r.get("pos", 0))
    errors: list = []
    items = [_to_item(r, fetch, errors) for r in rows]
    docx = build_docx(items, d, config)
    base = clipping_basename(d)                              # "NEWS - MMDDYYYY"
    docx_name = f"{base}.docx"
    eml = build_eml_bytes(items, d, docx_bytes=docx, docx_name=docx_name, config=config)
    html = build_html(items, d, config, docx_bytes=docx)      # prévia inline (mesmo HTML do e-mail)
    res = {"docx": docx, "eml": eml, "docx_name": docx_name,
           "eml_name": f"{base}.eml",
           "html": html, "html_name": f"{base}.html",
           "items": items, "errors": errors}
    if whatsapp:
        # Depois do Word e do e-mail, de propósito: o que já está pronto não espera por isto,
        # e qualquer falha aqui só tira o HTML (build_whatsapp nunca levanta exceção).
        from .whatsapp import build_whatsapp
        wa = build_whatsapp(items, d, config)
        res.update({"whatsapp": wa["html"], "whatsapp_error": wa["error"],
                    "whatsapp_name": f"{base}.html"})     # o cliente recebe "NEWS - MMDDYYYY.html"
    return res


def build_whatsapp_from_payload(payload: list[dict], d: date | None = None,
                                config: dict | None = None) -> dict:
    """Só o HTML do WhatsApp — a ETAPA 3 da aba Clipping (Word → blast → HTML).

    Roda DEPOIS do Word e do blast: os corpos e as traduções já estão guardados em
    `clipping_bodies` pela geração do Word (nada é raspado de novo nem traduzido de novo) e os
    3 destaques do blast já estão em `config.blast_highlights[data]`. Nunca levanta exceção.
    """
    d = d or date.today()
    rows = sorted(payload, key=lambda r: r.get("pos", 0))
    errors: list = []
    items = [_to_item(r, True, errors) for r in rows]
    from .whatsapp import build_whatsapp
    wa = build_whatsapp(items, d, config)
    return {"whatsapp": wa["html"], "whatsapp_error": wa["error"],
            "whatsapp_name": f"{clipping_basename(d)}.html", "items": items, "errors": errors}


def build_all_from_payload(payload: list[dict], d: date | None = None,
                           config: dict | None = None) -> dict:
    """O BOTÃO ÚNICO da aba Clipping (24/11/2026): Word + destaques do blast + HTML do WhatsApp
    + e-mail novo, numa rodada só do robô.

    Antes eram 3 cliques e 3 rodadas (Word → blast → HTML), cada uma ligando o robô do zero
    (20 s a 1 min só para subir). Aqui os corpos são lidos/raspados UMA vez e ficam na memória
    para o Word, para a IA do blast e para o HTML. A IA continua sendo UMA chamada (português
    p/ o WhatsApp + inglês p/ o e-mail na mesma resposta).

    Ordem e garantias:
      1) itens (corpo + tradução) → 2) Word  ← o que já funcionava; nada abaixo derruba isto
      3) destaques (1 chamada) — sem IA, o resto sai sem a seção de destaques
      4) HTML do WhatsApp com os destaques NOVOS (português)
      5) e-mail novo (visual do HTML, destaques em inglês); falhou → e-mail antigo (cópia do Word)
    """
    from .blast import highlights
    from .email_brand import build_brand_email
    from .whatsapp import build_whatsapp

    d = d or date.today()
    config = dict(config or {})
    rows = sorted(payload, key=lambda r: r.get("pos", 0))
    errors: list = []
    items = [_to_item(r, True, errors) for r in rows]
    docx = build_docx(items, d, config)
    base = clipping_basename(d)
    docx_name = f"{base}.docx"

    # 3) destaques — com os corpos que acabaram de ser lidos (nada de reler o banco)
    tudo = config.get("tudo") or {}
    n = int(tudo.get("n") or 3)
    if tudo.get("ia", True):
        noticias = [{"title": it.title, "source_name": it.source_name, "sector": it.sector,
                     "take": it.take, "pin": r.get("pin"), "body": it.body}
                    for r, it in zip(rows, items)]
        bl = highlights(noticias, n=n)
    else:   # sem IA: os destaques JÁ carimbados para a data — é o caminho do "Atualizar HTML e e-mail"
        # depois de o analista EDITAR os destaques na aba (ou do "destaques por IA" desligado)
        from .blast import _ingles
        st = ((config.get("blast_highlights") or {}).get(d.isoformat()) or {})
        pt = [x for x in (st.get("destaques") or []) if str(x).strip()]
        en, fonte = _ingles(pt, list(st.get("destaques_en") or []))   # inglês faltando → Google, sem IA
        bl = {"destaques": pt, "destaques_en": en, "en_fonte": fonte if fonte != "ia" else "analista",
              "provedor": st.get("provedor"), "erro": None if pt else "sem IA e sem destaques do dia"}

    # o HTML lê os destaques de `config.blast_highlights[data]` — põe os NOVOS lá (cópia local)
    cfg_html = dict(config)
    bh = dict(cfg_html.get("blast_highlights") or {})
    if bl.get("destaques"):
        bh[d.isoformat()] = {"destaques": bl["destaques"], "destaques_en": bl.get("destaques_en") or [],
                             "provedor": bl.get("provedor")}
    else:
        bh.pop(d.isoformat(), None)
    cfg_html["blast_highlights"] = bh

    wa = build_whatsapp(items, d, cfg_html)
    em = build_brand_email(items, d, wa.get("model"), wa.get("html"), wa.get("fonts"), config,
                           bl.get("destaques_en"), docx, docx_name)
    if em["eml"]:
        eml, html = em["eml"], em["html"]
    else:                                   # rede de segurança: o e-mail de antes
        eml = build_eml_bytes(items, d, docx_bytes=docx, docx_name=docx_name, config=config)
        html = build_html(items, d, config, docx_bytes=docx)
    return {"docx": docx, "eml": eml, "docx_name": docx_name, "eml_name": f"{base}.eml",
            "html": html, "html_name": f"{base}.html",
            "whatsapp": wa.get("html"), "whatsapp_error": wa.get("error"), "whatsapp_name": f"{base}.html",
            "blast": bl, "email_novo": bool(em["eml"]), "email_error": em["error"],
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
    ap.add_argument("--whatsapp", action="store_true", help="gera também o HTML do WhatsApp")
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
    res = build_from_payload(payload, d, fetch=not a.no_fetch, config=_cfg, whatsapp=a.whatsapp)
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    (out / res["docx_name"]).write_bytes(res["docx"])
    (out / res["eml_name"]).write_bytes(res["eml"])
    print(f"OK -> {out / res['docx_name']}  ({len(res['docx'])} bytes)")
    print(f"OK -> {out / res['eml_name']}  ({len(res['eml'])} bytes)")
    if a.whatsapp:
        if res.get("whatsapp"):
            (out / f"WhatsApp - {res['whatsapp_name']}").write_bytes(res["whatsapp"])
            print(f"OK -> {out / ('WhatsApp - ' + res['whatsapp_name'])}  ({len(res['whatsapp'])} bytes)")
        else:
            print("HTML do WhatsApp NÃO gerado:", res.get("whatsapp_error"))
    if res["errors"]:
        print("avisos:", res["errors"])


if __name__ == "__main__":
    main()
