# -*- coding: utf-8 -*-
"""Aclara (ARA.TO) e Viridis (VMM.AX) na cobertura formal — 2026-10-23.

As duas são desenvolvedoras pré-produção de terras raras (argila iônica). O robô precisa:
(1) reconhecê-las como COBERTAS (prioridade na fila da IA, ficha do Market Pulse, ticker na notícia);
(2) ter um tópico `rare_earths` (tema "Rare earths" da Home) que puxe o setor para mineração;
(3) deixar passar no filtro de keyword a manchete com o nome delas mesmo sem "terras raras";
(4) mapear os tickers (ARA.TO / VMM.AX) e os tickers de tela do resumo da hora (ARA / VMM).

Rodar: python -m pytest tests/test_rare_earths_coverage.py -v
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from hunter.news_take_classifier import (
    COVERED_COMPANY_ALIASES, INDUSTRY_PLAYERS, _MINING_COMPANIES, _MINING_TOPICS,
    _classify_sector, detect_covered_companies, detect_industry_players, detect_topics,
)
from hunter.classify import classify, TICKER_MAP
from hunter.filter import _strong_pattern
from hunter.digest import COV, ALL_TICKERS


def _kw(title: str) -> bool:
    """Só a parte de keyword do filtro (fonte geral, filter=True)."""
    return bool(_strong_pattern().search(title))


class TestCobertas:
    def test_aclara_manchete_real(self):
        assert detect_covered_companies("Aclara conclui engenharia básica de planta de terras raras") == ["ACLARA"]

    def test_aclara_custo_refinaria(self):
        assert "ACLARA" in detect_covered_companies("Aclara revisa para R$ 2,5 bi custo de refinaria de terras raras nos EUA")

    def test_viridis_manchete_real(self):
        t = "Australiana Viridis avança em projeto extrair terras raras em MG, mas enfrenta resistência de moradores"
        assert detect_covered_companies(t) == ["VIRIDIS"]

    def test_viridis_nome_completo_e_ticker_asx(self):
        assert "VIRIDIS" in detect_covered_companies("Viridis Mining and Minerals (ASX: VMM) reports Colossus drilling results")
        assert "VIRIDIS" in detect_covered_companies("Drilling update from ASX: VMM at Poços de Caldas")

    def test_projetos_so_na_forma_qualificada(self):
        assert "VIRIDIS" in detect_covered_companies("Projeto Colossus recebe licença prévia em Poços de Caldas")
        assert "ACLARA" in detect_covered_companies("Módulo Carina tem estudo de viabilidade concluído")
        # "colossus" sozinho é outra coisa (supercomputador da xAI); "carina" é nome de gente
        assert detect_covered_companies("xAI expands Colossus supercomputer in Memphis") == []
        assert detect_covered_companies("Carina assume a diretoria financeira da mineradora") == []

    def test_aclara_verbo_em_espanhol_nao_conta(self):
        assert "ACLARA" not in detect_covered_companies("Codelco aclara que no venderá activos en Chile")
        assert "ACLARA" not in detect_covered_companies("Aclara que la empresa no participará de la subasta")

    def test_aclara_minusculo_so_com_nome_completo(self):
        assert "ACLARA" not in detect_covered_companies("la minera aclara su posición")
        assert "ACLARA" in detect_covered_companies("aclara resources inc announces offtake")

    def test_as_duas_sao_mineracao_e_a_cobertura_tem_16(self):
        assert {"ACLARA", "VIRIDIS"} <= _MINING_COMPANIES
        assert set(COVERED_COMPANY_ALIASES) >= {"ACLARA", "VIRIDIS"}
        assert len(COVERED_COMPANY_ALIASES) == 16


class TestTopicoTerrasRaras:
    def test_topico_existe_e_e_mineracao(self):
        assert "rare_earths" in _MINING_TOPICS

    def test_detecta_pt_en_e_oxidos(self):
        for t in ("Terras raras atraem 18 empresas estrangeiras", "US firms eye Malaysia rare earths",
                  "NdPr oxide prices climb in China", "Preço do neodímio sobe", "ionic clay project in Minas Gerais",
                  "Argila iônica: a nova fronteira", "rare-earth magnet plant in New York"):
            assert "rare_earths" in detect_topics(t), t

    def test_nao_detecta_sem_o_termo(self):
        assert "rare_earths" not in detect_topics("Iron ore prices rise in Qingdao")

    def test_setor_vai_para_mineracao(self):
        assert _classify_sector(["rare_earths"], []) == "steel_mining"
        assert _classify_sector([], ["VIRIDIS"]) == "steel_mining"

    def test_pares_nao_cobertos_sao_players(self):
        assert detect_industry_players("Lynas plans Brazil rare earths plant with state support") == {"lynas": "steel_mining"}
        assert "serra verde" in detect_industry_players("Australiana compra projeto próximo de mina da Serra Verde")
        for name in ("lynas", "mp materials", "serra verde", "brazilian rare earths", "meteoric resources"):
            assert INDUSTRY_PLAYERS[name] == "steel_mining"
        # players NÃO viram cobertas
        assert detect_covered_companies("Lynas plans Brazil rare earths plant") == []


class TestFiltroEKeywords:
    def test_nome_passa_sem_terras_raras_no_titulo(self):
        assert _kw("Aclara fecha acordo de offtake nos EUA")
        assert _kw("Viridis closes A$30m placement")

    def test_plural_rare_earths_passa(self):
        assert _kw("US firms eye Malaysia rare earths in push to cut China reliance")
        assert _kw("NdPr prices rise as magnet makers restock")

    def test_pares_passam(self):
        assert _kw("Lynas plans Brazil plant with state support")
        assert _kw("MP Materials signs DoD deal")


class TestTickers:
    def test_ticker_map(self):
        assert TICKER_MAP["Aclara"] == "ARA.TO" and TICKER_MAP["Viridis"] == "VMM.AX"
        assert classify("Aclara conclui engenharia básica de planta de terras raras", "")["tickers"] == ["ARA.TO"]
        assert classify("Viridis avança em projeto em MG", "")["tickers"] == ["VMM.AX"]

    def test_setor_coluna_mining(self):
        assert classify("Aclara revisa custo de refinaria de terras raras", "")["sector"] == "mining"

    def test_resumo_da_hora(self):
        assert COV["ACLARA"] == "ARA" and COV["VIRIDIS"] == "VMM"
        assert {"ARA", "VMM"} <= ALL_TICKERS
        assert len(COV) == 16
