"""
Testes do filtro de ingestão (hunter/filter.py).

Foco: fronteira de palavra (não casar keyword dentro de outra palavra) e
regra de fontes gerais (exigem keyword no título).

Rodar: python -m pytest tests/test_filter.py -v
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from hunter.fetcher import RawArticle
from hunter.filter import filter_articles, _keyword_pattern

_NOW = datetime.now(timezone.utc)


def _mk(title, snippet="", source="Mining.com"):
    return RawArticle(
        url=f"http://x/{hash((title, snippet, source))}",
        domain="x.com", source_name=source, title=title, snippet=snippet,
        published_at=_NOW, found_at=_NOW, needs_filter=True,
    )


def _passes(title, snippet="", source="Mining.com"):
    return len(filter_articles([_mk(title, snippet, source)])) == 1


class TestWordBoundary:
    def test_aura_not_in_restaurante(self):
        """'aura' não deve casar dentro de 'restaurante'."""
        assert not _passes("Assistir aos jogos da Copa em bares e restaurantes",
                           "futebol", "Folha de S.Paulo")

    def test_app_not_in_application(self):
        assert not _passes("New banking application launches today", "fintech app", "Exame")

    def test_keyword_pattern_excludes_substring(self):
        pat = _keyword_pattern()
        assert not pat.search("restaurante")        # contém 'aura'
        assert pat.search("aura minerals expansion") # 'aura' isolado casa


class TestLegitimateStillPass:
    def test_vale_mining(self):
        assert _passes("Vale eleva producao de minerio de ferro", "recorde", "Mining.com")

    def test_gerdau_steel(self):
        assert _passes("Gerdau anuncia expansao de capacidade de aco", "nova usina", "Money Times")

    def test_hrc_demand(self):
        assert _passes("China HRC prices rise on steel demand", "asia", "SMM")

    def test_suzano_pulp(self):
        assert _passes("Suzano eleva preco da celulose", "BHKP", "Valor Econômico")


class TestBroadSourcesRequireTitleKeyword:
    def test_general_source_no_title_keyword_blocked(self):
        """Fonte geral (UOL) sem keyword no título → bloqueada mesmo com kw no snippet."""
        assert not _passes("Shakira atuara na inauguracao do Mundial", "minerio de ferro", "UOL Economia")

    def test_general_source_with_title_keyword_passes(self):
        assert _passes("Vale bate recorde de minerio", "trimestre", "Metrópoles")

    def test_specialized_source_snippet_match_ok(self):
        """Fonte especializada (Mining.com) pode casar só no snippet."""
        assert _passes("Quarterly results beat expectations", "iron ore output up", "Mining.com")


class TestBlocklist:
    def test_crypto_title_blocked(self):
        assert not _passes("Bitcoin sobe e Vale acompanha rali", "minerio", "InfoMoney")

    def test_soccer_title_blocked(self):
        assert not _passes("Futebol: final da Copa movimenta mercado de aço", "steel", "G1")
