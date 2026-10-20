"""Manchete da PaperAge com ACENTO ESTRAGADO — "Södra" gravado como "SÃ¶dra" (2026-10-20).

A PaperAge manda a página só com "Content-Type: text/html" (sem charset) e declara
<meta charset="utf-8"> DENTRO do HTML. Sem charset no cabeçalho, o requests chuta ISO-8859-1
e o `.text` estraga cada letra acentuada em 2 caracteres ("ö" → "Ã¶"). As outras 6 fontes
HTML dizem o charset no cabeçalho e não mudam (medido: títulos idênticos antes/depois).

Medido no banco (filtro no servidor): 15 manchetes estragadas, TODAS da PaperAge (25/06 a
20/10/2026). Só 2 outras linhas têm um par parecido e são títulos CORRETOS — "Comitê" +
espaço fixo (ABTCP) e "Metrô" + aspas curvas (IBRAM): não podem mudar.

Conserto em 3 camadas (o molde do Fastmarkets, 2026-08-11): (1) a raiz, no download
(`html_scrapers._use_page_charset`); (2) rede de segurança no clipping (`clean_headline`);
(3) `scripts/backfill_titulos_acento.py` para o que já está gravado.

Rodar: python -m pytest tests/test_titulo_acento_estragado.py -v
"""
import re
import sys
import zipfile
from datetime import date
from io import BytesIO
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent.parent))

import hunter.html_scrapers as HS
from clipping.build import ClippingItem, clean_headline
from clipping.generate import build_from_payload

# a do usuário, exatamente como está no banco
SODRA_CRU = "SÃ¶dra Establishing Commercial Sales Office in Massachusetts"
SODRA     = "Södra Establishing Commercial Sales Office in Massachusetts"

# As 15 do banco, como deveriam ser. "Paulínia" estragado tem um hífen INVISÍVEL (U+00AD):
# na tela aparecia "PaulÃnia".
CERTOS = [
    SODRA,
    "Solenis Relocates Pulp Center of Excellence to Paulínia, Brazil",
    "Metsä Group's Carbon Dioxide Capture Plant Receives EUR 21 Million in Aid",
    "Metsä Fibre Initiates New Change Negotiations about Layoffs at Joutseno Pulp Mill",
    "Metsä Board to Modernize Kraftliner Machine Winder at Kemi Mill",
    "Metsä Tissue Appoints Michael Lanschützer as SVP, Greaseproof Papers",
    "Valmet to Supply First Commercial-Scale 3D Molded Fiber Line to Metsä's Äänekoski Mill",
    "Röchling Industrial Kimberly Appoints Ken Stager as New Managing Director",
    "Kadant Black Clawson Starts Up New Green Liquor System at Södra Cell Mörrum",
    "Metsä Group Investing EUR 25 Million in New Production Line for Muoto Fibre Packaging",
    "Metsä Group CEO Says Profitability Improved Significantly in 2Q 2026",
    "Södra Posts Negative Second Quarter 2026 Earnings on Weak Demand",
    "Metsä Fibre Develops Its Logistics and Reduces Emissions Across the Entire Supply Chain",
    "Metsä Board Appoints Malin Nygren as VP, Husum Pulp and Board Mill",
    "Stora Enso to Increase Fluff Pulp Production with EUR 19 Million Investment at Skutskär Mill",
]

# as 2 linhas do banco que PARECEM estragadas e estão certas
PARECIDOS = [
    "Comitê\xa0aprova R$ 442 milhões para Unidades de Conservação em 19 estados",
    "Campanha “Vá de Metrô” incentiva mobilidade sustentável durante a EXPOSIBRAM 2026",
]

MINERACAO = "Mineração será essencial para a transição energética"


def _estraga(t: str) -> str:
    """O defeito: UTF-8 lido como Latin-1 (conferido letra a letra contra as 15 do banco)."""
    return t.encode("utf-8").decode("latin-1")


def _resposta(corpo: bytes, content_type: str) -> requests.Response:
    """Resposta HTTP de verdade, montada como o requests monta na rede: o .encoding sai do
    cabeçalho (text/* sem charset = ISO-8859-1)."""
    r = requests.Response()
    r.status_code = 200
    r._content = corpo
    r.headers["Content-Type"] = content_type
    r.encoding = requests.utils.get_encoding_from_headers(r.headers)
    return r


def _home_paperage(titulos, meta='<meta charset="utf-8">') -> str:
    """Home da PaperAge reduzida (mesma estrutura da página de 20/10/2026)."""
    links = "\r\n".join(
        f'<a href="https://www.paperage.com/2026news/10-20-2026materia-{i}.html">{t}</a>'
        for i, t in enumerate(titulos))
    return ('<!DOCTYPE html>\r\n<html lang="en-US"  dir="ltr">\r\n<head>\r\n'
            f'    <title>PaperAge</title>\r\n    {meta}\r\n</head>\r\n<body>\r\n{links}\r\n</body></html>')


def _raspa(monkeypatch, resp) -> list[str]:
    monkeypatch.setattr(HS.requests, "get", lambda *a, **k: resp)
    src = next(s for s in HS.HTML_SOURCES if s["label"] == "PaperAge")
    return [a.title for a in HS._scrape_source(src)]


class TestColeta:
    """A raiz: a página chega em UTF-8 e o cabeçalho não diz o charset."""

    def test_o_caso_do_usuario(self, monkeypatch):
        html = _home_paperage([SODRA]).encode("utf-8")
        assert _raspa(monkeypatch, _resposta(html, "text/html")) == [SODRA]

    def test_as_15_do_banco(self, monkeypatch):
        html = _home_paperage(CERTOS).encode("utf-8")
        assert _raspa(monkeypatch, _resposta(html, "text/html")) == CERTOS

    def test_meta_no_formato_antigo(self, monkeypatch):
        meta = '<meta http-equiv="Content-Type" content="text/html; charset=utf-8">'
        html = _home_paperage([SODRA], meta=meta).encode("utf-8")
        assert _raspa(monkeypatch, _resposta(html, "text/html")) == [SODRA]

    def test_cabecalho_com_charset_continua_mandando(self, monkeypatch):
        """Quem diz o charset no cabeçalho segue como antes (é o que o navegador faz) — mesmo
        com um <meta> dizendo outra coisa."""
        html = _home_paperage([MINERACAO]).encode("latin-1")      # o <meta> diz utf-8
        assert _raspa(monkeypatch, _resposta(html, "text/html; charset=ISO-8859-1")) == [MINERACAO]
        html = _home_paperage([SODRA]).encode("utf-8")
        assert _raspa(monkeypatch, _resposta(html, "text/html; charset=utf-8")) == [SODRA]

    def test_sem_declaracao_nenhuma(self, monkeypatch):
        """Sem cabeçalho e sem <meta>: UTF-8 se os bytes forem UTF-8; senão, o Latin-1 de
        sempre (o chute estatístico leria este português como windows-1250: "Mineraçăo")."""
        html = _home_paperage([SODRA], meta="").encode("utf-8")
        assert _raspa(monkeypatch, _resposta(html, "text/html")) == [SODRA]
        html = _home_paperage([MINERACAO], meta="").encode("latin-1")
        assert _raspa(monkeypatch, _resposta(html, "text/html")) == [MINERACAO]


class TestRedeDeSeguranca:
    """O clipping conserta sozinho o que já está no banco (o push é ignore-duplicates)."""

    def test_o_caso_do_usuario(self):
        assert clean_headline(SODRA_CRU) == SODRA

    def test_as_15_do_banco(self):
        assert _estraga(SODRA) == SODRA_CRU
        for certo in CERTOS:
            assert clean_headline(_estraga(certo)) == certo

    def test_os_2_parecidos_do_banco_ficam_intactos(self):
        for t in PARECIDOS:
            assert clean_headline(t) == t

    def test_titulo_certo_passa_intacto(self):
        """Consertar o que já está certo não faz nada (rodar 2× = rodar 1×)."""
        for t in CERTOS + ["São Paulo à frente: Vale e CSN", "Câmara aprova", "Cristián Infante",
                           "MANHÃ\xa0DE ALTA NO MINÉRIO", ""]:
            assert clean_headline(t) == t

    def test_aspas_curvas_e_travessao(self):
        """3 bytes no UTF-8 → "â" + 2 caracteres invisíveis."""
        assert clean_headline(_estraga("Metsä’s mill — “record”")) == "Metsä’s mill — “record”"

    def test_entidade_e_acento_juntos(self):
        assert clean_headline("MetsÃ¤ Board &amp; Pulp") == "Metsä Board & Pulp"


def test_item_limpa_sozinho():
    """Vale p/ QUEM construir o item — não dá p/ esquecer de chamar a função."""
    it = ClippingItem(url="https://www.paperage.com/2026news/10-20-2026sodra.html", title=SODRA_CRU,
                      source_name="PaperAge", body="<p>x</p>", matched_keywords=[],
                      domain="paperage.com", take="=", sector="PP")
    assert it.title == SODRA


def test_o_word_e_o_email_saem_com_o_acento_certo():
    """Prova no artefato: o texto dentro do .docx e o e-mail, não o retorno da função."""
    payload = [{"url": "https://www.paperage.com/2026news/10-20-2026sodra.html", "title": SODRA_CRU,
                "source_name": "PaperAge", "take": "=", "sector": "PP", "pos": 0,
                "body": "<p>The Swedish company is opening a sales office.</p>"}]
    res = build_from_payload(payload, date(2026, 10, 20), fetch=False)

    with zipfile.ZipFile(BytesIO(res["docx"])) as z:
        xml = z.read("word/document.xml").decode("utf-8")
    texto = "".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", xml))

    assert SODRA in texto
    assert SODRA_CRU[:5] not in texto, "acento estragado sobreviveu no Word"
    assert SODRA in res["html"] and SODRA_CRU[:5] not in res["html"]
