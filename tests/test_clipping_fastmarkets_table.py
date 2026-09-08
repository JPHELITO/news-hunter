"""As tabelas de preço da Fastmarkets deixaram de ser IMAGEM e viraram <table> de verdade.

Contexto (2026-09-08): a Fastmarkets passou a publicar as tabelas pelo CKEditor —
`<style>…CSS do CMS…</style><figure class="table"><table class="ck-table-resized">` — em
vez de um PNG. Dois bugs somados faziam a tabela SUMIR do clipping:

  1. o sanitizador não conhecia `<table>`: caía no ramo genérico "desce nos filhos" e,
     como cada célula é um número curto (< 12 chars), NADA sobrava. A `<figure>` que
     envolve a tabela era pior ainda — só olhava se havia `<img>` dentro e descartava;
  2. o CSS embutido (~1,4 mil chars) contava como texto do corpo em `_sanitize_ok`, que
     media 53% de preservação, achava que o sanitizador tinha comido a matéria e jogava o
     artigo inteiro p/ o caminho lento do DOM — onde ele sumia de novo.

Rodar: python -m pytest tests/test_clipping_fastmarkets_table.py -v
"""
import sys
from datetime import date
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).parent.parent))

from clipping.build import ClippingItem, _html_to_blocks, build_docx   # noqa: E402
from clipping.docx_to_email import docx_to_email_html                  # noqa: E402
from clipping.html_utils import article_to_safe_html, plain_text       # noqa: E402
from clipping.reader import _sanitize_ok                               # noqa: E402

# Recorte FIEL do que a API news/v3/articles devolve hoje (artigo "Small changes in PIX
# European containerboard price indices", 08/09/2026) — style + figure.table + colspan.
_CSS = ("<style type=\"text/css\">figure.table{margin-left:0 !important;} "
        "figure.table table{border-collapse:collapse !important;} "
        "figure.table thead tr th{color:#6a1b9a;font-weight:bold;} "
        + "/* enche o CSS p/ ele pesar como no artigo real */ " * 30 + "</style>")

_TABELA = (
    '<figure class="table" style="width:453px;">'
    '<table class="ck-table-resized" style="border-style:none;">'
    '<colgroup><col style="width:36.76%;"></colgroup>'
    '<thead>'
    '<tr><th style="border-style:none;" colspan="6">PIX Packaging Europe (&euro; per tonne)</th></tr>'
    '<tr><th>&nbsp;</th><th>08/09/2026</th><th>Change</th>'
    '<th colspan="3">Confidence int. (95%)</th></tr>'
    '</thead><tbody>'
    '<tr><td>PIX Kraftliner</td>'
    '<td style="text-align:right;">870.59</td><td style="text-align:right;">+0.61</td>'
    '<td style="text-align:right;">842.56</td><td>-</td><td style="text-align:right;">898.62</td></tr>'
    '<tr><td>PIX RB Fluting</td>'
    '<td style="text-align:right;">554.63</td><td style="text-align:right;">-0.31</td>'
    '<td style="text-align:right;">531.88</td><td>-</td><td style="text-align:right;">577.38</td></tr>'
    '<tr><td colspan="6"><i>Source: Fastmarkets.</i></td></tr>'
    '</tbody></table></figure>'
)

_PARAGRAFOS = "".join(
    f"<p>Fastmarkets calculated its weekly PIX index number {i} at &euro;870.59 per tonne "
    f"on September 8, up by &euro;0.61 (0.07%) from &euro;869.98 per tonne on September 1.</p>"
    for i in range(6)
)

_ARTIGO = _PARAGRAFOS + _CSS + _TABELA


class TestSanitizador:
    def test_tabela_sobrevive(self):
        """O bug do usuário: a tabela sumia do clipping. Tem que virar <table> com os números."""
        safe = article_to_safe_html(_ARTIGO)
        assert "<table" in safe
        for v in ("PIX Packaging Europe", "PIX Kraftliner", "870.59", "+0.61", "577.38"):
            assert v in safe, v

    def test_colspan_preservado(self):
        """O título da tabela ocupa as 6 colunas; 'Confidence int.' ocupa 3."""
        safe = article_to_safe_html(_ARTIGO)
        assert 'colspan="6"' in safe
        assert 'colspan="3"' in safe

    def test_numeros_alinhados_a_direita(self):
        assert "text-align:right" in article_to_safe_html(_ARTIGO)

    def test_cabecalho_vira_th(self):
        assert "<th" in article_to_safe_html(_ARTIGO)

    def test_css_do_cms_nao_vira_paragrafo(self):
        """O <style> que vem antes da tabela é do CMS — não pode virar texto da matéria."""
        safe = article_to_safe_html(_ARTIGO)
        assert "border-collapse" not in plain_text(safe)
        assert "figure.table" not in plain_text(safe)


class TestSanitizeOk:
    def test_css_nao_conta_como_corpo(self):
        """O miolo do <style> não é texto visível: contá-lo derrubava o artigo p/ o DOM."""
        assert "border-collapse" not in plain_text(_CSS)
        assert plain_text(_CSS) == ""

    def test_artigo_com_tabela_passa_no_piso(self):
        """Com a tabela preservada e o CSS fora da conta, o caminho rápido da API vale."""
        safe = article_to_safe_html(_ARTIGO)
        assert _sanitize_ok(_ARTIGO, safe, "https://dashboard.fastmarkets.com/a/x", "Fastmarkets")


class TestBlocosDoWord:
    def _tabela(self):
        blocks = _html_to_blocks(article_to_safe_html(_ARTIGO))
        tabelas = [b for b in blocks if b["type"] == "table"]
        assert len(tabelas) == 1
        return tabelas[0]["rows"]

    def test_linhas_e_celulas(self):
        rows = self._tabela()
        assert len(rows) == 5                       # título + cabeçalho + 2 dados + fonte
        assert [c["text"] for c in rows[2]][:3] == ["PIX Kraftliner", "870.59", "+0.61"]

    def test_cabecalho_marcado(self):
        rows = self._tabela()
        assert rows[0][0]["header"] is True
        assert rows[2][0]["header"] is False

    def test_colspan_chega_nos_blocos(self):
        assert self._tabela()[0][0]["colspan"] == 6

    def test_tabela_na_ordem_certa(self):
        """A tabela vem DEPOIS dos parágrafos, como no artigo — não jogada no fim."""
        tipos = [b["type"] for b in _html_to_blocks(article_to_safe_html(_ARTIGO))]
        assert tipos.index("table") == len(tipos) - 1
        assert tipos.count("text") == 6


def _item() -> ClippingItem:
    return ClippingItem(
        url="https://dashboard.fastmarkets.com/a/f4a918a1",
        title="Small changes in PIX European containerboard price indices",
        source_name="Fastmarkets", body=article_to_safe_html(_ARTIGO),
        matched_keywords=[], domain="dashboard.fastmarkets.com", take="=", sector="PP",
    )


class TestWordEEmail:
    def test_docx_tem_tabela_de_verdade(self):
        xml = ZipFile(BytesIO(build_docx([_item()], date(2026, 9, 8), {}))).read(
            "word/document.xml").decode("utf-8")
        assert "<w:tbl>" in xml
        assert "870.59" in xml

    def test_email_derivado_do_word_tem_a_tabela(self):
        """O HTML do e-mail sai do .docx — o `w:tbl` era pulado em silêncio no `html()`."""
        docx = build_docx([_item()], date(2026, 9, 8), {})
        html = docx_to_email_html(docx, url_by_bookmark={"art0": "https://n1"})
        assert "<table" in html
        assert "870.59" in html
        assert 'colspan="6"' in html
