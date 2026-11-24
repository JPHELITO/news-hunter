"""
Cache dos corpos do clipping (`clipping_bodies`) — a tradução boa nunca é apagada.

24/11/2026: a tradução da matéria do Valor (feita às 06:30) sumiu às 13:01. Uma leitura do cache
FALHOU (rede da nuvem lenta) → o robô achou que não havia corpo guardado → raspou de novo → a
tradução falhou (todos os tradutores lotados) → `store_body` gravou "" por cima (o upsert grava
toda coluna que vier no pedido).
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clipping import bodies  # noqa: E402

ENV = {"SUPABASE_URL": "https://s", "SUPABASE_SERVICE_KEY": "k"}


def _gravado(**kw):
    with mock.patch.dict("os.environ", ENV), mock.patch("requests.post") as post:
        bodies.store_body("https://valor.globo.com/x", "Título", "Valor", "<p>corpo</p>", **kw)
    return post.call_args.kwargs["json"][0]


def test_sem_traducao_o_pedido_nao_leva_as_colunas_de_traducao():
    row = _gravado()
    assert "translated_title" not in row and "translated_body" not in row
    assert row["body"] == "<p>corpo</p>" and row["char_len"] == len("<p>corpo</p>")


def test_traducao_vazia_tambem_nao_vai():
    row = _gravado(translated_title="  ", translated_body="")
    assert "translated_title" not in row and "translated_body" not in row


def test_com_traducao_ela_vai():
    row = _gravado(translated_title="Title", translated_body="<p>body</p>")
    assert row["translated_title"] == "Title" and row["translated_body"] == "<p>body</p>"


def _resp(rows):
    r = mock.Mock(); r.ok = True; r.json.return_value = rows
    return r


def test_leitura_que_falha_tenta_de_novo_antes_de_dizer_que_nao_tem():
    import requests
    boa = _resp([{"body": "<p>x</p>", "translated_title": "T", "translated_body": "", "char_len": 500}])
    with mock.patch.dict("os.environ", ENV), \
         mock.patch("requests.get", side_effect=[requests.exceptions.ReadTimeout(), boa]) as get:
        row = bodies.get_stored_body("https://valor.globo.com/x")
    assert get.call_count == 2 and row["translated_title"] == "T"


def test_respondeu_que_nao_tem_nao_repete():
    with mock.patch.dict("os.environ", ENV), mock.patch("requests.get", return_value=_resp([])) as get:
        assert bodies.get_stored_body("https://valor.globo.com/x") is None
    assert get.call_count == 1
