"""
De onde vem cada dado do hub (hub_export/sources.py) — o orçamento de ≤ 5 MB/dia do Supabase.

O plano grátis tem 5 GB/mês, o sistema inteiro já anda a ~90 MB/dia e o Supabase conta ~1 KB de
cabeçalho por resposta, além do corpo comprimido. O que estes testes TRAVAM, e por quê:
  1) cache do risquinho: o `spark` inteiro (o caro: ~55 KB para as 35 commodities) só desce para
     o código cujo trio (asof, v, ph) mudou — o `ph` muda na 2ª passada das de tick vivo, depois
     do fechamento, sem mudar o `asof`; código que perdeu o spark sai do cache;
  2) cache do diário por papel: sonda `max(daily_updated_at)`; igual → zero leitura; mudou → só os
     papéis com carimbo novo (o `quotes.daily` inteiro são ~250 KB comprimidos);
  3) partida a frio do diário: o cache nasce da cópia PUBLICADA (Mindassets, fora do Supabase) e
     só relê quem tem carimbo diferente no banco — sem isso, cada troca de runner (4×/dia) relia
     o diário inteiro;
  4) memória × estado: vence o carimbo mais novo; empate → a memória (chegou depois);
  5) o banco falhou num ciclo: o spark e as commodities ficam com a última leitura boa.
"""
from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import test_hub_export_fixtures as F  # noqa: E402
from hub_export import state as S  # noqa: E402
from hub_export.encode import gzip_bytes, to_json_bytes  # noqa: E402
from hub_export.exporters import quotes_daily  # noqa: E402
from hub_export.sources import _newer  # noqa: E402
from hub_export.supa import SupaError  # noqa: E402


def spark_reads(http):
    return [c for c in http.supa_calls("commodities") if c.params["select"] == "code,spark"]


def daily_reads(http):
    return [c for c in http.supa_calls("quotes") if c.params["select"] == "ticker,daily,daily_updated_at"]


def comm(http, code):
    return next(r for r in http.database["commodities"] if r["code"] == code)


# ── spark ─────────────────────────────────────────────────────────────────────────────────────
def test_spark_so_desce_para_quem_mudou():
    st = S.empty_state()
    src, http = F.make_sources(state=st)
    out = {r["code"]: r for r in src.commodities()}
    (leitura,) = spark_reads(http)
    assert leitura.params["code"] == 'in.("COPPER","IRON_ORE","PULP_BHKP_CHINA")'
    assert out["COPPER"]["spark"]["ph"] == 0 and out["HRC_CHINA"]["spark"] is None
    # ciclo seguinte, nada mudou: só a leitura leve (que já é a sonda)
    src, http = F.make_sources(state=st)
    src.commodities()
    assert spark_reads(http) == [] and len(http.supa_calls("commodities")) == 1
    # 2ª passada do cobre depois do fechamento: mesmo asof, ph 0 → 1
    http = F.FakeHTTP()
    comm(http, "COPPER")["spark"]["ph"] = 1
    comm(http, "COPPER")["spark"]["d"]["c"] = 0.9
    src, http = F.make_sources(http=http, state=st)
    out = {r["code"]: r for r in src.commodities()}
    (leitura,) = spark_reads(http)
    assert leitura.params["code"] == 'in.("COPPER")'
    assert out["COPPER"]["spark"]["d"]["c"] == 0.9
    # o minério virou o dia
    http = F.FakeHTTP()
    comm(http, "IRON_ORE")["spark"]["asof"] = "2026-11-03"
    comm(http, "COPPER")["spark"]["ph"] = 1
    comm(http, "COPPER")["spark"]["d"]["c"] = 0.9
    src, http = F.make_sources(http=http, state=st)
    src.commodities()
    assert spark_reads(http)[0].params["code"] == 'in.("IRON_ORE")'


def test_spark_que_sumiu_sai_do_cache_e_do_arquivo():
    st = S.empty_state()
    F.make_sources(state=st)[0].commodities()
    http = F.FakeHTTP()
    comm(http, "COPPER")["spark"] = None
    src, _ = F.make_sources(http=http, state=st)
    out = {r["code"]: r for r in src.commodities()}
    assert out["COPPER"]["spark"] is None and "COPPER" not in st["spark"]


def test_banco_fora_fica_a_ultima_leitura_boa():
    st = S.empty_state()
    F.make_sources(state=st)[0].commodities()

    class SparkFora(F.FakeHTTP):
        def _supa(self, call):
            if call.params.get("select") == "code,spark":
                return F.FakeResp(503, {})
            return super()._supa(call)
    http = SparkFora()
    comm(http, "IRON_ORE")["spark"]["asof"] = "2026-11-03"     # mudou, mas o spark não desce
    src, http = F.make_sources(http=http, state=st)
    out = {r["code"]: r for r in src.commodities()}
    assert len(spark_reads(http)) == 1                         # tentou…
    assert out["IRON_ORE"]["spark"]["asof"] == "2026-11-02"    # …e ficou o desenho anterior
    src, _ = F.make_sources(http=F.FakeHTTP(supa_error={"commodities": 500}), state=st)
    assert len(src.commodities()) == len(F.COMMODITIES)        # a leitura leve anterior
    src, _ = F.make_sources(http=F.FakeHTTP(supa_error={"commodities": 500}))
    with pytest.raises(SupaError):                             # sem leitura anterior: erro
        src.commodities()


# ── diário ────────────────────────────────────────────────────────────────────────────────────
def test_diario_sonda_e_rele_so_o_papel_que_mudou():
    st = S.empty_state()
    src, http = F.make_sources(state=st)
    src.daily()
    sel = [c.params["select"] for c in http.supa_calls("quotes")]
    assert sel == ["daily_updated_at", "ticker,daily_updated_at", "ticker,daily,daily_updated_at"]
    assert "ticker" not in daily_reads(http)[0].params          # partida a frio: tudo, sem filtro
    # nada mudou: só a sonda (limit 1)
    src, http = F.make_sources(state=st)
    src.daily()
    (sonda,) = http.supa_calls("quotes")
    assert sonda.params == {"select": "daily_updated_at",
                            "order": "daily_updated_at.desc.nullslast", "limit": "1"}
    # a VALE ganhou o fechamento de ontem
    http = F.FakeHTTP()
    vale = next(r for r in http.database["quotes"] if r["ticker"] == "VALE3.SA")
    vale["daily"].append([1793710800, 61.0])
    vale["daily_updated_at"] = "2026-11-03T18:00:00.5+00:00"
    src, http = F.make_sources(http=http, state=st)
    d = src.daily()
    assert [c.params.get("ticker") for c in daily_reads(http)] == ['in.("VALE3.SA")']
    assert d["VALE3.SA"]["pts"][-1] == [1793710800, 61.0]
    assert d["VALE3.SA"]["at"] == "2026-11-03T18:00:00Z"


def _publicado(state=None):
    src, _ = F.make_sources(state=state or S.empty_state())
    return gzip_bytes(to_json_bytes(quotes_daily.build(src)))


def test_partida_a_frio_do_diario_semeada_da_copia_publicada():
    publicado = _publicado()
    http = F.FakeHTTP(published={"quotes-daily.json": publicado})
    pub = S.PublishedCopies(http=http, env=F.env())
    src, http = F.make_sources(http=http, published=pub)
    d = src.daily()
    assert daily_reads(http) == []                              # ninguém relido
    assert json.loads(gzip.decompress(publicado))["series"]["VALE3.SA"]["pts"] == \
        d["VALE3.SA"]["pts"]
    # um papel com carimbo novo no banco: só ele é relido
    http = F.FakeHTTP(published={"quotes-daily.json": publicado})
    scco = next(r for r in http.database["quotes"] if r["ticker"] == "SCCO")
    scco["daily_updated_at"] = "2026-11-03T18:01:00+00:00"
    pub = S.PublishedCopies(http=http, env=F.env())
    src, http = F.make_sources(http=http, published=pub)
    src.daily()
    assert [c.params.get("ticker") for c in daily_reads(http)] == ['in.("SCCO")']


def test_copia_publicada_de_outra_versao_nao_semeia():
    doc = json.loads(gzip.decompress(_publicado()))
    doc["schemaVersion"] = 2
    http = F.FakeHTTP(published={"quotes-daily.json": to_json_bytes(doc)})
    src, http = F.make_sources(http=http, published=S.PublishedCopies(http=http, env=F.env()))
    src.daily()
    assert len(daily_reads(http)) == 1 and "ticker" not in daily_reads(http)[0].params


# ── memória × estado ──────────────────────────────────────────────────────────────────────────
def test_vence_o_carimbo_mais_novo_e_empate_e_da_memoria():
    a = {"price": 1, "updated_at": "2026-11-03T18:00:00Z"}
    b = {"price": 2, "updated_at": "2026-11-03T18:05:00Z"}
    assert _newer(a, b) and not _newer(b, a)
    assert _newer(a, dict(a, price=3))                          # empate: quem chegou depois
    assert _newer(None, a) and not _newer(a, {"price": 9, "updated_at": None})
    st = F.previous_state()
    st["quotes"]["rows"]["VALE3.SA"] = {"price": 1.0, "change_pct": None, "change_abs": None,
                                        "updated_at": "2026-11-03T17:00:00Z", "intraday": None}
    src, _ = F.make_sources(state=st)
    assert src.quotes()["VALE3.SA"]["price"] == 61.23           # a memória é mais nova
