"""HTML do clipping para o WhatsApp — a 4ª saída do MESMO job (Word, e-mail e prévia
continuam exatamente como estão).

POR QUE ASSIM
-------------
O desenho (cabeçalho STEEL & MINING / PULP & PAPER, emblema do Itaú animado, cores, rodapé
com o time e os disclaimers) mora num lugar SÓ: `reports-lib.js`, no repositório da
dashboard — o mesmo motor que gera os relatórios do IABr e da SECEX na aba Reports. Este
módulo não redesenha nada: monta o MODELO (dados) a partir dos MESMOS `ClippingItem` que
acabaram de virar Word — mesmo corpo raspado, mesma tradução, mesmo take, mesmo setor,
mesma regra que tira os "bullets" do Platts — e pede ao `reports-lib.js` que desenhe,
rodando-o no Node que já vem dentro do Playwright do contêiner (nada a instalar).
Lição do CLAUDE.md: duas saídas do mesmo conteúdo não se montam em paralelo — aqui o
CONTEÚDO é um só; só a apresentação muda.

De onde vem o `reports-lib.js`: primeiro a versão publicada (raw do GitHub, `main`), para
uma mudança de desenho feita na dashboard valer aqui sem commit novo; se a rede falhar, a
cópia guardada em `clipping/assets/report-kit/` (atualizar com `sync_report_kit.py`).

NUNCA DERRUBA O WORD: qualquer falha aqui vira {"html": None, "error": "..."} e o job
segue entregando Word + e-mail (run_jobs grava o motivo em `config.whatsapp.error`).
"""
from __future__ import annotations

import base64
import io
import json
import logging
import os
import shutil
import subprocess
import tempfile
import time
from datetime import date
from pathlib import Path

from .build import (
    ClippingItem, SECTOR_ORDER, _DEFAULT_ANALYSTS, _fetch_image, _html_to_blocks,
    intro_has_content,
)

log = logging.getLogger(__name__)

_HERE = Path(__file__).resolve().parent
_RENDER_JS = _HERE / "render_news.js"
VENDOR_DIR = _HERE / "assets" / "report-kit"
KIT_URL = os.environ.get(
    "REPORT_KIT_URL", "https://raw.githubusercontent.com/JPHELITO/IBBA-Research-Dashboard/main")
KIT_FILES = {"lib": "reports-lib.js",
             "display": "assets/fonts/unbounded.woff2",
             "body": "assets/fonts/manrope.woff2"}

# imagens das matérias (gráficos): largura máx., peso máx. de UMA e orçamento do arquivo todo
IMG_MAX_W = 1000
IMG_MAX_BYTES = 380_000
IMG_BUDGET = 2_400_000


# ═══════════════ modelo ═══════════════════════════════════════════════════════

def _flatten(im):
    """RGBA/P → RGB sobre fundo BRANCO (no JPEG o transparente viraria preto)."""
    from PIL import Image
    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        im = im.convert("RGBA")
        bg = Image.new("RGB", im.size, (255, 255, 255))
        bg.paste(im, mask=im.split()[-1])
        return bg
    return im.convert("RGB")


def _image(src: str, article_url: str, budget: list) -> dict | None:
    """Mesma busca do Word (`_fetch_image`: data: ou http). Reduz para no máx. 1000px e fica
    com o MENOR entre PNG otimizado (gráfico, poucas cores) e JPEG 80 (foto). Pula ícone,
    imagem pesada demais e o que estourar o orçamento do arquivo."""
    got = _fetch_image(src, article_url)
    if not got:
        return None
    data, ext = got
    w = h = None
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(data))
        im.load()
        w, h = im.size
        if w < 120 or h < 60:                      # ícone/pixel, não é gráfico
            return None
        if w > IMG_MAX_W:
            im = im.resize((IMG_MAX_W, max(1, round(h * IMG_MAX_W / w))), Image.LANCZOS)
            w, h = im.size
        rgb = _flatten(im)
        b_png, b_jpg = io.BytesIO(), io.BytesIO()
        rgb.save(b_png, "PNG", optimize=True)
        rgb.save(b_jpg, "JPEG", quality=80, optimize=True, progressive=True)
        fmt, out = min((("png", b_png.getvalue()), ("jpeg", b_jpg.getvalue())), key=lambda c: len(c[1]))
    except Exception as e:                         # sem Pillow / formato estranho: só se já for leve
        log.debug("whatsapp: imagem sem Pillow (%s): %s", src[:60], e)
        fmt = {"jpg": "jpeg"}.get(ext, ext)
        out = data
        if fmt not in ("png", "jpeg", "gif"):
            return None
    if len(out) > IMG_MAX_BYTES or budget[0] + len(out) > IMG_BUDGET:
        log.info("whatsapp: imagem de %d KB fica de fora (orçamento)", len(out) // 1024)
        return None
    budget[0] += len(out)
    blk = {"t": "img", "src": f"data:image/{fmt};base64,{base64.b64encode(out).decode('ascii')}"}
    if w and h:
        blk["w"], blk["h"] = int(w), int(h)
    return blk


def _blocks(html_body: str, domain: str, article_url: str, budget: list, images: bool = True) -> list[dict]:
    """Corpo → blocos, pelo MESMO parser do Word (`_html_to_blocks`) e com as mesmas regras."""
    raw = _html_to_blocks(html_body) if html_body else []
    if domain == "core.spglobal.com":              # Word: os "bullets" do Platts são highlights → fora
        raw = [b for b in raw if b.get("type") != "list_item"]
    out: list[dict] = []
    for b in raw:
        t = b.get("type")
        if t == "text":
            d = {"t": "p", "x": b.get("text") or ""}
            if b.get("bold"):
                d["b"] = 1
            out.append(d)
        elif t in ("h3", "h4"):
            out.append({"t": t, "x": b.get("text") or ""})
        elif t == "blockquote":
            out.append({"t": "q", "x": b.get("text") or ""})
        elif t == "list_item":
            out.append({"t": "li", "x": b.get("text") or ""})
        elif t == "table":
            from .html_utils import plain_text      # célula vem com HTML inline; o Word usa texto puro
            rows = [[{"text": plain_text(c.get("text") or ""), "header": bool(c.get("header")),
                      "colspan": int(c.get("colspan") or 1), "align": c.get("align") or ""}
                     for c in (r or [])] for r in (b.get("rows") or [])]
            if rows:
                out.append({"t": "table", "rows": rows})
        elif t == "image" and images:
            img = _image(b.get("src") or "", article_url, budget)
            if img:
                out.append(img)
    return out


def _pubs(lst) -> list[dict]:
    """Recent Publications / Earnings Review — mesmos campos que o Word lê (`_pub_bullets`)."""
    out = []
    for p in lst or []:
        name = (p.get("name") or p.get("title") or "").strip()
        if not name:
            continue
        out.append({"name": name,
                    "sector": p.get("sector") if p.get("sector") in SECTOR_ORDER else "SM",
                    "link": (p.get("link") or p.get("pdf_url") or p.get("report_url") or "").strip(),
                    "date": (p.get("date") or "")[:10],
                    "companies": [str(c) for c in (p.get("companies") or []) if c]})
    return out


def news_model(items: list[ClippingItem], d: date, config: dict | None = None) -> dict:
    """O que o HTML mostra — tudo tirado do que acabou de gerar o Word."""
    config = config or {}
    budget = [0]
    out_items = []
    for i, it in enumerate(items):
        bil = bool(it.translated_title)
        out_items.append({
            "i": i,
            "sector": it.sector if it.sector in SECTOR_ORDER else "SM",
            "take": it.take if it.take in ("+", "-", "=") else "=",
            "title": it.title,
            "tr_title": it.translated_title if bil else "",
            "source": it.source_name or it.domain,
            "url": it.url,
            "blocks": _blocks(it.body, it.domain, it.url, budget),
            # tradução sem as imagens: elas já estão no original (não pesar o arquivo 2×)
            "tr_blocks": _blocks(it.translated_body, it.domain, it.url, budget, images=False) if bil else [],
        })
    intro = config.get("intro") or {}
    er = config.get("earnings_review") or {}
    hl = ((config.get("blast_highlights") or {}).get(d.isoformat()) or {}).get("destaques") or []
    return {
        "date": d.isoformat(),
        "intro_html": (intro.get("html") or "") if intro_has_content(intro) else "",
        "highlights": [str(x).strip() for x in hl if str(x).strip()],
        "items": out_items,
        "publications": _pubs(config.get("recent_publications")),
        "earnings": ({"label": (er.get("label") or "Earnings Review").strip() or "Earnings Review",
                      "items": _pubs(er.get("items"))} if er.get("on") else None),
        "analysts": [{k: (a.get(k) or "").strip() for k in ("name", "role", "phone", "email")}
                     for a in (config.get("analysts") or _DEFAULT_ANALYSTS)
                     if (a.get("name") or a.get("email") or "").strip()],
        "_img_bytes": budget[0],
    }


# ═══════════════ o motor de desenho (reports-lib.js) ═════════════════════════

def _kit() -> dict:
    """reports-lib.js + as 2 fontes: publicado (raw do GitHub) → cópia guardada."""
    got: dict = {}
    try:
        import requests
        for k, rel in KIT_FILES.items():
            r = requests.get(f"{KIT_URL}/{rel}", timeout=20)
            if r.status_code != 200 or len(r.content) < 1000:
                raise RuntimeError(f"{rel}: HTTP {r.status_code}")
            got[k] = r.content
        if b"renderNews" not in got["lib"]:
            raise RuntimeError("reports-lib.js publicado ainda sem renderNews")
        src = "publicado"
    except Exception as e:
        log.info("whatsapp: kit publicado indisponível (%s) — uso a cópia guardada", e)
        got = {k: (VENDOR_DIR / Path(rel).name).read_bytes() for k, rel in KIT_FILES.items()}
        src = "cópia guardada"
    return {"lib": got["lib"].decode("utf-8"),
            "display": base64.b64encode(got["display"]).decode("ascii"),
            "body": base64.b64encode(got["body"]).decode("ascii"),
            "src": src}


def _node() -> str | None:
    """Node do sistema ou o que vem DENTRO do Playwright (o contêiner do job já tem)."""
    n = shutil.which("node")
    if n:
        return n
    try:
        from playwright._impl._driver import compute_driver_executable
        exe = compute_driver_executable()
        node = exe[0] if isinstance(exe, (tuple, list)) else exe
        if node and Path(node).exists():
            return str(node)
    except Exception as e:
        log.debug("whatsapp: Node do Playwright indisponível: %s", e)
    return None


def render(model: dict, kit: dict) -> str:
    node = _node()
    if not node:
        raise RuntimeError("Node não encontrado (nem o do Playwright)")
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        (t / "reports-lib.js").write_text(kit["lib"], encoding="utf-8")
        (t / "model.json").write_text(json.dumps(model, ensure_ascii=False), encoding="utf-8")
        (t / "fonts.json").write_text(json.dumps({"display": kit["display"], "body": kit["body"]}), encoding="utf-8")
        out = t / "out.html"
        r = subprocess.run([node, str(_RENDER_JS), str(t / "reports-lib.js"), str(t / "model.json"),
                            str(t / "fonts.json"), str(out)], capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            raise RuntimeError("render_news.js: " + ((r.stderr or r.stdout or "").strip()[-400:] or f"saída {r.returncode}"))
        html = out.read_text(encoding="utf-8")
    if "</html>" not in html or len(html) < 5000:
        raise RuntimeError("HTML incompleto")
    return html


def build_whatsapp(items: list[ClippingItem], d: date, config: dict | None = None) -> dict:
    """{"html": bytes|None, "error": str|None, "kit": str} — nunca levanta exceção."""
    t0 = time.time()
    try:
        model = news_model(items, d, config)
        kit = _kit()
        html = render(model, kit)
        log.info("whatsapp: HTML pronto (%d KB, %d itens, imagens %d KB, motor: %s) em %.1fs",
                 len(html.encode("utf-8")) // 1024, len(items), model["_img_bytes"] // 1024, kit["src"], time.time() - t0)
        return {"html": html.encode("utf-8"), "error": None, "kit": kit["src"]}
    except Exception as e:
        log.warning("whatsapp: HTML NÃO gerado (%s) — Word e e-mail seguem normais", e)
        return {"html": None, "error": str(e)[:300], "kit": ""}
