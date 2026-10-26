"""
Pregão de cada papel no `quotes.intraday` (2026-10-26): `traded_at` (último negócio) e
`session` ([abre, fecha] do pregão de hoje), lidos do `meta` do Yahoo.

A home usa as duas chaves para escrever no cartão do heatmap a DATA do preço e se a bolsa
está aberta. O caso que motivou o pedido: às 10h40 de uma segunda o B3 já negociava, mas o
Yahoo ainda não tinha nenhuma barra do dia — o cartão mostrava o fechamento de sexta como
se fosse de hoje. Por isso o objeto TEM de ser gravado também sem barras.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import hunter.prices as prices  # noqa: E402

SEG_ABRE, SEG_FECHA = 1793019600, 1793044800    # 26/10/2026 10:00–17:00 BRT
SEXTA_FECH = 1792785955                          # 23/10/2026 17:05:55 BRT


def _resposta(meta: dict, ts: list | None = None, closes: list | None = None):
    res = {"meta": meta}
    if ts is not None:
        res["timestamp"] = ts
        res["indicators"] = {"quote": [{"close": closes}]}

    class R:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"chart": {"result": [res]}}
    return R()


def _meta(**extra) -> dict:
    m = {"regularMarketPrice": 61.49, "chartPreviousClose": 61.0,
         "regularMarketTime": SEXTA_FECH,
         "currentTradingPeriod": {"regular": {"start": SEG_ABRE, "end": SEG_FECHA}}}
    m.update(extra)
    return m


def test_fetch_devolve_janela_e_hora_do_ultimo_negocio(monkeypatch):
    monkeypatch.setattr(prices.requests, "get", lambda *a, **k: _resposta(_meta()))
    d = prices._fetch_yahoo_chart("VALE3.SA")
    assert d["quote_time"] == SEXTA_FECH
    assert d["session"] == [SEG_ABRE, SEG_FECHA]


def test_janela_ausente_ou_invertida_vira_none(monkeypatch):
    for ctp in ({}, {"regular": {}}, {"regular": {"start": SEG_FECHA, "end": SEG_ABRE}},
                {"regular": {"start": "x", "end": SEG_FECHA}}, None):
        monkeypatch.setattr(prices.requests, "get",
                            lambda *a, _c=ctp, **k: _resposta(_meta(currentTradingPeriod=_c)))
        d = prices._fetch_yahoo_chart("VALE3.SA")
        assert d["price"] == 61.49            # o preço nunca depende da janela
        assert d["session"] is None


def _grava(monkeypatch, dados: dict) -> dict[str, dict]:
    gravado = {}
    monkeypatch.setattr(prices, "fetch_yahoo", lambda syms, range_=None, interval=None: dados)
    monkeypatch.setattr(prices, "_supa_upsert",
                        lambda tabela, rows: gravado.update({r["ticker"]: r for r in rows}) or len(rows))
    prices.update_quotes()
    return gravado


def test_update_quotes_grava_o_pregao_no_intraday(monkeypatch):
    pts = [[SEG_ABRE + 1800, 12.41], [SEG_ABRE + 2100, 12.45]]
    gravado = _grava(monkeypatch, {"VALE": {
        "price": 12.45, "change_abs": .05, "change_pct": .4, "prev": 12.4, "series": pts,
        "quote_time": SEG_ABRE + 2100, "session": [SEG_ABRE + 1800, SEG_FECHA]}})
    it = gravado["VALE"]["intraday"]
    assert it["pts"] == pts and it["prev"] == 12.4          # o que já existia, intacto
    assert it["traded_at"] == SEG_ABRE + 2100
    assert it["session"] == [SEG_ABRE + 1800, SEG_FECHA]


def test_sem_barras_do_dia_o_pregao_ainda_e_gravado(monkeypatch):
    """B3 às 10h40 de 26/10/2026: aberto, Yahoo sem nenhuma barra do dia. Antes o
    intraday ia None e a home não teria como dizer que o preço ainda é o de sexta."""
    gravado = _grava(monkeypatch, {"VALE3.SA": {
        "price": 61.49, "change_abs": .49, "change_pct": .8, "prev": 61.0, "series": [],
        "quote_time": SEXTA_FECH, "session": [SEG_ABRE, SEG_FECHA]}})
    it = gravado["VALE3.SA"]["intraday"]
    assert it["pts"] == []
    assert it["traded_at"] == SEXTA_FECH
    assert it["session"] == [SEG_ABRE, SEG_FECHA]


def test_sem_hora_nem_janela_o_preco_segue_gravado(monkeypatch):
    gravado = _grava(monkeypatch, {"VALE": {"price": 12.45, "change_abs": None,
                                            "change_pct": None, "series": None}})
    assert gravado["VALE"]["price"] == 12.45
    it = gravado["VALE"]["intraday"]
    assert it["traded_at"] is None and it["session"] is None and it["pts"] == []
