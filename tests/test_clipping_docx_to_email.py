"""O HTML do e-mail é DERIVADO do .docx — estes testes travam esse contrato.

Contexto (2026-08-10): o HTML era montado à mão, em paralelo ao Word, e divergia. O usuário
foi pegando um a um: "os títulos não estão com o highlight amarelo", "os tamanhos parecem
estranhos", "os espaçamentos", "a barrinha não é a mesma". Ele estava certo — o `.docx` sai
perfeito, então ele passou a ser a FONTE do e-mail (`clipping/docx_to_email.py`).

Rodar: python -m pytest tests/test_clipping_docx_to_email.py -v
"""
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from clipping.build import ClippingItem, build_docx                     # noqa: E402
from clipping.docx_to_email import docx_to_email_html                   # noqa: E402
from clipping.eml import build_html                                     # noqa: E402


def _items():
    return [
        ClippingItem(url="https://core.spglobal.com/a/1", title="Iron ore eases on demand",
                     source_name="S&P Platts", body="<p>Prices eased this week.</p>",
                     matched_keywords=[], domain="core.spglobal.com", take="-", sector="SM"),
        ClippingItem(url="https://dashboard.fastmarkets.com/a/2", title="Containerboard stable",
                     source_name="Fastmarkets", body="<p>Prices were stable.</p>",
                     matched_keywords=[], domain="dashboard.fastmarkets.com", take="=", sector="PP"),
    ]


def _cfg():
    return {"intro": {"on": True, "html": "<div>Dear <b>clients</b>,</div>"},
            "recent_publications": [{"name": "Vale 2Q26", "sector": "SM", "link": "https://x.com/v"}]}


def _docx():
    return build_docx(_items(), date(2026, 8, 10), _cfg())


def _html():
    return docx_to_email_html(_docx(), url_by_bookmark={"art0": "https://n1", "art1": "https://n2"})


class TestDerivadoDoWord:
    def test_highlight_amarelo_dos_titulos(self):
        """A reclamação nº 1: título de notícia tem realce AMARELO no Word."""
        assert "background:#FFFF00" in _html()

    def test_highlight_preto_das_secoes(self):
        assert "background:#000000" in _html()

    def test_tamanhos_vem_do_word(self):
        """Corpo 9pt, título 12pt, índice 11pt, seção 16pt — não um 11pt para tudo."""
        tam = {float(x) for x in re.findall(r"font-size:([\d.]+)pt", _html())}
        assert {9.0, 11.0, 12.0, 16.0} <= tam, tam

    def test_logo_nao_duplica(self):
        """A logo está no corpo E no cabeçalho do Word — injetar a do cabeçalho fazia o
        e-mail sair com DUAS. Regra geral: nenhuma imagem repetida no HTML."""
        srcs = re.findall(r'src="(data:[^"]+)"', _html())
        assert srcs, "esperava ao menos a logo"
        assert len(srcs) == len(set(srcs)), "imagem repetida no e-mail (logo duplicada?)"

    def test_imagens_no_tamanho_do_word(self):
        """Largura vem do extent do .docx (não inventada). A BARRA é a exceção: sai no
        tamanho da forma (~1496px), fora da tabela do texto — ver TestBarrinha."""
        larguras = [int(w) for w in re.findall(r'<img[^>]*width="(\d+)"', _html())]
        assert larguras
        for w in larguras:
            assert 0 < w <= 700 or w > 1000, w

    def test_ancora_interna_virou_link_externo(self):
        """No Word o índice usa âncora (art0); em e-mail âncora não funciona."""
        html = _html()
        assert "https://n1" in html and 'href="#art0"' not in html

    def test_intro_rica_sobrevive(self):
        assert "<b>clients</b>" in _html()

    def test_espacamento_em_pt_do_word(self):
        assert re.search(r"margin:[\d.]+pt 0 [\d.]+pt 0", _html())

    def test_bullets_com_recuo_do_numbering(self):
        assert re.search(r'<ul type="disc" style="margin:0;padding-left:[\d.]+pt"', _html())


class TestIntegracaoComOEmail:
    def test_build_html_usa_o_docx_quando_recebe(self):
        html = build_html(_items(), date(2026, 8, 10), _cfg(), docx_bytes=_docx())
        assert "background:#FFFF00" in html, "deveria ter vindo do Word"

    def test_build_html_cai_no_montado_se_o_docx_e_invalido(self):
        """Fallback: .docx corrompido não pode derrubar a geração."""
        html = build_html(_items(), date(2026, 8, 10), _cfg(), docx_bytes=b"nao sou docx")
        assert "Sector Headlines" in html and "background:#FFFF00" not in html

    def test_sem_docx_segue_o_caminho_antigo(self):
        html = build_html(_items(), date(2026, 8, 10), _cfg())
        assert "Sector Headlines" in html


class TestBarrinha:
    """A barra do topo é FIGURA — como sempre foi no e-mail do usuário.

    Prova (2026-08-10): no PDF de um e-mail real enviado por ele, a barra é uma imagem de
    **1497x56 px** — o colar-como-RTF faz o Word RASTERIZAR a forma. Ele disse 2x que "a
    barrinha é uma figura" e estava certo; eu tinha reproduzido como célula de tabela.
    """

    def test_barra_e_imagem_no_tamanho_da_forma(self):
        html = _html()
        m = re.search(r'<img src="data:image/png;base64,[^"]+" width="(\d+)" height="(\d+)"', html)
        assert m, "a barra deveria ser uma imagem"
        assert int(m.group(1)) > 1000 and int(m.group(2)) == 55, m.groups()

    def test_barra_fica_FORA_da_tabela_do_texto(self):
        """⚠️ INVARIANTE: dentro da tabela do texto, a imagem de 1496px arrastava a coluna
        p/ 15,58in e o texto saía CORTADO ao imprimir (medido). A barra vem antes."""
        html = _html()
        assert html.index("<img src=") < html.index('width="100%"'),             "a barra voltou p/ dentro da tabela do texto — vai cortar o texto ao imprimir"

    def test_texto_da_barra_no_alt(self):
        """Virou pixel: o alt preserva o texto p/ leitor de tela e busca."""
        assert 'alt="Itau BBA | Equity Research"' in _html()

    def test_sem_fonte_cai_na_celula_preta(self, monkeypatch):
        import clipping.docx_to_email as m
        monkeypatch.setattr(m, "_ttf", lambda bold, px: None)
        html = _html()
        assert 'bgcolor="#000000"' in html and "Equity Research" in html
