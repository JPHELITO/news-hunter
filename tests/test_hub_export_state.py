"""
Estado do exportador do hub (hub_export/state.py) e a PARTIDA A FRIO contra a cópia publicada.

O que estes testes TRAVAM, e por quê:
  1) o estado vai e volta do disco (troca atômica) e mora em $RUNNER_TEMP no Actions — o runner
     do loop troca a cada ~6 h e leva o estado junto, de propósito (sem tabela nova no Supabase);
  2) arquivo de estado corrompido, de outra versão ou com forma errada = começar do zero, com um
     aviso, NUNCA uma exceção (o ciclo do robô não pode cair por isso);
  3) runner novo (sem hash no estado): a cópia PUBLICADA no Mindassets decide — igual ao que foi
     montado agora → registra e não reenvia; diferente, 404 ou erro → envia. É o que impede que
     cada troca de runner (4×/dia) vire 4 envios de tudo;
  4) a cópia publicada vem descomprimida quando é gzip (o quotes-daily.json), e se o endereço
     nem responde, a mesma execução não fica esperando o timeout de novo para cada arquivo.
"""
from __future__ import annotations

import json
import logging
import sys
import tempfile
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import test_hub_export_fixtures as F  # noqa: E402
from hub_export import state as S  # noqa: E402
from hub_export.encode import gzip_bytes, to_json_bytes  # noqa: E402
from hub_export.run import run_cycle  # noqa: E402


def run(http, path, **kw):
    kw.setdefault("memory", F.memory())
    return {r.name: r for r in run_cycle(env=F.env(), http=http, now=F.NOW, state_file=path,
                                         **F.run_kw(**kw))}


# ── disco ─────────────────────────────────────────────────────────────────────────────────────
def test_onde_mora_o_estado(tmp_path):
    assert S.state_path({"RUNNER_TEMP": "/r", "HUB_EXPORT_STATE_DIR": "/s"}) == \
        Path("/r") / "hub_export_state.json"
    assert S.state_path({"HUB_EXPORT_STATE_DIR": "/s"}) == Path("/s") / "hub_export_state.json"
    assert S.state_path({}) == Path(tempfile.gettempdir()) / "hub_export_state.json"


def test_grava_e_le(tmp_path):
    p = tmp_path / "sub" / "hub_export_state.json"
    st = S.empty_state()
    st["files"]["live.json"] = {"hash": "abc", "sentAt": "2026-11-03T18:00:00Z", "revision": 7}
    st["quotes"]["rows"]["VALE3.SA"] = {"price": 61.23}
    assert S.save(p, st) is True
    assert S.load(p) == st
    assert not (tmp_path / "sub" / "hub_export_state.json.tmp").exists()


def test_ausente_e_vazio():
    assert S.load(Path(tempfile.gettempdir()) / "nao-existe-hub-export.json") == S.empty_state()


def test_corrompido_ou_estranho_comeca_do_zero(tmp_path, caplog):
    p = tmp_path / "st.json"
    for conteudo in ("{meio arquivo", "[]", json.dumps({"version": 99, "files": {}}), "\x00\x01"):
        p.write_text(conteudo, encoding="utf-8")
        with caplog.at_level(logging.WARNING):
            assert S.load(p) == S.empty_state()
    assert "começando do zero" in caplog.text
    # seção com o tipo errado é trocada pela vazia, o resto fica
    st = S.empty_state()
    st["files"] = {"live.json": {"hash": "x"}}
    st["spark"] = "lixo"
    p.write_text(json.dumps(st), encoding="utf-8")
    lido = S.load(p)
    assert lido["files"] == {"live.json": {"hash": "x"}} and lido["spark"] == {}


def test_run_cycle_com_estado_corrompido_segue(tmp_path):
    p = tmp_path / "st.json"
    p.write_text("{nada disso é json", encoding="utf-8")
    res = run(F.FakeHTTP(), p)
    assert {r.action for r in res.values()} == {"enviado"}
    assert json.loads(p.read_text(encoding="utf-8"))["version"] == S.STATE_VERSION


# ── cópia publicada ───────────────────────────────────────────────────────────────────────────
def test_copia_publicada_404_json_e_gzip():
    doc = {"schemaVersion": 1, "generatedAt": "2026-11-03T18:00:00Z", "series": {}}
    http = F.FakeHTTP(published={"a.json": to_json_bytes(doc),
                                 "b.json": gzip_bytes(to_json_bytes(doc)),
                                 "c.json": b"<html>nao e json</html>"})
    pub = S.PublishedCopies(http=http, env=F.env())
    assert pub.get("a.json") == doc and pub.get("b.json") == doc
    assert pub.get("c.json") is None and pub.get("x.json") is None
    assert pub.content_hash("a.json") == pub.content_hash("b.json") is not None
    n = len(http.calls)
    pub.get("a.json")
    assert len(http.calls) == n                                   # uma vez por execução


def test_endereco_fora_do_ar_nao_espera_de_novo():
    class SemRede(F.FakeHTTP):
        def request(self, method, url, **kw):
            self.calls.append(F.Call(method, url, {}, {}, None, kw.get("timeout")))
            raise requests.ConnectTimeout("timeout")
    http = SemRede()
    pub = S.PublishedCopies(http=http, env=F.env())
    assert pub.get("live.json") is None and pub.get("config.json") is None
    assert len(http.calls) == 1 and http.calls[0].timeout == S.PUBLIC_TIMEOUT_S


def test_base_padrao_e_sobrescrevivel():
    assert S.PublishedCopies(env={}).base == S.DEFAULT_PUBLIC_BASE
    assert S.DEFAULT_PUBLIC_BASE.endswith("/metals-mining-pulp-paper_commodities-datahouse")
    assert S.PublishedCopies(env={"HUB_EXPORT_PUBLIC_BASE": "https://x/y/"}).base == "https://x/y"


# ── partida a frio ────────────────────────────────────────────────────────────────────────────
def test_partida_a_frio_igual_a_copia_publicada_nao_envia(tmp_path):
    primeiro = F.FakeHTTP()
    assert {r.action for r in run(primeiro, tmp_path / "a.json").values()} == {"enviado"}
    # runner novo: estado vazio, mas a cópia publicada é exatamente o que foi enviado
    http = F.FakeHTTP(published=dict(primeiro.uploads))
    res = run(http, tmp_path / "b.json")
    assert {n: r.action for n, r in res.items()} == {n: "igual" for n in res}
    assert http.feeder_calls() == []
    st = S.load(tmp_path / "b.json")
    assert all(st["files"][n]["hash"] == res[n].hash for n in res)   # registrou a cópia
    # e no ciclo seguinte já é "igual" pelo estado, sem baixar a cópia de novo
    http2 = F.FakeHTTP(published=dict(primeiro.uploads))
    run(http2, tmp_path / "b.json", only="live,intraday")
    assert not [c for c in http2.calls if c.url.startswith(F.PUBLIC_BASE)]


def test_partida_a_frio_diferente_ou_404_envia(tmp_path):
    primeiro = F.FakeHTTP()
    run(primeiro, tmp_path / "a.json")
    publicado = dict(primeiro.uploads)
    del publicado["config.json"]                                   # 404
    http = F.FakeHTTP(published=publicado)
    http.database["rpc"]["get_dashboard_flags"][0]["enabled"] = False   # config mudaria
    http.database["commodities"][0]["price"] = 98.0                     # live mudou
    res = run(http, tmp_path / "b.json")
    assert res["config.json"].action == "enviado" and res["config.json"].reason == "partida a frio"
    assert res["live.json"].action == "enviado"
    assert res["intraday.json"].action == "igual"
    assert res["quotes-daily.json"].action == "igual"


def test_partida_a_frio_com_copia_velha_do_live_aciona_a_pulsacao(tmp_path):
    """Igual à cópia publicada, mas ela foi gerada há 20 min: o live.json vai pela pulsação."""
    from datetime import timedelta
    primeiro = F.FakeHTTP()
    run_cycle(env=F.env(), http=primeiro, now=F.NOW - timedelta(minutes=20), memory=F.memory(),
              state_file=tmp_path / "a.json", **F.run_kw())
    http = F.FakeHTTP(published=dict(primeiro.uploads))
    res = run(http, tmp_path / "b.json")
    assert res["live.json"].action == "enviado" and res["live.json"].reason == "pulsação"
    assert res["intraday.json"].action == "igual"
