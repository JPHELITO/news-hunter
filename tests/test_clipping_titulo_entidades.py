"""Título com CÓDIGO DE HTML dentro — "Arauco&rsquo;s Sucuri&uacute;" (2026-08-11).

O Fastmarkets entrega a manchete com entidades HTML (&rsquo; &amp; &uacute; &#38;) e o banco
guardava assim: o snippet passava pelo _html_to_text (decodifica), o TÍTULO ia cru. Na
dashboard o navegador decodifica sozinho e ninguém percebe; no Word/e-mail vira texto
literal — o usuário via o título "todo cagado" no clipping.

Medido no banco (11.540 manchetes): 124 afetadas, 122 Fastmarkets + 2 GMK; nenhuma com
codificação dupla; e ZERO dos 11.416 títulos limpos mudaria ao decodificar.

⚠️ A prova que vale é o ARTEFATO: o texto DENTRO do .docx (um "&" vira "&amp;" no XML do
Word, então olhar só a função esconderia um erro de escape na hora de escrever).

Rodar: python -m pytest tests/test_clipping_titulo_entidades.py -v
"""
import re
import sys
import zipfile
from datetime import date
from io import BytesIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from clipping.build import ClippingItem, clean_headline
from clipping.generate import build_from_payload
from hunter.fastmarkets_scraper import _clean_title

# a manchete real do incidente (está no banco assim)
CRU   = ("Arauco&rsquo;s Sucuri&uacute; project reaches 75% completion; "
         "CEO remains optimistic on pulp demand: Lat Am Forest Products 2026")
LIMPO = ("Arauco’s Sucuriú project reaches 75% completion; "
         "CEO remains optimistic on pulp demand: Lat Am Forest Products 2026")


class TestCleanHeadline:
    def test_o_caso_do_usuario(self):
        assert clean_headline(CRU) == LIMPO

    def test_e_comercial_nas_duas_formas(self):
        assert clean_headline("MM Board &#38; Paper expands") == "MM Board & Paper expands"
        assert clean_headline("Suzano &amp; Klabin") == "Suzano & Klabin"

    def test_acentos_e_aspas(self):
        assert clean_headline("Mets&auml; Board") == "Metsä Board"
        assert clean_headline("Cristi&aacute;n Infante") == "Cristián Infante"
        assert clean_headline("d&#8217;Italia") == "d’Italia"

    def test_titulo_limpo_passa_intacto(self):
        """0 dos 11.416 títulos sem entidade mudariam — o '&' solto não pode virar nada."""
        for t in ["Pulp & Paper prices rise 3%", "AT&T sells stake", "Vale & CSN em disputa",
                  "Iron ore falls to $95/t", "R&D spending up 10%", ""]:
            assert clean_headline(t) == t

    def test_nao_decodifica_duas_vezes(self):
        """Um passe só: '&amp;#38;' vira '&#38;', não '&' (não existe caso duplo no banco)."""
        assert clean_headline("MM Board &amp;#38; Paper") == "MM Board &#38; Paper"


def test_item_limpa_sozinho():
    """Vale p/ QUEM construir o item — não dá p/ esquecer de chamar a função."""
    it = ClippingItem(url="https://dashboard.fastmarkets.com/a/1", title=CRU,
                      source_name="Fastmarkets", body="<p>x</p>", matched_keywords=[],
                      domain="dashboard.fastmarkets.com", take="=", sector="PP",
                      translated_title="Projeto Sucuri&uacute; da Arauco")
    assert it.title == LIMPO
    assert it.translated_title == "Projeto Sucuriú da Arauco"


def test_scraper_do_fastmarkets_decodifica_na_coleta():
    """A raiz: o título ia cru da API do FM pro banco."""
    assert _clean_title(CRU) == LIMPO
    assert _clean_title("Mets&auml;  Board   invests") == "Metsä Board invests"
    assert _clean_title(None) == ""


def test_o_word_sai_com_o_titulo_de_verdade():
    """Prova no artefato: o texto dentro do .docx, não o retorno da função."""
    payload = [{"url": "https://dashboard.fastmarkets.com/a/1", "title": CRU,
                "source_name": "Fastmarkets", "take": "=", "sector": "PP", "pos": 0,
                "body": "<p>Arauco is advancing the project.</p>"}]
    res = build_from_payload(payload, date(2026, 8, 11), fetch=False)

    with zipfile.ZipFile(BytesIO(res["docx"])) as z:
        xml = z.read("word/document.xml").decode("utf-8")
    texto = "".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", xml))

    assert "Arauco’s Sucuriú project" in texto
    assert "rsquo" not in xml and "uacute" not in xml, "entidade sobreviveu no Word"

    # e-mail: é derivado do próprio .docx, então tem que herdar o título limpo
    html = res["html"]
    assert "Arauco’s Sucuriú" in html
    assert "&rsquo;" not in html and "&amp;rsquo;" not in html
