"""Rascunho de e-mail (.eml) do clipping — HTML que REPLICA o Word gerado.

O usuário antes fazia CTRL-A no Word + colar-como-RTF no e-mail; agora o .eml já sai
nesse formato: banner preto do Itaú BBA, Sector Headlines / Recent Publications /
Earnings (cabeçalhos PRETOS em negrito), bloco de analistas, e os corpos por setor
(título em negrito → "Source: <fonte>" em itálico → texto → Free Translation).

build_eml_bytes(items, d, docx_bytes=…) devolve os bytes do .eml (HTML + .docx anexado).
"""
from __future__ import annotations

import base64
import logging
import re
from datetime import date
from email.message import EmailMessage
from html import escape
from pathlib import Path

from .build import (
    ClippingItem, SECTOR_ORDER, SECTOR_LABEL, TAKE_SYMBOL, _DEFAULT_ANALYSTS,
)

log = logging.getLogger(__name__)

# Logo "itaú BBA" do banner (a MESMA imagem do template.docx) — embutida em base64
# para o e-mail ser self-contained. Se faltar o arquivo, o banner cai só no texto.
_LOGO_PATH = Path(__file__).resolve().parent / "assets" / "itau_bba_logo.png"
try:
    _LOGO_B64 = base64.b64encode(_LOGO_PATH.read_bytes()).decode("ascii")
except Exception:                                    # pragma: no cover
    _LOGO_B64 = ""

MONTH_EN = {1: "January", 2: "February", 3: "March", 4: "April", 5: "May", 6: "June",
            7: "July", 8: "August", 9: "September", 10: "October", 11: "November", 12: "December"}

# Arial (fonte do Word) com margin:0 inline — sobrevive ao encaminhar/responder no Outlook.
_FONT = "Arial,Helvetica,sans-serif"
_PB = f"margin:0;font-size:11.0pt;font-family:{_FONT}"
_P = f'<p style="{_PB}">'
_PJ = f'<p style="{_PB};text-align:justify">'
BLANK = f'{_P}&nbsp;</p>'

# take: + verde, - vermelho, = preto (igual ao Word)
_TAKE_COLOR = {"+": "00B050", "-": "FF0000", "=": "000000"}
_ORANGE = "FF5000"


def _esc(s) -> str:
    return escape(str(s or ""), quote=False)


def _attr(s) -> str:
    return escape(str(s or ""), quote=True)


def _intro_line_html(ln: str) -> str:
    """Converte **negrito** e [texto](url) na mensagem de abertura; escapa o resto."""
    out, pos = [], 0
    for m in re.finditer(r'\*\*(.+?)\*\*|\[([^\]]+)\]\((https?://[^)\s]+)\)', ln):
        if m.start() > pos:
            out.append(_esc(ln[pos:m.start()]))
        if m.group(1) is not None:                       # **negrito**
            out.append(f'<b>{_esc(m.group(1))}</b>')
        else:                                            # [texto](url)
            out.append(f'<a href="{_attr(m.group(3))}">{_esc(m.group(2))}</a>')
        pos = m.end()
    if pos < len(ln):
        out.append(_esc(ln[pos:]))
    return "".join(out)


def _fmt_date(d: date) -> str:
    return f"{d.day:02d} {MONTH_EN[d.month]} {d.year}"


def _banner_date(d: date) -> str:
    return f"{d.month:02d}/{d.day:02d}/{d.year}"          # MM/DD/YYYY (como o banner do Word)


def _by_sector(items: list[ClippingItem]):
    groups: dict[str, list[ClippingItem]] = {}
    for it in items:
        groups.setdefault(it.sector or "SM", []).append(it)
    ordered = [(s, groups[s]) for s in SECTOR_ORDER if groups.get(s)]
    ordered += [(s, v) for s, v in groups.items() if s not in SECTOR_ORDER]
    return ordered


def _section_h(text: str) -> str:
    """Cabeçalho de seção — PRETO em negrito (Sector Headlines, STEEL & MINING, Recent…)."""
    return f'{_P}<b><span style="font-size:14.0pt;color:#000000">{_esc(text)}</span></b></p>'


def _banner_html(d: date) -> str:
    """Letterhead PRETO (branco no preto), 2 linhas + data + logo — como o cabeçalho do Word."""
    logo = (
        f'<p style="margin:8px 0 0 0"><img src="data:image/png;base64,{_LOGO_B64}" '
        f'width="104" height="55" alt="Itaú BBA" style="display:block;border:0;outline:none"></p>'
    ) if _LOGO_B64 else ""
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" width="100%" '
        'style="border-collapse:collapse;margin:0"><tr>'
        '<td bgcolor="#000000" style="background:#000000;padding:7px 12px">'
        f'<p style="{_PB};color:#ffffff;font-weight:bold">Itaú BBA | Equity Research</p>'
        f'<p style="{_PB};color:#ffffff;font-weight:bold">'
        f'LatAm S&amp;M and P&amp;P Daily News &ndash; {_banner_date(d)}</p>'
        '</td></tr></table>'
        f'{logo}'
    )


def _take_html(take: str) -> str:
    sym = TAKE_SYMBOL.get(take, "")
    col = _TAKE_COLOR.get(take, "000000")
    return f'&nbsp;<b><span style="color:#{col}">{_esc(sym)}</span></b>' if sym else ""


def _style_body(body_html: str) -> str:
    """Envolve o corpo (HTML já seguro) numa fonte Arial + limita imagens à largura."""
    if not body_html:
        return ""
    body_html = re.sub(r'<img ', '<img style="max-width:100%;height:auto" ', body_html)
    return f'<div style="font-size:11.0pt;font-family:{_FONT};text-align:justify">{body_html}</div>'


def _pub_html(title_label: str, pub_list) -> str:
    """Bloco de publicações (Recent Publications / Earnings Review): cabeçalho preto + bullets."""
    lis = []
    for pub in (pub_list or []):
        name = _esc((pub.get("name") or "").strip())
        if not name:
            continue
        sec   = SECTOR_LABEL.get(pub.get("sector"), pub.get("sector") or "")
        link  = (pub.get("link") or "").strip()
        title = f'<a href="{_attr(link)}">{name}</a>' if link else name
        lis.append(f'<li style="{_PB}"><b>{_esc(sec)} &ndash;</b> {title}</li>')
    if not lis:
        return ""
    return _section_h(title_label) + f'<ul type="disc">{"".join(lis)}</ul>' + BLANK


def build_html(items: list[ClippingItem], d: date, config: dict | None = None) -> str:
    config = config or {}
    intro = config.get("intro") or {}
    intro_html = ""
    if intro.get("on") and (intro.get("text") or "").strip():
        intro_html = "".join(
            (f'{_PJ}{_intro_line_html(ln)}</p>' if ln.strip() else BLANK) for ln in intro["text"].splitlines()
        ) + BLANK
    _er = config.get("earnings_review") or {}
    recent_html   = _pub_html("Recent Publications", config.get("recent_publications"))
    earnings_html = _pub_html(_er.get("label") or "Earnings Review", _er.get("items")) if _er.get("on") else ""

    # ── Sector Headlines (índice) — lista ÚNICA contínua (igual ao Word): ──
    #    SETOR - título[link] \ tradução [Fonte] (take)
    idx_lis = []
    for sector, its in _by_sector(items):
        label = SECTOR_LABEL.get(sector, sector)
        for it in its:
            tr  = f' \\ {_esc(it.translated_title)}' if it.translated_title else ""
            src = f'<b> [{_esc(it.source_name)}]</b>' if it.source_name else ""
            idx_lis.append(f'<li style="{_PB}"><b>{_esc(label)} -</b> '
                           f'<a href="{_attr(it.url)}">{_esc(it.title)}</a>{tr}{src}{_take_html(it.take)}</li>')
    index_block = _section_h("Sector Headlines") + f'<ul type="disc">{"".join(idx_lis)}</ul>' + BLANK

    # ── Analistas: nome (laranja) / cargo / telefones / e-mail (link) ──
    _analysts = config.get("analysts") or _DEFAULT_ANALYSTS
    ab = []
    for a in _analysts:
        nm = (a.get("name") or "").strip(); rl = (a.get("role") or "").strip()
        ph = (a.get("phone") or "").strip(); em = (a.get("email") or "").strip()
        if not (nm or em):
            continue
        if nm:
            ab.append(f'{_P}<b><span style="font-size:10.0pt;color:#{_ORANGE}">{_esc(nm)}</span></b></p>')
        if rl:
            ab.append(f'{_P}<span style="font-size:10.0pt">{_esc(rl)}</span></p>')
        if ph:
            ab.append(f'{_P}<span style="font-size:10.0pt">{_esc(ph)}</span></p>')
        if em:
            ab.append(f'{_P}<a href="mailto:{_attr(em)}"><span style="font-size:10.0pt">{_esc(em)}</span></a></p>')
        ab.append(BLANK)
    analysts_block = "".join(ab)

    # ── Corpos por setor — título (negrito) → Source: fonte (itálico) → texto → Free Translation ──
    sections = []
    for sector, its in _by_sector(items):
        label = SECTOR_LABEL.get(sector, sector)
        sections.append(_section_h(label) + BLANK)
        for it in its:
            title_disp = f'{_esc(it.title)} (Original)' if it.translated_title else _esc(it.title)
            sections.append(f'{_P}<b><span style="font-size:12.0pt">{title_disp}</span></b></p>')
            if it.source_name:
                sections.append(f'{_P}<i><span style="font-size:11.0pt">Source: {_esc(it.source_name)}</span></i></p>')
            sections.append(_style_body(it.body) or f'{_P}[Corpo do artigo não disponível]</p>')
            if it.translated_title or it.translated_body:
                sections.append(BLANK + f'{_P}<b><span style="font-size:12.0pt">'
                                        f'{_esc(it.translated_title)} (Free Translation)</span></b></p>')
                if it.source_name:
                    sections.append(f'{_P}<i><span style="font-size:11.0pt">Source: {_esc(it.source_name)}</span></i></p>')
                sections.append(_style_body(it.translated_body))
            sections.append(BLANK)

    # Largura: o Outlook IGNORA max-width em <div> (o texto correria de ponta a ponta da
    # janela); table com width fixo ele respeita — fica com a "cara de página" do Word.
    return ('<html><head><meta charset="utf-8">'
            '<meta name="color-scheme" content="light only">'
            '<meta name="supported-color-schemes" content="light"></head>'
            f'<body lang="EN-US" style="margin:0;padding:0;background:#ffffff;color:#000000;'
            f'word-wrap:break-word;font-family:{_FONT}">'
            '<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
            'width="640" style="width:640px;border-collapse:collapse"><tr>'
            '<td style="padding:0">'
            f'{_banner_html(d)}{BLANK}{intro_html}{index_block}{recent_html}{earnings_html}'
            f'{analysts_block}{"".join(sections)}'
            '</td></tr></table></body></html>')


# ── Imagens: data-URI/URL → anexo inline "cid:" (o Outlook BLOQUEIA data:image) ──────

_IMG_SRC_RE = re.compile(r'<img\b[^>]*?\bsrc="([^"]+)"', re.I)


def _inline_images(html: str) -> tuple[str, list[tuple[str, bytes, str]]]:
    """Troca cada <img src=...> por src="cid:N" e devolve as imagens p/ anexar inline.

    O Outlook não mostra `data:image;base64` (logo/tabelas do Platts viravam quadrado
    quebrado) e pede permissão p/ imagem remota — `cid:` funciona nos dois casos.
    Se o download falhar, o src original fica como estava (nunca perde a imagem).
    """
    from .build import _fetch_image

    parts: list[tuple[str, bytes, str]] = []
    seen: dict[str, str] = {}

    def repl(m: re.Match) -> str:
        src = m.group(1)
        if src.startswith("cid:"):
            return m.group(0)
        cid = seen.get(src)
        if cid is None:
            try:
                got = _fetch_image(src)
            except Exception:
                got = None
            if not got:
                log.info("eml: imagem não embutida (segue como link): %.80s", src)
                return m.group(0)
            data, ext = got
            cid = f"img{len(parts) + 1}"
            parts.append((cid, data, "jpeg" if ext == "jpg" else ext))
            seen[src] = cid
        return m.group(0).replace(f'src="{src}"', f'src="cid:{cid}"')

    return _IMG_SRC_RE.sub(repl, html), parts


def build_plain_text(items: list[ClippingItem], d: date) -> str:
    lines = [f"ITAU BBA Daily News: LatAm Steel & Mining, Pulp & Paper - {_banner_date(d)}",
             "", "Sector Headlines"]
    for sector, its in _by_sector(items):
        label = SECTOR_LABEL.get(sector, sector)
        for it in its:
            sym = TAKE_SYMBOL.get(it.take, "")
            lines.append(f"  - {label} - {it.title} [{it.source_name}] {sym}")
    lines.append("")
    lines.append("(Corpos das matérias no corpo do e-mail em HTML / documento anexo.)")
    return "\n".join(lines)


def build_eml_bytes(items: list[ClippingItem], d: date | None = None,
                    docx_bytes: bytes | None = None,
                    docx_name: str | None = None,
                    config: dict | None = None) -> bytes:
    d = d or date.today()
    msg = EmailMessage()
    msg["Subject"] = (f"*** ITAÚ BBA Daily News: LatAm Steel & Mining, Pulp & Paper "
                      f"- {_banner_date(d)} ***")
    # X-Unsent: 1 -> o Outlook abre o .eml como RASCUNHO NOVO (pronto p/ preencher e
    # enviar). Sem isso ele abre como mensagem RECEBIDA e só dá p/ "Encaminhar" —
    # é de onde vinham o "FW:" e o cabeçalho de encaminhamento em cima do clipping.
    msg["X-Unsent"] = "1"
    msg.set_content(build_plain_text(items, d), charset="utf-8")

    html_email, images = _inline_images(build_html(items, d, config))
    msg.add_alternative(html_email, subtype="html")
    if images:
        # anexa as imagens ao corpo HTML (vira multipart/related) — assim o Outlook mostra
        html_part = msg.get_payload()[-1]
        for cid, data, subtype in images:
            html_part.add_related(data, maintype="image", subtype=subtype, cid=f"<{cid}>")

    if docx_bytes:
        name = docx_name or f"clipping_{d.strftime('%Y%m%d')}.docx"
        msg.add_attachment(
            docx_bytes,
            maintype="application",
            subtype="vnd.openxmlformats-officedocument.wordprocessingml.document",
            filename=name,
        )
    return bytes(msg)
