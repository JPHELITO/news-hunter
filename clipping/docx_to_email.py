"""Converte o .docx do clipping em HTML de e-mail — LÊ o Word em vez de imitar.

Por que existe (2026-08-10): o HTML do e-mail era montado à mão, em paralelo ao Word, e
vivia divergindo — o usuário pegava um a um ("os títulos não estão com o highlight
amarelo", "os tamanhos parecem estranhos", "os espaçamentos"). E ele estava certo: o
`.docx` sai perfeito, então a fonte da verdade tem que ser ELE. Aqui o HTML é derivado do
próprio arquivo: tamanho de fonte, negrito, cor, **highlight**, espaçamento, recuo,
alinhamento, imagens e links vêm todos do XML do Word.

O que NÃO dá p/ derivar (e por que): colar-como-RTF é Word+Outlook, só roda em máquina com
Office — o runner do Actions é Linux. Daí a tradução.

Uso:
    html = docx_to_email_html(docx_bytes, url_by_bookmark={"art0": "https://…"})

As imagens saem como `data:` e o `eml._inline_images` as converte em anexo `cid:` (que é o
que o Outlook mostra). Falha na conversão → quem chama cai no HTML montado (fallback).
"""
from __future__ import annotations

import base64
import logging
import re
from html import escape
from io import BytesIO
from zipfile import ZipFile

log = logging.getLogger(__name__)

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_WP = "{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}"
_MC = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"

_FONT_FALLBACK = "Arial,Helvetica,sans-serif"
_EMU_PX = 914400 / 96          # EMU por pixel

# O Outlook não conhece `background` por nome de realce do Word — mapeia p/ hex.
_HL_HEX = {
    "yellow": "#FFFF00", "green": "#00FF00", "cyan": "#00FFFF", "magenta": "#FF00FF",
    "blue": "#0000FF", "red": "#FF0000", "darkBlue": "#000080", "darkCyan": "#008080",
    "darkGreen": "#008000", "darkMagenta": "#800080", "darkRed": "#800000",
    "darkYellow": "#808000", "darkGray": "#808080", "lightGray": "#C0C0C0",
    "black": "#000000", "white": "#FFFFFF",
}


def _esc(s) -> str:
    return escape(str(s or ""), quote=False)


def _attr(s) -> str:
    return escape(str(s or ""), quote=True)


def _px(emu: str | int | None) -> int | None:
    try:
        return round(int(emu) / _EMU_PX)
    except (TypeError, ValueError):
        return None


# Fonte p/ desenhar a barra: Arial (Windows) → Liberation Sans (Linux, metricamente igual à
# Arial; vem no container do Playwright) → DejaVu. Sem nenhuma, a barra cai p/ HTML.
_FONTS_BOLD = ("arialbd.ttf", "Arial Bold.ttf",
               "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
               "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
               "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
_FONTS_REG = ("arial.ttf", "Arial.ttf",
              "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
              "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")


def _ttf(bold: bool, px: float):
    from PIL import ImageFont
    for c in (_FONTS_BOLD if bold else _FONTS_REG):
        try:
            return ImageFont.truetype(c, round(px))
        except Exception:
            continue
    return None


def _bar_png(linhas: list[tuple[str, bool]], w_px: int, h_px: int, pt: float,
             radius: int) -> str | None:
    """Desenha a barra preta do topo COMO IMAGEM, no tamanho exato da forma do Word.

    Por que imagem: no e-mail que o usuário mandava à mão (colar-como-RTF), o Word
    RASTERIZAVA a forma — medido no PDF de um e-mail real dele: a barra é uma imagem de
    **1497×56 px**. Ele repetiu 2× que "a barrinha é uma figura" e estava certo.
    Texto desenhado com Arial/Liberation (metricamente iguais). Sem fonte → devolve None e
    o chamador usa a célula preta em HTML.
    """
    try:
        from PIL import Image, ImageDraw
    except ImportError:                                        # pragma: no cover
        return None
    s = 2                                                      # 2× e reduz: texto liso
    fb, fr = _ttf(True, pt * 96 / 72 * s), _ttf(False, pt * 96 / 72 * s)
    if not fb or not fr:
        log.info("eml: sem fonte p/ desenhar a barra — usando HTML")
        return None
    try:
        im = Image.new("RGB", (w_px * s, h_px * s), "#FFFFFF")
        dr = ImageDraw.Draw(im)
        try:
            dr.rounded_rectangle([0, 0, w_px * s - 1, h_px * s - 1], radius=radius * s,
                                 fill="#000000", corners=(False, True, True, False))
        except TypeError:                                      # Pillow < 9.4
            dr.rectangle([0, 0, w_px * s - 1, h_px * s - 1], fill="#000000")
        # lIns/tIns do bodyPr da forma: 0,1in e 0,05in
        x, alt = round(9.6 * s), pt * 96 / 72 * 1.25
        y = round((h_px - alt * len(linhas)) / 2) * s
        for txt, bold in linhas:
            dr.text((x, y), txt, font=(fb if bold else fr), fill="#FFFFFF")
            y += round(alt * s)
        im = im.resize((w_px, h_px), Image.LANCZOS)
        from io import BytesIO as _B
        buf = _B()
        im.save(buf, "PNG", optimize=True)
        return base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception as e:                                     # pragma: no cover
        log.info("eml: nao desenhei a barra (%s)", e)
        return None


def _tw_pt(twips: str | int | None) -> float | None:
    """twip (1/20 pt) → pt."""
    try:
        return int(twips) / 20.0
    except (TypeError, ValueError):
        return None


class _DocxEmail:
    def __init__(self, docx_bytes: bytes, url_by_bookmark: dict[str, str] | None = None):
        self.zip = ZipFile(BytesIO(docx_bytes))
        self.urls = url_by_bookmark or {}
        self.rels = self._load_rels("word/_rels/document.xml.rels")
        self.default_pt, self.default_font = self._defaults()
        self.list_pt = self._list_indent_pt()
        # a barra sai FORA da tabela do texto (senão a imagem de 1496px arrasta
        # a coluna inteira p/ 15,58in e o texto é cortado ao imprimir)
        self.banner_html = ""

    # ── partes do pacote ──────────────────────────────────────────────────────
    def _load_rels(self, path: str) -> dict[str, tuple[str, str]]:
        """rId → (Target, TargetMode)."""
        out: dict[str, tuple[str, str]] = {}
        try:
            from lxml import etree
            root = etree.fromstring(self.zip.read(path))
        except Exception:
            return out
        for rel in root:
            out[rel.get("Id")] = (rel.get("Target") or "", rel.get("TargetMode") or "Internal")
        return out

    def _media(self, rid: str, part_rels: dict | None = None) -> tuple[bytes, str] | None:
        rels = part_rels if part_rels is not None else self.rels
        target = (rels.get(rid) or ("", ""))[0]
        if not target:
            return None
        name = "word/" + target.lstrip("/").replace("../", "")
        try:
            data = self.zip.read(name)
        except KeyError:
            return None
        ext = name.rsplit(".", 1)[-1].lower()
        return data, ("jpeg" if ext in ("jpg", "jpeg") else ext)

    def _defaults(self) -> tuple[float, str]:
        """Tamanho e fonte padrão do documento (docDefaults/styles)."""
        pt, font = 11.0, _FONT_FALLBACK
        try:
            from lxml import etree
            root = etree.fromstring(self.zip.read("word/styles.xml"))
            sz = root.find(f".//{_W}docDefaults//{_W}rPr/{_W}sz")
            if sz is not None and sz.get(f"{_W}val"):
                pt = int(sz.get(f"{_W}val")) / 2.0
            rf = root.find(f".//{_W}docDefaults//{_W}rPr/{_W}rFonts")
            if rf is not None and rf.get(f"{_W}ascii"):
                font = f'{rf.get(f"{_W}ascii")},Helvetica,sans-serif'
        except Exception:
            pass
        return pt, font

    def _list_indent_pt(self) -> float:
        """Recuo do bullet como o Word define em numbering.xml (nivel 0). Fallback 51pt."""
        try:
            from lxml import etree
            root = etree.fromstring(self.zip.read("word/numbering.xml"))
            ind = root.find(f".//{_W}abstractNum/{_W}lvl/{_W}pPr/{_W}ind")
            v = _tw_pt(ind.get(f"{_W}left")) if ind is not None else None
            return v or 51.0
        except Exception:
            return 51.0

    # ── run ───────────────────────────────────────────────────────────────────
    def _run_html(self, r, *, link: str = "") -> str:
        rPr = r.find(f"{_W}rPr")
        css: list[str] = []
        pre, pos = "", ""
        if rPr is not None:
            if rPr.find(f"{_W}b") is not None and rPr.find(f"{_W}b").get(f"{_W}val") != "0":
                pre, pos = pre + "<b>", "</b>" + pos
            if rPr.find(f"{_W}i") is not None and rPr.find(f"{_W}i").get(f"{_W}val") != "0":
                pre, pos = pre + "<i>", "</i>" + pos
            u = rPr.find(f"{_W}u")
            if u is not None and (u.get(f"{_W}val") or "single") != "none":
                pre, pos = pre + "<u>", "</u>" + pos
            sz = rPr.find(f"{_W}sz")
            if sz is not None and sz.get(f"{_W}val"):
                css.append(f"font-size:{int(sz.get(f'{_W}val')) / 2.0:.1f}pt")
            col = rPr.find(f"{_W}color")
            if col is not None and (col.get(f"{_W}val") or "auto") != "auto":
                css.append(f"color:#{col.get(f'{_W}val')}")
            hl = rPr.find(f"{_W}highlight")
            if hl is not None and (hl.get(f"{_W}val") or "none") != "none":
                # É ESTE o "highlight amarelo dos títulos" que faltava no e-mail.
                css.append(f"background:{_HL_HEX.get(hl.get(f'{_W}val'), hl.get(f'{_W}val'))}")
            rf = rPr.find(f"{_W}rFonts")
            if rf is not None and rf.get(f"{_W}ascii"):
                css.append(f'font-family:{rf.get(f"{_W}ascii")},Helvetica,sans-serif')

        inner = ""
        for el in r:
            tag = el.tag
            if tag == f"{_W}t":
                inner += _esc(el.text or "")
            elif tag == f"{_W}tab":
                inner += "&nbsp;&nbsp;&nbsp;&nbsp;"
            elif tag in (f"{_W}br", f"{_W}cr"):
                inner += "<br>"
            elif tag == f"{_W}drawing":
                inner += self._drawing_html(el)
            elif tag == f"{_MC}AlternateContent":
                # O Word embrulha forma MODERNA (a barrinha preta) em mc:AlternateContent:
                # <mc:Choice Requires="wps"><w:drawing>… Sem isto o banner sumia do e-mail.
                d = el.find(f"{_MC}Choice/{_W}drawing")
                if d is None:
                    d = el.find(f".//{_W}drawing")
                if d is not None:
                    inner += self._drawing_html(d)
            elif tag in (f"{_W}noBreakHyphen", f"{_W}softHyphen"):
                inner += ""
        if not inner:
            return ""
        html = f'<span style="{";".join(css)}">{pre}{inner}{pos}</span>' if css else f"{pre}{inner}{pos}"
        if link:
            html = f'<a href="{_attr(link)}">{html}</a>'
        return html

    # ── imagens e a forma do banner ───────────────────────────────────────────
    def _drawing_html(self, drawing) -> str:
        blip = drawing.find(f".//{_A}blip")
        if blip is not None:
            rid = blip.get(f"{_R}embed")
            got = self._media(rid)
            if not got:
                return ""
            data, sub = got
            ext = drawing.find(f".//{_WP}extent")
            w = _px(ext.get("cx")) if ext is not None else None
            b64 = base64.b64encode(data).decode("ascii")
            wattr = f' width="{w}"' if w else ""
            return (f'<img src="data:image/{sub};base64,{b64}"{wattr} '
                    f'style="max-width:100%;height:auto;display:block;border:0" alt="">')
        txbx = drawing.find(f".//{_W}txbxContent")
        if txbx is not None:
            return self._banner_html(drawing, txbx)
        return ""

    def _banner_html(self, drawing, txbx) -> str:
        """A 'barrinha de cima' → IMAGEM, do tamanho exato da forma do Word.

        É o que o colar-como-RTF sempre fez: medi o PDF de um e-mail real do usuário e a
        barra lá é uma **imagem de 1497×56 px** — o Word rasteriza a forma ao colar. Ele
        insistiu ("tenho CERTEZA que essa barrinha é uma figura") e estava certo.

        A imagem sai no tamanho da forma (`wp:extent`), com o arco `round2SameRect` na
        direita — que na prática fica fora da vista, como no Word (forma de ~1496px numa
        página de ~560px). Vai em TABELA PRÓPRIA, sem `width=100%`: assim a barra larga não
        arrasta a largura da tabela do TEXTO (foi o que cortou o texto de todas as páginas
        quando uma imagem de 903px esticou a coluna). Sem Pillow/fonte → célula preta em HTML.
        """
        ext = drawing.find(f".//{_WP}extent")
        w = _px(ext.get("cx")) if ext is not None else 1496
        h = _px(ext.get("cy")) if ext is not None else 55
        raio = self._corner_radius(drawing, h)

        linhas_txt: list[tuple[str, bool]] = []
        linhas_html: list[str] = []
        for p in txbx.findall(f".//{_W}p"):
            runs = p.findall(f"{_W}r")
            txt = "".join("".join(t.text or "" for t in r.findall(f"{_W}t")) for r in runs)
            if not txt.strip():
                continue
            bold = any(r.find(f"{_W}rPr/{_W}b") is not None for r in runs)
            linhas_txt.append((txt, bold))
            linhas_html.append(f'<p style="margin:0;font-family:{self.default_font};'
                               f'font-size:{self.default_pt:.1f}pt;color:#ffffff;'
                               f'line-height:normal">'
                               f'{"<b>" if bold else ""}{_esc(txt)}{"</b>" if bold else ""}</p>')

        png = _bar_png(linhas_txt, w, h, self.default_pt, raio) if linhas_txt else None
        if png:
            self.banner_html = ('<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
                    'style="border-collapse:collapse;margin:0"><tr><td style="padding:0">'
                    f'<img src="data:image/png;base64,{png}" width="{w}" height="{h}" '
                    f'style="display:block;border:0" alt="Itau BBA | Equity Research">'
                    '</td></tr></table>')
            return ""          # o banner é emitido fora, pelo docx_to_email_html
        # reserva: célula preta fluida (mesma altura/tipografia da forma)
        return ('<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" '
                'style="width:100%;border-collapse:collapse;margin:0"><tr>'
                f'<td bgcolor="#000000" height="{h}" valign="middle" '
                f'style="background:#000000;height:{h}px;padding:0 12px;vertical-align:middle">'
                f'{"".join(linhas_html)}</td></tr></table>')

    @staticmethod
    def _corner_radius(drawing, h: int) -> int:
        """Raio do arco da forma (a:gd 'val 16667' = 16,667% da menor dimensão)."""
        adj = 16667
        gd = drawing.find(f".//{_A}avLst/{_A}gd")
        if gd is not None and (gd.get("fmla") or "").startswith("val "):
            try:
                adj = int((gd.get("fmla") or "val 16667").split()[1])
            except (ValueError, IndexError):
                pass
        return max(2, round(h * adj / 100000.0))

    # ── parágrafo ─────────────────────────────────────────────────────────────
    def _para(self, p) -> tuple[str, bool]:
        """Devolve (html_do_paragrafo, é_bullet)."""
        pPr = p.find(f"{_W}pPr")
        css = [f"font-family:{self.default_font}"]
        bullet = False
        if pPr is not None:
            jc = pPr.find(f"{_W}jc")
            if jc is not None and jc.get(f"{_W}val"):
                v = jc.get(f"{_W}val")
                css.append("text-align:" + {"both": "justify", "center": "center",
                                            "right": "right", "left": "left"}.get(v, v))
            sp = pPr.find(f"{_W}spacing")
            before = after = None
            if sp is not None:
                before, after = _tw_pt(sp.get(f"{_W}before")), _tw_pt(sp.get(f"{_W}after"))
                line, rule = sp.get(f"{_W}line"), (sp.get(f"{_W}lineRule") or "auto")
                if line:
                    css.append(f"line-height:{_tw_pt(line):.1f}pt" if rule in ("atLeast", "exact")
                               else f"line-height:{int(line) / 240.0:.2f}")
            css.append(f"margin:{before or 0:.1f}pt 0 {after or 0:.1f}pt 0")
            ind = pPr.find(f"{_W}ind")
            if ind is not None and ind.get(f"{_W}left"):
                css.append(f"margin-left:{_tw_pt(ind.get(f'{_W}left')):.1f}pt")
            bullet = pPr.find(f"{_W}numPr") is not None
            # tamanho da MARCA de parágrafo: define a altura da linha em branco
            mark = pPr.find(f"{_W}rPr/{_W}sz")
            if mark is not None and mark.get(f"{_W}val"):
                css.append(f"font-size:{int(mark.get(f'{_W}val')) / 2.0:.1f}pt")
        if not any(c.startswith("margin:") for c in css):
            css.append("margin:0")

        parts: list[str] = []
        for el in p:
            if el.tag == f"{_W}r":
                parts.append(self._run_html(el))
            elif el.tag == f"{_W}hyperlink":
                rid, anchor = el.get(f"{_R}id"), el.get(f"{_W}anchor")
                href = ""
                if rid and self.rels.get(rid, ("", ""))[1] == "External":
                    href = self.rels[rid][0]
                elif anchor:
                    # âncora interna não funciona em e-mail → aponta p/ a URL da notícia
                    href = self.urls.get(anchor, "")
                for r in el.findall(f"{_W}r"):
                    parts.append(self._run_html(r, link=href))
        body = "".join(parts)
        if not body.strip():
            body = "&nbsp;"
        return f'<p style="{";".join(css)}">{body}</p>', bullet

    # ── documento ─────────────────────────────────────────────────────────────
    def html(self, *, logo_do_cabecalho: bool = False) -> str:
        from lxml import etree
        root = etree.fromstring(self.zip.read("word/document.xml"))
        body = root.find(f"{_W}body")
        out: list[str] = []
        lista: list[str] = []           # bullets consecutivos

        def flush():
            if lista:
                out.append(f'<ul type="disc" style="margin:0;padding-left:{self.list_pt:.1f}pt">'
                           f'{"".join(lista)}</ul>')
                lista.clear()

        # ⚠️ NAO injetar a logo do cabecalho por padrao: o CORPO do clipping ja tem a
        # mesma imagem (media/image1.png, inline) — injetar duplicava a logo no e-mail.
        logo_pendente = self._logo_html() if logo_do_cabecalho else ""
        for el in body:
            if el.tag != f"{_W}p":
                continue
            html_p, bullet = self._para(el)
            if bullet:
                lista.append(f'<li style="margin:0">{html_p}</li>')
                continue
            flush()
            out.append(html_p)
            if logo_pendente and "bgcolor=\"#000000\"" in html_p:
                out.append(logo_pendente)      # a logo do Word vive no CABEÇALHO: entra aqui
                logo_pendente = ""
        flush()
        if logo_pendente:                       # documento sem banner → logo no topo
            out.insert(0, logo_pendente)
        return "".join(out)

    def _logo_html(self) -> str:
        """A logo itaú BBA está no CABEÇALHO do Word (nenhum CTRL-A a copia) — puxo de lá
        com o tamanho que ela tem no documento."""
        from lxml import etree
        for name in ("word/header2.xml", "word/header1.xml", "word/header3.xml"):
            try:
                hroot = etree.fromstring(self.zip.read(name))
            except KeyError:
                continue
            blip = hroot.find(f".//{_A}blip")
            if blip is None:
                continue
            rels = self._load_rels(name.replace("word/", "word/_rels/") + ".rels")
            got = self._media(blip.get(f"{_R}embed"), rels)
            if not got:
                continue
            data, sub = got
            ext = hroot.find(f".//{_WP}extent")
            w = _px(ext.get("cx")) if ext is not None else None
            b64 = base64.b64encode(data).decode("ascii")
            wattr = f' width="{w}"' if w else ""
            return (f'<p style="margin:6pt 0 0 0"><img src="data:image/{sub};base64,{b64}"'
                    f'{wattr} style="display:block;border:0" alt="Itau BBA"></p>')
        return ""


def docx_to_email_html(docx_bytes: bytes, *, url_by_bookmark: dict[str, str] | None = None,
                       col_max_px: int = 1100) -> str:
    """.docx do clipping → HTML de e-mail (Outlook-friendly), derivado do próprio Word.

    `url_by_bookmark`: bookmark do Word ('art0') → URL da notícia, p/ o índice virar link
    externo (âncora interna não funciona em e-mail).
    """
    conv = _DocxEmail(docx_bytes, url_by_bookmark)
    corpo = conv.html()
    if not corpo.strip():
        raise ValueError("conversão do .docx devolveu corpo vazio")
    return ('<html><head><meta charset="utf-8">'
            '<meta name="color-scheme" content="light only">'
            '<meta name="supported-color-schemes" content="light">'
            '<style>@page WordSection1{size:8.27in 11.69in;margin:0.6in 0.6in 0.6in 0.6in;}'
            'div.WordSection1{page:WordSection1;}</style></head>'
            f'<body lang="EN-US" style="margin:0;padding:0;background:#ffffff;color:#000000;'
            f'word-wrap:break-word;font-family:{conv.default_font}">'
            '<div class="WordSection1">'
            f'{conv.banner_html}'
            '<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" '
            'style="width:100%;border-collapse:collapse"><tr><td style="padding:0">'
            f'<div style="max-width:{col_max_px}px">{corpo}</div>'
            '</td></tr></table></div></body></html>')
