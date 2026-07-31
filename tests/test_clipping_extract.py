"""Extração de corpo do clipping (html_utils): filtro de banner de anúncio +
remoção do rodapé 'Fonte: <veículo>' e do widget de anúncio do Portal Celulose.

Achados do deep-dive Portal Celulose (2026-07-31). Rodar:
  python -m pytest tests/test_clipping_extract.py -v
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from bs4 import BeautifulSoup

from clipping.html_utils import (
    article_to_safe_html,
    extract_article_container,
    _AD_IMG_RE,
)


class TestAdImageFilter:
    def test_banner_filtrado(self):
        assert _AD_IMG_RE.search(
            "https://x/wp-content/uploads/2025/09/Banner-Central-700x110-px.gif")

    def test_publicidade_filtrada(self):
        assert _AD_IMG_RE.search("https://x/uploads/publicidade-topo.jpg")

    def test_ads_path_filtrado(self):
        assert _AD_IMG_RE.search("https://x/ads/300x250.png")

    def test_foto_conteudo_com_dimensao_mantida(self):
        # foto REAL com dimensão NxM do WordPress no nome — NÃO pode ser filtrada
        assert not _AD_IMG_RE.search(
            "https://x/uploads/2026/07/Arauco-usa-drones-cabos-MS-002-1024x683.jpg")

    def test_anuncio_ambiguo_nao_filtra(self):
        # "anuncio" em PT é ambíguo (aviso/anúncio) → deliberadamente NÃO filtrado
        assert not _AD_IMG_RE.search("https://x/uploads/anuncio-de-resultados-suzano.jpg")

    def test_uploads_nao_casa_ads(self):
        assert not _AD_IMG_RE.search("https://x/wp-content/uploads/2026/07/foto.jpg")

    def test_safe_html_remove_banner_mantem_conteudo(self):
        raw = ('<div><p>Parágrafo real do corpo com bastante conteúdo aqui presente.</p>'
               '<img src="https://x/uploads/2025/09/Banner-Central-700x110-px.gif">'
               '<img src="https://x/uploads/2026/07/Arauco-drones-1024x683.jpg"></div>')
        out = article_to_safe_html(raw)
        assert "Banner-Central" not in out      # banner some
        assert "Arauco-drones" in out           # foto de conteúdo fica


_PORTAL_HTML = """
<html><body>
<div class="td-post-content">
  <p>Primeiro parágrafo real do artigo com conteúdo suficiente para passar no filtro.</p>
  <div class="angwp_11527 _ning_cont _ning_hidden _ning_outer">
    <img src="https://portalcelulose.com.br/wp-content/uploads/2025/09/Banner-Central-700x110-px.gif">
  </div>
  <p>Segundo parágrafo real do corpo do artigo, também com conteúdo suficiente aqui.</p>
  <div class="post-bottom-meta post-bottom-source">
    <div class="post-bottom-meta-title">Fonte</div>
    <span class="tagcloud"><a href="#">Valor Econômico</a></span>
  </div>
</div>
</body></html>
"""


class TestPortalCeluloseContainer:
    def _body(self):
        soup = BeautifulSoup(_PORTAL_HTML, "lxml")
        cont = extract_article_container(soup, "https://portalcelulose.com.br/materia-x/")
        assert cont is not None
        return article_to_safe_html(str(cont))

    def test_corpo_real_presente(self):
        body = self._body()
        assert "Primeiro parágrafo real" in body
        assert "Segundo parágrafo real" in body

    def test_credito_fonte_removido(self):
        # o rodapé "Fonte: Valor Econômico" NÃO pode vazar como parágrafo solto
        assert "Valor Econômico" not in self._body()

    def test_banner_widget_removido(self):
        assert "Banner-Central" not in self._body()
