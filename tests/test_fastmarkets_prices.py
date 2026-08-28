# -*- coding: utf-8 -*-
"""Preços PIX de celulose (Fastmarkets) — captura, conversão do resale e publicação.

Cobre as quatro coisas que podem quebrar calado:
  1. a conta do RESALE (yuan com VAT → dólar), que é a fórmula da planilha do analista;
  2. o parse da API (`midChangeSincePreviousProportion` é PROPORÇÃO, não porcentagem —
     0.005779 é +0,58%);
  3. o histórico PAGO nunca ir para `commodities.daily`, que o navegador do cliente lê;
  4. preço nunca derrubar a coleta de manchetes, que é o trabalho principal do scraper.
"""
from datetime import datetime, timezone

import pytest

from hunter import fastmarkets_scraper as fm
from hunter import prices as pr


def _ep(dia: str) -> int:
    return int(datetime.fromisoformat(dia + "T00:00:00+00:00").timestamp())


def _dia(ep: int) -> str:
    return datetime.fromtimestamp(ep, timezone.utc).strftime("%Y-%m-%d")


# ── 1. resale: yuan com VAT → dólar ──────────────────────────────────────────
# Números REAIS de 28/08/2026, conferidos contra a planilha `FOEX - Price Database.xlsm`
# (aba DATA, colunas E/F e G/H). Se algum dia a conta mudar, é aqui que dói primeiro.
FX_28AGO = 6.7198


def test_reproduz_o_numero_da_planilha_do_analista():
    # a função arredonda a 4 casas (é preço, não constante) → tolerância 1e-4
    assert pr.resale_cny_para_usd(4544, FX_28AGO) == pytest.approx(576.0943685903, abs=1e-4)
    assert pr.resale_cny_para_usd(4870, FX_28AGO) == pytest.approx(619.0265355032, abs=1e-4)


def test_a_conta_e_tira_vat_descontar_e_converter():
    """Ordem importa: o desconto de 150 é em YUAN e depois do VAT."""
    esperado = (4544 / 1.13 - 150) / FX_28AGO
    assert pr.resale_cny_para_usd(4544, FX_28AGO) == pytest.approx(esperado, abs=1e-4)
    # trocar a ordem (descontar antes do VAT) daria outro número — trava contra "arrumar"
    assert pr.resale_cny_para_usd(4544, FX_28AGO) != pytest.approx((4544 - 150) / 1.13 / FX_28AGO)


def test_cambio_invalido_devolve_none_em_vez_de_numero_errado():
    for fx in (0, -1, None, "", "n/a"):
        assert pr.resale_cny_para_usd(4544, fx) is None
    assert pr.resale_cny_para_usd(None, FX_28AGO) is None


def test_desconto_fixo_faz_a_variacao_em_dolar_diferir_da_em_yuan():
    """É POR ISTO que a variação é recalculada, não copiada da API."""
    a, b = 4412, 4544                       # +2,99% em yuan
    ua = pr.resale_cny_para_usd(a, FX_28AGO)
    ub = pr.resale_cny_para_usd(b, FX_28AGO)
    em_yuan = (b / a - 1) * 100
    em_dolar = (ub / ua - 1) * 100
    assert em_yuan == pytest.approx(2.99, abs=0.01)
    assert em_dolar == pytest.approx(3.11, abs=0.02)
    assert abs(em_dolar - em_yuan) > 0.1, "o desconto fixo tem que mover a base"


# ── 2. parse da API ──────────────────────────────────────────────────────────
class _Resp:
    def __init__(self, payload, ok=True, status=200):
        self._p, self.ok, self.status = payload, ok, status

    def json(self):
        return self._p

    def text(self):
        return str(self._p)


class _Ctx:
    """Contexto fake do Playwright: só o ctx.request.post que _collect_prices usa."""

    def __init__(self, resp):
        self.chamadas = []
        ctx = self

        class _Req:
            def post(self, url, **kw):
                ctx.chamadas.append((url, kw))
                return resp

        self.request = _Req()


PAYLOAD = {"instruments": [{
    "symbol": "FP-PLP-0034",
    "prices": [   # a API devolve do mais NOVO para o mais velho
        {"date": "2026-08-28", "mid": 638.72, "midChangeSincePreviousProportion": 0.005779},
        {"date": "2026-08-21", "mid": 635.05, "midChangeSincePreviousProportion": -0.001},
        {"date": "2026-08-14", "mid": 635.69, "midChangeSincePreviousProportion": 0.002},
    ]}]}


def test_parse_preco_variacao_e_data():
    out = fm._collect_prices(_Ctx(_Resp(PAYLOAD)), "Bearer x")
    d = out["FP-PLP-0034"]
    assert d["price"] == 638.72
    assert d["change_pct"] == pytest.approx(0.5779)      # proporção → porcentagem
    assert d["assessed_at"] == "2026-08-28"


def test_serie_sai_em_ordem_crescente():
    serie = fm._collect_prices(_Ctx(_Resp(PAYLOAD)), "Bearer x")["FP-PLP-0034"]["series"]
    assert [_dia(e) for e, _ in serie] == ["2026-08-14", "2026-08-21", "2026-08-28"]


def test_ordem_da_api_nao_importa():
    """Preço, variação e data saem da MESMA linha — a mais recente POR DATA."""
    bagunçado = {"instruments": [{"symbol": "FP-PLP-0034", "prices": [
        {"date": "2026-08-14", "mid": 635.69, "midChangeSincePreviousProportion": 0.002},
        {"date": "2026-08-28", "mid": 638.72, "midChangeSincePreviousProportion": 0.005779},
        {"date": "2026-08-21", "mid": 635.05, "midChangeSincePreviousProportion": -0.001},
    ]}]}
    d = fm._collect_prices(_Ctx(_Resp(bagunçado)), "Bearer x")["FP-PLP-0034"]
    assert (d["price"], d["assessed_at"]) == (638.72, "2026-08-28")
    assert d["change_pct"] == pytest.approx(0.5779)


def test_ponto_sem_data_nao_vira_assessed_none():
    """`str(None)[:10]` daria a STRING 'None' — que é verdadeira e iria pro banco."""
    furado = {"instruments": [{"symbol": "FP-PLP-0034", "prices": [
        {"date": None, "mid": 999.0},
        {"date": "2026-08-28", "mid": 638.72},
    ]}]}
    d = fm._collect_prices(_Ctx(_Resp(furado)), "Bearer x")["FP-PLP-0034"]
    assert d["assessed_at"] == "2026-08-28" and d["price"] == 638.72


def test_pede_exatamente_os_simbolos_registrados():
    ctx = _Ctx(_Resp(PAYLOAD))
    fm._collect_prices(ctx, "Bearer x")
    pedidos = ctx.chamadas[0][1]["multipart"]["symbols"].split(",")
    assert pedidos == list(fm._PRICE_SYMBOLS)


def test_simbolo_de_fora_da_lista_e_ignorado():
    intruso = {"instruments": [{"symbol": "FP-PLP-9999",
                                "prices": [{"date": "2026-08-28", "mid": 1.0}]}]}
    assert fm._collect_prices(_Ctx(_Resp(intruso)), "Bearer x") == {}


# ── 3. preço é acessório: nunca derruba a coleta ─────────────────────────────
def test_sem_bearer_devolve_vazio_sem_chamar_a_api():
    ctx = _Ctx(_Resp(PAYLOAD))
    assert fm._collect_prices(ctx, "") == {}
    assert ctx.chamadas == []


def test_erro_http_devolve_vazio():
    assert fm._collect_prices(_Ctx(_Resp({}, ok=False, status=403)), "Bearer x") == {}


def test_excecao_na_chamada_devolve_vazio():
    class _Req:
        def post(self, *a, **kw):
            raise RuntimeError("rede caiu")

    class _Boom:
        request = _Req()

    assert fm._collect_prices(_Boom(), "Bearer x") == {}


# ── 4. publicação na tabela commodities ──────────────────────────────────────
@pytest.fixture
def upserts(monkeypatch):
    feitos = []
    monkeypatch.setattr(pr, "_supa_upsert", lambda t, rows: feitos.append((t, rows)) or len(rows))
    monkeypatch.setattr(pr, "_cny_por_usd", lambda: FX_28AGO)
    return feitos


def _import(assessed="2026-08-28"):
    return {"price": 638.72, "change_pct": 0.5779, "assessed_at": assessed,
            "series": [[_ep("2026-08-21"), 635.05], [_ep("2026-08-28"), 638.72]]}


def _resale(assessed="2026-08-28"):
    return {"price": 4544.0, "change_pct": 2.9918, "assessed_at": assessed,
            "series": [[_ep("2026-08-21"), 4412.0], [_ep("2026-08-28"), 4544.0]]}


def _por_code(rows):
    return {r["code"]: r for r in rows}


def test_importacao_vai_como_veio(upserts):
    pr.update_fastmarkets_commodities({"FP-PLP-0034": _import()})
    tabela, rows = upserts[0]
    r = _por_code(rows)["PULP_NBSK_CHINA"]
    assert tabela == "commodities"
    assert r["price"] == 638.72 and r["unit"] == "USD/t"
    assert r["change_pct"] == pytest.approx(0.5779)     # a da fonte, intacta
    assert r["assessed_at"] == "2026-08-28"


def test_resale_e_publicado_em_dolar(upserts):
    pr.update_fastmarkets_commodities({"FP-PLP-0068": _resale()})
    r = _por_code(upserts[0][1])["PULP_EUCA_RESALE_CN"]
    assert r["unit"] == "USD/t", "o cartão não pode dizer yuan"
    # o nome carrega a FIBRA (eucalipto = BHKP), para parear com o Net da mesma fibra
    assert r["name"] == "BHKP China Resale"
    assert r["price"] == pytest.approx(576.0943685903, abs=1e-4)
    assert r["price"] != 4544.0


def test_variacao_do_resale_e_recalculada_em_dolar(upserts):
    pr.update_fastmarkets_commodities({"FP-PLP-0068": _resale()})
    r = _por_code(upserts[0][1])["PULP_EUCA_RESALE_CN"]
    assert r["change_pct"] == pytest.approx(3.11, abs=0.02)
    assert r["change_pct"] != pytest.approx(2.9918, abs=0.01), "não pode copiar a da fonte"


def test_sem_cambio_o_resale_e_pulado_mas_o_resto_entra(monkeypatch):
    feitos = []
    monkeypatch.setattr(pr, "_supa_upsert", lambda t, rows: feitos.append(rows) or len(rows))
    monkeypatch.setattr(pr, "_cny_por_usd", lambda: None)
    pr.update_fastmarkets_commodities({"FP-PLP-0068": _resale(), "FP-PLP-0034": _import()})
    codes = set(_por_code(feitos[0]))
    assert codes == {"PULP_NBSK_CHINA"}, "sem câmbio, resale mantém o valor antigo"


def test_nunca_grava_a_serie_paga_em_daily(upserts):
    """`market.html` lê `commodities?select=*` — tudo ali vai pro navegador do cliente.
    O histórico da Fastmarkets é PAGO e mora na tabela privada `commodity_history`."""
    pr.update_fastmarkets_commodities({"FP-PLP-0068": _resale(), "FP-PLP-0034": _import()})
    for r in upserts[0][1]:
        assert "daily" not in r and "daily_updated_at" not in r, r["code"]


def test_simbolo_que_nao_veio_e_pulado_mantendo_o_valor_antigo(upserts):
    pr.update_fastmarkets_commodities({"FP-PLP-0034": _import()})
    assert len(upserts[0][1]) == 1, "só o símbolo capturado pode ir para o banco"


def test_sem_precos_nao_toca_no_banco(upserts):
    assert pr.update_fastmarkets_commodities({}) == 0
    assert upserts == []


# ── 5. os dois arquivos têm que combinar ─────────────────────────────────────
def test_simbolos_do_scraper_e_do_publicador_batem():
    """Comentário dos dois arquivos promete isto — o teste cobra."""
    assert set(fm._PRICE_SYMBOLS) == set(pr.FASTMARKETS_COMMODITIES)


def test_os_dois_de_resale_estao_registrados_como_tal():
    assert pr._FM_RESALE_CNY <= set(pr.FASTMARKETS_COMMODITIES)
    for sym in pr._FM_RESALE_CNY:
        assert pr.FASTMARKETS_COMMODITIES[sym][2] == "USD/t"


def test_nome_do_resale_usa_a_fibra_nao_a_especie():
    """A fonte fala em eucalipto/pinus radiata; o analista lê BHKP/NBSK. O `code` guarda
    a espécie (não renomear: é chave no banco e no histórico), o NOME guarda a fibra."""
    nomes = {c: n for c, n, _ in pr.FASTMARKETS_COMMODITIES.values()}
    assert nomes["PULP_EUCA_RESALE_CN"] == "BHKP China Resale"
    assert nomes["PULP_RADIATA_RESALE_CN"] == "NBSK China Resale"
    # e cada Resale tem o Net da MESMA fibra publicado junto
    assert nomes["PULP_BHKP_CHINA"] == "BHKP China Net"
    assert nomes["PULP_NBSK_CHINA"] == "NBSK China Net"


def test_codes_sao_unicos_e_nao_colidem_com_os_do_platts():
    codes = [c for c, _, _ in pr.FASTMARKETS_COMMODITIES.values()]
    assert len(codes) == len(set(codes))
    platts = {c for c, _, _ in pr.PLATTS_COMMODITIES.values()}
    assert not (set(codes) & platts)
