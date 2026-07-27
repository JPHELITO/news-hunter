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


def build_html(items: list[ClippingItem], d: date) -> str:
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

    # ── Contatos ──
    contacts = [f'{_P}<b><span style="color:#FF5000">Equity Research</span></b></p>']
    for name, mail in CONTACTS:
        contacts.append(f'{_PJ}<b><span style="font-size:10.0pt">{_esc(name)} /</span></b>&nbsp;'
                        f'<a href="mailto:{mail}"><span style="font-size:10.0pt">{mail}</span></a></p>')
    contacts_block = "".join(contacts)

    return ('<html><head><meta charset="utf-8"></head>'
            '<body lang="EN-US" style="word-wrap:break-word">'
            f'{header}{BLANK}{index_block}{BLANK}{contacts_block}{BLANK}{"".join(sections)}'
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
                    docx_name: str | None = None) -> bytes:
    d = d or date.today()
    msg = EmailMessage()
    msg["Subject"] = f"Equity Research Daily - {_fmt_date(d)}"
    msg["From"] = ""
    msg["To"] = ""
    msg.set_content(build_plain_text(items, d), charset="utf-8")
    msg.add_alternative(build_html(items, d), subtype="html")
    if docx_bytes:
        name = docx_name or f"clipping_{d.strftime('%Y%m%d')}.docx"
        msg.add_attachment(
            docx_bytes,
            maintype="application",
            subtype="vnd.openxmlformats-officedocument.wordprocessingml.document",
            filename=name,
        )
    return bytes(msg)
