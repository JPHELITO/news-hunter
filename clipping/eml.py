"""Rascunho de e-mail (.eml) do clipping — HTML estilo Outlook, mesma seleção do .docx.

build_eml_bytes(items, d, docx_bytes=None) devolve os bytes do .eml:
  • cabeçalho + Sector Headlines (agrupado por setor, take colorido, link)
  • corpos das matérias inline (por setor, bilíngue quando houver tradução)
  • bloco de contatos
  • o .docx anexado (se docx_bytes for passado), pronto para revisar e enviar.
"""
from __future__ import annotations

import logging
from datetime import date
from email.message import EmailMessage
from html import escape

from .build import (
    ClippingItem, SECTOR_ORDER, SECTOR_LABEL, TAKE_SYMBOL, TAKE_COLOR_HEX,
)

log = logging.getLogger(__name__)

MONTH_EN = {1: "January", 2: "February", 3: "March", 4: "April", 5: "May", 6: "June",
            7: "July", 8: "August", 9: "September", 10: "October", 11: "November", 12: "December"}

# Estilo Calibri com margin:0 inline (sobrevive ao encaminhar/responder no Outlook)
_PB = "margin:0;font-size:11.0pt;font-family:Calibri,sans-serif"
_P = f'<p style="{_PB}">'
_PJ = f'<p style="{_PB};text-align:justify">'
_PC = f'<p style="{_PB};text-align:center">'
BLANK = f'{_P}&nbsp;</p>'

CONTACTS = [
    ("Daniel Sasson, CFA", "daniel.sasson@itaubba.com"),
    ("Marcelo Furlan",     "marcelo.palhares@itaubba.com"),
    ("João Paulo Helito",  "joao.helito@itaubba.com"),
]


def _esc(s) -> str:
    return escape(str(s or ""), quote=False)


def _intro_line_html(ln: str) -> str:
    """Converte **negrito** e [texto](url) na mensagem de abertura; escapa o resto."""
    import re
    out, pos = [], 0
    for m in re.finditer(r'\*\*(.+?)\*\*|\[([^\]]+)\]\((https?://[^)\s]+)\)', ln):
        if m.start() > pos:
            out.append(_esc(ln[pos:m.start()]))
        if m.group(1) is not None:                       # **negrito**
            out.append(f'<b>{_esc(m.group(1))}</b>')
        else:                                            # [texto](url)
            out.append(f'<a href="{escape(m.group(3), quote=True)}">{_esc(m.group(2))}</a>')
        pos = m.end()
    if pos < len(ln):
        out.append(_esc(ln[pos:]))
    return "".join(out)


def _fmt_date(d: date) -> str:
    return f"{d.day:02d} {MONTH_EN[d.month]} {d.year}"


def _by_sector(items: list[ClippingItem]):
    groups: dict[str, list[ClippingItem]] = {}
    for it in items:
        groups.setdefault(it.sector or "SM", []).append(it)
    # ordem canônica SM→PP→NR→CEMENT; setores fora da lista vão ao fim
    ordered = [(s, groups[s]) for s in SECTOR_ORDER if groups.get(s)]
    ordered += [(s, v) for s, v in groups.items() if s not in SECTOR_ORDER]
    return ordered


def _take_html(take: str) -> str:
    sym = TAKE_SYMBOL.get(take, "")
    col = TAKE_COLOR_HEX.get(take, "595959")
    return f'&nbsp;<b><span style="color:#{col}">{sym}</span></b>' if sym else ""


def _pub_html(title_label: str, pub_list) -> str:
    """Bloco de publicações no e-mail (Recent Publications / Earnings Review)."""
    lis = []
    for pub in (pub_list or []):
        name = _esc((pub.get("name") or "").strip())
        if not name:
            continue
        sec   = SECTOR_LABEL.get(pub.get("sector"), pub.get("sector") or "")
        link  = (pub.get("link") or "").strip()
        title = f'<a href="{_esc(link)}">{name}</a>' if link else name
        lis.append(f'<li style="{_PB}"><b>{_esc(sec)} &ndash;</b> {title}</li>')
    if not lis:
        return ""
    hdr = f'{_P}<b><span style="font-size:14.0pt;color:#FF5000">{_esc(title_label)}</span></b></p>'
    return hdr + f'<ul type="disc">{"".join(lis)}</ul>' + BLANK


def build_html(items: list[ClippingItem], d: date, config: dict | None = None) -> str:
    config = config or {}
    intro = config.get("intro") or {}
    intro_html = ""
    if intro.get("on") and (intro.get("text") or "").strip():
        intro_html = "".join(
            (f'{_P}{_intro_line_html(ln)}</p>' if ln.strip() else BLANK) for ln in intro["text"].splitlines()
        ) + BLANK
    _er = config.get("earnings_review") or {}
    recent_html   = _pub_html("Recent Publications", config.get("recent_publications"))
    earnings_html = _pub_html(_er.get("label") or "Earnings Review", _er.get("items")) if _er.get("on") else ""
    header = (f'{_PC}<b><span style="font-size:18.0pt;color:#FF5000">'
              f'*** Equity Research Daily &ndash; {_esc(_fmt_date(d))} ***</span></b></p>')

    # ── Sector Headlines (índice) ──
    idx = [f'{_P}<b><span style="font-size:14.0pt;color:#FF5000">Sector Headlines</span></b></p>']
    for sector, its in _by_sector(items):
        label = SECTOR_LABEL.get(sector, sector)
        lis = []
        for it in its:
            src = f' [{_esc(it.source_name)}]' if it.source_name else ""
            lis.append(f'<li style="{_PB}"><b>{_esc(label)} &ndash;</b> '
                       f'<a href="{_esc(it.url)}">{_esc(it.title)}</a>{src}{_take_html(it.take)}</li>')
        idx.append(f'<ul type="disc">{"".join(lis)}</ul>')
    index_block = "".join(idx)

    # ── Corpos por setor (bilíngue quando houver tradução) ──
    sections = []
    for sector, its in _by_sector(items):
        label = SECTOR_LABEL.get(sector, sector)
        sections.append(f'{_P}<b><span style="font-size:13.0pt;background:#111;color:#fff">'
                        f'&nbsp;{_esc(label)}&nbsp;</span></b></p>{BLANK}')
        for it in its:
            src = f' ({_esc(it.source_name)})' if it.source_name else ""
            # original
            sections.append(f'{_P}<b><span style="font-size:14.0pt">{_esc(it.title)}{src}</span></b></p>')
            sections.append((it.body or f'{_P}[Corpo do artigo não disponível]</p>'))
            # tradução (bloco extra)
            if it.translated_title or it.translated_body:
                sections.append(f'{BLANK}{_P}<b><span style="font-size:12.0pt;color:#555">'
                                f'{_esc(it.translated_title)} (Free Translation)</span></b></p>')
                sections.append(it.translated_body or "")
            sections.append(f'{_P}<span style="color:#555">Source:</span> '
                            f'<a href="{_esc(it.url)}">{_esc(it.url)}</a></p>{BLANK}')

    # ── Contatos (analistas configuráveis no admin — fallback = CONTACTS) ──
    _analysts = (config or {}).get("analysts") or None
    _clist = ([(a.get("name", ""), a.get("email", "")) for a in _analysts
               if (a.get("name") or a.get("email"))] if _analysts else CONTACTS)
    contacts = [f'{_P}<b><span style="color:#FF5000">Equity Research</span></b></p>']
    for name, mail in _clist:
        _mailhtml = (f'&nbsp;<a href="mailto:{escape(mail, quote=True)}">'
                     f'<span style="font-size:10.0pt">{_esc(mail)}</span></a>') if mail else ""
        contacts.append(f'{_PJ}<b><span style="font-size:10.0pt">{_esc(name)} /</span></b>{_mailhtml}</p>')
    contacts_block = "".join(contacts)

    return ('<html><head><meta charset="utf-8"></head>'
            '<body lang="EN-US" style="word-wrap:break-word">'
            f'{intro_html}{header}{BLANK}{index_block}{BLANK}{recent_html}{earnings_html}'
            f'{contacts_block}{BLANK}{"".join(sections)}'
            '</body></html>')


def build_plain_text(items: list[ClippingItem], d: date) -> str:
    lines = [f"*** Equity Research Daily - {_fmt_date(d)} ***", "", "Sector Headlines"]
    for sector, its in _by_sector(items):
        label = SECTOR_LABEL.get(sector, sector)
        for it in its:
            sym = TAKE_SYMBOL.get(it.take, "")
            lines.append(f"  - {label} - {it.title} [{it.source_name}] {sym}")
    lines.append("")
    lines.append("(Corpos das matérias no documento anexo.)")
    return "\n".join(lines)


def build_eml_bytes(items: list[ClippingItem], d: date | None = None,
                    docx_bytes: bytes | None = None,
                    docx_name: str | None = None,
                    config: dict | None = None) -> bytes:
    d = d or date.today()
    msg = EmailMessage()
    msg["Subject"] = f"Equity Research Daily - {_fmt_date(d)}"
    msg["From"] = ""
    msg["To"] = ""
    msg.set_content(build_plain_text(items, d), charset="utf-8")
    msg.add_alternative(build_html(items, d, config), subtype="html")
    if docx_bytes:
        name = docx_name or f"clipping_{d.strftime('%Y%m%d')}.docx"
        msg.add_attachment(
            docx_bytes,
            maintype="application",
            subtype="vnd.openxmlformats-officedocument.wordprocessingml.document",
            filename=name,
        )
    return bytes(msg)
