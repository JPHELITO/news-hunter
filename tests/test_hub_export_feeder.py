"""
Cliente do Dashboard Data Feeder (hub_export/feeder.py) — o único caminho até o Mindassets.

O que estes testes TRAVAM, e por quê:
  1) o protocolo de 3 passos do Feeder exatamente como o manual do hub descreve (initialize com
     name/sourceFileName/contentType/sizeBytes/sourceModifiedAt e Bearer; PUT com os cabeçalhos
     que o initialize mandou; complete) — e a revisão devolvida;
  2) falha no PUT avisa o Feeder (`failed`: `storage_rejected` com o status, ou `network_error`)
     e levanta a falha ORIGINAL, mesmo que o próprio aviso falhe;
  3) nova tentativa: no máximo UMA, só em erro de conexão ou 5xx; 4xx nunca repete; sem tempo
     no orçamento, não repete;
  4) timeouts de 20 s na API e 60 s no PUT;
  5) a chave do projeto e a URL assinada (assinatura na query) NUNCA aparecem em log nem em
     mensagem de exceção — o log do Actions é público. As exceções do `requests` trazem a URL
     inteira no texto; é exatamente isso que não pode vazar.
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import test_hub_export_fixtures as F  # noqa: E402
from hub_export.feeder import FeederClient, FeederError, missing_env  # noqa: E402

PAYLOAD = b'{"schemaVersion":1}'


def client(http, **kw):
    return FeederClient(F.FEEDER_URL, F.PROJECT_ID, F.FEEDER_KEY, http=http, retry_wait=0,
                        sleep=lambda s: None, **kw)


def steps(http):
    out = []
    for c in http.feeder_calls():
        if c.method == "PUT":
            out.append("PUT")
        else:
            out.append(c.url.rsplit("/", 1)[1] if c.url.endswith(("/complete", "/failed"))
                       else "initialize")
    return out


def test_caminho_feliz_tres_chamadas_corpo_e_cabecalhos():
    http = F.FakeHTTP()
    rev = client(http).upload("live.json", PAYLOAD, "application/json",
                              modified_at="2026-11-03T18:05:12Z")
    assert rev == 1
    init, put, done = http.feeder_calls()
    assert steps(http) == ["initialize", "PUT", "complete"]
    assert init.method == "POST"
    assert init.url == f"{F.FEEDER_URL}/api/projects/{F.PROJECT_ID}/files/uploads"
    assert init.headers["Authorization"] == f"Bearer {F.FEEDER_KEY}"
    assert init.headers["Content-Type"] == "application/json"
    assert json.loads(init.data) == {"name": "live.json", "sourceFileName": "live.json",
                                     "contentType": "application/json", "sizeBytes": len(PAYLOAD),
                                     "sourceModifiedAt": "2026-11-03T18:05:12Z"}
    assert put.method == "PUT" and put.url.startswith(F.PUT_HOST)
    assert put.headers == {"content-type": "application/json"}      # exatamente os pedidos
    assert "Authorization" not in put.headers                       # a chave não vai ao storage
    assert put.data == PAYLOAD
    assert done.method == "POST"
    assert done.url == f"{F.FEEDER_URL}/api/projects/{F.PROJECT_ID}/files/uploads/upd-1/complete"
    assert done.headers["Authorization"] == f"Bearer {F.FEEDER_KEY}"
    assert http.uploads["live.json"] == PAYLOAD


def test_gzip_declara_application_gzip():
    http = F.FakeHTTP()
    client(http).upload("quotes-daily.json", b"\x1f\x8b...", "application/gzip")
    init, put, _ = http.feeder_calls()
    assert json.loads(init.data)["contentType"] == "application/gzip"
    assert put.headers == {"content-type": "application/gzip"}


def test_timeouts_20s_na_api_e_60s_no_put():
    http = F.FakeHTTP()
    client(http).upload("live.json", PAYLOAD, "application/json")
    init, put, done = http.feeder_calls()
    assert (init.timeout, put.timeout, done.timeout) == (20.0, 60.0, 20.0)


def test_put_recusado_chama_failed_com_o_status_e_levanta():
    http = F.FakeHTTP(feeder_script={"PUT": [403]})
    with pytest.raises(FeederError, match="PUT: HTTP 403"):
        client(http).upload("live.json", PAYLOAD, "application/json")
    assert steps(http) == ["initialize", "PUT", "failed"]           # 4xx não repete; sem complete
    failed = http.feeder_calls()[-1]
    assert failed.url.endswith("/uploads/upd-1/failed")
    assert json.loads(failed.data) == {"reason": "storage_rejected", "storageStatus": 403}


def test_put_5xx_repete_uma_vez():
    http = F.FakeHTTP(feeder_script={"PUT": [503]})
    assert client(http).upload("live.json", PAYLOAD, "application/json") == 1
    assert steps(http) == ["initialize", "PUT", "PUT", "complete"]


def test_put_5xx_duas_vezes_desiste_e_avisa():
    http = F.FakeHTTP(feeder_script={"PUT": [503, 502]})
    with pytest.raises(FeederError, match="HTTP 502"):
        client(http).upload("live.json", PAYLOAD, "application/json")
    assert steps(http) == ["initialize", "PUT", "PUT", "failed"]
    assert json.loads(http.feeder_calls()[-1].data) == {"reason": "storage_rejected",
                                                        "storageStatus": 502}


def test_put_sem_rede_vira_network_error():
    http = F.FakeHTTP(feeder_script={"PUT": [F.connection_error(), F.connection_error()]})
    with pytest.raises(FeederError, match="PUT: ConnectionError"):
        client(http).upload("live.json", PAYLOAD, "application/json")
    assert steps(http) == ["initialize", "PUT", "PUT", "failed"]
    assert json.loads(http.feeder_calls()[-1].data) == {"reason": "network_error"}


def test_falha_do_aviso_nao_esconde_a_falha_original():
    http = F.FakeHTTP(feeder_script={"PUT": [403], "failed": [500, requests.ConnectionError("x")]})
    with pytest.raises(FeederError, match="PUT: HTTP 403"):
        client(http).upload("live.json", PAYLOAD, "application/json")


def test_complete_5xx_repete_uma_vez():
    http = F.FakeHTTP(feeder_script={"complete": [500]})
    assert client(http).upload("live.json", PAYLOAD, "application/json") == 1
    assert steps(http) == ["initialize", "PUT", "complete", "complete"]


def test_complete_4xx_nao_repete():
    http = F.FakeHTTP(feeder_script={"complete": [409]})
    with pytest.raises(FeederError, match="complete: HTTP 409"):
        client(http).upload("live.json", PAYLOAD, "application/json")
    assert steps(http) == ["initialize", "PUT", "complete"]


def test_initialize_sem_rede_repete_uma_vez_e_4xx_nao():
    http = F.FakeHTTP(feeder_script={"initialize": [requests.Timeout("lento")]})
    assert client(http).upload("live.json", PAYLOAD, "application/json") == 1
    assert steps(http) == ["initialize", "initialize", "PUT", "complete"]
    http = F.FakeHTTP(feeder_script={"initialize": [401]})
    with pytest.raises(FeederError, match="initialize: HTTP 401"):
        client(http).upload("live.json", PAYLOAD, "application/json")
    assert steps(http) == ["initialize"]


def test_sem_tempo_no_orcamento_nao_repete():
    relogio = iter([100.0] * 50)
    http = F.FakeHTTP(feeder_script={"initialize": [503]})
    c = FeederClient(F.FEEDER_URL, F.PROJECT_ID, F.FEEDER_KEY, http=http, retry_wait=1.0,
                     sleep=lambda s: None, clock=lambda: next(relogio))
    with pytest.raises(FeederError, match="HTTP 503"):
        c.upload("live.json", PAYLOAD, "application/json", deadline=101.0)   # sobra 1 s
    assert steps(http) == ["initialize"]


def test_orcamento_esgotado_nem_chama():
    http = F.FakeHTTP()
    c = FeederClient(F.FEEDER_URL, F.PROJECT_ID, F.FEEDER_KEY, http=http, clock=lambda: 200.0)
    with pytest.raises(FeederError, match="orçamento"):
        c.upload("live.json", PAYLOAD, "application/json", deadline=100.0)
    assert http.feeder_calls() == []


def test_resposta_sem_update_ou_url_e_erro_claro():
    class Ruim(F.FakeHTTP):
        def _feeder(self, call):
            return F.FakeResp(200, {"update": {}, "upload": {}})
    with pytest.raises(FeederError, match="sem update.id/upload.url"):
        client(Ruim()).upload("live.json", PAYLOAD, "application/json")


def test_a_chave_e_a_url_assinada_nunca_aparecem_em_log_nem_excecao(caplog):
    caplog.set_level(logging.DEBUG)
    erros = []
    roteiros = [{"PUT": [F.connection_error(), F.connection_error()]},
                {"PUT": [403], "failed": [F.connection_error(), F.connection_error()]},
                {"initialize": [requests.ConnectionError(f"{F.FEEDER_URL} {F.FEEDER_KEY}")] * 2},
                {"complete": [503, 503]}]
    for roteiro in roteiros:
        http = F.FakeHTTP(feeder_script={k: list(v) for k, v in roteiro.items()})
        with pytest.raises(FeederError) as exc:
            client(http).upload("live.json", PAYLOAD, "application/json")
        erros.append(str(exc.value))
        # nenhuma exceção do requests encadeada (o traceback dela traria a URL inteira)
        assert exc.value.__cause__ is None
        assert exc.value.__context__ is None or exc.value.__suppress_context__
    texto = caplog.text + "\n".join(erros)
    assert F.FEEDER_KEY not in texto
    assert "ASSINATURA-SECRETA" not in texto and "X-Amz-Signature" not in texto
    assert "/bucket/" not in texto                                  # nem o caminho do objeto
    assert "oci.example.test" in texto                              # o host pode (e ajuda)


def test_variaveis_obrigatorias():
    assert missing_env({}) == ["DASHBOARD_FEEDER_URL", "DASHBOARD_FEEDER_PROJECT_ID",
                               "DASHBOARD_FEEDER_API_KEY"]
    assert missing_env(F.env()) == []
    assert missing_env(F.env(DASHBOARD_FEEDER_API_KEY="  ")) == ["DASHBOARD_FEEDER_API_KEY"]
    with pytest.raises(FeederError, match="DASHBOARD_FEEDER_API_KEY"):
        FeederClient.from_env(F.env(DASHBOARD_FEEDER_API_KEY=""))
    assert FeederClient.from_env(F.env()).base_url == F.FEEDER_URL
