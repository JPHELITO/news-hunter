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
from email import policy
from email.message import EmailMessage
from html import escape
from pathlib import Path

from .build import (
    ClippingItem, SECTOR_ORDER, SECTOR_LABEL, TAKE_SYMBOL, _DEFAULT_ANALYSTS,
    InlineSeg, intro_has_content, line_text, parse_intro_lines,
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


def _segs_html(segs: list[InlineSeg]) -> str:
    """Segmentos da mensagem de abertura → HTML (negrito/itálico/sublinhado/cor/link).

    São os MESMOS segmentos que o Word usa (`build.parse_intro_lines`) → e-mail e .docx
    saem com a mesma formatação. Cor sempre inline (o Outlook ignora CSS de <head>)."""
    out = []
    for sg in segs:
        if not sg.text:
            continue
        t = _esc(sg.text)
        if sg.bold:
            t = f"<b>{t}</b>"
        if sg.italic:
            t = f"<i>{t}</i>"
        if sg.underline and not sg.url:                  # link já vem sublinhado
            t = f"<u>{t}</u>"
        color = sg.color or ("0000FF" if sg.url else "")
        if sg.url:
            t = f'<a href="{_attr(sg.url)}" style="color:#{color}">{t}</a>'
        elif color:
            t = f'<span style="color:#{color}">{t}</span>'
        out.append(t)
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
    """Cabeçalho de seção (Sector Headlines / STEEL & MINING / Recent Publications…).

    Usa o MESMO idioma que o próprio Word escreve ao exportar o clipping em HTML:
    16pt Arial negrito, `color:white;background:black` — o realce PRETO de verdade, igual
    ao .docx. Antes era texto preto de 14pt SEM realce: era a diferença que fazia o e-mail
    "não parecer o Word" (o usuário reparou em 2026-08-10). O `background` inline é o que o
    motor do Word/Outlook entende — foi copiado da exportação dele mesmo, não inventado."""
    return (f'{_P}<b><span style="font-size:16.0pt;color:#ffffff;background:#000000">'
            f'{_esc(text)}</span></b></p>')


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


def build_html(items: list[ClippingItem], d: date, config: dict | None = None,
               docx_bytes: bytes | None = None) -> str:
    """HTML do e-mail (e da prévia).

    Com o `.docx` em mão, o HTML é **derivado do próprio Word** (`docx_to_email_html`) —
    tamanho de fonte, negrito, cor, highlight (o amarelo dos títulos!), espaçamento, recuo
    e imagens saem do arquivo, não de regras escritas à mão aqui. Sem o .docx (ou se a
    conversão falhar) cai no HTML montado abaixo, que é aproximado."""
    if docx_bytes:
        try:
            from .docx_to_email import docx_to_email_html
            # o índice do Word usa âncora interna (art0, art1…); em e-mail âncora não
            # funciona → mapeia p/ a URL da notícia, na MESMA ordem em que o Word numerou
            urls = {f"art{i}": it.url for i, it in enumerate(items)}
            urls.update({f"art{i}tr": it.url for i, it in enumerate(items)})
            return docx_to_email_html(docx_bytes, url_by_bookmark=urls)
        except Exception as e:
            log.warning("eml: nao consegui derivar o HTML do Word (%s) — usando o HTML montado", e)

    config = config or {}
    intro = config.get("intro") or {}
    intro_html = ""
    if intro_has_content(intro):
        intro_html = "".join(
            (f'{_PJ}{_segs_html(ln)}</p>' if line_text(ln).strip() else BLANK)
            for ln in parse_intro_lines(intro)
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

    # LARGURA — a coluna ACOMPANHA a janela (`width="100%"`), com teto num <div> POR DENTRO
    # p/ não virar linha quilométrica em monitor grande. Era fixa em 640px e o usuário viu
    # como "cortado" (2026-08-10): na janela dele (~1900px) as manchetes quebravam no meio e
    # sobrava um vazio enorme à direita.
    # ⚠️ O teto NÃO pode ir na TABELA: medido no motor do Word, `max-width` na tabela é
    # convertido em largura FIXA (tabela virou 11,46in numa página de 7,07in úteis → voltava
    # a cortar ao imprimir/encaminhar). Em <div> ele é ignorado pelo Outlook (fluido lá) e
    # respeitado por Gmail/webmail/mobile. Medido: tabela 100% (com ou sem style) = 7,07in
    # = exatamente a largura útil → cabe sempre ao paginar.
    # O @page ajusta a margem do caminho IMPRIMIR/ENCAMINHAR (padrão A4 = 1,18in de cada
    # lado; 0,6in dá 7,07in úteis). ⚠️ TEM que ser no formato do Word (`@page WordSection1`
    # + `div.WordSection1` envolvendo o corpo): `@page` sozinho o importador de HTML do Word
    # IGNORA (medido). No painel de leitura do Outlook o @page é ignorado — lá não muda.
    return ('<html><head><meta charset="utf-8">'
            '<meta name="color-scheme" content="light only">'
            '<meta name="supported-color-schemes" content="light">'
            '<style>@page WordSection1{size:8.27in 11.69in;margin:0.6in 0.6in 0.6in 0.6in;}'
            'div.WordSection1{page:WordSection1;}</style></head>'
            f'<body lang="EN-US" style="margin:0;padding:0;background:#ffffff;color:#000000;'
            f'word-wrap:break-word;font-family:{_FONT}">'
            '<div class="WordSection1">'
            '<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
            'width="100%" style="width:100%;border-collapse:collapse"><tr>'
            '<td style="padding:0">'
            f'<div style="max-width:{_COL_MAX}px">'
            f'{_banner_html(d)}{BLANK}{intro_html}{index_block}{recent_html}{earnings_html}'
            f'{analysts_block}{"".join(sections)}'
            '</div></td></tr></table></div></body></html>')


# ── Imagens: data-URI/URL → anexo inline "cid:" (o Outlook BLOQUEIA data:image) ──────

_IMG_TAG_RE = re.compile(r'<img\b[^>]*?>', re.I)      # a tag INTEIRA (precisa p/ pôr width)
_IMG_SRC_RE = re.compile(r'\bsrc="([^"]+)"', re.I)    # o src DENTRO da tag

# Teto p/ fotos REMOTAS embutidas (base64 infla ~33%) — evita e-mail gigante quando a
# edição tem muitas fotos. Passou do teto: a imagem segue como link, não some.
_INLINE_BUDGET = 2_500_000

# A coluna do e-mail é FLUIDA (acompanha a janela); _COL_MAX é só o teto p/ os clientes que
# entendem max-width. _IMG_MAX_W = teto de cada imagem (o Outlook não conhece max-width, então
# sem largura em ATRIBUTO uma imagem grande arrasta a tabela e corta o texto de tudo).
_COL_MAX   = 1100
_IMG_MAX_W = 620


def _px_size(data: bytes) -> tuple[int, int] | None:
    """Largura×altura em pixels do binário da imagem — usa o leitor do python-docx
    (já é dependência do clipping; NÃO precisa de Pillow no runner do Actions)."""
    try:
        from docx.image.image import Image as _DocxImage
        im = _DocxImage.from_blob(data)
        return int(im.px_width), int(im.px_height)
    except Exception as e:                                    # formato exótico/corrompido
        log.info("eml: tamanho da imagem ilegível (%s)", e)
        return None


def _img_with_width(tag: str, data: bytes) -> str:
    """Põe `width=` explícito na tag, limitado à largura da coluna.

    ⚠️ **`max-width:100%` NÃO EXISTE no motor do Outlook/Word.** Sem `width=`, uma imagem
    grande (ex.: print de tabela do Platts com 903px) ESTICA a tabela de 640px inteira →
    a coluna vira ~904px e TODO o texto do e-mail passa da margem e sai CORTADO à direita
    (medido: tabela 9,42in numa página de 5,91in úteis). Com `width=`, o motor respeita.
    Só a largura é escrita — a altura o próprio motor calcula, preservando a proporção.
    """
    if re.search(r'\bwidth\s*=', tag, re.I):                  # já tem (ex.: a logo)
        return tag
    size = _px_size(data)
    if not size:
        return tag
    w = min(size[0], _IMG_MAX_W)
    return tag[:-1].rstrip() + f' width="{w}">'


def _inline_images(html: str) -> tuple[str, list[tuple[str, bytes, str]]]:
    """Troca cada <img src=...> por src="cid:N" e devolve as imagens p/ anexar inline.

    O Outlook não mostra `data:image;base64` (logo/tabelas do Platts viravam quadrado
    quebrado) e pede permissão p/ imagem remota — `cid:` funciona nos dois casos.
    Se o download falhar, o src original fica como estava (nunca perde a imagem).
    Aproveita que os bytes estão em mão para fixar a LARGURA de cada imagem (ver
    `_img_with_width`) — é o que impede uma imagem larga de estourar o e-mail.
    """
    from .build import _fetch_image

    parts: list[tuple[str, bytes, str]] = []
    seen: dict[str, tuple[str, bytes]] = {}
    budget = [_INLINE_BUDGET]

    def repl(m: re.Match) -> str:
        tag = m.group(0)
        msrc = _IMG_SRC_RE.search(tag)
        if not msrc:
            return tag
        src = msrc.group(1)
        if src.startswith("cid:"):
            return tag
        # Foto remota só entra enquanto couber no orçamento; data-URI SEMPRE entra
        # (fora do e-mail ela não existe — ficaria quebrada de qualquer jeito).
        is_data = src.startswith("data:")
        if not is_data and budget[0] <= 0:
            return tag
        hit = seen.get(src)
        if hit is None:
            try:
                got = _fetch_image(src)
            except Exception:
                got = None
            if not got:
                log.info("eml: imagem não embutida (segue como link): %.80s", src)
                return tag
            data, ext = got
            if not is_data:
                budget[0] -= len(data)
            cid = f"img{len(parts) + 1}"
            parts.append((cid, data, "jpeg" if ext == "jpg" else ext))
            seen[src] = hit = (cid, data)
        cid, data = hit
        return _img_with_width(tag.replace(f'src="{src}"', f'src="cid:{cid}"'), data)

    return _IMG_TAG_RE.sub(repl, html), parts


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

    # o .docx e a fonte da verdade do formato -> o HTML sai dele
    html_email, images = _inline_images(build_html(items, d, config, docx_bytes=docx_bytes))
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
    # ⚠️ CRLF OBRIGATÓRIO. bytes(msg) grava as linhas com \n (LF) — e um .eml com LF é
    # invalido: o Outlook nao decodifica o quoted-printable e JOGA O CODIGO-FONTE NA TELA
    # ("<t=able role=3D..."). policy.SMTP grava \r\n. Era ESTE o bug do "e-mail cagado".
    return msg.as_bytes(policy=policy.SMTP)
