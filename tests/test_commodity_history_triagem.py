"""update_commodity_history: triagem barata ANTES de baixar a série — mesma decisão de antes.

Contexto (2026-10-15): a rotina baixava `commodities?select=...,daily` de TODAS as linhas em
toda rodada do hunt (~336 por dia), 191 KB (54 KB comprimido), só para descobrir que nada
estava atrasado (o gatilho é 18h). Agora pergunta só `code,daily_updated_at` (2,5 KB) e a
série viaja só para as atrasadas.

Estes testes travam: (1) sem nada atrasado, só a triagem viaja; (2) as atrasadas recebem
exatamente o PATCH de antes; (3) os casos de borda da regra das 18h (sem carimbo, carimbo
ilegível) seguem contando como atrasados; (4) falha em qualquer leitura = não grava nada.
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import hunter.prices as prices  # noqa: E402

AGORA = dt.datetime.now(dt.timezone.utc)


def _iso(horas_atras: float) -> str:
    return (AGORA - dt.timedelta(hours=horas_atras)).isoformat()


def _ep(d: str) -> int:
    return int(dt.datetime.fromisoformat(d + "T00:00:00+00:00").timestamp())


def _linhas() -> list[dict]:
    return [
        {"code": "HRC_CHINA", "price": 450.5, "assessed_at": "2026-10-14",
         "daily": [[_ep("2026-10-10"), 440.0]], "daily_updated_at": _iso(30)},      # atrasada
        {"code": "MET_COAL", "price": 190.0, "assessed_at": "2026-10-14",
         "daily": [[_ep("2026-10-13"), 189.0]], "daily_updated_at": _iso(2)},       # em dia
        {"code": "REBAR_TURKEY", "price": 600.0, "assessed_at": "2026-10-15",
         "daily": [], "daily_updated_at": None},                                    # sem carimbo
        {"code": "IOBFC04", "price": None, "assessed_at": None,
         "daily": [], "daily_updated_at": "lixo"},                                  # ilegível, sem preço
        {"code": "COPPER", "price": 4.9, "assessed_at": "2026-10-15",
         "daily": [], "daily_updated_at": _iso(20)},                                # Yahoo, atrasada
    ]


class _Resp:
    def __init__(self, payload, ok=True):
        self._p, self.ok = payload, ok
        self.status_code = 200 if ok else 500
        self.text = ""

    def json(self):
        return self._p


@pytest.fixture
def banco(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "k")
    est = {"linhas": _linhas(), "gets": [], "patches": [], "falha_get": set(), "erro_http": set()}

    def fake_get(url, params=None, headers=None, timeout=None):
        params = params or {}
        sel = params.get("select", "")
        passo = "triagem" if sel == "code,daily_updated_at" else "serie"
        est["gets"].append((passo, dict(params)))
        if passo in est["falha_get"]:
            raise OSError("sem rede")
        if passo in est["erro_http"]:
            return _Resp({"message": "erro"}, ok=False)
        if passo == "triagem":
            return _Resp([{"code": r["code"], "daily_updated_at": r["daily_updated_at"]}
                          for r in est["linhas"]])
        pedidas = params["code"][len("in.("):-1].replace('"', "").split(",")
        return _Resp([dict(r) for r in est["linhas"] if r["code"] in pedidas])

    def fake_patch(url, json=None, headers=None, timeout=None):
        est["patches"].append((url.split("code=eq.")[1], json))
        return _Resp(None)

    serie = [[int((AGORA - dt.timedelta(days=d)).timestamp()), 4.0 + d / 100] for d in range(5, 0, -1)]
    monkeypatch.setattr(prices.requests, "get", fake_get)
    monkeypatch.setattr(prices.requests, "patch", fake_patch)
    monkeypatch.setattr(prices, "fetch_yahoo",
                        lambda syms, range_=None, interval=None: {s: {"series": serie} for s in syms})
    return est


def test_nada_atrasado_so_a_triagem_viaja(banco):
    for r in banco["linhas"]:
        r["daily_updated_at"] = _iso(1)
    assert prices.update_commodity_history() == 0
    assert [p for p, _ in banco["gets"]] == ["triagem"]
    assert banco["patches"] == []


def test_so_as_atrasadas_tem_a_serie_baixada(banco):
    prices.update_commodity_history()
    passo, params = banco["gets"][1]
    assert passo == "serie"
    pedidas = set(params["code"][len("in.("):-1].replace('"', "").split(","))
    assert pedidas == {"HRC_CHINA", "REBAR_TURKEY", "IOBFC04", "COPPER"}   # MET_COAL em dia: fora
    assert params["select"] == "code,price,assessed_at,daily,daily_updated_at"


def test_atrasadas_recebem_o_mesmo_patch_de_antes(banco):
    assert prices.update_commodity_history() == 3
    por_code = dict(banco["patches"])
    assert set(por_code) == {"HRC_CHINA", "REBAR_TURKEY", "COPPER"}   # IOBFC04 sem preço: pula
    assert por_code["HRC_CHINA"]["daily"] == [[_ep("2026-10-10"), 440.0], [_ep("2026-10-14"), 450.5]]
    assert por_code["REBAR_TURKEY"]["daily"] == [[_ep("2026-10-15"), 600.0]]
    assert len(por_code["COPPER"]["daily"]) == 5                      # série do Yahoo
    assert all("daily_updated_at" in j for j in por_code.values())


def test_atualizada_por_outro_processo_no_meio_sai_da_lista(banco, monkeypatch):
    """Entre a triagem e a leitura da série, outra rodada atualizou a HRC → não regrava."""
    orig = prices.requests.get

    def get_com_corrida(url, params=None, **kw):
        r = orig(url, params=params, **kw)
        if params and params.get("select") != "code,daily_updated_at":
            for linha in r._p:
                if linha["code"] == "HRC_CHINA":
                    linha["daily_updated_at"] = _iso(0.01)
        return r
    monkeypatch.setattr(prices.requests, "get", get_com_corrida)
    prices.update_commodity_history()
    assert "HRC_CHINA" not in dict(banco["patches"])


@pytest.mark.parametrize("passo", ["triagem", "serie"])
def test_queda_de_rede_em_qualquer_leitura_nao_grava_nada(banco, passo):
    banco["falha_get"].add(passo)
    assert prices.update_commodity_history() == 0
    assert banco["patches"] == []


@pytest.mark.parametrize("passo", ["triagem", "serie"])
def test_erro_http_em_qualquer_leitura_nao_grava_nada(banco, passo):
    banco["erro_http"].add(passo)
    assert prices.update_commodity_history() == 0
    assert banco["patches"] == []


def test_filtro_vai_em_params_nunca_colado_na_url(banco):
    """Regra do projeto: valor com &, #, + ou espaço quebra a query em silêncio na URL."""
    prices.update_commodity_history()
    assert banco["gets"] and all("select" in p for _, p in banco["gets"])
