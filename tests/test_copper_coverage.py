"""Cobre na cobertura formal (2026-11-23): Hudbay (HBM.TO), Lundin Mining (LUN.TO), Capstone Copper (CS.TO)
e Ero Copper (ERO). Manchetes REAIS do banco (news_articles, 90 dias até 23/11) + as armadilhas de nome."""
from hunter.news_take_classifier import (
    COVERED_COMPANY_ALIASES, _MINING_COMPANIES, detect_covered_companies, classify_take,
)
from hunter.filter import _match_text
from hunter.classify import classify
from hunter.digest import COV
from hunter.reuters_scraper import _RELEVANT_KW
from hub_export.exporters import news_reference as R

NOVAS = {"HUDBAY", "LUNDIN MINING", "CAPSTONE COPPER", "ERO COPPER"}


def cov(t):
    return set(detect_covered_companies(t))


class TestManchetesReais:
    def test_hudbay(self):
        assert "HUDBAY" in cov("Hudbay cuts cost outlook as Copper Mountain improves")
        assert "HUDBAY" in cov("Hudbay, Mitsubishi approve Copper World construction")
        assert "HUDBAY" in cov("Hudbay lifts Snow Lake gold output estimate by 60%")

    def test_lundin(self):
        assert "LUNDIN MINING" in cov("Lundin tem forte 3T e recorde em Chapada")
        assert "LUNDIN MINING" in cov("Lundin cuts copper outlook again as wild Chile weather bites")
        assert "LUNDIN MINING" in cov("MPF aciona Lundin e ANM por resíduos de Chapada")
        assert "LUNDIN MINING" in cov("Lundin Mining investirá US$ 110 milhões no projeto Saúva e expansão de Chapada")

    def test_capstone(self):
        assert "CAPSTONE COPPER" in cov("Capstone cuts copper outlook as restart slips")
        assert "CAPSTONE COPPER" in cov("Capstone to invest $3B in Chile to double copper production")
        assert "CAPSTONE COPPER" in cov("Capstone Copper to sell Mexican mine to Luca Mining for up to $385M")

    def test_ero(self):
        assert "ERO COPPER" in cov("Projeto com Vale no Pará pode quase dobrar operação da Ero Copper")
        assert {"ERO COPPER", "VALE"} <= cov("Projeto com Vale no Pará pode quase dobrar operação da Ero Copper")
        assert "ERO COPPER" in cov("Ero tem produção recorde de cobre em Tucumã")
        assert "ERO COPPER" in cov("TSX: ERO reports third quarter results")


class TestAtivosQualificados:
    def test_ativos_inequivocos(self):
        assert "CAPSTONE COPPER" in cov("Mantoverde expansion hits nameplate throughput")
        assert "CAPSTONE COPPER" in cov("Pinto Valley mine life extended to 2039")
        assert "LUNDIN MINING" in cov("Caserones output falls after tailings issue")
        assert "LUNDIN MINING" in cov("BHP and partner advance Josemaria in Argentina's San Juan")
        assert "LUNDIN MINING" in cov("Filo del Sol resource grows again")
        assert "ERO COPPER" in cov("Mineração Caraíba amplia mina na Bahia")
        assert "HUDBAY" in cov("Peru protesters block road to Constancia mine")

    def test_lugar_bicho_e_palavra_comum_nao_contam(self):
        assert not cov("Chapada Diamantina recebe recorde de turistas") & NOVAS
        assert not cov("Formiga saúva ameaça plantios de eucalipto em MS") & NOVAS
        assert not cov("Vicuña population recovers in the Andes") & NOVAS
        assert not cov("Santo Domingo hosts Caribbean mining summit") & NOVAS
        assert not cov("Tucumã recebe investimento em saúde") & NOVAS
        assert not cov("Snow Lake Resources files lithium study") & NOVAS
        assert not cov("Copper Mountain ski resort opens early") & NOVAS
        assert not cov("Constância nos embarques de minério sustenta preço") & NOVAS


class TestNomesAmbiguos:
    def test_lundin_gold_e_familia_nao_sao_a_lundin_mining(self):
        assert "LUNDIN MINING" not in cov("Lundin Gold posts record Fruta del Norte output")
        assert "LUNDIN MINING" not in cov("Lukas Lundin's NGEx finds more copper in Argentina")
        assert "LUNDIN MINING" not in cov("Lundin Group backs new Argentine explorer")
        assert "LUNDIN MINING" not in cov("Lundin Energy merges with Aker BP")
        # o CEO da Lundin Mining é Jack Lundin → conta
        assert "LUNDIN MINING" in cov("Jack Lundin says Vicuña decision due next year")

    def test_capstone_palavra_comum_nao_conta(self):
        assert "CAPSTONE COPPER" not in cov("the deal is the capstone of a decade of consolidation")
        assert "CAPSTONE COPPER" not in cov("Capstone Green Energy wins microturbine order")
        assert "CAPSTONE COPPER" not in cov("Capstone Infrastructure sells wind assets")
        assert "CAPSTONE COPPER" not in cov("The Capstone of Chile's mining reform")

    def test_ero_so_como_nome_proprio(self):
        assert "ERO COPPER" not in cov("zero carbon copper is the new premium")
        assert "ERO COPPER" not in cov("ero ero")
        assert "ERO COPPER" not in cov("Part ERO-2 recall")


class TestCadastro:
    def test_as_quatro_sao_mineracao_e_a_cobertura_tem_20(self):
        assert NOVAS <= _MINING_COMPANIES
        assert NOVAS <= set(COVERED_COMPANY_ALIASES)
        assert len(COVERED_COMPANY_ALIASES) == 20

    def test_setor_e_take_de_empresa(self):
        r = classify_take("Capstone cuts copper outlook as restart slips", {"source_name": "Mining.com"})
        assert "CAPSTONE COPPER" in r["covered_companies_mentioned"]
        assert r["include_in_report"] is True
        assert r["sector"] in ("copper", "mining", "steel_mining")

    def test_resumo_da_hora(self):
        assert COV["HUDBAY"] == "HBM" and COV["LUNDIN MINING"] == "LUN"
        assert COV["CAPSTONE COPPER"] == "CS" and COV["ERO COPPER"] == "ERO"
        assert len(COV) == 20

    def test_hub_mesmo_mapa_do_site(self):
        m = {c: (t, q, d) for c, t, q, d in R.PULSE_CO}
        assert m["HUDBAY"] == ("HBM", "HBM.TO", "sm")
        assert m["LUNDIN MINING"] == ("LUN", "LUN.TO", "sm")
        assert m["CAPSTONE COPPER"] == ("CS", "CS.TO", "sm")
        assert m["ERO COPPER"] == ("ERO", "ERO", "sm")
        # o resumo da hora e o hub falam a mesma língua
        assert {c: t for c, (t, _q, _d) in m.items()} == COV


class TestFiltroDeEntrada:
    def test_nomes_passam_em_fonte_geral(self):
        assert _match_text("Hudbay eleva guidance de ouro")
        assert _match_text("Lundin tem forte 3T e recorde em Chapada")
        assert _match_text("Capstone to invest $3B in Chile")
        assert _match_text("Ero conclui rampa de Tucumã")
        assert _match_text("Mantoverde atinge capacidade")

    def test_minusculo_ambiguo_nao_passa(self):
        assert not _match_text("the capstone of the reform")
        assert not _match_text("ero")

    def test_ticker_map(self):
        assert "HBM.TO" in classify("Hudbay Minerals beats estimates")["tickers"]
        assert "LUN.TO" in classify("Lundin Mining sells stake")["tickers"]
        assert "CS.TO" in classify("Capstone Copper raises capex")["tickers"]
        assert "ERO" in classify("Ero Copper hits record")["tickers"]
        assert classify("Hudbay Minerals beats estimates")["sector"] == "mining"

    def test_reuters_slug(self):
        assert _RELEVANT_KW.search("/markets/commodities/hudbay-lifts-gold-output-2026-11-12/")
        assert _RELEVANT_KW.search("/business/lundin-mining-names-new-cfo-2026-11-12/")
