"""E-mail do clipping com a IDENTIDADE do HTML do WhatsApp (24/11/2026).

O e-mail de hoje (`eml.py` + `docx_to_email.py`) é uma cópia do Word. Este módulo desenha o
mesmo conteúdo com o visual do HTML do WhatsApp (`reports-lib.js`): cabeçalho preto com o
emblema do Itaú nos 3 traços, "Nov 24 / Daily News", destaques, manchetes com o take em
selo colorido, "Our research", matérias completas e o rodapé do time com os avisos legais.

O CONTEÚDO é um só: o modelo vem de `whatsapp.news_model` (os MESMOS `ClippingItem` do Word).
Só a apresentação muda — e tem de sobreviver ao Outlook do Windows, que desenha e-mail com o
motor do Word:
  * layout só com <table> e estilo INLINE (o Outlook ignora quase todo CSS de <head>);
  * a letra inline é Arial; Manrope/Unbounded entram por classe num <style> que o Outlook
    não lê (`<!--[if !mso]>`) — iPhone/Mac mostram a fonte da marca, o Outlook fica em Arial;
  * o topo (emblema + "Nov 24" vazado + "Daily News") é uma IMAGEM tirada do próprio HTML do
    WhatsApp (o Outlook não faz traço vazado, fonte da web nem SVG);
  * toda <img> com `width=` em atributo (sem isso uma imagem larga corta o texto todo);
  * coluna FLUIDA (100% da janela — pedido do usuário em 24/11: a coluna fixa de 660px
    parecia "centralizada" no Outlook). `width=100%` em atributo e estilo, SEM max-width na
    tabela (o Word converte max-width em largura fixa e a folha corta ao imprimir). O topo é
    uma foto de 660px alinhada à esquerda numa faixa preta que vai até a borda — a foto sai
    sem os arcos decorativos, então o preto dela emenda com o da faixa em qualquer largura;
  * o índice ("Sector headlines") leva à matéria DENTRO do e-mail: link `#storyN` → marca
    `<a name="storyN">` (só letra+número, o formato de marcador que o Word aceita); cada
    matéria tem "Back to headlines" de volta;
  * 25/11: topo preto mais baixo (só a foto do e-mail — `HERO_COMPACT_CSS`) e as listas do
    índice e de "Our research" mais juntas (`IDX_GAP`/`PUB_GAP`), para ver tudo de uma vez.
"""
from __future__ import annotations

import base64
import logging
import re
from datetime import date
from email import policy
from email.message import EmailMessage
from html import escape

from .build import ClippingItem, intro_has_content, parse_intro_lines, line_text
from .eml import _banner_date, _inline_images, build_plain_text

log = logging.getLogger(__name__)

COL = 660                    # largura da coluna (px)
PAD = 24                     # margem interna esquerda/direita
IMG_MAX = COL - 2 * PAD      # 612: teto de cada imagem do corpo

# TOPO MAIS BAIXO (pedido do usuário em 25/11: "um pouco menor em comprimento vertical, não muito
# menor"). Muda só a FOTO do e-mail — o HTML do WhatsApp continua igual: menos ar entre as linhas,
# o emblema no tamanho que o próprio HTML usa no celular e a data grande um pouco menor.
# Medido a 660px: foto 349 → 291px; faixa preta inteira 373 → 307px (−18%).
HERO_COMPACT_CSS = (".topbar{padding-top:14px!important;row-gap:10px!important}"
                    ".emb{width:54px!important;height:54px!important}"
                    ".lockup{padding-top:16px!important}"
                    ".kicker{margin-bottom:6px!important}"
                    "h1 .period{font-size:98px!important}"
                    "h1 .word{margin-top:5px!important}")
HERO_TAIL = 12               # preto da foto depois de "Daily News" (era 14)
BAND_TAIL = 14               # faixa preta depois da foto (era 22)

# ÍNDICE e "OUR RESEARCH" MAIS JUNTOS (pedido do usuário em 25/11: "um pouco mais próximo um do
# outro, para ser mais fácil de visualizar todos os headlines" — e o mesmo nos últimos 10
# relatórios). Medido: manchete 70 → 54px, relatório 117 → 94px.
IDX_GAP = 7                  # respiro em cima e embaixo de cada manchete do índice (era 13)
PUB_GAP = 8                  # idem em cada relatório de "Our research" (era 14)

SECTOR = {"NR": "Natural Resources", "SM": "Steel & Mining", "PP": "Pulp & Paper"}
SHORT = {"NR": "NR", "SM": "S&M", "PP": "P&P"}
ORDER = ["NR", "SM", "PP"]
MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
MINUS = "&minus;"

ARIAL = "Arial,Helvetica,sans-serif"
PLATFORM_URL = "https://www.itau.com.br/itaubba-pt/portal/auth/mind"

# As cores do reports-lib.js (tema escuro = o padrão do WhatsApp; claro = o "Light" dele).
# hair = o laranja translúcido já misturado com o fundo (o Outlook não entende rgba).
THEMES = {
    "light": dict(page="#FFFFFF", sheet="#FFFFFF", band="#000000", text="#1C1C1C", fg="#000000",
                  soft="#4A4A4A", mute="#666666", faint="#707070", line="#E6E6E6", line2="#BDBDBD",
                  hair="#FFAA7F", orange="#FF5500", otext="#C74200", pos="#138A4B", neg="#D92D2D",
                  badge_ink="#FFFFFF", note="#F3F3F3", link="#000000"),
    "dark": dict(page="#000000", sheet="#000000", band="#000000", text="#E3E3E3", fg="#FFFFFF",
                 soft="#BDBDBD", mute="#939393", faint="#7E7E7E", line="#262626", line2="#4D4D4D",
                 hair="#662200", orange="#FF5500", otext="#FF5500", pos="#2FBF71", neg="#FF5D5D",
                 badge_ink="#000000", note="#141414", link="#FFFFFF"),
}


def _e(s) -> str:
    return escape(str(s or ""), quote=False)


def _a(s) -> str:
    return escape(str(s or ""), quote=True)


def _txt(size, color, weight="normal", lh=None, extra="", cls="fb") -> str:
    """Estilo inline de texto. `mso-line-height-rule:exactly` faz o Outlook respeitar a entrelinha."""
    lh = lh or round(size * 1.55)
    # ⚠️ espaço entre parágrafos tem de ser MARGEM: o motor do Word/Outlook ignora padding em
    # <p> (medido: as matérias saíam com os parágrafos grudados).
    extra = extra.replace("padding", "margin")
    return (f'class="{cls}" style="margin:0;font-family:{ARIAL};font-size:{size}px;line-height:{lh}px;'
            f'mso-line-height-rule:exactly;color:{color};font-weight:{weight};{extra}"')


def _rule(color, top=0, bottom=0) -> str:
    """Linha horizontal de 1px que o Outlook desenha (célula com fundo, não <hr>)."""
    return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
            f'style="border-collapse:collapse"><tr><td style="padding:{top}px 0 {bottom}px 0">'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
            f'style="border-collapse:collapse"><tr><td height="1" bgcolor="{color}" '
            f'style="height:1px;line-height:1px;font-size:1px;background:{color}">&nbsp;</td></tr></table>'
            f'</td></tr></table>')


def _badge(take: str, T: dict) -> str:
    """O selo do take (+ verde, − vermelho, = contorno) — uma célula de 20×20."""
    if take == "+":
        bg, ink, sym, bd = T["pos"], T["badge_ink"], "+", T["pos"]
    elif take == "-":
        bg, ink, sym, bd = T["neg"], T["badge_ink"], MINUS, T["neg"]
    else:
        bg, ink, sym, bd = T["sheet"], T["soft"], "=", T["line2"]
    label = {"+": "positive", "-": "negative"}.get(take, "neutral")
    return (f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="border-collapse:separate">'
            f'<tr><td width="20" height="20" align="center" valign="middle" bgcolor="{bg}" title="Our take: {label}" '
            f'style="width:20px;height:20px;border:1px solid {bd};border-radius:6px;background:{bg};'
            f'font-family:{ARIAL};font-size:13px;line-height:18px;mso-line-height-rule:exactly;'
            f'font-weight:bold;color:{ink};text-align:center">{sym}</td></tr></table>')


def _story_anchor(it: dict) -> str:
    """Nome da marca de destino da matéria. Só letra + número: é o formato de marcador que o
    Word aceita (o Outlook importa o HTML como documento do Word ao abrir o rascunho)."""
    return f"story{int(it['i'])}"


def _long_date(d: date) -> str:
    return f"{['January','February','March','April','May','June','July','August','September','October','November','December'][d.month-1]} {d.day}, {d.year}"


# ── a mensagem de abertura (mesmo parser do Word: negrito/itálico/cor/link) ──────────

def _note_html(config: dict, T: dict) -> str:
    intro = (config or {}).get("intro") or {}
    if not intro_has_content(intro):
        return ""
    lines = []
    for ln in parse_intro_lines(intro):
        if not line_text(ln).strip():
            lines.append("&nbsp;")
            continue
        out = []
        for sg in ln:
            if not sg.text:
                continue
            t = _e(sg.text)
            if sg.bold:
                t = f"<b>{t}</b>"
            if sg.italic:
                t = f"<i>{t}</i>"
            col = (sg.color or "").upper()
            # preto/quase preto vira a cor do texto do tema (senão some no escuro)
            if col and sum(int(col[i:i + 2], 16) for i in (0, 2, 4)) < 120:
                col = ""
            if sg.url:
                c = f"#{col}" if col else T["otext"]
                t = f'<a href="{_a(sg.url)}" style="color:{c};text-decoration:underline">{t}</a>'
            elif col:
                t = f'<span style="color:#{col}">{t}</span>'
            elif sg.underline:
                t = f"<u>{t}</u>"
            out.append(t)
        lines.append("".join(out))
    body = "<br>".join(lines)
    return (f'<tr><td class="px" style="padding:22px {PAD}px 0 {PAD}px">'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse"><tr>'
            f'<td width="2" bgcolor="{T["orange"]}" style="width:2px;background:{T["orange"]};font-size:1px;line-height:1px">&nbsp;</td>'
            f'<td bgcolor="{T["note"]}" style="background:{T["note"]};padding:13px 18px">'
            f'<p {_txt(14, T["text"], lh=22)}>{body}</p></td></tr></table></td></tr>')


# ── cabeçalho de seção: "I" laranja + título grande ─────────────────────────────────

def _sec_head(anchor: str, num: str, title: str, T: dict, aside: str = "") -> str:
    aside_html = f'<p {_txt(13, T["mute"], lh=20, extra="padding-top:6px")}>{_e(aside)}</p>' if aside else ""
    return (f'<tr><td class="px" style="padding:44px {PAD}px 0 {PAD}px">'
            f'<a name="{anchor}" id="{anchor}"></a>'
            f'<p {_txt(14, T["otext"], lh=18, cls="fd")}>{num}</p>'
            f'<p {_txt(30, T["fg"], lh=36, extra="padding-top:8px;letter-spacing:-0.5px", cls="fd fdl")}>{_e(title)}</p>'
            f'{aside_html}{_rule(T["line2"], top=14)}</td></tr>')


# ── o corpo de uma matéria (blocos do MESMO parser do Word) ─────────────────────────

def _blocks_html(blocks: list[dict], T: dict, title: str = "") -> str:
    out = []
    # a raspagem às vezes traz o próprio título como 1º bloco (Mining.com) — não repetir
    norm = lambda s: re.sub(r"\W+", " ", str(s or "")).strip().lower()
    if blocks and title and norm(blocks[0].get("x")) == norm(title):
        blocks = blocks[1:]
    p_style = _txt(14.5, T["text"], lh=23, extra="padding:0 0 12px 0")
    for b in blocks or []:
        t = b.get("t")
        if t == "p":
            x = _e(b.get("x"))
            out.append(f'<p {p_style}>{"<b>" + x + "</b>" if b.get("b") else x}</p>')
        elif t in ("h3", "h4"):
            out.append(f'<p {_txt(15, T["fg"], "bold", 22, "padding:6px 0 8px 0")}>{_e(b.get("x"))}</p>')
        elif t == "q":
            out.append(f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
                       f'style="border-collapse:collapse;margin:0 0 12px 0"><tr>'
                       f'<td width="2" bgcolor="{T["hair"]}" style="width:2px;background:{T["hair"]};font-size:1px">&nbsp;</td>'
                       f'<td style="padding:0 0 0 14px"><p {_txt(14.5, T["soft"], lh=23, extra="font-style:italic")}>{_e(b.get("x"))}</p></td>'
                       f'</tr></table>')
        elif t == "li":
            out.append(f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
                       f'style="border-collapse:collapse"><tr>'
                       f'<td width="16" valign="top" style="width:16px;padding:0 0 6px 0"><p {_txt(14.5, T["orange"], lh=23)}>&bull;</p></td>'
                       f'<td valign="top" style="padding:0 0 6px 0"><p {_txt(14.5, T["text"], lh=23)}>{_e(b.get("x"))}</p></td>'
                       f'</tr></table>')
        elif t == "img" and str(b.get("src", "")).startswith("data:image/"):
            w, h = int(b.get("w") or 0), int(b.get("h") or 0)
            if w > IMG_MAX:
                h = round(h * IMG_MAX / w) if h else 0
                w = IMG_MAX
            size = (f' width="{w}"' + (f' height="{h}"' if h else "")) if w else f' width="{IMG_MAX}"'
            out.append(f'<p style="margin:4px 0 16px 0"><img class="fluid" src="{b["src"]}"{size} alt="" '
                       f'style="display:block;border:0;outline:none;max-width:100%;height:auto"></p>')
        elif t == "table" and b.get("rows"):
            rows = []
            for r in b["rows"]:
                cells = []
                for c in r or []:
                    tag = "th" if c.get("header") else "td"
                    span = f' colspan="{int(c["colspan"])}"' if int(c.get("colspan") or 1) > 1 else ""
                    al = c.get("align") if c.get("align") in ("right", "center") else "left"
                    wt = "bold" if c.get("header") else "normal"
                    cells.append(f'<{tag}{span} align="{al}" style="padding:5px 8px;border-bottom:1px solid {T["line"]};'
                                 f'font-family:{ARIAL};font-size:12px;line-height:16px;color:{T["text"]};'
                                 f'font-weight:{wt};text-align:{al}" class="fb">{_e(c.get("text"))}</{tag}>')
                rows.append("<tr>" + "".join(cells) + "</tr>")
            out.append(f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
                       f'style="border-collapse:collapse;margin:2px 0 16px 0;border-top:1px solid {T["line2"]}">'
                       + "".join(rows) + "</table>")
    return "".join(out)


# ── o documento ─────────────────────────────────────────────────────────────────────

def build_brand_html(model: dict, d: date, hero_png: bytes | None, disclaimer: dict | None,
                     theme: str = "light", config: dict | None = None) -> str:
    T = THEMES[theme]
    items = model.get("items") or []
    secs = [s for s in ORDER if any(it["sector"] == s for it in items)]
    ordered = [it for s in secs for it in items if it["sector"] == s]
    cnt = {"+": 0, "=": 0, "-": 0}
    for it in ordered:
        cnt[it["take"] if it["take"] in ("+", "-") else "="] += 1
    n = len(ordered)

    # (a frase "11 headlines this morning — …" do topo SAIU a pedido do usuário em 24/11)
    rows: list[str] = []

    # ── TOPO (faixa preta nos dois temas): a imagem + a frase
    alt = f"Steel & Mining / Pulp & Paper · Itaú BBA Equity Research — {MON[d.month-1]} {d.day} · Daily News"
    if hero_png:
        # altura em ATRIBUTO também: sem ela o motor do Word achatava a imagem
        from .eml import _px_size
        pw, ph = _px_size(hero_png) or (COL * 2, COL)
        hh = round(ph * COL / pw)
        hero = (f'<img class="hero-img" src="data:image/png;base64,{base64.b64encode(hero_png).decode("ascii")}" '
                f'width="{COL}" height="{hh}" alt="{_a(alt)}" '
                f'style="display:block;border:0;outline:none;width:{COL}px;height:{hh}px">')
    else:   # sem a imagem: o mesmo topo em texto (o que o Outlook consegue), tão baixo quanto a foto
        hero = (f'<p {_txt(13, "#FFFFFF", "bold", 18, f"padding:16px {PAD}px 0 {PAD}px", cls="fd")}>STEEL &amp; MINING / PULP &amp; PAPER</p>'
                f'<p {_txt(15, "#FF5500", lh=22, extra=f"padding:24px {PAD}px 0 {PAD}px")}>LatAm S&amp;M and P&amp;P</p>'
                f'<p {_txt(56, "#FF5500", lh=62, extra=f"padding:0 {PAD}px", cls="fd")}>{MON[d.month-1]} {d.day}</p>'
                f'<p {_txt(36, "#FFFFFF", "bold", 42, f"padding:2px {PAD}px 0 {PAD}px", cls="fd")}>Daily News</p>')
    # a faixa preta vai até a borda da janela; a foto fica à esquerda (o texto dela começa nos
    # mesmos 24px do corpo) e o preto da foto emenda com o da faixa
    rows.append(f'<tr><td align="left" bgcolor="{T["band"]}" style="background:{T["band"]};padding:0;text-align:left">{hero}</td></tr>')
    rows.append(f'<tr><td height="{BAND_TAIL}" bgcolor="{T["band"]}" style="height:{BAND_TAIL}px;'
                f'line-height:{BAND_TAIL}px;font-size:1px;background:{T["band"]}">&nbsp;</td></tr>')
    if theme == "light":   # a faixa preta termina num fio laranja
        rows.append(f'<tr><td height="2" bgcolor="{T["orange"]}" style="height:2px;line-height:2px;font-size:2px;background:{T["orange"]}">&nbsp;</td></tr>')

    rows.append(_note_html(config or {}, T))

    # ── I. Highlights — os 3 destaques do blast, EM INGLÊS (o blast do WhatsApp é em português).
    # Sem a versão em inglês a seção não sai: o e-mail é todo em inglês (pedido do usuário, 24/11).
    num = iter(["I", "II", "III", "IV", "V"])
    hl = [x for x in (model.get("highlights_en") or []) if str(x).strip()]
    if hl:
        rows.append(_sec_head("hl", next(num), "Highlights", T))
        lis = []
        for i, x in enumerate(hl):
            lis.append(f'<tr><td width="22" valign="top" style="width:22px;padding:14px 0 14px 0">'
                       f'<p {_txt(15, T["orange"], "bold", 24)}>&mdash;</p></td>'
                       f'<td valign="top" style="padding:14px 0 14px 0;{"" if i == len(hl)-1 else "border-bottom:1px solid " + T["line"]}">'
                       # data-hl: a prévia da aba Clipping troca este texto AO VIVO quando o analista edita
                       f'<p data-hl="{i}" {_txt(15, T["text"], lh=24)}>{_e(x)}</p></td></tr>')
        rows.append(f'<tr><td class="px" style="padding:4px {PAD}px 0 {PAD}px">'
                    f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
                    f'style="border-collapse:collapse">{"".join(lis)}</table></td></tr>')

    # ── II. Sector headlines: o placar dos takes + a lista
    rows.append(_sec_head("heads", next(num), "Sector headlines", T))
    bar = []
    for t, col in (("+", T["pos"]), ("=", T["line2"]), ("-", T["neg"])):
        if cnt[t]:
            if bar:
                bar.append(f'<td width="3" style="width:3px;font-size:1px;line-height:1px">&nbsp;</td>')
            bar.append(f'<td width="{round(100*cnt[t]/max(n,1))}%" height="6" bgcolor="{col}" '
                       f'style="height:6px;line-height:6px;font-size:1px;background:{col}">&nbsp;</td>')
    legend = " &nbsp;&nbsp; ".join(
        f'<span style="color:{c}">&#9632;</span> {cnt[t]} {lbl}'
        for t, c, lbl in (("+", T["pos"], "positive"), ("=", T["line2"], "neutral"), ("-", T["neg"], "negative")))
    rows.append(
        f'<tr><td class="px" style="padding:18px {PAD}px 4px {PAD}px">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse"><tr>'
        f'<td width="118" valign="middle" style="width:118px"><p {_txt(13, T["mute"], lh=24)}>'
        f'<span class="fd" style="font-size:24px;color:{T["fg"]};font-family:{ARIAL}">{n}</span>&nbsp; headline{"s" if n != 1 else ""}</p></td>'
        f'<td valign="middle" style="padding:0"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="border-collapse:collapse"><tr>{"".join(bar)}</tr></table></td>'
        f'</tr></table>'
        # legenda numa linha própria: lado a lado, no celular a barra sumia espremida
        f'<p {_txt(12.5, T["mute"], lh=18, extra="padding-top:8px")}>{legend}</p></td></tr>')
    for s in secs:
        rows.append(f'<tr><td class="px" style="padding:16px {PAD}px 2px {PAD}px">'
                    f'<p {_txt(11.5, T["otext"], "bold", 16, "letter-spacing:1.2px")}>{_e(SECTOR[s].upper())}</p></td></tr>')
        lis = []
        for it in [x for x in ordered if x["sector"] == s]:
            head = it["tr_title"] or it["title"]
            sub = f'<p {_txt(12.5, T["mute"], lh=17, extra="padding-top:1px")}>{_e(it["title"])}</p>' if it["tr_title"] else ""
            # ÍNDICE: o título leva à matéria completa MAIS ABAIXO no próprio e-mail (o link do
            # site da fonte fica no fim de cada matéria, "Open at …")
            go = _story_anchor(it)
            title = f'<a href="#{go}" style="color:{T["fg"]};text-decoration:none">{_e(head)}</a>'
            lis.append(f'<tr><td width="20" valign="top" style="width:20px;padding:{IDX_GAP}px 0 {IDX_GAP}px 0">{_badge(it["take"], T)}</td>'
                       f'<td valign="top" style="padding:{IDX_GAP}px 0 {IDX_GAP}px 12px;border-bottom:1px solid {T["line"]}">'
                       f'<p {_txt(15, T["fg"], "bold", 20)}>{title}</p>{sub}'
                       f'<p {_txt(12.5, T["mute"], lh=17, extra="padding-top:2px")}>{_e(it["source"])}'
                       f'{"&nbsp;&nbsp;&middot;&nbsp;&nbsp;" if it["source"] else ""}'
                       f'<a href="#{go}" style="color:{T["otext"]};text-decoration:underline">Full story&nbsp;&darr;</a></p></td></tr>')
        rows.append(f'<tr><td class="px" style="padding:0 {PAD}px">'
                    f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
                    f'style="border-collapse:collapse">{"".join(lis)}</table></td></tr>')

    # ── III. Our research (Recent Publications + Earnings Review)
    def pub_rows(lst):
        out = []
        for p in lst or []:
            name = (p.get("name") or "").strip()
            if not name:
                continue
            link = (p.get("link") or "").strip()
            tick = " · ".join(c for c in (p.get("companies") or []) if c)
            dt = ""
            if p.get("date"):
                y, m, dd = p["date"][:10].split("-")
                dt = f"{MON[int(m)-1]} {int(dd)}"
            pill = (f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="border-collapse:separate"><tr>'
                    f'<td style="border:1px solid {T["hair"]};border-radius:10px;padding:1px 8px;font-family:{ARIAL};'
                    f'font-size:10.5px;line-height:14px;mso-line-height-rule:exactly;font-weight:bold;letter-spacing:0.5px;'
                    f'color:{T["otext"]}" class="fb">{_e(SHORT.get(p.get("sector"), "S&M"))}</td></tr></table>')
            nm = (f'<a href="{_a(link)}" style="color:{T["fg"]};text-decoration:none">{_e(name)}</a>'
                  if link.startswith("http") else _e(name))
            meta = [x for x in (dt,) if x]
            open_ = (f' &nbsp;&middot;&nbsp; <a href="{_a(link)}" style="color:{T["otext"]};text-decoration:underline">Open report&nbsp;&rsaquo;</a>'
                     if link.startswith("http") else "")
            out.append(f'<tr><td style="padding:{PUB_GAP}px 0 {PUB_GAP}px 0;border-bottom:1px solid {T["line"]}">{pill}'
                       f'<p {_txt(15, T["fg"], "bold", 20, "padding-top:4px")}>{nm}</p>'
                       + (f'<p {_txt(12, T["mute"], lh=16, extra="padding-top:1px")}>{_e(tick)}</p>' if tick else "")
                       + f'<p {_txt(12, T["mute"], lh=16, extra="padding-top:2px")}>{" ".join(meta)}{open_}</p></td></tr>')
        return out

    pubs = pub_rows(model.get("publications"))
    er = model.get("earnings")
    er_rows = pub_rows(er.get("items")) if er else []
    if pubs or er_rows:
        rows.append(_sec_head("research", next(num), "Our research", T, aside="Itaú BBA reports on the sector"))
        if pubs:
            rows.append(f'<tr><td class="px" style="padding:0 {PAD}px"><table role="presentation" width="100%" '
                        f'cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse">{"".join(pubs)}</table></td></tr>')
        if er_rows:
            rows.append(f'<tr><td class="px" style="padding:26px {PAD}px 0 {PAD}px"><p {_txt(20, T["fg"], lh=26, cls="fd fdl")}>'
                        f'{_e(er.get("label") or "Earnings Review")}</p></td></tr>')
            rows.append(f'<tr><td class="px" style="padding:0 {PAD}px"><table role="presentation" width="100%" '
                        f'cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse">{"".join(er_rows)}</table></td></tr>')

    # ── IV. Full stories
    rows.append(_sec_head("stories", next(num), "Full stories", T))
    for si, s in enumerate(secs):
        lst = [x for x in ordered if x["sector"] == s]
        rows.append(f'<tr><td class="px" style="padding:{18 if si == 0 else 40}px {PAD}px 0 {PAD}px">'
                    f'<p {_txt(22, T["fg"], lh=28, cls="fd fdl")}>{_e(SECTOR[s])}'
                    f'<span class="fb" style="font-family:{ARIAL};font-size:13px;color:{T["mute"]}">&nbsp;&nbsp;{len(lst)} stor{"y" if len(lst) == 1 else "ies"}</span></p>'
                    f'{_rule(T["line"], top=12)}</td></tr>')
        for k, it in enumerate(lst):
            meta = (f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse"><tr>'
                    f'<td valign="middle">{_badge(it["take"], T)}</td>'
                    f'<td valign="middle" style="padding:0 0 0 10px"><p {_txt(12.5, T["otext"], "bold", 18)}>{_e(SECTOR[s])}'
                    f'<span style="color:{T["mute"]};font-weight:normal">&nbsp;&nbsp; {_e(it["source"])}</span></p></td></tr></table>')
            head = it["tr_title"] or it["title"]
            orig = (f'<p {_txt(12.5, T["mute"], lh=18, extra="padding-top:4px")}>Original: {_e(it["title"])}</p>'
                    if it["tr_title"] else "")
            if it["tr_title"] and it.get("tr_blocks"):
                body = (f'<p {_txt(11, T["mute"], "bold", 16, "letter-spacing:1px;padding:0 0 10px 0")}>ENGLISH &middot; FREE TRANSLATION</p>'
                        + _blocks_html(it["tr_blocks"], T, it["tr_title"])
                        + _rule(T["line"], top=4, bottom=14)
                        + f'<p {_txt(11, T["mute"], "bold", 16, "letter-spacing:1px;padding:0 0 10px 0")}>ORIGINAL</p>'
                        + f'<p {_txt(15, T["fg"], "bold", 22, "padding:0 0 10px 0")}>{_e(it["title"])}</p>'
                        + _blocks_html(it["blocks"], T, it["title"]))
            else:
                body = _blocks_html(it["blocks"], T, it["title"]) or f'<p {_txt(14, T["mute"], lh=22)}>Full text not available — see the source.</p>'
            link = it.get("url") or ""
            back = (f'<a href="#heads" style="color:{T["otext"]};text-decoration:underline">'
                    f'Back to headlines&nbsp;&uarr;</a>')
            src = (f'<p {_txt(12.5, T["mute"], lh=18, extra="padding-top:2px")}>'
                   + (f'<a href="{_a(link)}" style="color:{T["mute"]};text-decoration:underline">'
                      f'Open at {_e(it["source"] or "source")}&nbsp;&rsaquo;</a>&nbsp;&nbsp;&middot;&nbsp;&nbsp;'
                      if link.startswith("http") else "")
                   + f'{back}</p>')
            last = k == len(lst) - 1
            go = _story_anchor(it)
            rows.append(f'<tr><td class="px" style="padding:24px {PAD}px 0 {PAD}px">'
                        f'<a name="{go}" id="{go}"></a>{meta}'
                        f'<p {_txt(19, T["fg"], "bold", 26, "padding-top:10px")}>{_e(head)}</p>{orig}'
                        f'<div style="padding-top:12px">{body}</div>{src}'
                        f'{"" if last else _rule(T["line"], top=24)}</td></tr>')

    # ── rodapé: o time + plataforma + avisos
    rows.append(f'<tr><td class="px" style="padding:48px {PAD}px 0 {PAD}px">{_rule(T["orange"])}'
                f'<p {_txt(24, T["fg"], lh=30, extra="padding-top:30px", cls="fd fdl")}>Steel &amp; Mining &middot; Pulp &amp; Paper team</p>'
                f'{_rule(T["line2"], top=16)}</td></tr>')
    ppl = []
    team = model.get("analysts") or []
    for i, p in enumerate(team):
        nm, em, ph, rl = p.get("name"), p.get("email"), p.get("phone"), p.get("role")
        ppl.append(f'<tr><td style="padding:14px 0 14px 0;{"" if i == len(team)-1 else "border-bottom:1px solid " + T["hair"]}">'
                   f'<p {_txt(15, T["fg"], "bold", 21)}>{_e(nm)}</p>'
                   + (f'<p {_txt(13, T["mute"], lh=19)}>{_e(rl)}</p>' if rl else "")
                   + (f'<p {_txt(13, T["text"], lh=19, extra="padding-top:2px")}><a href="mailto:{_a(em)}" '
                      f'style="color:{T["text"]};text-decoration:none">{_e(em)}</a></p>' if em else "")
                   + (f'<p {_txt(13, T["mute"], lh=19)}>{_e(ph)}</p>' if ph else "")
                   + '</td></tr>')
    rows.append(f'<tr><td class="px" style="padding:0 {PAD}px"><table role="presentation" width="100%" cellpadding="0" '
                f'cellspacing="0" border="0" style="border-collapse:collapse">{"".join(ppl)}</table></td></tr>')
    sources = []
    for it in ordered:
        if it["source"] and it["source"] not in sources:
            sources.append(it["source"])
    fine = ("News: " + (", ".join(sources) or "third-party sources") + ". Summaries and free translations of third-party "
            "content for our clients; the original articles prevail. Our take (+ positive, = neutral, − negative) is "
            "Itaú BBA’s view. Itaú BBA does and seeks to do business with companies covered in its research reports. "
            "As a result, investors should be aware that the firm may have a conflict of interest that could affect the "
            "objectivity of this report. Investors should not consider this report as the sole factor in making their "
            "investment decision. Full disclosures below.")
    rows.append(f'<tr><td class="px" style="padding:22px {PAD}px 0 {PAD}px">'
                f'<p {_txt(14, T["fg"], lh=20)}><a href="{PLATFORM_URL}" style="color:{T["fg"]};text-decoration:underline">'
                f'Access all reports on Itaú BBA Research Platform</a></p>'
                f'<p {_txt(11.5, T["faint"], lh=18, extra="padding-top:18px")}>{_e(fine)}</p></td></tr>')
    if disclaimer:
        dh = [f'<p {_txt(12, T["soft"], "bold", 18, "padding-top:20px")}>Disclaimer and analyst disclosures</p>']
        for k, para in enumerate(disclaimer.get("paras") or []):
            dh.append(f'<p {_txt(10, T["faint"], lh=15, extra="padding-top:6px")}>{k+1}. {_e(para)}</p>')
        if disclaimer.get("note"):
            dh.append(f'<p {_txt(10, T["faint"], lh=15, extra="padding-top:8px")}>{_e(disclaimer["note"])}</p>')
        if disclaimer.get("analysts"):
            dh.append(f'<p {_txt(10.5, T["soft"], "bold", 16, "padding-top:12px")}>Relevant Information – Analysts</p>')
            head = "".join(f'<td align="center" style="padding:3px 6px;border-bottom:1px solid {T["line2"]};font-family:{ARIAL};'
                           f'font-size:10px;color:{T["soft"]};font-weight:bold" class="fb">{h}</td>'
                           for h in ["Analysts", "1", "2", "3", "4", "5"])
            body = "".join("<tr>" + "".join(
                f'<td{" " if j == 0 else " align=center "}style="padding:3px 6px;border-bottom:1px solid {T["line"]};'
                f'font-family:{ARIAL};font-size:10px;color:{T["faint"]}" class="fb">{_e(c)}</td>'
                for j, c in enumerate(r)) + "</tr>" for r in disclaimer["analysts"])
            dh.append(f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;margin-top:6px">'
                      f'<tr>{head}</tr>{body}</table>')
        for k, para in enumerate(disclaimer.get("items") or []):
            dh.append(f'<p {_txt(10, T["faint"], lh=15, extra="padding-top:6px")}>{k+1}. {_e(para)}</p>')
        rows.append(f'<tr><td class="px" style="padding:0 {PAD}px">{"".join(dh)}</td></tr>')
    rows.append(f'<tr><td class="px" style="padding:30px {PAD}px 40px {PAD}px">'
                f'<p {_txt(13, T["mute"], lh=20)}><b class="fd" style="font-family:{ARIAL};font-size:15px;color:{T["fg"]}">Itaú BBA</b>'
                f'&nbsp;&nbsp; Equity Research &middot; Steel &amp; Mining / Pulp &amp; Paper</p></td></tr>')

    title = f"LatAm S&M and P&P — {MON[d.month-1]} {d.day} Daily News | Itaú BBA"
    fonts = (model.get("_fonts") or {})
    font_css = ""
    if fonts.get("display") and fonts.get("body"):
        font_css = ("@font-face{font-family:'Unbounded';font-style:normal;font-weight:200 900;"
                    f"src:url(data:font/woff2;base64,{fonts['display']}) format('woff2')}}"
                    "@font-face{font-family:'Manrope';font-style:normal;font-weight:200 800;"
                    f"src:url(data:font/woff2;base64,{fonts['body']}) format('woff2')}}")
    scheme = "light only" if theme == "light" else "dark only"
    # O 2º <style> (fontes da marca + celular) fica escondido do Outlook: ele não usa e,
    # vendo uma fonte que não tem, cairia em Times New Roman.
    head = ('<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<meta name="color-scheme" content="{scheme}"><meta name="supported-color-schemes" content="{scheme}">'
            f'<title>{_e(title)}</title>'
            # @page no formato do Word (margem 0,6in = 7,07in úteis ≥ 660px) — vale ao imprimir/encaminhar
            '<style>@page WordSection1{size:8.27in 11.69in;margin:0.6in 0.6in 0.6in 0.6in;}'
            'div.WordSection1{page:WordSection1;}</style>'
            '<!--[if mso]><style>table,td,p,a,span,b{font-family:Arial,Helvetica,sans-serif!important}</style><![endif]-->'
            '<!--[if !mso]><!--><style>' + font_css +
            '.fb{font-family:Manrope,Arial,Helvetica,sans-serif!important}'
            '.fd{font-family:Unbounded,Manrope,Arial,Helvetica,sans-serif!important}'
            '.fdl{font-weight:300!important}'
            # a Manrope troca "(c)" por "©" — desliga a troca
            '*{font-feature-settings:"calt" 0!important;font-variant-ligatures:none!important}'
            f'@media (max-width:{COL + 20}px){{.col{{width:100%!important}}'
            '.px{padding-left:16px!important;padding-right:16px!important}'
            '.hero-img{width:100%!important;height:auto!important}'
            'img.fluid{width:100%!important;max-width:100%!important;height:auto!important}}'
            '</style><!--<![endif]--></head>')
    return ('<!doctype html><html lang="en">' + head +
            f'<body style="margin:0;padding:0;background:{T["page"]};-webkit-text-size-adjust:100%">'
            '<div class="WordSection1">'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="{T["page"]}" '
            f'style="border-collapse:collapse;background:{T["page"]}"><tr><td align="center" style="padding:0">'
            # coluna FLUIDA: ocupa a janela do e-mail inteira (nada de max-width aqui — ver o topo)
            f'<table role="presentation" class="col" width="100%" cellpadding="0" cellspacing="0" border="0" '
            f'bgcolor="{T["sheet"]}" style="width:100%;border-collapse:collapse;background:{T["sheet"]}">'
            + "".join(r for r in rows if r) +
            '</table></td></tr></table></div></body></html>')


# ── topo + avisos tirados do próprio HTML do WhatsApp (o mesmo desenho, sem redesenhar) ──

def hero_and_disclaimer(wa_html: str, width: int = COL, pad: int = PAD) -> tuple[bytes | None, dict | None]:
    """Abre o HTML do WhatsApp num navegador sem tela (o Playwright que o job já tem), fotografa
    o topo (emblema + data + "Nov 24 / Daily News") a 2× e lê o texto dos avisos legais."""
    import tempfile
    from pathlib import Path
    from playwright.sync_api import sync_playwright
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "wa.html"
        f.write_text(wa_html, encoding="utf-8")
        with sync_playwright() as p:
            b = p.chromium.launch()
            ctx = b.new_context(viewport={"width": width, "height": 900}, device_scale_factor=2,
                                reduced_motion="reduce")
            pg = ctx.new_page()
            pg.goto(f.as_uri() + "?shot=1")
            # sem os arcos decorativos: cortados na borda da foto, viravam um risco solto quando a
            # faixa preta do e-mail continua à direita (coluna fluida) — sem eles o preto emenda
            # + HERO_COMPACT_CSS: o topo do e-mail é mais baixo que o do HTML (pedido de 25/11)
            pg.add_style_tag(content=f":root{{--pad:{pad}px!important}}.theme-sw{{display:none!important}}"
                                     ".hero .shapes{display:none!important}" + HERO_COMPACT_CSS)
            pg.evaluate("document.fonts.ready")
            pg.wait_for_timeout(600)
            bottom = pg.evaluate("(() => { const w = document.querySelector('h1 .word');"
                                 " const r = w.getBoundingClientRect(); return Math.ceil(r.bottom + window.scrollY); })()")
            png = pg.screenshot(clip={"x": 0, "y": 0, "width": width, "height": bottom + HERO_TAIL})
            disc = pg.evaluate("""(() => {
              const d = document.querySelector('details.disc'); if (!d) return null;
              const t = e => (e ? e.textContent : '').replace(/\\s+/g, ' ').trim();
              const ols = d.querySelectorAll('ol');
              return {
                paras: ols[0] ? [...ols[0].querySelectorAll('li')].map(t) : [],
                note: t(d.querySelector(':scope > p')),
                analysts: [...d.querySelectorAll('table.dtab tbody tr')].map(r => [...r.children].map(t)),
                items: ols[1] ? [...ols[1].querySelectorAll('li')].map(t) : []
              };
            })()""")
            b.close()
    return png, disc


def build_brand_eml(items: list[ClippingItem], html: str, d: date) -> bytes:
    """Mesmo envelope do e-mail antigo (assunto, X-Unsent, imagens cid:, CRLF) — SEM o Word
    anexado (pedido do usuário em 24/11: o Word continua no botão de download da aba)."""
    msg = EmailMessage()
    msg["Subject"] = (f"*** ITAÚ BBA Daily News: LatAm Steel & Mining, Pulp & Paper "
                      f"- {_banner_date(d)} ***")
    msg["X-Unsent"] = "1"
    msg.set_content(build_plain_text(items, d), charset="utf-8")
    html_email, images = _inline_images(html)
    msg.add_alternative(html_email, subtype="html")
    if images:
        part = msg.get_payload()[-1]
        for cid, data, subtype in images:
            part.add_related(data, maintype="image", subtype=subtype, cid=f"<{cid}>")
    return msg.as_bytes(policy=policy.SMTP)


def build_brand_email(items: list[ClippingItem], d: date, model: dict | None, wa_html: bytes | str | None,
                      fonts: dict | None, config: dict | None, highlights_en: list | None) -> dict:
    """O e-mail novo pronto: {"eml": bytes|None, "html": str|None, "error": str|None}.

    Nunca levanta exceção — quem chama cai no e-mail antigo (cópia do Word) se vier `eml=None`.
    Versão CLARA (escolha do usuário em 24/11/2026)."""
    try:
        if not model:
            raise RuntimeError("sem o modelo do HTML do WhatsApp")
        m = dict(model)
        m["highlights_en"] = [x for x in (highlights_en or []) if str(x).strip()]
        if fonts:
            m["_fonts"] = fonts
        hero = disc = None
        if wa_html:
            try:
                hero, disc = hero_and_disclaimer(wa_html.decode("utf-8") if isinstance(wa_html, bytes) else wa_html)
            except Exception as e:                      # sem a foto do topo: topo em texto
                log.warning("e-mail novo: topo em texto (foto falhou: %s)", e)
        html = build_brand_html(m, d, hero, disc, theme="light", config=config)
        eml = build_brand_eml(items, html, d)
        return {"eml": eml, "html": html, "error": None}
    except Exception as e:
        log.warning("e-mail novo NÃO gerado (%s) — vai o e-mail antigo", e)
        return {"eml": None, "html": None, "error": str(e)[:300]}
