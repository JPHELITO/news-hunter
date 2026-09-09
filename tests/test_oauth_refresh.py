"""A sessão tem de se manter viva SOZINHA, sem navegador e sem login.

Contexto (2026-09-09): o auto-login de Platts/Fastmarkets não funciona da nuvem — o Okta
da S&P recusa o IP de datacenter do GitHub. A sessão só sobrevive porque o refresh token
renova, e o servidor **rotaciona** esse token a cada uso. Guardar um token já consumido
mata a sessão de vez, e a única saída passa a ser o login que está bloqueado.

Estes testes travam as duas coisas que não podem regredir: o token rotacionado é gravado
SEMPRE, e uma gravação atrasada nunca sobrescreve uma sessão mais nova.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hunter import oauth_refresh as orf  # noqa: E402


def _state_platts(exp: float, rt: str = "RT-VELHO") -> dict:
    return {"cookies": [], "origins": [{"origin": "https://core.spglobal.com", "localStorage": [
        {"name": "okta-token-storage", "value": json.dumps({
            "idToken": {"idToken": "id", "expiresAt": int(exp),
                        "clientId": "0oaCLIENT"},
            "accessToken": {"accessToken": "at", "expiresAt": int(exp)},
            "refreshToken": {"refreshToken": rt, "expiresAt": int(exp),
                             "tokenUrl": "https://idp.example/v1/token",
                             "scopes": ["openid", "offline_access"]}})}]}]}


def _state_fm(exp: float, rt: str = "RT-VELHO") -> dict:
    return {"cookies": [], "origins": [{"origin": "https://dashboard.fastmarkets.com", "localStorage": [
        {"name": "oidc.user:https://auth.fastmarkets.com/:fastmarkets.dashboard.code",
         "value": json.dumps({"access_token": "at", "refresh_token": rt,
                              "expires_at": int(exp), "scope": "openid offline_access",
                              "profile": {"iss": "https://auth.fastmarkets.com"}})}]}]}


class _Resp:
    def __init__(self, status, payload=None, text=""):
        self.status_code, self._p, self.text = status, payload, text
        self.ok = 200 <= status < 300

    def json(self):
        if self._p is None:
            raise ValueError("sem json")
        return self._p


@pytest.fixture
def bancada(tmp_path, monkeypatch):
    """Isola o disco e o store; devolve helpers p/ montar o cenário."""
    from hunter import playwright_session as ps
    monkeypatch.setattr(ps, "get_cookies_dir", lambda: tmp_path)
    monkeypatch.setattr(ps, "pull_session", lambda p: True)      # store == disco no teste
    empurrados: list[tuple[str, str]] = []
    monkeypatch.setattr(ps, "_push_session_to_store",
                        lambda p, s: empurrados.append((p, s)))

    def escrever(provider, state):
        (tmp_path / f"{provider}_state.json").write_text(json.dumps(state), encoding="utf-8")

    def ler(provider):
        return json.loads((tmp_path / f"{provider}_state.json").read_text(encoding="utf-8"))

    return {"dir": tmp_path, "push": empurrados, "escrever": escrever, "ler": ler}


def test_renova_e_grava_o_token_ROTACIONADO(bancada, monkeypatch):
    """O caso que quebrou a produção: o servidor devolve um refresh token NOVO e o
    antigo morre. Se não gravarmos o novo, a sessão acaba."""
    bancada["escrever"]("platts", _state_platts(time.time() + 60))   # vence em 1 min
    chamadas = []

    def fake_post(url, data=None, **kw):
        chamadas.append((url, data))
        return _Resp(200, {"access_token": "AT-NOVO", "refresh_token": "RT-NOVO",
                           "id_token": "ID-NOVO", "expires_in": 3600})
    monkeypatch.setattr(orf.requests, "post", fake_post)

    novo = orf.refresh("platts")
    assert novo and novo > time.time() + 3000

    url, data = chamadas[0]
    assert url == "https://idp.example/v1/token"
    assert data["grant_type"] == "refresh_token"
    assert data["refresh_token"] == "RT-VELHO"      # manda o antigo
    assert data["client_id"] == "0oaCLIENT"

    tok = json.loads(bancada["ler"]("platts")["origins"][0]["localStorage"][0]["value"])
    assert tok["refreshToken"]["refreshToken"] == "RT-NOVO"   # e guarda o NOVO
    assert tok["accessToken"]["accessToken"] == "AT-NOVO"
    assert bancada["push"], "o rotacionado tem de ir para o store na hora"
    empurrado = json.loads(bancada["push"][-1][1])
    assert "RT-NOVO" in json.dumps(empurrado)


def test_fastmarkets_usa_o_mesmo_caminho(bancada, monkeypatch):
    bancada["escrever"]("fastmarkets", _state_fm(time.time() + 60))
    monkeypatch.setattr(orf, "_token_endpoint", lambda iss: iss.rstrip("/") + "/connect/token")
    vistos = []

    def fake_post(url, data=None, **kw):
        vistos.append((url, data))
        return _Resp(200, {"access_token": "AT2", "refresh_token": "RT2", "expires_in": 7200})
    monkeypatch.setattr(orf.requests, "post", fake_post)

    assert orf.refresh("fastmarkets")
    url, data = vistos[0]
    assert url == "https://auth.fastmarkets.com/connect/token"
    assert data["client_id"] == "fastmarkets.dashboard.code"
    d = json.loads(bancada["ler"]("fastmarkets")["origins"][0]["localStorage"][0]["value"])
    assert d["refresh_token"] == "RT2" and d["access_token"] == "AT2"


def test_com_folga_NAO_gasta_uma_rotacao(bancada, monkeypatch):
    """Rotação é recurso finito: só renova perto de vencer."""
    bancada["escrever"]("platts", _state_platts(time.time() + 50 * 60))
    monkeypatch.setattr(orf.requests, "post",
                        lambda *a, **k: pytest.fail("não devia ter chamado o servidor"))
    assert orf.refresh("platts")            # devolve o prazo atual, sem renovar
    assert not bancada["push"]


def test_force_renova_mesmo_com_folga(bancada, monkeypatch):
    bancada["escrever"]("platts", _state_platts(time.time() + 50 * 60))
    monkeypatch.setattr(orf.requests, "post", lambda *a, **k: _Resp(
        200, {"access_token": "A", "refresh_token": "R", "expires_in": 3600}))
    assert orf.refresh("platts", force=True)
    assert bancada["push"]


def test_erro_do_servidor_NAO_estraga_a_sessao(bancada, monkeypatch):
    """400 invalid_grant = nada foi consumido. O que está guardado tem de ficar intacto,
    senão um erro passageiro viraria uma sessão morta."""
    antes = _state_platts(time.time() + 60)
    bancada["escrever"]("platts", antes)
    monkeypatch.setattr(orf.requests, "post",
                        lambda *a, **k: _Resp(400, None, '{"error":"invalid_grant"}'))
    assert orf.refresh("platts") is None
    assert bancada["ler"]("platts") == antes
    assert not bancada["push"]


def test_rede_caiu_NAO_estraga_a_sessao(bancada, monkeypatch):
    antes = _state_platts(time.time() + 60)
    bancada["escrever"]("platts", antes)

    def explode(*a, **k):
        raise OSError("sem rede")
    monkeypatch.setattr(orf.requests, "post", explode)
    assert orf.refresh("platts") is None
    assert bancada["ler"]("platts") == antes


def test_sessao_sem_refresh_token_devolve_None(bancada, monkeypatch):
    st = _state_platts(time.time() + 60)
    tok = json.loads(st["origins"][0]["localStorage"][0]["value"])
    tok["refreshToken"]["refreshToken"] = ""
    st["origins"][0]["localStorage"][0]["value"] = json.dumps(tok)
    bancada["escrever"]("platts", st)
    monkeypatch.setattr(orf.requests, "post",
                        lambda *a, **k: pytest.fail("não há o que renovar"))
    assert orf.refresh("platts") is None


def test_keep_alive_nunca_levanta(bancada, monkeypatch):
    monkeypatch.setattr(orf, "refresh", lambda p, **k: (_ for _ in ()).throw(RuntimeError("x")))
    assert orf.keep_alive(("platts",)) == {"platts": None}


# ── trava anti-corrida do save_state ─────────────────────────────────────────
def test_save_state_NAO_regrava_por_cima_de_sessao_mais_nova(tmp_path, monkeypatch):
    """Dois processos leem e escrevem a mesma sessão (loop + cron de backup). O atrasado
    devolveria ao store um refresh token já consumido. Foi o que o log de 03/09 mostrou:
    o tamanho gravado passou a noite alternando entre dois valores, sem progredir."""
    from hunter import playwright_session as ps
    monkeypatch.setattr(ps, "get_cookies_dir", lambda: tmp_path)
    empurrados = []
    monkeypatch.setattr(ps, "_push_session_to_store", lambda p, s: empurrados.append(s))

    velho = _state_platts(time.time() + 5 * 60)          # o que este processo tem
    novo = _state_platts(time.time() + 55 * 60, "RT-BOM")  # o que já está no store

    class _Ctx:
        def storage_state(self):
            return velho

    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "k")
    monkeypatch.setattr(ps.requests if hasattr(ps, "requests") else orf.requests, "get",
                        lambda *a, **k: _Resp(200, [{"state": json.dumps(novo)}]),
                        raising=False)
    import requests as _rq
    monkeypatch.setattr(_rq, "get", lambda *a, **k: _Resp(200, [{"state": json.dumps(novo)}]))

    ps.save_state(_Ctx(), "platts")
    assert not empurrados, "não pode regravar por cima da sessão mais nova"
    # o arquivo local segue sendo escrito (é a foto deste processo)
    assert (tmp_path / "platts_state.json").exists()


def test_save_state_avanca_quando_a_sessao_e_mais_nova(tmp_path, monkeypatch):
    from hunter import playwright_session as ps
    monkeypatch.setattr(ps, "get_cookies_dir", lambda: tmp_path)
    empurrados = []
    monkeypatch.setattr(ps, "_push_session_to_store", lambda p, s: empurrados.append(s))

    novo = _state_platts(time.time() + 55 * 60, "RT-NOVO")
    velho_no_store = _state_platts(time.time() + 5 * 60)

    class _Ctx:
        def storage_state(self):
            return novo

    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "k")
    import requests as _rq
    monkeypatch.setattr(_rq, "get",
                        lambda *a, **k: _Resp(200, [{"state": json.dumps(velho_no_store)}]))

    ps.save_state(_Ctx(), "platts")
    assert empurrados and "RT-NOVO" in empurrados[-1]
