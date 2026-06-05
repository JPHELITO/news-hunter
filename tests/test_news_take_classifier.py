"""
Testes unitários para hunter/news_take_classifier.py

Rodar com:
    python -m pytest tests/test_news_take_classifier.py -v
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from hunter.news_take_classifier import (
    normalize_text,
    detect_covered_companies,
    detect_region,
    detect_topics,
    should_exclude_news,
    classify_take,
)


# ─────────────────────────────────────────────────────────────────────────────
# normalize_text
# ─────────────────────────────────────────────────────────────────────────────
class TestNormalizeText:
    def test_lowercase(self):
        assert normalize_text("HRC Prices") == "hrc prices"

    def test_removes_accents(self):
        result = normalize_text("celulose")
        assert "celulose" in result
        result2 = normalize_text("aço")
        assert "aco" in result2

    def test_empty(self):
        assert normalize_text("") == ""

    def test_multiple_spaces(self):
        assert normalize_text("iron  ore  prices") == "iron ore prices"

    def test_punctuation_removed(self):
        result = normalize_text("prices: up! +5%")
        assert ":" not in result
        assert "!" not in result


# ─────────────────────────────────────────────────────────────────────────────
# detect_covered_companies
# ─────────────────────────────────────────────────────────────────────────────
class TestDetectCoveredCompanies:
    def test_gerdau(self):
        assert "GERDAU" in detect_covered_companies("Gerdau announces capacity expansion")

    def test_ggbr4(self):
        assert "GERDAU" in detect_covered_companies("GGBR4 shares rise")

    def test_vale(self):
        assert "VALE" in detect_covered_companies("Vale iron ore production hits record")

    def test_csn_mineracao(self):
        assert "CMIN" in detect_covered_companies("CSN Mineracao reports strong results")

    def test_suzano(self):
        assert "SUZANO" in detect_covered_companies("Suzano pulp prices rise in Q3")

    def test_arauco_copec(self):
        assert "COPEC" in detect_covered_companies("Arauco announces new pulp mill")

    def test_no_false_positive(self):
        result = detect_covered_companies("Steel market in China recovers")
        assert result == []

    def test_multiple_companies(self):
        result = detect_covered_companies("Gerdau and Vale post strong quarterly results")
        assert "GERDAU" in result
        assert "VALE" in result


# ─────────────────────────────────────────────────────────────────────────────
# detect_region
# ─────────────────────────────────────────────────────────────────────────────
class TestDetectRegion:
    def test_china(self):
        assert detect_region("China HRC prices rise amid stronger demand") == "china_asia"

    def test_us(self):
        assert detect_region("US steel capability utilization falls") == "us"

    def test_brazil(self):
        assert detect_region("Scrap prices fall in Brazil") == "brazil"

    def test_europe(self):
        assert detect_region("Pulp prices rise in Europe") == "europe"

    def test_turkey(self):
        assert detect_region("Turkish rebar exports rise") == "rest_of_world"

    def test_unknown(self):
        assert detect_region("Inventories rise for producers") == "unknown"


# ─────────────────────────────────────────────────────────────────────────────
# detect_topics
# ─────────────────────────────────────────────────────────────────────────────
class TestDetectTopics:
    def test_hrc_demand(self):
        topics = detect_topics("China HRC prices rise amid stronger demand")
        assert "hrc" in topics
        assert "demand" in topics

    def test_utilization(self):
        topics = detect_topics("US steel capability utilization falls")
        assert "utilization" in topics

    def test_met_coal(self):
        topics = detect_topics("Met coal prices increase in China")
        assert "met_coal" in topics
        assert "prices" in topics

    def test_scrap(self):
        topics = detect_topics("Scrap prices fall in Brazil")
        assert "scrap" in topics
        assert "prices" in topics

    def test_pulp_capacity(self):
        topics = detect_topics("New pulp capacity starts in China")
        assert "pulp" in topics
        assert "capacity" in topics

    def test_occ(self):
        topics = detect_topics("OCC prices decline sharply")
        assert "occ" in topics
        assert "prices" in topics

    def test_inventories(self):
        topics = detect_topics("Inventories rise for pulp producers")
        assert "inventories" in topics


# ─────────────────────────────────────────────────────────────────────────────
# should_exclude_news
# ─────────────────────────────────────────────────────────────────────────────
class TestShouldExcludeNews:
    def test_rationale_excluded(self):
        exclude, reason = should_exclude_news(
            "Platts steel rationale for HRC prices",
            {"source_name": "S&P Platts"},
        )
        assert exclude is True
        assert reason == "rationale_news"

    def test_rationale_in_text(self):
        exclude, reason = should_exclude_news(
            "Iron ore price rationale: IODEX assessment",
            {},
        )
        assert exclude is True
        assert reason == "rationale_news"

    def test_relevant_not_excluded(self):
        exclude, _ = should_exclude_news(
            "China HRC prices rise amid stronger demand",
            {},
        )
        assert exclude is False

    def test_no_topics_excluded(self):
        exclude, reason = should_exclude_news(
            "Some completely irrelevant news article",
            {},
        )
        assert exclude is True

    def test_pig_iron_only_excluded(self):
        exclude, reason = should_exclude_news(
            "Pig iron prices move in Brazil",
            {},
        )
        assert exclude is True
        assert reason == "irrelevant_commodity"


# ─────────────────────────────────────────────────────────────────────────────
# classify_take — 10 casos obrigatórios do enunciado
# ─────────────────────────────────────────────────────────────────────────────
class TestClassifyTakeRequiredCases:

    def test_1_china_hrc_demand_up(self):
        """China HRC prices rise amid stronger demand → + steel_mining"""
        r = classify_take("China HRC prices rise amid stronger demand", {})
        assert r["include_in_report"] is True
        assert r["sector"] == "steel_mining"
        assert "hrc" in r["normalized_topics"]
        assert "demand" in r["normalized_topics"]
        assert r["take"] == "+"

    def test_2_us_utilization_falls(self):
        """US steel capability utilization falls → -"""
        r = classify_take("US steel capability utilization falls", {})
        assert r["include_in_report"] is True
        assert r["sector"] == "steel_mining"
        assert "utilization" in r["normalized_topics"]
        assert r["take"] == "-"

    def test_3_met_coal_up_negative(self):
        """Met coal prices increase in China → - (pressão de custo)"""
        r = classify_take("Met coal prices increase in China", {})
        assert r["include_in_report"] is True
        assert r["sector"] == "steel_mining"
        assert "met_coal" in r["normalized_topics"]
        assert r["take"] == "-"

    def test_4_scrap_fall_positive(self):
        """Scrap prices fall in Brazil → +"""
        r = classify_take("Scrap prices fall in Brazil", {})
        assert r["include_in_report"] is True
        assert r["sector"] == "steel_mining"
        assert "scrap" in r["normalized_topics"]
        assert r["take"] == "+"

    def test_5_new_pulp_capacity_third_party(self):
        """New pulp capacity starts in China by non-covered producer → -"""
        r = classify_take(
            "New pulp capacity starts in China by non-covered producer",
            {},
        )
        assert r["include_in_report"] is True
        assert r["sector"] == "pulp_paper"
        assert "capacity" in r["normalized_topics"]
        assert r["take"] == "-"

    def test_6_pulp_prices_rise_europe(self):
        """Pulp prices rise in Europe → +"""
        r = classify_take("Pulp prices rise in Europe", {})
        assert r["include_in_report"] is True
        assert r["sector"] == "pulp_paper"
        assert "pulp" in r["normalized_topics"]
        assert r["take"] == "+"

    def test_7_occ_decline_positive(self):
        """OCC prices decline → +"""
        r = classify_take("OCC prices decline", {})
        assert r["include_in_report"] is True
        assert r["sector"] == "pulp_paper"
        assert "occ" in r["normalized_topics"]
        assert r["take"] == "+"

    def test_8_inventories_rise_negative(self):
        """Inventories rise for pulp producers → -"""
        r = classify_take("Inventories rise for pulp producers", {})
        assert r["include_in_report"] is True
        assert r["sector"] == "pulp_paper"
        assert "inventories" in r["normalized_topics"]
        assert r["take"] == "-"

    def test_9_gerdau_capacity_expansion(self):
        """Gerdau announces capacity expansion → review (empresa coberta)"""
        r = classify_take("Gerdau announces capacity expansion", {})
        assert r["include_in_report"] is True
        assert "GERDAU" in r["covered_companies_mentioned"]
        assert r["take"] == "review"

    def test_10_rationale_excluded(self):
        """Platts steel rationale → excluído"""
        r = classify_take("Platts steel rationale", {})
        assert r["include_in_report"] is False
        assert r["exclusion_reason"] == "rationale_news"


# ─────────────────────────────────────────────────────────────────────────────
# Casos adicionais
# ─────────────────────────────────────────────────────────────────────────────
class TestClassifyTakeAdditional:

    def test_iron_ore_price_up_positive(self):
        r = classify_take("Iron ore prices rise in China amid steel demand recovery", {})
        assert r["take"] == "+"

    def test_met_coal_falls_positive(self):
        r = classify_take("Met coal prices fall sharply as supply increases", {})
        assert r["take"] == "+"

    def test_pulp_price_down_negative(self):
        r = classify_take("Pulp prices decline in European market", {})
        assert r["take"] == "-"

    def test_capacity_cut_third_party_positive(self):
        r = classify_take(
            "Major steelmaker closes blast furnace reducing capacity",
            {},
        )
        assert r["take"] == "+"

    def test_turkish_rebar_exports_up_negative(self):
        r = classify_take(
            "Turkish rebar exports rise strongly to global markets",
            {},
        )
        assert r["take"] == "-"

    def test_suzano_no_auto_negative_capacity(self):
        """Suzano expansão: regra de oferta de terceiro NÃO aplica."""
        r = classify_take("Suzano announces major capacity expansion in Brazil", {})
        assert "SUZANO" in r["covered_companies_mentioned"]
        assert r["take"] == "review"

    def test_demand_down_negative(self):
        r = classify_take("Steel demand falls in Europe amid weak construction", {})
        assert r["take"] == "-"

    def test_inventories_down_positive(self):
        r = classify_take("Iron ore inventories decline at Chinese ports", {})
        assert r["take"] == "+"

    def test_vale_generic_no_direction(self):
        """Vale mencionada mas sem sinal direcional claro."""
        r = classify_take("Vale reports quarterly production numbers", {})
        assert r["include_in_report"] is True
        assert "VALE" in r["covered_companies_mentioned"]
        assert r["take"] in ("review", "=", "+", "-")

    def test_aisi_utilization_up(self):
        r = classify_take("AISI reports US steel capacity utilization up 2 points", {})
        assert r["take"] == "+"

    def test_csn_mineracao_alias(self):
        r = classify_take("CSN Mineracao posts record iron ore shipments", {})
        assert "CMIN" in r["covered_companies_mentioned"]

    def test_klabin_pulp(self):
        r = classify_take("Klabin pulp production increases strongly in Q2", {})
        assert "KLABIN" in r["covered_companies_mentioned"]
        assert r["sector"] in ("pulp_paper", "company_specific", "steel_mining")

    def test_european_pp_no_pulp_price_excluded(self):
        """Notícia europeia de P&P sem preço de celulose → excluída."""
        r = classify_take(
            "European paper demand weakens in first quarter amid lower consumption",
            {},
        )
        assert r["include_in_report"] is False
        assert r["exclusion_reason"] == "too_specific_europe"

    def test_european_pp_with_pix_included(self):
        """Notícia europeia de P&P com PIX → incluída."""
        r = classify_take(
            "PIX pulp prices decline in European market BHKP assessment",
            {},
        )
        assert r["include_in_report"] is True

    def test_scrap_up_negative(self):
        r = classify_take("Scrap prices surge in US market driven by strong demand", {})
        assert r["take"] == "-"

    def test_occ_up_negative(self):
        r = classify_take("OCC prices rise sharply in Europe", {})
        assert r["take"] == "-"

    def test_confidence_range(self):
        """Confidence deve estar entre 0 e 1."""
        cases = [
            "China HRC prices rise amid stronger demand",
            "US steel capability utilization falls",
            "Gerdau announces capacity expansion",
            "OCC prices decline",
            "Platts steel rationale",
        ]
        for text in cases:
            r = classify_take(text, {})
            assert 0.0 <= r["confidence"] <= 1.0, f"Confidence fora de range: {r['confidence']} para '{text}'"

    def test_matched_rules_not_empty_when_included(self):
        r = classify_take("China HRC prices rise amid stronger demand", {})
        if r["include_in_report"]:
            assert len(r["matched_rules"]) > 0


# ─────────────────────────────────────────────────────────────────────────────
# Aliases ambíguos — falsos positivos ('vale' = verbo PT, etc.)
# ─────────────────────────────────────────────────────────────────────────────
class TestAmbiguousAliasFalsePositives:

    def test_vale_verb_not_detected(self):
        """'vale a pena' (verbo) NÃO deve detectar a empresa VALE."""
        assert detect_covered_companies("Esse investimento vale a pena?") == []

    def test_crypto_vale_excluded(self):
        """Cripto que usa 'vale' como verbo → excluído, sem VALE."""
        r = classify_take("Hyperliquid (HYPE) disparou 200% e ainda vale a pena", {})
        assert "VALE" not in r["covered_companies_mentioned"]
        assert r["include_in_report"] is False

    def test_bitcoin_excluded(self):
        r = classify_take("Bitcoin sobe e analistas dizem que vale comprar", {})
        assert r["include_in_report"] is False
        assert r["exclusion_reason"] == "irrelevant_region"

    def test_soccer_excluded(self):
        r = classify_take("Un Mundial caotico se acerca al silbatazo inicial", {})
        assert r["include_in_report"] is False

    def test_politics_excluded(self):
        r = classify_take("AMLO critica carta sobre elecciones en Mexico", {})
        assert r["include_in_report"] is False

    def test_vale_ticker_still_detected(self):
        """Vale legítimo (ticker) CONTINUA detectado."""
        assert "VALE" in detect_covered_companies("Vale (VALE3) ve demanda forte na China")

    def test_vale_with_mining_context_detected(self):
        """'Vale' + contexto de mineração CONTINUA detectado."""
        assert "VALE" in detect_covered_companies("A nova aposta da Vale: minerio de ferro")

    def test_vale_english_quarterly_detected(self):
        assert "VALE" in detect_covered_companies("Gerdau and Vale post strong quarterly results")

    def test_texas_tx_not_ternium(self):
        """'TX' (Texas) sem contexto NÃO deve virar TERNIUM."""
        assert "TERNIUM" not in detect_covered_companies("Storm hits Texas TX power grid")

    def test_crypto_with_gold_topic_excluded(self):
        """Cripto que menciona 'gold' (tópico) ainda é excluído como off-topic."""
        r = classify_take("Gold-backed stablecoin token launches on blockchain", {})
        assert r["include_in_report"] is False

    def test_crime_city_aluminio_excluded(self):
        """'em Alumínio' (cidade de SP) numa notícia policial → excluído."""
        r = classify_take(
            "Roubo a farmacia tem perseguicao e 3 mortos em tiroteio com a "
            "Policia Militar na Rodovia Raposo Tavares, em Aluminio", {})
        assert r["include_in_report"] is False
        assert r["exclusion_reason"] == "irrelevant_region"

    def test_crime_quadrilha_excluded(self):
        r = classify_take("Quadrilha suspeita de roubar Ozempic em farmacias e alvo de operacao", {})
        assert r["include_in_report"] is False

    def test_legit_aluminum_market_still_included(self):
        """Notícia real de mercado de alumínio CONTINUA incluída."""
        r = classify_take("Aluminium prices rise on smelter cuts in China", {})
        assert r["include_in_report"] is True


# ─────────────────────────────────────────────────────────────────────────────
# Integração: classify_article_take
# ─────────────────────────────────────────────────────────────────────────────
class TestClassifyArticleTake:
    def test_dict_gets_new_fields(self):
        from hunter.news_take_classifier import classify_article_take
        art = {
            "title": "China HRC prices rise amid stronger demand",
            "snippet": "Hot rolled coil markets firm up in Asia",
            "source_name": "Fastmarkets",
        }
        result = classify_article_take(art)
        # Campos originais preservados
        assert result["title"] == art["title"]
        assert result["source_name"] == art["source_name"]
        # Novos campos adicionados
        assert "take" in result
        assert "take_reason" in result
        assert "take_confidence" in result
        assert "take_sector" in result
        assert "take_region" in result
        assert "take_topics" in result
        assert "include_in_report" in result

    def test_topics_serialized_as_string(self):
        from hunter.news_take_classifier import classify_article_take
        art = {"title": "Iron ore prices rise in China", "snippet": ""}
        result = classify_article_take(art)
        assert isinstance(result["take_topics"], str)  # ";" separado
