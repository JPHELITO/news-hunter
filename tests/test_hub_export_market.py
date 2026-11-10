"""
Onda 2 (WP2.1B) — a paginação de RPC e os arquivos da Market que não são a família de históricos:
ratings.json, commodities-daily.json e quotes-monthly.json (PLANO, Apêndice F.3–F.5).

O que estes testes TRAVAM, e por quê:
  1) `rpc_all`: o PostgREST do Supabase CORTA RPC em 1.000 linhas, calado (medido: o Insiders do
     site recebe 1.000 de 2.039). A leitura pagina por `limit`/`offset` NA QUERY (nunca o cabeçalho
     `Range`, que a Cloudflare trata como bytes), para na página incompleta (ou quando a soma chega
     à contagem, sem a página vazia do fim) e CONFERE a soma com a contagem — senão, erro;
  2) o mini-PostgREST falso entende o que a Onda 2 pede: `->` com índice negativo, filtro, ordem e
     página sobre o resultado de RPC, o corte de 1.000;
  3) ratings.json: forma exata; empresa oculta fora; `note`/`source`/ids/carimbos de usuário NUNCA
     (nem pedidos à RPC, e o registro recusa o arquivo se aparecerem); flag `market_ratings`
     desligada — ou `hub_market_ratings` desligada — → `companies: []` sem ler nada; sonda barata
     antes da RPC (RPC só quando muda, na partida a frio e 1×/dia);
  4) commodities-daily.json: SÓ os 3 códigos de fonte livre — os pagos nunca são pedidos ao banco
     nem saem (nem se o banco os devolver), `commodity_history` nunca é lida; sonda do último ponto;
     partida a frio semeada da cópia publicada;
  5) quotes-monthly.json: a parte mensal da série leve, só meses INTEIROS antes do mês do corte
     (muda ~1×/mês, não todo dia); junto com o quotes-daily.json reproduz a série leve inteira;
     zero leitura nova quando a cópia publicada vale; cópia de outro mês → relê só aquele papel.
"""
from __future__ import annotations

import gzip
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import test_hub_export_fixtures as F  # noqa: E402
from hub_export import state as S  # noqa: E402
from hub_export.encode import content_hash, forbidden_keys, gzip_bytes, to_json_bytes  # noqa: E402
from hub_export.exporters import commodities_daily, quotes_daily, quotes_monthly, ratings  # noqa: E402
from hub_export.run import run_cycle  # noqa: E402
from hub_export.supa import SupaError, SupaReader  # noqa: E402


def reader(http):
    return SupaReader(F.SUPA_URL, F.SUPA_KEY, http=http)


def _big_rpc(n):
    rows = [{"id": i, "v": f"linha {i}"} for i in range(n)]

    def rpc(database, args):
        return list(reversed(rows))                     # a RPC devolve fora de ordem
    return rpc


# ── rpc_all ───────────────────────────────────────────────────────────────────────────────────
def test_rpc_corta_em_mil_e_rpc_all_le_tudo_conferindo_a_contagem():
    http = F.FakeHTTP()
    http.database["rpc"]["get_big"] = _big_rpc(2345)
    r = reader(http)
    assert len(r.rpc("get_big", "id,v")) == 1000        # o corte calado do PostgREST
    http.calls.clear()
    rows = r.rpc_all("get_big", "id,v", order="id.asc", tag="t")
    assert [x["id"] for x in rows] == list(range(2345))
    calls = http.supa_calls("rpc/get_big")
    assert [(c.params["limit"], c.params["offset"]) for c in calls] == \
        [("1000", "0"), ("1000", "1000"), ("1000", "2000")]
    assert all(c.method == "POST" and "Range" not in c.headers for c in calls)   # nunca o cabeçalho
    assert calls[0].headers.get("Prefer") == "count=exact"                       # contagem na 1ª
    assert all("Prefer" not in c.headers for c in calls[1:])
    assert r.by_source["t"]["calls"] == 3


def test_rpc_all_multiplo_exato_nao_pede_a_pagina_vazia():
    http = F.FakeHTTP()
    http.database["rpc"]["get_big"] = _big_rpc(2000)
    rows = reader(http).rpc_all("get_big", "id", order="id.asc")
    assert len(rows) == 2000 and len(http.supa_calls("rpc/get_big")) == 2
    # sem contagem na resposta: para na 1ª página incompleta (aqui, a vazia)

    class SemContagem(F.FakeHTTP):
        def _postgrest(self, call, path, name):
            resp = super()._postgrest(call, path, name)
            resp.headers.pop("Content-Range", None)
            return resp
    http = SemContagem()
    http.database["rpc"]["get_big"] = _big_rpc(2000)
    assert len(reader(http).rpc_all("get_big", "id", order="id.asc")) == 2000
    assert len(http.supa_calls("rpc/get_big")) == 3


def test_rpc_all_contagem_que_nao_fecha_e_erro():
    class Mexeu(F.FakeHTTP):
        """Uma linha some entre a 1ª e a 2ª página (o robô apagou uma versão de insiders)."""
        def _postgrest(self, call, path, name):
            if call.params.get("offset") == "1000":
                self.database["rpc"]["get_big"] = _big_rpc(1499)
            return super()._postgrest(call, path, name)
    http = Mexeu()
    http.database["rpc"]["get_big"] = _big_rpc(1500)
    with pytest.raises(SupaError, match="contagem diz 1500"):
        reader(http).rpc_all("get_big", "id", order="id.asc")
    with pytest.raises(ValueError, match="order"):
        reader(F.FakeHTTP()).rpc_all("get_big", "id", order="")


def test_rpc_aceita_filtro_ordem_e_pagina_sobre_o_resultado():
    http = F.FakeHTTP()
    rows = reader(http).rpc("get_rating_history", "ticker,effective_date",
                            effective_date="gte.2026-01-01", order="effective_date.desc", limit=2)
    assert rows == [{"ticker": "AUGO", "effective_date": "2026-11-20"},
                    {"ticker": "ARA", "effective_date": "2026-10-16"}]


def test_mini_postgrest_seta_com_indice():
    http = F.FakeHTTP()
    rows = http.request("GET", F.SUPA_URL + "/rest/v1/commodities",
                        params={"select": "code,last:daily->-1,first:daily->0,v:daily->-1->1,"
                                          "x:daily->99,s:spark->>v", "code": 'in.("COPPER")'}).json()
    assert rows == [{"code": "COPPER", "last": [F.D_1103, 5.1234], "first": [1631232000, 4.31],
                     "v": 5.1234, "x": None, "s": "2"}]


# ── ratings.json ──────────────────────────────────────────────────────────────────────────────
def test_ratings_forma_exata_e_empresa_oculta_fora():
    src, http = F.make_sources(state=F.previous_state())
    rt = ratings.build(src)
    assert list(rt) == ["schemaVersion", "generatedAt", "updatedAt", "companies"]
    assert rt["updatedAt"] == "2026-11-06T15:07:53Z"           # o maior updated_at do histórico
    assert [c["code"] for c in rt["companies"]] == ["ARA", "AUGO", "VALE"]   # CSNA3 oculta: fora
    assert all(list(c) == ["code", "name", "symbol", "events"] for c in rt["companies"])
    aura = rt["companies"][1]
    assert aura["name"] == "Aura" and aura["symbol"] == "AUGO"
    assert [e["date"] for e in aura["events"]] == ["2021-08-10", "2024-11-04", "2026-11-20"]
    assert aura["events"][1] == {"date": "2024-11-04", "rating": "NR", "targetPrice": None,
                                 "currency": None, "listing": "AURA33.SA"}
    assert aura["events"][2]["date"] == "2026-11-20"           # no futuro: quem descarta é o hub
    vale = rt["companies"][2]["events"]
    assert vale[1] == {"date": "2026-08-17", "rating": "OP", "targetPrice": 18, "currency": "USD",
                       "listing": "VALE"}                      # listing nulo → yahoo_symbol
    # pela RPC paginada (ordem estável, contagem), com select explícito e SEM `note`
    (call,) = http.supa_calls("rpc/get_rating_history")
    assert call.params["select"] == ratings.COLS and "note" not in call.params["select"]
    assert call.params["order"] == "ticker.asc,effective_date.asc"
    assert call.headers.get("Prefer") == "count=exact"


def test_ratings_normalizacao_campo_a_campo():
    sujo = [{"ticker": " VALE ", "company_name": "Vale", "yahoo_symbol": "VALE",
             "effective_date": "2026-01-02T00:00:00", "rating": "op", "target_price": -1,
             "currency": "USDX", "listing": "  ", "note": "segredo", "source": "import", "id": 9,
             "updated_by": "uuid"},
            {"ticker": "VALE", "effective_date": "não é data", "rating": "MP"},
            {"ticker": "", "effective_date": "2026-01-01", "rating": "OP"},
            {"ticker": "X", "effective_date": "2026-01-01", "rating": "BUY", "target_price": True}]
    cos = ratings.companies(sujo)
    assert cos == [{"code": "VALE", "name": "Vale", "symbol": "VALE",
                    "events": [{"date": "2026-01-02", "rating": "OP", "targetPrice": None,
                                "currency": None, "listing": "VALE"}]},
                   {"code": "X", "name": None, "symbol": None,
                    "events": [{"date": "2026-01-01", "rating": None, "targetPrice": None,
                                "currency": None, "listing": None}]}]
    assert forbidden_keys({"companies": cos}, extra=ratings.FORBIDDEN) == []


def test_ratings_flag_desligada_nao_le_nada():
    for flags in ([{"key": "market_ratings", "enabled": False}],
                  [{"key": "market_ratings", "enabled": True},
                   {"key": "hub_market_ratings", "enabled": False}],   # o hub manda
                  []):                                                 # ausente = desligada
        http = F.FakeHTTP()
        http.database["rpc"]["get_dashboard_flags"] = flags
        src, http = F.make_sources(http=http, state=S.empty_state())
        assert ratings.build(src)["companies"] == [] and ratings.build(src)["updatedAt"] is None
        assert not http.supa_calls("rpc/get_rating_history")
        assert not http.supa_calls("stock_guide_rating_history")


def test_ratings_sonda_antes_da_rpc(tmp_path):
    st = F.previous_state()
    src, http = F.make_sources(state=st)
    src.ratings()
    assert len(http.supa_calls("rpc/get_rating_history")) == 1
    # mesma hora, nada mudou: só as 2 sondas (contagem + max), a RPC não
    src, http = F.make_sources(state=st, now=F.NOW + timedelta(minutes=60))
    assert src.ratings()["companies"]
    assert not http.supa_calls("rpc/get_rating_history")
    sondas = http.supa_calls("stock_guide_rating_history") + http.supa_calls("stock_guide_companies")
    assert len(sondas) == 2 and all(c.params["limit"] == "1" and c.headers.get("Prefer") ==
                                    "count=exact" for c in sondas)
    # o admin mudou um alvo (updated_at sobe) → relê
    http = F.FakeHTTP()
    http.database["stock_guide_rating_history"][0].update(target_price=14,
                                                         updated_at="2026-11-07T10:00:00+00:00")
    src, http = F.make_sources(http=http, state=st, now=F.NOW + timedelta(minutes=120))
    assert src.ratings()["companies"][2]["events"][0]["targetPrice"] == 14
    assert len(http.supa_calls("rpc/get_rating_history")) == 1
    # empresa renomeada no Stock Guide (só a tabela de empresas muda) → relê
    http = F.FakeHTTP()
    http.database["stock_guide_companies"][1].update(company_name="Aura Minerals",
                                                    updated_at="2026-11-07T11:00:00+00:00")
    src, http = F.make_sources(http=http, state=st, now=F.NOW + timedelta(minutes=180))
    assert src.ratings()["companies"][1]["name"] == "Aura Minerals"
    # e 1×/dia mesmo sem mudança (06 UTC)
    http.calls.clear()
    src, http = F.make_sources(http=http, state=st, now=datetime(2026, 11, 4, 6, 30, tzinfo=timezone.utc))
    src.ratings()
    assert len(http.supa_calls("rpc/get_rating_history")) == 1


def test_ratings_o_registro_recusa_coluna_interna(tmp_path, monkeypatch):
    """A rede embaixo da lista branca: se um dia o construtor deixar `note` passar, o arquivo não sai."""
    from hub_export.exporters import ratings as rt_mod
    original = rt_mod.companies

    def vazado(rows):
        out = original(rows)
        out[0]["events"][0]["note"] = "Import BBG"
        return out
    monkeypatch.setattr(rt_mod, "companies", vazado)
    http = F.FakeHTTP()
    res = {r.name: r for r in run_cycle(env=F.env(), http=http, now=F.NOW, memory=F.memory(),
                                         state_file=tmp_path / "st.json", only="ratings",
                                         **F.run_kw())}
    assert res["ratings.json"].action == "erro" and "chave proibida" in res["ratings.json"].reason
    assert "ratings.json" not in http.uploads


# ── commodities-daily.json ────────────────────────────────────────────────────────────────────
def test_lista_branca_e_exatamente_as_3_de_fonte_livre():
    """Código novo aqui é decisão do usuário (D1/R1, U5): os pagos NUNCA. Este teste recusa
    qualquer outro código na lista."""
    assert commodities_daily.FREE_SERIES == {"IRON_ORE_62": "Trading Economics",
                                             "COPPER": "Yahoo Finance", "GOLD": "Yahoo Finance"}
    assert not set(commodities_daily.FREE_SERIES) & set(F.PAID_CODES)


def test_commodities_daily_forma_exata_e_so_os_livres():
    src, http = F.make_sources(state=F.previous_state())
    cd = commodities_daily.build(src)
    assert list(cd) == ["schemaVersion", "generatedAt", "series"]
    assert list(cd["series"]) == ["IRON_ORE_62", "COPPER", "GOLD"]
    assert cd["series"]["IRON_ORE_62"] == {
        "updatedAt": "2026-11-03T14:38:50Z", "unit": "USD/t", "source": "Trading Economics",
        "pts": [[1478995200, 72.5], [F.D_1030, 103.9], [F.D_1102, 104.25], [F.D_1103, 104.6]]}
    assert all(list(s) == ["updatedAt", "unit", "source", "pts"] for s in cd["series"].values())
    assert cd["series"]["GOLD"]["source"] == "Yahoo Finance"
    # o banco nem foi perguntado pelos pagos (toda leitura de série leva `code=in.(…)` só com os
    # livres); commodity_history nunca
    series_reads = [c for c in http.supa_calls("commodities") if "daily" in c.params["select"]]
    assert series_reads
    for c in series_reads:
        pedidos = {x.strip('"') for x in c.params["code"][len("in.("):-1].split(",")}
        assert pedidos <= set(F.FREE_CODES), pedidos
    assert not http.supa_calls("commodity_history")
    texto = json.dumps(cd)
    for pago in F.PAID_CODES:
        assert f'"{pago}"' not in texto


def test_pago_que_o_banco_devolva_nao_sai():
    """Mesmo se o filtro `code=in.(…)` fosse ignorado, a montagem só aceita os da lista."""
    class SemFiltro(F.FakeHTTP):
        def _postgrest(self, call, path, name):
            if name == "commodities":
                call.params.pop("code", None)
            return super()._postgrest(call, path, name)
    src, _ = F.make_sources(http=SemFiltro(), state=S.empty_state())
    cd = commodities_daily.build(src)
    assert list(cd["series"]) == ["IRON_ORE_62", "COPPER", "GOLD"]
    assert set(src.state["commDaily"]["rows"]) == set(F.FREE_CODES)    # nem no estado
    for pago in F.PAID_CODES:
        assert f'"{pago}"' not in json.dumps(cd)


def daily_reads(http):
    return [c for c in http.supa_calls("commodities") if c.params["select"] == "code,unit,daily,daily_updated_at"]


def test_commodities_daily_sonda_do_ultimo_ponto():
    st = S.empty_state()
    src, http = F.make_sources(state=st)
    src.commodities_daily()
    assert len(daily_reads(http)) == 1                         # 1ª vez: as 3
    sonda = [c for c in http.supa_calls("commodities") if "last:daily->-1" in c.params["select"]]
    assert len(sonda) == 1 and sonda[0].params["select"] == "code,unit,daily_updated_at,last:daily->-1"
    # nada mudou (o TE reescreveu os 10 anos: o carimbo andou, o último ponto não) → só a sonda
    http = F.FakeHTTP()
    te = next(r for r in http.database["commodities"] if r["code"] == "IRON_ORE_62")
    te["daily_updated_at"] = "2026-11-03T17:38:50+00:00"
    src, http = F.make_sources(http=http, state=st, now=F.NOW + timedelta(minutes=60))
    out = commodities_daily.build(src)
    assert daily_reads(http) == []
    assert out["series"]["IRON_ORE_62"]["updatedAt"] == "2026-11-03T17:38:50Z"   # o carimbo da sonda
    # o ouro ganhou o dia → relê só o ouro
    http = F.FakeHTTP()
    gold = next(r for r in http.database["commodities"] if r["code"] == "GOLD")
    gold["daily"].append([F.D_1103 + 86400, 4030.0])
    src, http = F.make_sources(http=http, state=st, now=F.NOW + timedelta(minutes=120))
    out = commodities_daily.build(src)
    assert [c.params["code"] for c in daily_reads(http)] == ['in.("GOLD")']
    assert out["series"]["GOLD"]["pts"][-1] == [F.D_1103 + 86400, 4030.0]
    # 1 leitura completa por dia (06 UTC), mesmo sem mudança
    src, http = F.make_sources(state=st, now=datetime(2026, 11, 4, 6, 10, tzinfo=timezone.utc))
    src.commodities_daily()
    assert len(daily_reads(http)) == 1 and "IRON_ORE_62" in daily_reads(http)[0].params["code"]


def test_commodities_daily_partida_a_frio_semeada_da_copia_publicada():
    src, _ = F.make_sources(state=S.empty_state())
    publicado = gzip_bytes(to_json_bytes(commodities_daily.build(src)))
    http = F.FakeHTTP(published={"commodities-daily.json": publicado})
    pub = S.PublishedCopies(http=http, env=F.env())
    src, http = F.make_sources(http=http, published=pub, state=S.empty_state(),
                               now=F.NOW + timedelta(minutes=30))
    out = commodities_daily.build(src)
    assert daily_reads(http) == []                             # só a sonda
    assert content_hash(out) == content_hash(json.loads(gzip.decompress(publicado)))


# ── quotes-monthly.json ───────────────────────────────────────────────────────────────────────
def test_cauda_mensal_so_meses_inteiros_antes_do_corte():
    src, _ = F.make_sources(state=F.previous_state())
    qm = quotes_monthly.build(src)
    assert list(qm) == ["schemaVersion", "generatedAt", "series"]
    assert list(qm["series"]) == list(quotes_daily.build(src)["series"])   # os mesmos papéis
    # VALE3.SA: corte em 26/10/2025 → os meses até setembro; o ponto de 01/10/2025 (o último antes
    # do corte NO MÊS DO CORTE, que anda todo dia) fica de fora
    assert qm["series"]["VALE3.SA"] == {"updatedAt": "2026-11-02T23:59:02Z",
                                        "pts": [[1577970000, 40.0], [1759168800, 55.0]]}
    assert qm["series"]["IBOV"]["pts"] == [[1577970000, 115000.0]]
    assert qm["series"]["AUGO"] == {"updatedAt": "2026-11-02T23:50:00Z", "pts": []}


def test_cauda_junto_com_o_quotes_daily_reproduz_a_serie_leve():
    """A regra do hub (F.3): os pontos daqui ANTERIORES ao 1º do quotes-daily + o quotes-daily
    inteiro = a série leve do site, ponto a ponto."""
    src, _ = F.make_sources(state=F.previous_state())
    qm, qd = quotes_monthly.build(src)["series"], quotes_daily.build(src)["series"]
    for r in F.QUOTES:
        t = r["ticker"]
        daily = qd[t]["pts"]
        first = daily[0][0] if daily else float("inf")
        assert [p for p in qm[t]["pts"] if p[0] < first] + daily == r["daily"], t


def test_cauda_muda_uma_vez_por_mes():
    vale = next(r for r in F.QUOTE_HISTORY if r["ticker"] == "VALE3.SA")["daily"]

    def tail(dua: str):
        return quotes_monthly.monthly_part(F.light_of(vale, dua), dua.replace("+00:00", "Z"))
    a = tail("2026-11-02T23:59:02+00:00")             # corte em 26/10/2025
    c = tail("2026-11-05T01:00:00+00:00")             # corte em 29/10/2025: o mesmo mês → igual
    b = tail("2026-11-20T01:00:00+00:00")             # corte em 13/11/2025: virou o mês → muda
    assert a == c == [[1577970000, 40.0], [1759168800, 55.0]]
    assert b == [[1577970000, 40.0], [1759168800, 55.0], [1759323600, 56.0]]   # outubro inteiro entrou
    # a série leve tem o ponto que anda (o último pregão antes do corte, no mês do corte); a cauda não
    assert [1759323600, 56.0] in F.light_of(vale, "2026-11-02T23:59:02+00:00")
    assert quotes_monthly.monthly_part([[1, 2.0]], None) == []


def monthly_reads(http):
    return [c for c in http.supa_calls("quotes") if c.params["select"] == "ticker,daily,daily_updated_at"]


def test_cauda_sem_leitura_nova_com_a_copia_publicada():
    # o que estaria publicado: o quotes-daily (400 dias) e o quotes-monthly do mesmo dia
    src, _ = F.make_sources(state=S.empty_state())
    pub_qd = gzip_bytes(to_json_bytes(quotes_daily.build(src)))
    pub_qm = gzip_bytes(to_json_bytes(quotes_monthly.build(src)))
    esperado = quotes_monthly.build(src)
    # runner novo: o diário nasce da cópia publicada (cortada em 400 dias) e a cauda, da outra cópia
    http = F.FakeHTTP(published={"quotes-daily.json": pub_qd, "quotes-monthly.json": pub_qm})
    pub = S.PublishedCopies(http=http, env=F.env())
    src, http = F.make_sources(http=http, published=pub, state=S.empty_state(),
                               now=F.NOW + timedelta(minutes=30))
    out = quotes_monthly.build(src)
    assert monthly_reads(http) == []                           # zero leitura da série leve
    assert content_hash(out) == content_hash(esperado)
    # sem a cópia do quotes-monthly (1ª vez): relê a série leve dos papéis cortados, uma vez
    http = F.FakeHTTP(published={"quotes-daily.json": pub_qd})
    pub = S.PublishedCopies(http=http, env=F.env())
    st = S.empty_state()
    src, http = F.make_sources(http=http, published=pub, state=st, now=F.NOW + timedelta(minutes=30))
    assert content_hash(quotes_monthly.build(src)) == content_hash(esperado)
    assert len(monthly_reads(http)) == 1 and monthly_reads(http)[0].params["ticker"].startswith("in.(")
    src, http = F.make_sources(http=F.FakeHTTP(), state=st, now=F.NOW + timedelta(minutes=90))
    quotes_monthly.build(src)
    assert monthly_reads(http) == []                           # o cache agora é a série inteira


def test_copia_mensal_de_outro_mes_ou_que_nao_bate_rele_o_papel():
    src, _ = F.make_sources(state=S.empty_state())
    pub_qd = gzip_bytes(to_json_bytes(quotes_daily.build(src)))
    qm = quotes_monthly.build(src)
    velho = json.loads(json.dumps(qm))
    velho["series"]["VALE3.SA"]["updatedAt"] = "2026-10-01T01:00:00Z"   # corte em setembro: outro mês
    velho["series"]["IBOV"]["pts"] = [[1577970000, 115000.0], [1793365200, 99.0]]   # não bate
    http = F.FakeHTTP(published={"quotes-daily.json": pub_qd,
                                 "quotes-monthly.json": gzip_bytes(to_json_bytes(velho))})
    pub = S.PublishedCopies(http=http, env=F.env())
    src, http = F.make_sources(http=http, published=pub, state=S.empty_state(),
                               now=F.NOW + timedelta(minutes=30))
    out = quotes_monthly.build(src)
    assert [c.params["ticker"] for c in monthly_reads(http)] == ['in.("IBOV","VALE3.SA")']
    assert content_hash(out) == content_hash(qm)
