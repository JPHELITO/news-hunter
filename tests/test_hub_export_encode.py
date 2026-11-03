"""
Codificação dos arquivos do hub (hub_export/encode.py).

O que estes testes TRAVAM, e por quê:
  1) bytes determinísticos — o mesmo conteúdo sai byte a byte igual (JSON compacto, UTF-8 cru,
     na ordem do construtor, começando por schemaVersion/generatedAt) e o gzip também (`mtime=0`):
     sem isso o "só envia o que mudou" não teria como funcionar entre execuções;
  2) o hash ignora `generatedAt`/`updatedAt` em QUALQUER nível — o carimbo anda todo ciclo com o
     preço parado, e isso não pode virar um envio a cada 5 min;
  3) datas sempre `AAAA-MM-DDTHH:MM:SSZ`, venham do banco (`…12.95015+00:00`) ou do `_now_iso()`;
  4) número que não é número finito vira None (NaN nem é JSON: quebraria o hub inteiro), e o
     zero legítimo continua zero.
"""
from __future__ import annotations

import gzip
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hub_export import encode as E  # noqa: E402


def test_json_compacto_utf8_na_ordem_do_construtor():
    obj = {"schemaVersion": 1, "generatedAt": "2026-11-03T18:05:12Z", "nome": "Celulose São Paulo",
           "x": [1, 2.5, None]}
    b = E.to_json_bytes(obj)
    assert b == ('{"schemaVersion":1,"generatedAt":"2026-11-03T18:05:12Z",'
                 '"nome":"Celulose São Paulo","x":[1,2.5,null]}').encode("utf-8")
    assert E.to_json_bytes(dict(obj)) == b                      # determinístico


def test_nan_nao_sai_como_json_invalido():
    with pytest.raises(ValueError):
        E.to_json_bytes({"p": float("nan")})


def test_gzip_deterministico_e_reversivel():
    data = E.to_json_bytes({"a": list(range(500))})
    g1, g2 = E.gzip_bytes(data), E.gzip_bytes(data)
    assert g1 == g2
    assert g1[:2] == b"\x1f\x8b" and g1[4:8] == b"\x00\x00\x00\x00"   # mtime zerado no cabeçalho
    assert gzip.decompress(g1) == data
    assert E.maybe_gunzip(g1) == data and E.maybe_gunzip(data) == data


def test_hash_ignora_carimbos_em_qualquer_nivel():
    a = {"schemaVersion": 1, "generatedAt": "2026-11-03T18:00:00Z",
         "quotes": {"updatedAt": "2026-11-03T18:00:00Z",
                    "items": [{"ticker": "VALE3.SA", "price": 61.2, "updatedAt": "x"}]},
         "series": {"VALE3.SA": {"updatedAt": "y", "pts": [[1, 2.0]]}}}
    b = {"schemaVersion": 1, "generatedAt": "2026-11-03T18:05:00Z",
         "quotes": {"updatedAt": "2026-11-03T18:05:00Z",
                    "items": [{"ticker": "VALE3.SA", "price": 61.2, "updatedAt": "z"}]},
         "series": {"VALE3.SA": {"updatedAt": "w", "pts": [[1, 2.0]]}}}
    assert E.content_hash(a) == E.content_hash(b)
    b["quotes"]["items"][0]["price"] = 61.3
    assert E.content_hash(a) != E.content_hash(b)


def test_hash_nao_depende_da_ordem_das_chaves_mas_depende_da_ordem_das_listas():
    assert E.content_hash({"a": 1, "b": 2}) == E.content_hash({"b": 2, "a": 1})
    assert E.content_hash({"l": [1, 2]}) != E.content_hash({"l": [2, 1]})


def test_hash_sobrevive_a_ida_e_volta_pelo_json():
    """A partida a frio compara o hash do que foi PUBLICADO (relido do JSON) com o montado agora."""
    import json
    obj = {"v": [0.1, 1e-05, -0.0, 128450.12, 2380.5, 7], "s": "ã"}
    assert E.content_hash(json.loads(E.to_json_bytes(obj))) == E.content_hash(obj)


@pytest.mark.parametrize("raw, esperado", [
    ("2026-11-03T17:39:11.95015+00:00", "2026-11-03T17:39:11Z"),
    ("2026-11-03T17:39:11.263223+00:00", "2026-11-03T17:39:11Z"),
    ("2026-11-03T17:39:11Z", "2026-11-03T17:39:11Z"),
    ("2026-11-03T17:39:11", "2026-11-03T17:39:11Z"),
    ("2026-11-03 14:39:11-03:00", "2026-11-03T17:39:11Z"),
    ("2026-11-03T14:39:11.5-0300", "2026-11-03T17:39:11Z"),
    ("2026-11-03T17:39:11.123456789+00:00", "2026-11-03T17:39:11Z"),
    (datetime(2026, 11, 3, 14, 39, 11, 999999, tzinfo=timezone(timedelta(hours=-3))),
     "2026-11-03T17:39:11Z"),
    (None, None), ("", None), ("ontem", None), (True, None), ("2026-13-40T00:00:00Z", None),
])
def test_iso_utc(raw, esperado):
    assert E.iso_utc(raw) == esperado


def test_now_iso_do_robo_vira_o_formato_do_contrato():
    from hunter.prices import _now_iso
    out = E.iso_utc(_now_iso())
    assert out is not None and len(out) == 20 and out.endswith("Z")


def test_iso_date():
    assert E.iso_date("2026-11-02") == "2026-11-02"
    assert E.iso_date("2026-11-02T23:00:00+00:00") == "2026-11-02"
    assert E.iso_date("02/11/2026") is None
    assert E.iso_date(None) is None and E.iso_date("2026-02-30") is None


def test_num_epoch_session_points():
    assert E.num(0) == 0 and E.num(0.0) == 0.0 and E.num(-1.5) == -1.5
    assert E.num(None) is None and E.num("1.2") is None and E.num(True) is None
    assert E.num(math.inf) is None and E.num(float("nan")) is None
    assert E.epoch_s(1793729041) == 1793729041 and E.epoch_s(1793729041.9) == 1793729041
    assert E.epoch_s(0) is None and E.epoch_s(-5) is None and E.epoch_s("1") is None
    assert E.session_pair([1, 2]) == [1, 2]
    assert E.session_pair([2, 1]) is None and E.session_pair([1, 1]) is None
    assert E.session_pair([1]) is None and E.session_pair(None) is None
    assert E.session_pair([0, 5]) is None and E.session_pair(["a", 5]) is None
    assert E.points([[1, 2.5], [2, None], [3, float("nan")], "x", [4, 5, 6], [5, 0]]) == \
        [[1, 2.5], [5, 0]]
    assert E.points(None) == []
