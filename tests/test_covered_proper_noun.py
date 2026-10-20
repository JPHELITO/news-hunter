"""
Vale e Aura como NOME PRÓPRIO na marcação de empresa coberta (hunter/news_take_classifier.py).

Caso real (20/10/2026): "Vale quer captar até US$ 1,5 bi em panda pounds na China" (Notícias de Mineração,
fonte que chega só com o título) saía SEM a Vale — a regra antiga exigia uma palavra do setor no texto — e
a Home não mostrava o selo VALE3. A mesma regra marcava "Vale a pena investir em ouro?" e "Vale do Aço".

Rodar: python -m pytest tests/test_covered_proper_noun.py -v
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from hunter.news_take_classifier import detect_covered_companies


@pytest.mark.parametrize("texto", [
    "Vale quer captar até US$ 1,5 bi em panda pounds na China",      # o caso que o usuário pegou
    "Mercado espera lucro menor para Vale",
    "Cosan articula indicação de nome para cadeira no conselho da Vale",
    "Vale anuncia a escolha de Gustavo Pimenta como próximo presidente",
    "BHP e Vale se enfrentam na Corte inglesa pelo desastre de Mariana",
    "Ações da Vale sobem",
    "Na Vale, sucessão avança",
    "À Vale, ministro pede R$ 100 bi",
    "VALE ANUNCIA NOVO CEO",
    "Vale’s iron ore output rises 3% in the first quarter",
    "Direct diesel sales in Brazil surge after Petrobras–Vale deal",
    "Vale Base Metals sells stake",
    "Vale Ventures announces investment in US carbon capture technologies",
    "Vale do Rio Doce: história",                                       # nome histórico (alias forte)
    "VALE3 sobe 2%",
])
def test_vale_empresa(texto):
    assert "VALE" in detect_covered_companies(texto)


@pytest.mark.parametrize("texto", [
    "Vale a pena investir em ouro?",                 # verbo — antes marcava (tinha "ouro")
    "Vale lembrar que o minério caiu",               # verbo — antes marcava ("minerio")
    "Vale tudo no mercado de ouro",
    "Sigma Lithium amplia produção no Vale do Jequitinhonha",   # região — antes marcava ("producao")
    "Startups do Vale do Silício captam",
    "Cidades do Vale do Aço recebem siderúrgica",
    "A pequena Ribas entra no mapa do ‘Vale da Celulose’",       # região — antes marcava
    "O vale do rio ficou alagado",
    "Mineração Vale Verde amplia mina de cobre em Alagoas",      # outra mineradora
])
def test_vale_nao_e_a_empresa(texto):
    assert "VALE" not in detect_covered_companies(texto)


def test_vale_do_aco_nao_apaga_a_empresa_vizinha():
    assert detect_covered_companies("Usiminas investe no Vale do Aço") == ["USIMINAS"]


def test_vale_em_qualquer_ponto_do_texto():
    # o classificador recebe título + resumo + corpo: a empresa no resumo também conta…
    assert "VALE" in detect_covered_companies("Minério recua em Dalian. A Vale manteve a meta do ano.")
    # …e o verbo no resumo continua não contando
    assert "VALE" not in detect_covered_companies("Dicas de investimento. Vale a pena diversificar a carteira.")


@pytest.mark.parametrize("texto,esperado", [
    ("Aura Minerals compra mina", True),
    ("Aura vai usar recursos com debênture para gestão de dívida e expansão", True),
    ("A Aura anunciou produção recorde de ouro", True),
    ("Uma aura de otimismo no mercado", False),
    ("Mercado vive uma Aura de otimismo", False),
    ("an aura of optimism over metals", False),
])
def test_aura(texto, esperado):
    assert ("AURA" in detect_covered_companies(texto)) is esperado
