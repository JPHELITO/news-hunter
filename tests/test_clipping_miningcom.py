"""Corpo do Mining.com no clipping (html_utils.extract_article_container + build._fetch_body_regular).

Caso real de 24/11/2026 — "Copper price roars back from bear market as Nvidia's Huang U-turn
revives AI trade": o Mining.com trocou de tema (~19/11/2026, tema "mdc-2026"). O container antigo
(div.post-inner-content .content) deixou de existir, a extração caiu no <article> inteiro e o
corpo saiu no Word, no e-mail e no HTML do WhatsApp com:
  - o próprio título repetido no começo (o h1 virava subtítulo), a linha fina, a data e a foto de capa;
  - no fim, "[googlesitekit-end-of-content]" + "Comments" + "No comments yet.".
O texto agora mora em section.the-content.

O HTML abaixo é o da matéria real, com a mesma estrutura e as mesmas classes, só com o miolo do
texto encurtado. Rodar:
  python -m pytest tests/test_clipping_miningcom.py -v
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from bs4 import BeautifulSoup

from clipping.html_utils import extract_article_container

URL = ("https://www.mining.com/copper-price-roars-back-from-bear-market-as-nvidias-huang-"
       "u-turn-revives-ai-trade/")

# Página real (tema mdc-2026), recortada: cabeçalho do <article> + texto + comentários.
_PAGE_2026 = """<html><body class="single single-post wp-theme-mdc-2026"><main class="py-5">
<div class="container single-post"><div class="row"><div class="col-12 col-lg-8">
<article class="single-article" data-post-id="1218979">
<div class="ad-zone mb-4">
<!-- Empty div for ad -->
</div>
<section class="mb-2 mt-3">
<h1 class="mb-2">Copper price roars back from bear market as Nvidia&#8217;s Huang U-turn revives AI trade</h1>
<p class="m-0">New York copper futures were still sliding minutes before the Nvidia announcement. Marex says the jury is out on whether the bottom is in.</p>
</section>
<section class="mb-2 small">
<a class="text-decoration-none" href="https://www.mining.com/external-author?external-author=Staff+Writer"><i class="ti ti-user"></i> Staff Writer</a>
    &amp;verbar;
    <span>November 23, 2026 - 10:09 am</span>
</section>
<section class="mb-2 py-2 border-top border-secondary">
<div class="d-flex flex-row flex-wrap gap-3 small">
<span class="fw-bold">Categories:</span>
<a class="link-dark" href="https://www.mining.com/category/markets/">Markets</a>
</div>
</section>
<section class="mb-4 pb-4 border-bottom border-primary border-3">
<div class="post-thumbnail mb-2">
<img alt="Copper price roars back" class="attachment-large size-large wp-post-image" src="https://www.mining.com/wp-content/uploads/2026/11/Jensen_Huang_Trump_White_House_April_2025-1024x683.jpg" width="1024"/> </div>
<p class="small">Nvidia CEO Jensen Huang delivers remarks as President Donald Trump looks on. Credit: Official White House Photo by Joyce N. Boghosian.</p>
</section>
<section class="mb-4 border-bottom border-secondary the-content">
<p class="wp-block-paragraph">Copper staged one of its biggest rallies of the year on Monday, bouncing out of the bear market it entered on Friday.</p>
<div class="wp-block-image">
<figure class="aligncenter size-full"><a href="https://www.mining.com/live-chart/copper/" target="_blank"><img alt="Chart of Comex copper price in 2026" class="wp-image-1218989" src="https://www.mining.com/wp-content/uploads/2026/11/copper-price-ytd-nov-23-2026-v2.jpg" width="1800"/></a></figure>
</div>
<h2 class="wp-block-heading" id="h-below-12-000-first">Below $12,000 first</h2>
<p class="wp-block-paragraph">London had a rougher morning before the Huang news broke.</p>
<h2 class="wp-block-heading" id="h-miners-add-25-billion">Miners add $25 billion</h2>
<div class="wp-block-image">
<figure class="aligncenter size-full"><img alt="Chart of 11 copper mining stocks" class="wp-image-1218987" src="https://www.mining.com/wp-content/uploads/2026/11/copper-miners-since-record-nov-23-2026.jpg" width="1800"/></figure>
</div>
<p class="wp-block-paragraph">The bounce recovers only a fraction of the damage.</p>
<p class="wp-block-paragraph"><em>(With files from Reuters and Bloomberg)</em></p>
<!--[googlesitekit-end-of-content]-->
</section>
<section class="mb-4">
<h2 class="mb-4">Comments</h2>
<div class="mdc-comment-login-prompt bg-light border-start border-4 border-primary p-3 mb-4 d-flex">
<span>Please sign in to leave a comment.</span>
<div class="d-flex gap-2">
<a class="btn btn-primary px-4" href="https://membership.tnm.global/login?src=www.mining.com/">Sign In</a>
<a class="btn btn-outline-primary px-4" href="https://membership.tnm.global/membership-plan">Join</a>
</div>
</div>
<p class="text-muted mb-0">No comments yet.</p>
</section>
</article>
</div></div></div></main></body></html>"""


class _Resp:
    def __init__(self, text):
        self.status_code = 200
        self.text = text


def _fetch(monkeypatch, page_html, url=URL):
    """Roda o caminho de PRODUÇÃO (build._fetch_body_regular) com a página servida localmente."""
    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp(page_html))
    try:
        from curl_cffi import requests as creq
        monkeypatch.setattr(creq, "get", lambda *a, **k: _Resp(page_html))
    except ImportError:   # sem curl_cffi o robô cai no requests puro (já trocado acima)
        pass
    from clipping.build import _fetch_body_regular
    return _fetch_body_regular(url)


class TestTemaNovo2026:
    """A matéria real de 24/11/2026, pelo mesmo caminho do Word/e-mail/WhatsApp."""

    @pytest.fixture
    def body(self, monkeypatch):
        out = _fetch(monkeypatch, _PAGE_2026)
        assert out, "corpo vazio"
        return out

    def test_container_e_o_texto(self):
        soup = BeautifulSoup(_PAGE_2026, "lxml")
        c = extract_article_container(soup, URL)
        assert c is not None and "the-content" in c.get("class", [])

    def test_sem_marcador_do_site_kit(self, body):
        assert "googlesitekit" not in body

    def test_sem_comentarios(self, body):
        for lixo in ("Comments", "No comments yet", "Please sign in", "Sign In", "Join"):
            assert lixo not in body, lixo

    def test_termina_no_credito_da_materia(self, body):
        assert body.rstrip().endswith("<p><em>(With files from Reuters and Bloomberg)</em></p>")

    def test_comeca_no_primeiro_paragrafo(self, body):
        assert body.startswith("<p>Copper staged one of its biggest rallies")

    def test_titulo_nao_repete(self, body):
        assert "roars back from bear market" not in body

    def test_sem_cabecalho_da_pagina(self, body):
        # linha fina, autor/data, categorias e a legenda da foto de capa ficam de fora
        for lixo in ("Marex says the jury", "Staff Writer", "November 23, 2026",
                     "Categories", "Official White House Photo"):
            assert lixo not in body, lixo

    def test_foto_de_capa_fora(self, body):
        # decisão de 30/07/2026: só imagens de CONTEÚDO entram, a foto de capa não
        assert "Jensen_Huang_Trump_White_House" not in body

    def test_graficos_do_meio_do_texto_ficam(self, body):
        a = body.find("copper-price-ytd-nov-23-2026-v2.jpg")
        b = body.find("copper-miners-since-record-nov-23-2026.jpg")
        assert a != -1 and b != -1 and a < b
        assert body.count("<img") == 2

    def test_subtitulos_da_materia_ficam(self, body):
        assert "<h3>Below $12,000 first</h3>" in body
        assert "<h3>Miners add $25 billion</h3>" in body


class TestCortesDoTemaNovo:
    def _container(self, inner):
        html = ('<article class="single-article"><section class="the-content">'
                f"{inner}</section></article>")
        return extract_article_container(BeautifulSoup(html, "lxml"), URL)

    def test_marcador_no_meio_do_paragrafo_guarda_o_que_vem_antes(self):
        c = self._container(
            "<p>Primeiro parágrafo.</p>"
            "<p>Último parágrafo.<!--[googlesitekit-end-of-content]--> lixo depois</p>"
            "<p>Bloco depois do marcador.</p>")
        t = c.get_text(" ", strip=True)
        assert "Último parágrafo." in t
        assert "lixo depois" not in t and "Bloco depois" not in t

    def test_marcador_escrito_como_texto_tambem_corta(self):
        c = self._container(
            "<p>Texto real.</p><p>[googlesitekit-end-of-content]</p><p>Depois.</p>")
        t = c.get_text(" ", strip=True)
        assert t == "Texto real."

    def test_outros_comentarios_html_nao_viram_texto(self):
        c = self._container("<p>Texto <!-- nota do editor --> real.</p>")
        assert "nota do editor" not in str(c)

    def test_area_de_comentarios_dentro_do_texto_e_cortada(self):
        c = self._container(
            "<p>Texto real.</p><h2>Comments</h2><p>No comments yet.</p>")
        assert c.get_text(" ", strip=True) == "Texto real."

    def test_paragrafo_que_comeca_com_comments_nao_e_cortado(self):
        c = self._container(
            "<p>Comments from the chief executive pushed the shares higher.</p>"
            "<p>Segundo parágrafo.</p>")
        t = c.get_text(" ", strip=True)
        assert "Comments from the chief executive" in t and "Segundo parágrafo." in t


class TestTemaAntigoContinua:
    """Até ~18/11/2026 — o seletor antigo continua valendo se a página voltar a ele."""

    _OLD = """<html><body><article><div class="post-inner-content">
<div class="content">
<p>Texto real do tema antigo com conteúdo suficiente.</p>
<p><em>(By Cecilia Jamasmie)</em></p>
</div>
<div class="more-news"><h3>More News</h3><p>Outra matéria</p></div>
<section class="comment-section"><p>No comments yet.</p></section>
</div></article></body></html>"""

    def test_container_antigo(self):
        c = extract_article_container(BeautifulSoup(self._OLD, "lxml"), URL)
        assert c is not None and "content" in c.get("class", [])
        t = c.get_text(" ", strip=True)
        assert t.endswith("(By Cecilia Jamasmie)")
        assert "More News" not in t and "No comments" not in t


class TestOutraFonteNaoMuda:
    def test_australian_mining_nao_cai_no_branch_do_mining_com(self):
        # "australianmining.com.au" contém "mining.com" — o branch é por domínio EXATO
        html = ('<html><body><div class="entry-content"><div class="content-inner">'
                "<p>Corpo do Australian Mining.</p></div></div>"
                '<section class="the-content"><p>Não é daqui.</p></section></body></html>')
        c = extract_article_container(
            BeautifulSoup(html, "lxml"), "https://www.australianmining.com.au/x/")
        assert c is not None and "content-inner" in c.get("class", [])
