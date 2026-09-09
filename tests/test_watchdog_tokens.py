"""O vigia tem de ver o CRACHÁ guardado, não só se a sessão logou.

Em 2026-09-09 o corpo de toda notícia da Platts falhou por uma semana com o
source_health VERDE: o roll-forward gravava o access token já vencido no store, o
caminho por API do clipping levava 401 e caía no navegador (também quebrado). Estes
testes protegem a checagem que fecha esse buraco.
"""
from __future__ import annotations

import base64
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts import watchdog  # noqa: E402


def _jwt(exp_epoch: float) -> str:
    """JWT de mentira: só o payload importa (a checagem não verifica assinatura)."""
    payload = base64.urlsafe_b64encode(json.dumps({"exp": exp_epoch}).encode()).decode().rstrip("=")
    return "eyJhbGciOiJSUzI1NiJ9." + payload + ".assinatura"


def _state_platts(tok: str) -> dict:
    return {"origins": [{"origin": "https://core.spglobal.com",
                         "localStorage": [{"name": "outra-coisa", "value": "{}"},
                                          {"name": "okta-token-storage",
                                           "value": json.dumps({"accessToken": {"accessToken": tok}})}]}]}


def _state_fm(tok: str) -> dict:
    return {"origins": [{"origin": "https://dashboard.fastmarkets.com",
                         "localStorage": [{"name": "oidc.user:https://auth:app",
                                           "value": json.dumps({"access_token": tok})}]}]}


class _Resp:
    def __init__(self, payload):
        self._p = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._p


def _rodar(monkeypatch, linhas, agora):
    monkeypatch.setattr(watchdog.requests, "get", lambda *a, **k: _Resp(linhas))
    return watchdog.check_tokens("https://x", {}, agora)


def test_jwt_exp_le_a_validade(monkeypatch):
    agora = dt.datetime(2026, 9, 9, 12, 0, tzinfo=dt.timezone.utc)
    assert watchdog._jwt_exp(_jwt(agora.timestamp())) == agora.timestamp()
    assert watchdog._jwt_exp("não é jwt") is None


def test_token_valido_nao_alarma(monkeypatch):
    agora = dt.datetime(2026, 9, 9, 12, 0, tzinfo=dt.timezone.utc)
    bom = _jwt(agora.timestamp() + 40 * 60)          # vence daqui a 40 min
    linhas = [{"source": "platts", "state": json.dumps(_state_platts(bom))},
              {"source": "fastmarkets", "state": json.dumps(_state_fm(bom))}]
    problemas, relatorio = _rodar(monkeypatch, linhas, agora)
    assert problemas == []
    assert any("válido" in l for l in relatorio)


def test_token_vencido_ha_muito_ALARMA(monkeypatch):
    """O caso de 09/09: crachá guardado vencido há horas = clipping sem corpo."""
    agora = dt.datetime(2026, 9, 9, 12, 0, tzinfo=dt.timezone.utc)
    velho = _jwt(agora.timestamp() - 3 * 3600)       # venceu há 3h
    linhas = [{"source": "platts", "state": json.dumps(_state_platts(velho))}]
    problemas, _ = _rodar(monkeypatch, linhas, agora)
    assert len(problemas) == 1
    assert "platts" in problemas[0] and "180 min" in problemas[0]


def test_vencido_ha_pouco_NAO_alarma(monkeypatch):
    """Tolerância: o loop roda a cada 30 min, um ciclo perdido não é incidente."""
    agora = dt.datetime(2026, 9, 9, 12, 0, tzinfo=dt.timezone.utc)
    recem = _jwt(agora.timestamp() - 20 * 60)        # venceu há 20 min < 90 de tolerância
    linhas = [{"source": "platts", "state": json.dumps(_state_platts(recem))}]
    problemas, _ = _rodar(monkeypatch, linhas, agora)
    assert problemas == []


def test_sessao_sem_token_ALARMA(monkeypatch):
    agora = dt.datetime(2026, 9, 9, 12, 0, tzinfo=dt.timezone.utc)
    linhas = [{"source": "platts", "state": json.dumps({"origins": []})}]
    problemas, _ = _rodar(monkeypatch, linhas, agora)
    assert len(problemas) == 1 and "SEM access token" in problemas[0]


def test_state_gzipado_tambem_e_lido(monkeypatch):
    """O store pode guardar o state comprimido; ilegível não pode virar falso OK."""
    import gzip
    agora = dt.datetime(2026, 9, 9, 12, 0, tzinfo=dt.timezone.utc)
    velho = _jwt(agora.timestamp() - 5 * 3600)
    empacotado = base64.b64encode(gzip.compress(json.dumps(_state_platts(velho)).encode())).decode()
    linhas = [{"source": "platts", "state": empacotado}]
    problemas, _ = _rodar(monkeypatch, linhas, agora)
    assert len(problemas) == 1 and "venceu" in problemas[0]
