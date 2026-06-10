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

    def test_pig_iron_now_product_included(self):
        """Pig iron saiu da exclusão de baixo valor: é produto vendido (export BR)
        → entra no relatório (gabarito 8.639 mostrou pig iron com take)."""
        exclude, reason = should_exclude_news(
            "Pig iron prices move in Brazil",
            {},
        )
        assert exclude is False

    # ── "mundial" (=global em PT) NÃO é off-topic; só futebol explícito é ──
    def test_producao_mundial_de_aco_nao_excluida(self):
        exclude, _ = should_exclude_news(
            "Produção mundial de aço bruto cai 2% em maio", {})
        assert exclude is False

    def test_copa_do_mundo_excluida(self):
        exclude, _ = should_exclude_news("Copa do Mundo movimenta o turismo no país", {})
        assert exclude is True

    def test_selecao_brasileira_excluida(self):
        exclude, _ = should_exclude_news("Seleção brasileira goleia em amistoso preparatório", {})
        assert exclude is True

    def test_partida_de_usina_nao_excluida(self):
        # "partida" (startup de alto-forno) não é mais tratada como futebol
        exclude, _ = should_exclude_news(
            "Gerdau conclui partida do novo alto-forno e eleva produção de aço", {})
        assert exclude is False


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

    def test_4_scrap_subject_neutral_brazil(self):
        """Scrap como SUJEITO fora dos EUA → neutro. Gabarito de 8.639: scrap é
        ~58-73% '='; a inversão global tinha 43% de erro. Só inverte nos EUA."""
        r = classify_take("Scrap prices fall in Brazil", {})
        assert r["include_in_report"] is True
        assert r["sector"] == "steel_mining"
        assert "scrap" in r["normalized_topics"]
        assert r["take"] == "="

    def test_4b_scrap_us_inverts(self):
        """Scrap doméstico dos EUA ainda inverte como custo (queda → +)."""
        r = classify_take("US ferrous scrap prices fall", {})
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
        """Gerdau announces capacity expansion → NEUTRO (empresa coberta).
        Gabarito 8.639: coberta+expansão é 89% '=' (evento estratégico de longo
        prazo, não sinal de preço diário). Antes era 'review' (sempre erro no eval)."""
        r = classify_take("Gerdau announces capacity expansion", {})
        assert r["include_in_report"] is True
        assert "GERDAU" in r["covered_companies_mentioned"]
        assert r["take"] == "="

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

    def test_turkish_rebar_up_positive(self):
        """Rebar turco é PRODUTO vendido no mercado global: preço em alta → +
        (gabarito 13/13). A inversão antiga (up→-) tinha 68-84% de erro."""
        r = classify_take(
            "Turkish rebar export prices rise strongly",
            {},
        )
        assert r["take"] == "+"

    def test_suzano_no_auto_negative_capacity(self):
        """Suzano expansão: regra de oferta de terceiro NÃO aplica (é coberta) →
        neutro (89% do gabarito), nunca negativo."""
        r = classify_take("Suzano announces major capacity expansion in Brazil", {})
        assert "SUZANO" in r["covered_companies_mentioned"]
        assert r["take"] == "="

    def test_demand_down_negative(self):
        # Região-foco (China) preserva o sinal direcional da demanda.
        r = classify_take("Steel demand falls in China amid weak construction", {})
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

    def test_occ_up_neutral(self):
        """OCC em ALTA é ambíguo no gabarito (39%+/25%-), não '-': removido
        occ_up_neg (tinha 62% de erro)."""
        r = classify_take("OCC prices rise sharply in Europe", {})
        assert r["take"] == "="

    def test_occ_down_positive(self):
        """OCC em QUEDA → alívio de custo de aparas → + (lado limpo mantido)."""
        r = classify_take("OCC prices drop sharply", {})
        assert r["take"] == "+"

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
# Regras de negócio Platts (imagem de regras) — slab neutro, ORES de baixo valor,
# company specifics neutro, neutralização regional.
# ─────────────────────────────────────────────────────────────────────────────
class TestPlattsBusinessRules:

    # ── Slab = NEUTRO ─────────────────────────────────────────────────────────
    def test_slab_is_neutral(self):
        r = classify_take("Steel slab prices rise", {})
        assert r["include_in_report"] is True
        assert "slab" in r["normalized_topics"]
        assert r["take"] == "="

    # ── ORES de baixo valor como assunto primário → excluídos ─────────────────
    def test_billet_primary_excluded(self):
        r = classify_take("Billet prices rise in Brazil", {})
        assert r["include_in_report"] is False
        assert r["exclusion_reason"] == "low_relevance"

    def test_plate_primary_excluded(self):
        r = classify_take("Heavy plate prices fall in Asia", {})
        assert r["include_in_report"] is False
        assert r["exclusion_reason"] == "low_relevance"

    def test_wire_rod_now_product_included(self):
        """Wire rod é produto (segue rebar) → incluído, não mais excluído."""
        r = classify_take("Wire rod production rises", {})
        assert r["include_in_report"] is True

    def test_pig_iron_with_inventories_included(self):
        """Pig iron é produto agora → incluído (estoque subindo → negativo)."""
        r = classify_take("Pig iron inventories rise in Brazil", {})
        assert r["include_in_report"] is True

    def test_billet_secondary_to_hrc_included(self):
        """Billet secundário a HRC → notícia entra (pelo HRC)."""
        r = classify_take("China HRC and billet prices rise strongly", {})
        assert r["include_in_report"] is True
        assert r["take"] == "+"

    # ── Company Specifics = NEUTRO (antes era "review") ───────────────────────
    def test_company_specific_generic_is_neutral(self):
        r = classify_take("Usiminas reports quarterly production numbers", {})
        assert r["include_in_report"] is True
        assert "USIMINAS" in r["covered_companies_mentioned"]
        assert r["take"] == "="

    # ── Neutralização regional: Europa / outras regiões sem empresa coberta ───
    def test_europe_steel_neutralized(self):
        r = classify_take("HRC prices rise in Germany", {})
        assert r["include_in_report"] is True
        assert r["take"] == "="
        assert "region_neutral" in r["matched_rules"]

    def test_rest_of_world_neutralized(self):
        r = classify_take("HRC prices rise in Mexico", {})
        assert r["include_in_report"] is True
        assert r["take"] == "="
        assert "region_neutral" in r["matched_rules"]

    def test_turkish_rebar_not_neutralized(self):
        """Turkish rebar é exceção: preserva o sinal direcional."""
        r = classify_take("Turkish rebar prices rise", {})
        assert r["take"] == "+"
        assert "region_neutral" not in r["matched_rules"]

    def test_china_not_neutralized(self):
        """Região-foco (China) preserva sinal."""
        r = classify_take("HRC prices rise in China", {})
        assert r["take"] == "+"
        assert "region_neutral" not in r["matched_rules"]


# ─────────────────────────────────────────────────────────────────────────────
# Aprendizados do gabarito (PDFs de takes manuais) — regras derivadas da análise
# de divergências. Travam o comportamento independentemente dos PDFs.
# ─────────────────────────────────────────────────────────────────────────────
class TestGabaritoLearnings:

    # ── Tópico "mining" genérico: macro de mineração entra no relatório ───────
    def test_mining_macro_included(self):
        r = classify_take("Brazil mining sector posts higher Q1 revenue", {})
        assert r["include_in_report"] is True

    def test_mineral_demand_included(self):
        r = classify_take("India rises as China slows in mineral demand shift", {})
        assert r["include_in_report"] is True

    # ── Palavras de direção que faltavam (formas no passado / termos de mercado)
    def test_utilization_increased_past_tense(self):
        r = classify_take("US steel capability utilization increased to 79.1% on the week", {})
        assert r["take"] == "+"

    def test_iron_ore_dip_negative(self):
        """'dip' agora conta como queda (antes virava '+' por 'firm')."""
        r = classify_take("Asian iron ore prices dip despite firm liquidity", {})
        assert r["take"] == "-"

    def test_bullish_positive(self):
        r = classify_take("China HRC prices bullish on restocking demand", {})
        assert r["take"] == "+"

    # ── Marcador de estabilidade → neutro (sinal fraco) ───────────────────────
    def test_neutral_marker_overrides_weak_signal(self):
        r = classify_take("China HRC market stable; prices rise modestly", {})
        assert r["take"] == "="
        assert "neutral_marker" in r["matched_rules"]

    def test_mixed_market_neutral(self):
        r = classify_take("Asian HRC prices mixed as yuan lifts some quotes", {})
        assert r["take"] == "="

    # ── "flat steel" é PRODUTO, não marcador de estabilidade ──────────────────
    def test_flat_steel_not_neutralized(self):
        r = classify_take("Brazilian flat steel market prices rise by up to Real 300/mt", {})
        assert r["take"] == "+"
        assert "neutral_marker" not in r["matched_rules"]

    # ── Movimento quantificado ($/t) prevalece sobre 'flat' de outro grade ────
    def test_quantified_move_beats_neutral_marker(self):
        r = classify_take("Pulp prices increase by $50/t in Asia; other grade pricing flat", {})
        assert r["take"] == "+"

    # ── Players da indústria (não cobertos) entram no relatório ───────────────
    def test_industry_player_mining_included(self):
        r = classify_take("Fortescue spends more on green energy, keeps shipment forecast steady", {})
        assert r["include_in_report"] is True
        assert r["sector"] == "steel_mining"

    def test_industry_player_pp_included(self):
        r = classify_take("Fire destroys Kimberly-Clark distribution center in California", {})
        assert r["include_in_report"] is True
        assert r["sector"] == "pulp_paper"

    def test_industry_player_not_in_covered(self):
        """Player de indústria NÃO entra em covered_companies (sem take company-specific)."""
        r = classify_take("BHP loses bid to appeal Brazil dam disaster ruling", {})
        assert r["include_in_report"] is True
        assert "BHP" not in r["covered_companies_mentioned"]

    # ── Notícia de tarifa/comércio entra (tariffs no setor steel) ─────────────
    def test_tariff_news_included(self):
        r = classify_take("Metals industry backs new US tariff actions, but clash on targets", {})
        assert r["include_in_report"] is True
        assert r["sector"] == "steel_mining"

    def test_antidumping_included(self):
        r = classify_take("UK recommends extending antidumping duties on welded pipes from China", {})
        assert r["include_in_report"] is True

    # ── Vale resgatada por contexto operacional (mines/operations) ────────────
    def test_vale_operational_context_detected(self):
        r = classify_take("Vale expects to resume operations at Fabrica, Viga mines in few weeks", {})
        assert "VALE" in r["covered_companies_mentioned"]
        assert r["include_in_report"] is True

    # ── P&P: anúncio de aumento de preço por produtor → "+" ───────────────────
    def test_producer_raises_prices_positive(self):
        r = classify_take("UPM raises woodfree paper prices by 6% in North America", {})
        assert r["take"] == "+"

    def test_price_hike_positive(self):
        r = classify_take("APP announces price hike of $50 per tonne for industrial white board", {})
        assert r["take"] == "+"

    def test_papers_plural_topic(self):
        """'papers' (plural) deve mapear para o tópico paper (antes só 'paper')."""
        r = classify_take("Suzano sets 10% price increase for coated papers in North America", {})
        assert "paper" in r["normalized_topics"]
        assert r["take"] == "+"

    # ── 'stocks' (plural) = inventários → queda é bullish ─────────────────────
    def test_stocks_plural_inventories(self):
        r = classify_take("Stocks of woodpulp at European ports slide in March", {})
        assert "inventories" in r["normalized_topics"]
        assert r["take"] == "+"

    # ── Política/indústria siderúrgica (sem produto) entra como neutra ────────
    def test_steel_industry_policy_included(self):
        r = classify_take("South Korea approves law to boost, decarbonize steel industry", {})
        assert r["include_in_report"] is True
        assert r["sector"] == "steel_mining"

    def test_steel_sector_origin_rules_included(self):
        r = classify_take("European steel industry demands stricter origin rules in EU act", {})
        assert r["include_in_report"] is True


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

    def test_texas_tx_with_steel_context_not_ternium(self):
        """'TX' (Texas) MESMO com contexto setorial de aço NÃO é TERNIUM.

        O contexto siderúrgico (steel/capacity/demand) está sempre presente em
        notícia de aço dos EUA e não desambigua 'TX' = Texas vs ticker Ternium.
        """
        assert "TERNIUM" not in detect_covered_companies(
            "Steel mill opens in Houston TX with new capacity")
        assert "TERNIUM" not in detect_covered_companies(
            "New steel plant in Dallas, TX boosts US capacity")

    def test_ternium_spelled_out_still_detected(self):
        """Notícia real de Ternium (nome soletrado) CONTINUA detectada."""
        assert "TERNIUM" in detect_covered_companies(
            "Ternium reports higher steel shipments in Mexico")

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
# Direção ancorada ao tópico — frases multi-tópico com direções opostas
# ─────────────────────────────────────────────────────────────────────────────
class TestTopicAnchoredDirection:

    def test_iron_down_metcoal_up_double_negative(self):
        """Minério cai + met coal sobe = dois sinais negativos = '-'."""
        r = classify_take("Iron ore prices fell while met coal costs rose", {})
        assert r["take"] == "-"

    def test_iron_up_metcoal_down_double_positive(self):
        r = classify_take("Iron ore prices rose while met coal costs fell", {})
        assert r["take"] == "+"

    def test_causal_subordinate_ignored_metcoal(self):
        """'as supply increases' é causa, não sinal: só met coal cai conta."""
        r = classify_take("Met coal prices fall sharply as supply increases", {})
        assert r["take"] == "+"

    def test_causal_subordinate_ignored_met_coal(self):
        """Subordinada causal 'driven by strong demand' é ignorada: só o met coal
        (custo) conta → '-'. (Antes usava scrap, que agora é neutro fora dos EUA.)"""
        r = classify_take("Met coal prices surge driven by strong demand", {})
        assert r["take"] == "-"

    def test_genuine_conflict_neutral(self):
        """HRC sobe (+) vs met coal custo sobe (-) = conflito real = '='."""
        r = classify_take("HRC prices rise but met coal costs jump", {})
        assert r["take"] == "="

    def test_demand_recovers_causal(self):
        r = classify_take("China steel demand recovers as iron ore inventories fall", {})
        assert r["take"] == "+"

    def test_utilization_eases_negative(self):
        r = classify_take("US steel capability utilization eases for third week", {})
        assert r["take"] == "-"


# ─────────────────────────────────────────────────────────────────────────────
# Setores copper / gold dedicados (spec)
# ─────────────────────────────────────────────────────────────────────────────
class TestCopperGoldSectors:

    def test_copper_sector(self):
        r = classify_take("Copper prices rise on Chinese demand and supply tightness", {})
        assert r["sector"] == "copper"

    def test_gold_sector(self):
        r = classify_take("Gold prices climb to record on safe-haven demand", {})
        assert r["sector"] == "gold"

    def test_iron_ore_not_copper_sector(self):
        """Minério de ferro continua steel_mining, não copper."""
        r = classify_take("Iron ore prices rise in China", {})
        assert r["sector"] == "steel_mining"


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


class TestGabarito8639Batch:
    """Comportamentos travados pelo gabarito de 8.639 manchetes (2026-06-10):
    macro com take, aço de baixo valor como produto, restart de oferta, fluxo P&P."""

    def test_macro_china_negative(self):
        r = classify_take("China property investment contracts further in November", {})
        assert r["include_in_report"] is True
        assert r["take"] == "-"

    def test_macro_china_positive(self):
        r = classify_take("Beijing rolls out fresh stimulus package for the economy", {})
        assert r["include_in_report"] is True
        assert r["take"] == "+"

    def test_macro_offset_neutral(self):
        r = classify_take("China economic recovery may lose steam, outlook uncertain", {})
        assert r["include_in_report"] is True
        assert r["take"] == "="

    def test_wire_rod_product_direction(self):
        # wire rod é produto (não mais excluído); região-foco BR não neutraliza
        r = classify_take("Brazilian wire rod prices fall", {})
        assert r["include_in_report"] is True
        assert r["take"] == "-"

    def test_restart_adds_supply_negative(self):
        r = classify_take("Stora Enso restarts pulp mill after maintenance", {})
        assert r["take"] == "-"

    def test_box_shipments_fall_negative(self):
        r = classify_take("US box shipments fall 5% in first quarter", {})
        assert r["include_in_report"] is True
        assert r["take"] == "-"

    def test_decarbonization_included(self):
        # política/decarbonização de aço entra (não mais excluída por falta de tópico)
        exclude, _ = should_exclude_news(
            "Steel decarbonization standards create uncertainty, WTO says", {})
        assert exclude is False

    def test_personal_finance_excluded(self):
        """Clickbait de finanças pessoais/consumo (entrava via snippet 'economia')."""
        for t in ["Com novas regras, vale a pena investir nos CDBs que rendem mais?",
                  "Melhores investimentos de renda fixa para 2026",
                  "Financiamento de carro fica mais caro com nova Selic"]:
            r = classify_take(t, {})
            assert r["include_in_report"] is False, t

    def test_business_consortium_kept(self):
        """Consórcio EMPRESARIAL (infra/logística) não é finanças pessoais → entra."""
        r = classify_take("Consórcio da K-Infra vence disputa por Rota da Celulose", {})
        assert r["include_in_report"] is True

    def test_negation_demand_not_reduced(self):
        """'have NOT reduced demand' → demanda resiliente → + (bug do feed Vale/iron ore)."""
        r = classify_take("Vale: Iran tensions have not reduced iron ore demand", {})
        assert r["take"] == "+"

    def test_negation_prices_not_declined(self):
        r = classify_take("Iron ore prices have not declined this week", {})
        assert r["take"] == "+"

    def test_pt_press_prices_up(self):
        """Imprensa BR: conjugações ('preços sobem/ganham') agora direcionais → +."""
        r = classify_take("MINÉRIO DE FERRO: preços sobem apoiados em fundamentos", {})
        assert r["take"] == "+"

    def test_pt_press_prices_down(self):
        r = classify_take("Minério de ferro: preços recuam com demanda fraca na China", {})
        assert r["take"] == "-"
