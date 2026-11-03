"""
O ponto de chamada do exportador do hub no hunt.py.

O hunt-loop baixa o master a cada ciclo de 5 min: este bloco entra no ar sozinho, e é por isso
que ele tem de ser inofensivo por construção. O que estes testes TRAVAM:
  1) com HUB_EXPORT_ENABLED desligado (o padrão no hunt-loop.yml) o pacote hub_export NEM É
     IMPORTADO — zero risco para o ciclo de notícias enquanto o canário não liga a variável;
  2) ligado, o exportador roda DEPOIS do bloco de preços (é de lá que vem a memória do ciclo) e
     antes do "done";
  3) o modo --playwright (hunt-playwright, 30 min) nunca exporta, mesmo com a variável ligada;
  4) qualquer exceção do exportador vira uma linha de aviso e o ciclo termina normalmente.

O hunt.main de verdade é executado, com os módulos do robô trocados por falsos (nada de rede).
"""
from __future__ import annotations

import logging
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PRICE_FNS = ("update_quotes", "update_commodities", "update_macro", "update_platts_commodities",
             "update_quote_history", "update_commodity_history", "update_iron_ore_62_te",
             "update_commodity_spark", "update_fastmarkets_commodities", "update_sina_commodities")


@pytest.fixture
def robo(monkeypatch):
    """Troca cada módulo que o hunt.main importa por um falso que só anota a chamada."""
    ordem: list[str] = []

    def mod(name, **fns):
        m = types.ModuleType(name)
        for fn, ret in fns.items():
            setattr(m, fn, (lambda _fn=fn, _ret=ret: (lambda *a, **k: (ordem.append(_fn), _ret)[1]))())
        monkeypatch.setitem(sys.modules, name, m)

    mod("hunter.fetcher", fetch_all=[])
    mod("hunter.filter", filter_articles=[])
    mod("hunter.sync", push_articles=0, push_take_fields=0, record_run=True, take_cache_path=None,
        record_source_health=True)
    mod("hunter.oauth_refresh", keep_alive=None)
    mod("hunter.classify", classify_article_dict=None)
    mod("hunter.cvm_filings", apply_cvm_sector=0)
    mod("hunter.news_take_classifier", classify_article_take=None)
    mod("hunter.llm_shadow", run_llm_shadow=None)
    mod("hunter.digest", run_digest=None)
    mod("hunter.prices", **{fn: 0 for fn in PRICE_FNS})
    mod("hunter.platts_scraper", collect_platts_headlines=[], get_platts_prices={},
        get_platts_health={})
    mod("hunter.fastmarkets_scraper", collect_fastmarkets_headlines=[],
        get_fastmarkets_prices={}, get_fastmarkets_health={})
    # o hub_export "de verdade" fora do sys.modules: dá para ver se o hunt.py o importa
    for name in [n for n in sys.modules if n == "hub_export" or n.startswith("hub_export.")]:
        monkeypatch.delitem(sys.modules, name)
    import hunt
    return hunt, ordem


def _main(hunt, monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["hunt.py", *argv])
    hunt.main()


def _exportador_falso(monkeypatch, ordem, erro=None):
    m = types.ModuleType("hub_export.run")

    def run_cycle(**kw):
        ordem.append("run_cycle")
        if erro:
            raise erro
        return []
    m.run_cycle = run_cycle
    monkeypatch.setitem(sys.modules, "hub_export.run", m)


def test_desligado_o_pacote_nem_e_importado(robo, monkeypatch):
    hunt, ordem = robo
    monkeypatch.delenv("HUB_EXPORT_ENABLED", raising=False)
    _main(hunt, monkeypatch)
    assert "update_quotes" in ordem                               # o ciclo rodou inteiro
    assert not [n for n in sys.modules if n == "hub_export" or n.startswith("hub_export.")]
    for valor in ("0", "", "true", "yes", " 1"):
        monkeypatch.setenv("HUB_EXPORT_ENABLED", valor)
        _main(hunt, monkeypatch)
        assert "hub_export.run" not in sys.modules, valor


def test_ligado_roda_depois_do_bloco_de_precos(robo, monkeypatch):
    hunt, ordem = robo
    monkeypatch.setenv("HUB_EXPORT_ENABLED", "1")
    _exportador_falso(monkeypatch, ordem)
    _main(hunt, monkeypatch)
    assert ordem.count("run_cycle") == 1
    assert ordem.index("run_cycle") > max(ordem.index(fn) for fn in
                                          ("update_quotes", "update_macro", "update_commodity_spark"))


def test_modo_playwright_nunca_exporta(robo, monkeypatch):
    hunt, ordem = robo
    monkeypatch.setenv("HUB_EXPORT_ENABLED", "1")
    _exportador_falso(monkeypatch, ordem)
    _main(hunt, monkeypatch, "--playwright")
    assert "collect_platts_headlines" in ordem and "run_cycle" not in ordem


def test_excecao_do_exportador_nao_derruba_o_ciclo(robo, monkeypatch, caplog):
    hunt, ordem = robo
    monkeypatch.setenv("HUB_EXPORT_ENABLED", "1")
    _exportador_falso(monkeypatch, ordem, erro=RuntimeError("Feeder caiu"))
    with caplog.at_level(logging.INFO):
        _main(hunt, monkeypatch)
    assert "hub_export: falhou (não-fatal): Feeder caiu" in caplog.text
    assert "=== News Hunter done ===" in caplog.text
