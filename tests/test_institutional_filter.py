# -*- coding: utf-8 -*-
"""Regra 'institutional_admin' (2026-10-26): nota administrativa de ANM/IBRAM/ANTAQ sem sinal de mercado sai do
feed. Medido em 30 dias: 101 itens dessas fontes, 100% "no take" da IA. Decisão do usuário: só essas três, e só
sem NENHUM sinal — coberta, player, tópico de produto coberto ou termo regulatório/econômico mantém a notícia.
Chave de desligar: INSTITUTIONAL_FILTER=0.

Rodar: python -m pytest tests/test_institutional_filter.py -v
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from hunter.news_take_classifier import should_exclude_news, classify_article_take


def _ex(title, source):
    return should_exclude_news(title, {"source_name": source, "title": title})


class TestSai:
    def test_nota_a_imprensa_ibram(self):
        assert _ex("Nota à imprensa – eleição 2026", "IBRAM") == (True, "institutional_admin")

    def test_encontro_antaq(self):
        assert _ex("ANTAQ participa de encontro internacional sobre melhoria regulatória", "ANTAQ") == (True, "institutional_admin")

    def test_curso_anm(self):
        assert _ex("Agência autoriza curso de formação para cadastro de reserva e prepara reposição de vagas", "ANM")[1] == "institutional_admin"

    def test_premio_e_evento_de_mineracao_nao_protegem(self):
        # "mineração" sozinha é o nome do setor — aparece em todo evento/prêmio
        assert _ex("Gestão de riscos climáticos na mineração é tema de encontro em Belo Horizonte", "IBRAM")[1] == "institutional_admin"
        assert _ex("AngloGold Ashanti conquista primeiro lugar em prêmio nacional de boas práticas na mineração", "IBRAM")[1] == "institutional_admin"

    def test_edital_de_conteineres_antaq(self):
        assert _ex("ANTAQ publica edital do primeiro leilão de terminal de contêineres em mais de dez anos", "ANTAQ")[1] == "institutional_admin"

    def test_palavra_fraca_nao_segura_evento(self):
        # "portuário"/"investimentos" são proteção FRACA: não valem em título de evento/curso/associação
        assert _ex("ANTAQ na Escola aproxima estudantes do Espírito Santo do setor portuário", "ANTAQ")[1] == "institutional_admin"
        assert _ex("ANTAQ apresenta novo Sistema de Desempenho Portuário durante 33º Encontro do SDP", "ANTAQ")[1] == "institutional_admin"
        assert _ex("IBRAM modera debate sobre investimentos e competitividade da mineração brasileira", "IBRAM")[1] == "institutional_admin"
        assert _ex("CoreX passa a integrar o quadro de associadas do IBRAM", "IBRAM")[1] == "institutional_admin"


class TestFica:
    def test_coberta(self):
        assert _ex("ANM suspende licença de operação da Vale em Itabira", "ANM")[1] != "institutional_admin"

    def test_termo_regulatorio(self):
        assert _ex("ANM publica resolução sobre segurança de barragens de rejeitos", "ANM")[1] != "institutional_admin"
        assert _ex("Arrecadação de CFEM bate recorde em setembro", "ANM")[1] != "institutional_admin"

    def test_termo_economico(self):
        assert _ex("Setor mineral fatura R$ 70 bilhões no trimestre, diz IBRAM", "IBRAM")[1] != "institutional_admin"
        assert _ex("Exportações minerais brasileiras crescem 12% no ano", "IBRAM")[1] != "institutional_admin"

    def test_produto_coberto_e_porto(self):
        assert _ex("Movimentação de minério de ferro nos portos cresce 8% no semestre", "ANTAQ")[1] != "institutional_admin"
        assert _ex("Terminal de celulose do Porto de Santos amplia capacidade", "ANTAQ")[1] != "institutional_admin"

    def test_player_da_industria(self):
        assert _ex("BHP apresenta plano de expansão à agência", "ANM")[1] != "institutional_admin"

    def test_palavra_fraca_segura_fora_de_evento(self):
        assert _ex("Preparação para Super El Niño mobiliza setor mineral para prevenir riscos climáticos", "ANM")[1] != "institutional_admin"
        assert _ex("Investimentos portuários crescem 15% no ano, aponta ANTAQ", "ANTAQ")[1] != "institutional_admin"

    def test_palavra_forte_segura_mesmo_em_evento(self):
        # prêmio é evento, mas "barragens" é termo forte → fica
        assert _ex("ANM conquista prêmio internacional de excelência em segurança de barragens", "ANM")[1] != "institutional_admin"

    def test_greve_e_interdicao(self):
        assert _ex("Greve de servidores paralisa atendimento da agência", "ANM")[1] != "institutional_admin"
        assert _ex("Agência interdita mina por risco de rompimento", "ANM")[1] != "institutional_admin"


class TestEscopo:
    def test_outra_fonte_nao_e_tocada(self):
        # o mesmo texto vindo de fonte setorial não ganha esse motivo
        assert _ex("Nota à imprensa – eleição 2026", "Siderurgia Brasil")[1] != "institutional_admin"
        assert _ex("ANTAQ participa de encontro internacional", "Valor Econômico")[1] != "institutional_admin"

    def test_chave_de_desligar(self, monkeypatch):
        monkeypatch.setenv("INSTITUTIONAL_FILTER", "0")
        assert _ex("Nota à imprensa – eleição 2026", "IBRAM")[1] != "institutional_admin"
        monkeypatch.setenv("INSTITUTIONAL_FILTER", "1")
        assert _ex("Nota à imprensa – eleição 2026", "IBRAM")[1] == "institutional_admin"

    def test_ponta_a_ponta_classify_article_take(self):
        fora = classify_article_take({"title": "Nota à imprensa – eleição 2026", "snippet": "", "source_name": "IBRAM"})
        assert fora["include_in_report"] is False and fora["exclusion_reason"] == "institutional_admin"
        fica = classify_article_take({"title": "ANM suspende licença de operação da Vale em Itabira", "snippet": "", "source_name": "ANM"})
        assert fica["include_in_report"] is True
