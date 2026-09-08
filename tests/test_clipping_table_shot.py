"""A tabela da Fastmarkets volta a ser IMAGEM — renderizada por nós, sem abrir o site.

Contexto (2026-09-08, logo depois de `29c76a1`): reconstruir a tabela como `<table>` de verdade
funcionou, mas saiu com cara de colagem de Excel no Word e no e-mail. O analista pediu o formato
antigo de volta ("como imagem"), fiel ao site. `clipping/table_shot.py` renderiza a tabela com o
**CSS que a própria Fastmarkets embute na matéria** e devolve um `<img>` data-URI — sem navegar
até a página (nada de sessão, paywall ou espera de SPA).

Rodar: python -m pytest tests/test_clipping_table_shot.py -v
"""
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from clipping.html_utils import article_to_safe_html                   # noqa: E402
from clipping.table_shot import TABLE_IMG_RE, tables_to_images         # noqa: E402

_TABELA = (
    '<style type="text/css">figure.table thead tr th{color:#6a1b9a;font-weight:bold}</style>'
    '<figure class="table" style="width:453px;"><table class="ck-table-resized">'
    '<thead><tr><th colspan="3">PIX Packaging Europe (&euro; per tonne)</th></tr></thead>'
    '<tbody><tr><td>PIX Kraftliner</td><td style="text-align:right;">870.59</td>'
    '<td style="text-align:right;">+0.61</td></tr></tbody>'
    '</table></figure>'
)
_ARTIGO = "<p>Fastmarkets calculated its weekly PIX Kraftliner index.</p>" + _TABELA


def _tem_navegador() -> bool:
    """True se dá p/ subir o Chromium do Playwright nesta máquina."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    try:
        with sync_playwright() as pw:
            pw.chromium.launch().close()
        return True
    except Exception:
        return False


_SEM_NAVEGADOR = not _tem_navegador()


class TestSemNavegador:
    """A etapa é OPCIONAL por construção: sem ela a tabela segue como tabela."""

    def test_artigo_sem_tabela_passa_intacto(self):
        html = "<p>Prices were stable this week.</p>"
        assert tables_to_images(html) is html          # nem parseia: devolve o mesmo objeto

    def test_vazio_nao_quebra(self):
        assert tables_to_images("") == ""
        assert tables_to_images(None) is None

    def test_navegador_indisponivel_devolve_o_html_original(self, monkeypatch):
        """Playwright fora do ar → a tabela NÃO some; ela volta a ser <table> no clipping."""
        import clipping.table_shot as ts

        # módulo em None no sys.modules → o `from playwright…` interno levanta ImportError
        monkeypatch.setitem(sys.modules, "playwright.sync_api", None)
        out = ts.tables_to_images(_ARTIGO, source="Fastmarkets")
        assert "<table" in out
        assert "870.59" in out


class TestSanitizador:
    def test_print_de_tabela_atravessa_o_sanitizador(self):
        """O <img> data-URI é imagem NOSSA — o sanitizador tem de deixar passar (ele só
        aceitava https, então o print seria descartado logo depois de ser gerado)."""
        png = ("<p>Texto do artigo com tamanho suficiente.</p>"
               '<img src="data:image/png;base64,iVBORw0KGgoAAAANSUhEUg==" alt="">')
        safe = article_to_safe_html(png)
        assert "data:image/png;base64," in safe
        assert 'class="reader-img"' in safe

    def test_data_uri_de_outro_tipo_continua_barrada(self):
        """Só imagem: `data:text/html` seguiria sendo descartada."""
        safe = article_to_safe_html(
            "<p>Texto do artigo com tamanho suficiente.</p>"
            '<img src="data:text/html;base64,PHNjcmlwdD4=" alt="">')
        assert "data:text/html" not in safe


@pytest.mark.skipif(_SEM_NAVEGADOR, reason="Chromium do Playwright indisponível neste ambiente")
class TestRenderReal:
    def test_tabela_vira_img_e_o_texto_fica(self):
        out = tables_to_images(_ARTIGO, source="Fastmarkets")
        assert "<table" not in out                       # virou imagem
        assert TABLE_IMG_RE.search(out)
        assert "Fastmarkets calculated" in out           # os parágrafos seguem intactos

    def test_o_png_tem_tamanho_de_tabela(self):
        """Print vazio/minúsculo = render quebrado. Confere as dimensões reais do PNG."""
        out = tables_to_images(_ARTIGO, source="Fastmarkets")
        import base64
        import struct
        b64 = re.search(r'src="data:image/png;base64,([^"]+)"', out).group(1)
        png = base64.b64decode(b64)
        w, h = struct.unpack(">II", png[16:24])          # cabeçalho IHDR
        assert w > 200 and h > 40, (w, h)

    def test_o_sanitizador_preserva_o_print(self):
        """Ponta a ponta: render → sanitizador → o corpo do clipping fica com a imagem."""
        safe = article_to_safe_html(tables_to_images(_ARTIGO, source="Fastmarkets"))
        assert TABLE_IMG_RE.search(safe)
        assert "<table" not in safe
