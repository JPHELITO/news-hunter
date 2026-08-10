"""Mensagem de abertura do clipping — parser rico (negrito/itálico/sublinhado/cor/link).

O editor da dashboard grava HTML em config['intro']['html']; mensagens antigas guardaram
`**negrito**`/`[texto](url)` em ['text']. O MESMO parser (`build.parse_intro_lines`) serve o
Word e o e-mail — estes testes travam esse contrato nos dois lados.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from clipping.build import (                                    # noqa: E402
    ClippingItem, intro_has_content, line_text, parse_intro_lines,
)
from clipping.eml import _segs_html, build_html                 # noqa: E402


def _txt(lines):
    return [line_text(ln) for ln in lines]


def _flat(lines):
    return [sg for ln in lines for sg in ln]


class TestLegacyMarkdown:
    """Mensagens já salvas (formato antigo) NÃO podem quebrar."""

    def test_bold_and_link(self):
        intro = {"on": True, "text": "Dear **clients**,\n\nAccess [our dashboard](https://x.com/a)"}
        lines = parse_intro_lines(intro)
        assert _txt(lines) == ["Dear clients,", "", "Access our dashboard"]
        segs = _flat(lines)
        assert [s.text for s in segs if s.bold] == ["clients"]
        link = [s for s in segs if s.url]
        assert len(link) == 1 and link[0].url == "https://x.com/a" and link[0].text == "our dashboard"

    def test_html_vazio_cai_no_texto(self):
        intro = {"on": True, "html": "   ", "text": "oi **mundo**"}
        assert _txt(parse_intro_lines(intro)) == ["oi mundo"]


class TestHtmlEditor:
    def test_bold_italic_underline(self):
        lines = parse_intro_lines({"on": True, "html": "<b>a</b><i>b</i><u>c</u><strong>d</strong><em>e</em>"})
        segs = _flat(lines)
        assert [(s.text, s.bold, s.italic, s.underline) for s in segs] == [
            ("a", True, False, False), ("b", False, True, False), ("c", False, False, True),
            ("d", True, False, False), ("e", False, True, False),
        ]

    def test_nested_formatting(self):
        segs = _flat(parse_intro_lines({"on": True, "html": "<b><i><u>x</u></i></b>"}))
        assert len(segs) == 1
        assert segs[0].bold and segs[0].italic and segs[0].underline

    def test_bold_fecha_e_nao_vaza(self):
        segs = _flat(parse_intro_lines({"on": True, "html": "<b>bold</b> normal"}))
        assert [(s.text, s.bold) for s in segs] == [("bold", True), (" normal", False)]

    def test_cor_font_e_span(self):
        segs = _flat(parse_intro_lines(
            {"on": True, "html": '<font color="#ff0000">a</font><span style="color: rgb(255, 80, 0)">b</span>'
                                 '<font color="red">c</font><span style="color:#00b050">d</span>'}))
        assert [s.color for s in segs] == ["FF0000", "FF5000", "FF0000", "00B050"]

    def test_font_com_style_tambem_pinta(self):
        segs = _flat(parse_intro_lines({"on": True, "html": '<font style="color:#00b050">x</font>'}))
        assert segs[0].color == "00B050"

    def test_cor_em_qualquer_tag(self):
        """O execCommand pinta o elemento que já envolve a seleção — não só <span>."""
        segs = _flat(parse_intro_lines(
            {"on": True, "html": '<i style="color:#ff5000">a</i>'
                                 '<b><u style="color:#0000ff">b</u></b>'
                                 '<div style="color:#00b050">c</div>'}))
        assert [(s.text, s.color, s.italic, s.bold, s.underline) for s in segs] == [
            ("a", "FF5000", True, False, False),
            ("b", "0000FF", False, True, True),
            ("c", "00B050", False, False, False),
        ]

    def test_link_com_cor_propria(self):
        segs = _flat(parse_intro_lines(
            {"on": True, "html": '<a href="https://x.com" style="color:#ff5000">x</a>'}))
        assert segs[0].url == "https://x.com" and segs[0].color == "FF5000"

    def test_tag_sem_fechamento_nao_vaza(self):
        segs = _flat(parse_intro_lines({"on": True, "html": '<div>a<img src="x.png">b</div>'}))
        assert [s.text for s in segs] == ["a", "b"]

    def test_background_color_nao_pinta_a_letra(self):
        segs = _flat(parse_intro_lines({"on": True, "html": '<span style="background-color:#ff0000">x</span>'}))
        assert segs[0].color == ""

    def test_cor_hex_3_digitos(self):
        segs = _flat(parse_intro_lines({"on": True, "html": '<font color="#f00">x</font>'}))
        assert segs[0].color == "FF0000"

    def test_link_com_formatacao(self):
        segs = _flat(parse_intro_lines(
            {"on": True, "html": '<b><a href="https://a.com/x">clique</a></b>'}))
        assert segs[0].url == "https://a.com/x" and segs[0].bold

    def test_span_sem_cor_nao_pinta(self):
        segs = _flat(parse_intro_lines({"on": True, "html": '<span class="foo">x</span>'}))
        assert segs[0].color == "" and segs[0].text == "x"

    def test_tag_desconhecida_preserva_texto(self):
        assert _txt(parse_intro_lines({"on": True, "html": "<mark><big>oi</big></mark>"})) == ["oi"]

    def test_entidades_decodificadas(self):
        assert _txt(parse_intro_lines({"on": True, "html": "a &amp; b &nbsp;c"})) == ["a & b \xa0c"]


class TestLinhas:
    def test_divs_viram_linhas(self):
        assert _txt(parse_intro_lines({"on": True, "html": "<div>a</div><div>b</div>"})) == ["a", "b"]

    def test_div_vazia_vira_linha_em_branco(self):
        assert _txt(parse_intro_lines(
            {"on": True, "html": "<div>a</div><div><br></div><div>b</div>"})) == ["a", "", "b"]

    def test_br_duplo_dá_uma_linha_em_branco(self):
        assert _txt(parse_intro_lines({"on": True, "html": "a<br><br>b"})) == ["a", "", "b"]

    def test_br_simples_quebra(self):
        assert _txt(parse_intro_lines({"on": True, "html": "a<br>b"})) == ["a", "b"]

    def test_primeira_div_nao_gera_branco_inicial(self):
        assert _txt(parse_intro_lines({"on": True, "html": "<div>a</div>"})) == ["a"]

    def test_p_tambem_quebra(self):
        assert _txt(parse_intro_lines({"on": True, "html": "<p>a</p><p>b</p>"})) == ["a", "b"]

    def test_newline_do_html_nao_quebra_linha(self):
        assert _txt(parse_intro_lines({"on": True, "html": "<div>a\n  b</div>"})) == ["a b"]


class TestSeguranca:
    def test_javascript_url_descartada(self):
        segs = _flat(parse_intro_lines({"on": True, "html": '<a href="javascript:alert(1)">x</a>'}))
        assert segs[0].url == "" and segs[0].text == "x"

    def test_script_removido(self):
        assert _txt(parse_intro_lines(
            {"on": True, "html": "<div>oi<script>alert(1)</script></div>"})) == ["oi"]

    def test_mailto_permitido(self):
        segs = _flat(parse_intro_lines({"on": True, "html": '<a href="mailto:a@b.com">a</a>'}))
        assert segs[0].url == "mailto:a@b.com"


class TestIntroHasContent:
    def test_desligada(self):
        assert not intro_has_content({"on": False, "html": "<b>x</b>"})

    def test_html_so_com_espaco(self):
        assert not intro_has_content({"on": True, "html": "<div><br></div>"})

    def test_ligada_com_texto(self):
        assert intro_has_content({"on": True, "html": "<div>oi</div>"})

    def test_texto_legado(self):
        assert intro_has_content({"on": True, "text": "oi"})


class TestEmailHtml:
    def test_segs_html(self):
        lines = parse_intro_lines({"on": True, "html":
                                   '<b>a</b><i>b</i><u>c</u><font color="#ff0000">d</font>'
                                   '<a href="https://x.com">e</a>'})
        out = _segs_html(lines[0])
        assert "<b>a</b>" in out and "<i>b</i>" in out and "<u>c</u>" in out
        assert '<span style="color:#FF0000">d</span>' in out
        assert '<a href="https://x.com" style="color:#0000FF">e</a>' in out

    def test_segs_html_escapa(self):
        lines = parse_intro_lines({"on": True, "html": "<b>a &lt;b&gt; c</b>"})
        assert _segs_html(lines[0]) == "<b>a &lt;b&gt; c</b>"

    def _item(self):
        return ClippingItem(url="https://n.com/1", title="T", source_name="S", body="<p>x</p>",
                            matched_keywords=[], domain="n.com", take="=", sector="SM")

    def test_build_html_com_intro_rica(self):
        cfg = {"intro": {"on": True, "html":
                         '<div>Dear <b>clients</b>,</div><div><br></div>'
                         '<div><span style="color:#FF5000">Veja</span> o '
                         '<a href="https://d.com">dashboard</a>.</div>'}}
        html = build_html([self._item()], date(2026, 8, 10), cfg)
        assert "<b>clients</b>" in html
        assert 'color:#FF5000' in html
        assert '<a href="https://d.com" style="color:#0000FF">dashboard</a>' in html
        assert "**" not in html.split("Sector Headlines")[0]     # nada de markdown cru no topo

    def test_build_html_legado_ainda_renderiza(self):
        cfg = {"intro": {"on": True, "text": "Dear **clients**,"}}
        html = build_html([self._item()], date(2026, 8, 10), cfg)
        assert "<b>clients</b>" in html and "**clients**" not in html
