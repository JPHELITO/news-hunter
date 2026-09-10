# -*- coding: utf-8 -*-
"""Preços da Platts — a captura na grid e as travas antes de publicar.

NASCEU DE UM ERRO REAL (09/09/2026). O cartão do IODEX 61% mostrou 100,60 dizendo que
era o assessment de ontem, com a variação em branco. O número certo era 99,05 — 100,60
era o de anteontem. O estrago vazou para `commodities.daily` e para a curva.

A cadeia era esta:
  1. o scraper vasculhava TODA resposta JSON da página atrás dos símbolos. As matérias
     da Platts carregam o assessment do dia em que foram escritas → o buffer terminava
     com números velhos, sem data e sem variação;
  2. a leitura da grid (que traz preço + variação + data juntos) sobrescrevia esse lixo
     — quando renderizava. Ela falha de vez em quando: medido 1 de 2 execuções seguidas;
  3. quando falhava, o lixo ia para o banco. Como não tinha data, o `assessed_at`
     anterior ficava de pé: data de ontem, preço de anteontem, variação nula.

Este arquivo tranca as três pontas: a varredura não volta, linha sem data não vira
preço, e data velha não sobrescreve preço novo.
"""
import pytest

from hunter import platts_scraper as ps
from hunter import prices as pr


# ── 1. a varredura de rede não pode voltar ───────────────────────────────────
def test_nao_existe_mais_captura_de_preco_fora_da_grid():
    """Trava contra 'restaurar o fallback': era ele que trazia preço de matéria."""
    assert not hasattr(ps, "_extract_prices"), (
        "preço só pode entrar pelo DOM da watchlist, onde preço, variação e data "
        "saem da mesma linha"
    )
    fonte = ps.__file__.replace(".pyc", ".py")
    with open(fonte, encoding="utf-8") as f:
        codigo = f.read()
    assert "_extract_prices(data" not in codigo


# ── 2. leitura da grid: preço sem data não é preço ───────────────────────────
def _linha(price="99.05", change="-1.55", assessed="09-Sep-2026", desc="IODEX 61%"):
    return {"price": price, "change": change, "assessed": assessed, "desc": desc}


def test_linha_completa_sai_com_preco_variacao_e_data_juntos():
    out, descartadas = ps._entradas_da_grid({"IODBZ00": _linha()})
    assert descartadas == []
    assert out["IODBZ00"] == {"price": 99.05, "assessed_at": "2026-09-09",
                              "change_pct": -1.55, "desc": "IODEX 61%"}


@pytest.mark.parametrize("assessed", ["", None, "   ", "n/a", "--"])
def test_linha_sem_data_legivel_e_descartada(assessed):
    """O CASO DE 09/09: preço bom, data ilegível → nada entra."""
    out, descartadas = ps._entradas_da_grid({"IODBZ00": _linha(assessed=assessed)})
    assert out == {}
    assert descartadas and "IODBZ00" in descartadas[0]


def test_uma_linha_furada_nao_derruba_as_outras():
    out, descartadas = ps._entradas_da_grid({
        "IODBZ00": _linha(assessed=""),                 # furada
        "IOPBQ00": _linha(price="98.55", change="-1.25"),
    })
    assert set(out) == {"IOPBQ00"} and len(descartadas) == 1


def test_variacao_em_branco_e_permitida_se_a_data_veio():
    """Grid pode mostrar a variação vazia de verdade; aí o campo fica ausente,
    e o cartão mostra o tracinho com honestidade — a data comprova que é do dia."""
    out, _ = ps._entradas_da_grid({"IODBZ00": _linha(change="")})
    assert out["IODBZ00"]["price"] == 99.05
    assert "change_pct" not in out["IODBZ00"]
    assert out["IODBZ00"]["assessed_at"] == "2026-09-09"


def test_preco_ilegivel_nao_vira_zero_nem_texto():
    out, _ = ps._entradas_da_grid({"IODBZ00": _linha(price="")})
    assert out == {}


def test_virgula_decimal_e_milhar_no_preco():
    out, _ = ps._entradas_da_grid({"STCBM00": _linha(price="1.234,56", change="0,5")})
    assert out["STCBM00"]["price"] == 1234.56
    assert out["STCBM00"]["change_pct"] == 0.5


# ── 3. publicação: as duas travas ────────────────────────────────────────────
@pytest.fixture
def publicado(monkeypatch):
    """Captura o que iria para a tabela, sem tocar no banco."""
    feitos = []
    monkeypatch.setattr(pr, "_supa_upsert", lambda t, rows: feitos.append((t, rows)) or len(rows))
    return feitos


def _guardado(monkeypatch, mapa):
    monkeypatch.setattr(pr, "_assessed_at_guardado", lambda codes: mapa)


def _por_code(feitos):
    return {r["code"]: r for _, rows in feitos for r in rows}


def test_captura_completa_e_publicada_inteira(publicado, monkeypatch):
    _guardado(monkeypatch, {"IRON_ORE": "2026-09-08"})
    pr.update_platts_commodities({"IODBZ00": {"price": 99.05, "change_pct": -1.55,
                                              "assessed_at": "2026-09-09"}})
    r = _por_code(publicado)["IRON_ORE"]
    assert (r["price"], r["change_pct"], r["assessed_at"]) == (99.05, -1.55, "2026-09-09")


def test_preco_sem_data_nao_e_publicado(publicado, monkeypatch):
    """O ERRO DE 09/09 EM UMA LINHA: 100,60 sem data em cima de um 09/09 guardado.

    Antes isto gravava o preço e deixava a data anterior de pé — 'ontem' com o número
    de anteontem. Agora não grava nada e o valor bom continua na tela.
    """
    _guardado(monkeypatch, {"IRON_ORE": "2026-09-09"})
    pr.update_platts_commodities({"IODBZ00": {"price": 100.6}})
    assert _por_code(publicado) == {}


def test_data_mais_velha_que_a_guardada_e_recusada(publicado, monkeypatch):
    _guardado(monkeypatch, {"IRON_ORE": "2026-09-09"})
    pr.update_platts_commodities({"IODBZ00": {"price": 100.6, "change_pct": 0.55,
                                              "assessed_at": "2026-09-08"}})
    assert _por_code(publicado) == {}


def test_revisao_no_mesmo_dia_passa(publicado, monkeypatch):
    """A Platts revisa assessment dentro do dia; isso é legítimo e tem de entrar."""
    _guardado(monkeypatch, {"IRON_ORE": "2026-09-09"})
    pr.update_platts_commodities({"IODBZ00": {"price": 99.10, "change_pct": -1.50,
                                              "assessed_at": "2026-09-09"}})
    assert _por_code(publicado)["IRON_ORE"]["price"] == 99.10


def test_simbolo_que_nao_veio_nao_apaga_o_que_esta_la(publicado, monkeypatch):
    """Não capturado = pulado. Nunca gravar linha vazia por cima do valor bom."""
    _guardado(monkeypatch, {})
    pr.update_platts_commodities({"IODBZ00": {"price": 99.05, "assessed_at": "2026-09-09"}})
    assert set(_por_code(publicado)) == {"IRON_ORE"}


def test_uma_recusa_nao_impede_as_outras(publicado, monkeypatch):
    _guardado(monkeypatch, {"IRON_ORE": "2026-09-09", "IOPBQ00": "2026-09-08"})
    pr.update_platts_commodities({
        "IODBZ00": {"price": 100.6},                                        # sem data
        "IOPBQ00": {"price": 97.35, "change_pct": -1.22, "assessed_at": "2026-09-10"},
    })
    assert set(_por_code(publicado)) == {"IOPBQ00"}


def test_banco_ilegivel_nao_congela_a_publicacao(publicado, monkeypatch):
    """Se a leitura do assessed_at guardado falhar, a trava da DATA cai — mas a
    trava do 'sem data não grava' continua de pé. Degradar, nunca parar."""
    _guardado(monkeypatch, {})
    pr.update_platts_commodities({
        "IODBZ00": {"price": 99.05, "change_pct": -1.55, "assessed_at": "2026-09-09"},
        "IOPBQ00": {"price": 98.55},
    })
    assert set(_por_code(publicado)) == {"IRON_ORE"}


# ── 4. o assessment sobe ANTES das séries que o copiam ───────────────────────
def test_platts_e_publicado_antes_de_commodity_history():
    """`commodities.daily` e o risquinho COPIAM a linha da commodity. Rodando antes do
    Platts, copiavam o estado da rodada anterior — foi assim que o ponto de 09/09 ficou
    com preço de dois dias antes mesmo depois de o preço certo entrar sete minutos depois.
    """
    from pathlib import Path
    fonte = Path(ps.__file__).resolve().parents[1] / "hunt.py"
    codigo = fonte.read_text(encoding="utf-8")
    ordem = [codigo.index('"platts_commodities"'),
             codigo.index('"commodity_history"'),
             codigo.index('"commodity_spark"')]
    assert ordem == sorted(ordem), "publicar o preço primeiro, copiar depois"
    # e o 62% do TE reescreve a série inteira: tem de vir DEPOIS da acumulação
    assert codigo.index('"iron_ore_62_te"') > codigo.index('"commodity_history"')
