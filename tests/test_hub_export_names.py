"""
Nome de arquivo por identificador (hub_export/names.py — PLANO, Apêndice F.0), a regra que o hub
repete em `historyFileName`/`mwShortFileName`. O nome vai tal qual numa URL pública e num
SharePoint que não distingue caixa: um nome errado é um arquivo que o hub nunca acha.

O que estes testes TRAVAM, e por quê:
  1) ida e volta nos 55 papéis do universo da Market, sem colisão (dois papéis no mesmo arquivo
     seria um papel mostrando o histórico do outro);
  2) a recusa de `^`, `=`, `@`, `-`, minúscula, `..` e ponto no fim (o `^BVSP` do Yahoo, o `TIO=F`,
     o `@IRON_ORE_62` do front, `BRK-B` — que viraria `brk-b` e voltaria como `BRK_B`);
  3) a volta só aceita o que é exatamente a ida de algum id (nada de "quase");
  4) as famílias: `history-<id>.json` só para papéis do universo (o `history-index.json` não é um
     papel) e `mw-short-<id>.json` para o WP2.4B.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hub_export import history as H  # noqa: E402
from hub_export import names  # noqa: E402
from hub_export.exporters.reference import MARKET_UNIVERSE  # noqa: E402


def test_ida_e_volta_nos_55_sem_colisao():
    assert len(MARKET_UNIVERSE) == 55 and len(set(MARKET_UNIVERSE)) == 55
    fids = [names.file_id(t) for t in MARKET_UNIVERSE]
    assert None not in fids
    assert len(set(fids)) == 55                               # 0 colisões
    for t, fid in zip(MARKET_UNIVERSE, fids):
        assert names.id_of(fid) == t
        assert fid == fid.lower() and "/" not in fid and ".." not in fid
    assert max((names.family_file("history", t) for t in MARKET_UNIVERSE), key=len) == \
        "history-gmexicob.mx.json"                            # o maior nome (pesquisa §5.3)


@pytest.mark.parametrize("ident, fid", [
    ("VALE3.SA", "vale3.sa"), ("KLBN11", "klbn11"), ("SP500_FUT", "sp500-fut"),
    ("IRON_ORE_62", "iron-ore-62"), ("IBOV", "ibov"), ("GMEXICOB.MX", "gmexicob.mx"),
    ("STERV.HE", "sterv.he"), ("B", "b"), ("9988.HK", "9988.hk")])
def test_exemplos_do_contrato(ident, fid):
    assert names.file_id(ident) == fid
    assert names.id_of(fid) == ident


@pytest.mark.parametrize("ruim, motivo", [
    ("^BVSP", "'^'"), ("TIO=F", "'='"), ("@IRON_ORE_62", "'@'"), ("BRK-B", "'-'"),
    ("vale3.sa", "'a'"), ("Vale3.SA", "'a'"), ("A B", "' '"), ("VALE3.SA/X", "'/'"),
    ("A..B", "'..'"), ("VALE3.", "ponto"), (".A", "começar"), ("_A", "começar"),
    ("", "vazio"), (None, "vazio"), (12, "vazio"), ("ÇA", "'Ç'")])
def test_recusa_com_motivo(ruim, motivo):
    assert names.file_id(ruim) is None
    assert names.family_file("history", ruim) is None
    assert motivo in names.id_problem(ruim)


@pytest.mark.parametrize("ruim", ["VALE3.SA", "vale3_sa", "a..b", "x.", ".x", "-x", "", None,
                                  "brk-b", "vale 3"])
def test_volta_so_aceita_o_que_e_ida(ruim):
    # "brk-b" voltaria como BRK_B, cuja ida é "brk-b": é ida legítima de BRK_B (aceita); o resto não
    if ruim == "brk-b":
        assert names.id_of(ruim) == "BRK_B"
    else:
        assert names.id_of(ruim) is None


def test_familias():
    assert names.family_file("history", "VALE3.SA") == "history-vale3.sa.json"
    assert names.family_file("mw-short", "KLBN11") == "mw-short-klbn11.json"
    assert names.family_ident("history", "history-vale3.sa.json") == "VALE3.SA"
    assert names.family_ident("mw-short", "mw-short-klbn11.json") == "KLBN11"
    for ruim in ("history-VALE3.SA.json", "history-vale3.sa", "historyvale3.sa.json",
                 "history-.json", "news-week-2026-w45.json", None):
        assert names.family_ident("history", ruim) is None
    # a família de históricos só reconhece papel do universo: o índice e os 5 de fora não são papéis
    assert H.ticker_from_file("history-vale3.sa.json") == "VALE3.SA"
    assert H.ticker_from_file("history-index.json") is None
    for fora in ("RIO.L", "BHP.AX", "GLEN.L", "MT.AS", "SP500_FUT"):
        assert H.file_name(fora) is None
        assert H.ticker_from_file(names.family_file("history", fora)) is None
