"""Testes da BARRA FANTASMA — feed morto disfarçado de mercado parado.

O incidente (2026-08-26): a série diária da Bolsa de Santiago morreu em 17/07 e o Yahoo
passou 27 pregões devolvendo, por papel, uma barra com `open=high=low=close` e volume
zero. HTTP 200, série completa, nenhum erro. CMPC/COPEC/CAP viraram linha reta no gráfico
enquanto a CAP caía 16% e a COPEC subia 7%; o fechamento gravado chegou a 19% de erro.

O que estes testes TRAVAM, e por quê:
  • que a assinatura exige as DUAS condições (volume zero **e** OHLC iguais) — volume zero
    sozinho derrubaria índice (^BVSP não reporta volume) e OHLC igual sozinho derrubaria
    papel travado em leilão legítimo;
  • que a falta de dado (volume ou OHLC ausentes) faz a barra PASSAR — melhor deixar
    entrar uma fantasma do que descartar pregão bom;
  • que o filtro vale para barra DIÁRIA e **não** para intradiária — candle de 5 minutos
    sem negócio é rotina e o sparkline do dia quer linha contínua, não buracos;
  • que a reconstrução casa a sessão pela JANELA DA PRÓPRIA BARRA (`[epoch, +24h)`) e não
    pela data UTC — em bolsa a leste (ASX) a sessão cruza a meia-noite UTC e agrupar por
    data partiria o pregão em dois;
  • que dia fantasma SEM fonte intradiária é REMOVIDO, nunca inventado;
  • que o vigia mede o estrago contra o valor RECONSTRUÍDO e não contra o carimbo — o
    carimbo às vezes calha de ser o preço certo (a CMPC em 22/07 fechou exatamente nos
    1.070 que o feed vinha repetindo), e comparar com ele mandaria e-mail todo dia sobre
    um número correto.

Rodar: python -m pytest tests/test_phantom_bars.py -v
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import hunter.prices as prices
import hunter.quote_history as qh
import scripts.prices_watchdog as wd
import scripts.repair_phantom_bars as rp

DIA = 86400
# Pregão "às 13:30 UTC" — o carimbo real da Bolsa de Santiago no inverno chileno.
D1 = 100 * DIA + 13 * 3600 + 1800
D2 = 101 * DIA + 13 * 3600 + 1800
D3 = 102 * DIA + 13 * 3600 + 1800


def _diario(barras):
    """[(ts, o, h, l, c, v), ...] -> payload `result` do Yahoo."""
    ts = [b[0] for b in barras]
    campos = ("open", "high", "low", "close", "volume")
    quote = {k: [b[i + 1] for b in barras] for i, k in enumerate(campos)}
    return {"timestamp": ts, "indicators": {"quote": [quote]}}


# ───────────────────────── a assinatura da barra fantasma ─────────────────────────
class TestEhFantasma:
    def test_ohlc_igual_e_volume_zero(self):
        assert qh.eh_fantasma(1070.0, 1070.0, 1070.0, 1070.0, 0) is True

    def test_volume_zero_mas_ohlc_variando_passa(self):
        # Índice não reporta volume negociado; a barra é boa.
        assert qh.eh_fantasma(1060.0, 1075.0, 1055.0, 1070.0, 0) is False

    def test_ohlc_igual_com_volume_passa(self):
        # Papel travado em leilão de verdade: houve negócio, o fechamento existe.
        assert qh.eh_fantasma(1070.0, 1070.0, 1070.0, 1070.0, 4_067_659) is False

    def test_volume_ausente_passa(self):
        assert qh.eh_fantasma(1070.0, 1070.0, 1070.0, 1070.0, None) is False

    def test_ohlc_ausente_passa(self):
        assert qh.eh_fantasma(None, None, None, 1070.0, 0) is False


class TestClosesDescartaFantasma:
    def test_descarta_so_a_fantasma(self):
        pts = qh._closes(_diario([
            (D1, 1099.0, 1099.0, 1064.3, 1070.0, 4_067_659),   # pregão real
            (D2, 1070.0, 1070.0, 1070.0, 1070.0, 0),           # FANTASMA
            (D3, 1045.0, 1050.9, 1036.9, 1037.0, 604_895),     # pregão real
        ]))
        assert pts == [[D1, 1070.0], [D3, 1037.0]]

    def test_payload_sem_ohlcv_passa_inteiro(self):
        # Fixtures antigas (e endpoints que só devolvem close) não podem ser filtradas.
        res = {"timestamp": [D1, D2], "indicators": {"quote": [{"close": [10.0, 10.0]}]}}
        assert qh._closes(res) == [[D1, 10.0], [D2, 10.0]]


class TestGranularidade:
    def test_diario_e_maior_filtram(self):
        assert prices._e_diario("1d") and prices._e_diario("1wk") and prices._e_diario("1mo")

    def test_intradiario_nao_filtra(self):
        # O sparkline do dia quer linha contínua; candle de 5min sem negócio é rotina.
        assert not prices._e_diario("5m")
        assert not prices._e_diario("1h")
        assert not prices._e_diario("")


# ─────────────────────────────── a reconstrução ───────────────────────────────
class TestFechamentoDaSessao:
    def test_pega_o_ultimo_ponto_da_janela_da_barra(self):
        intra = [[D1 + 3600, 1080.0], [D1 + 7200, 1075.0], [D2 + 3600, 1050.0]]
        assert rp.fechamento_da_sessao(intra, D1) == 1075.0
        assert rp.fechamento_da_sessao(intra, D2) == 1050.0

    def test_sessao_que_cruza_a_meia_noite_utc_nao_se_parte(self):
        # ASX: barra carimbada às 23:00 UTC, sessão seguindo pela madrugada UTC adentro.
        abertura = 100 * DIA + 23 * 3600
        intra = [[abertura + 1800, 4.30], [abertura + 3 * 3600, 4.19]]   # já é "o dia seguinte" em UTC
        assert rp.fechamento_da_sessao(intra, abertura) == 4.19

    def test_sem_ponto_na_janela_devolve_none(self):
        assert rp.fechamento_da_sessao([[D1 + 3600, 1080.0]], D3) is None


class TestReconstruir:
    def test_troca_o_carimbo_pelo_fechamento_real(self):
        guardada = [[D1, 1070.0], [D2, 1070.0], [D3, 1037.0]]
        fantasmas = [[D2, 1070.0]]
        intra = [[D2 + 3600, 1090.0], [D2 + 7200, 1083.9]]
        corrigida, trocados, removidos = rp.reconstruir(guardada, fantasmas, intra)
        assert corrigida == [[D1, 1070.0], [D2, 1083.9], [D3, 1037.0]]
        assert trocados == [[D2, 1070.0, 1083.9]]
        assert removidos == []

    def test_carimbo_que_calha_de_estar_certo_nao_vira_troca(self):
        guardada = [[D1, 1070.0], [D2, 1070.0], [D3, 1037.0]]
        intra = [[D2 + 7200, 1070.0]]
        corrigida, trocados, removidos = rp.reconstruir(guardada, [[D2, 1070.0]], intra)
        assert corrigida == guardada and trocados == [] and removidos == []

    def test_dia_sem_fonte_e_REMOVIDO_nunca_inventado(self):
        guardada = [[D1, 1070.0], [D2, 1070.0], [D3, 1037.0]]
        corrigida, trocados, removidos = rp.reconstruir(guardada, [[D2, 1070.0]], [[D1, 9.0]])
        assert corrigida == [[D1, 1070.0], [D3, 1037.0]]
        assert removidos == [[D2, 1070.0]] and trocados == []

    def test_preenche_dia_do_incidente_que_faltava_DENTRO_do_intervalo(self):
        guardada = [[D1, 1070.0], [D3, 1037.0]]            # D2 nunca entrou na série
        intra = [[D2 + 7200, 1088.0]]
        corrigida, trocados, _ = rp.reconstruir(guardada, [[D2, 1070.0]], intra)
        assert corrigida == [[D1, 1070.0], [D2, 1088.0], [D3, 1037.0]]
        assert trocados == [[D2, None, 1088.0]]

    def test_NAO_estica_a_serie_para_fora_do_intervalo_coberto(self):
        # Inventar histórico onde o papel nunca teve série não é consertar.
        guardada = [[D2, 1070.0], [D3, 1037.0]]
        intra = [[D1 + 7200, 1099.0]]
        corrigida, trocados, _ = rp.reconstruir(guardada, [[D1, 1070.0]], intra)
        assert corrigida == guardada and trocados == []


class TestAferir:
    def test_mede_a_fonte_contra_o_fechamento_oficial(self):
        reais = [[D1, 100.0], [D2, 200.0]]
        intra = [[D1 + 3600, 100.0], [D2 + 3600, 202.0]]     # 2º dia erra 1%
        af = rp.aferir(reais, intra)
        assert af["n"] == 2 and af["identicos"] == 1
        assert abs(af["pior"] - 1.0) < 1e-9

    def test_encaixe_errado_estoura_o_limiar(self):
        # É esta conta que recusa reconstrução mal-encaixada antes de gravar.
        reais = [[D1, 100.0], [D2, 200.0]]
        intra = [[D1 + 3600, 180.0], [D2 + 3600, 90.0]]
        assert rp.aferir(reais, intra)["medio"] > rp.ERRO_MEDIO_MAX


# ──────────────────────────────────── o vigia ────────────────────────────────────
class TestCorridasFantasma:
    def test_agrupa_dias_seguidos_e_ignora_solto(self):
        barras = [(D1, 9.0, 9.0, 9.0, 9.0, 0)]                      # solta
        barras.append((D2, 1.0, 2.0, 0.5, 1.5, 10))                 # real
        base = 200 * DIA
        for i in range(4):                                          # corrida de 4
            barras.append((base + i * DIA, 7.0, 7.0, 7.0, 7.0, 0))
        corridas = wd.corridas_fantasma(_diario(barras))
        assert [c[2] for c in corridas] == [4]                      # a de 1 dia não entra

    def test_corrida_no_fim_da_serie_e_fechada(self):
        barras = [(D1, 1.0, 2.0, 0.5, 1.5, 10),
                  (D2, 7.0, 7.0, 7.0, 7.0, 0),
                  (D3, 7.0, 7.0, 7.0, 7.0, 0)]
        assert wd.corridas_fantasma(_diario(barras)) == [(D2, D3, 2)]


class TestNaoReparados:
    def _serie(self, monkeypatch, pontos):
        monkeypatch.setattr(wd.qh, "load_series", lambda _t: pontos)

    def test_serie_ja_consertada_nao_alarma(self, monkeypatch):
        self._serie(monkeypatch, [[D2, 1083.9]])
        intra = [[D2 + 7200, 1083.9]]
        assert wd.nao_reparados("X", {D2 // DIA: 1070.0}, intra) == 0

    def test_carimbo_ainda_no_banco_alarma(self, monkeypatch):
        self._serie(monkeypatch, [[D2, 1070.0]])
        intra = [[D2 + 7200, 1083.9]]
        assert wd.nao_reparados("X", {D2 // DIA: 1070.0}, intra) == 1

    def test_carimbo_que_e_o_preco_certo_NAO_alarma(self, monkeypatch):
        # O caso real da CMPC em 22/07 — comparar com o carimbo mandaria e-mail
        # todo dia sobre um número correto.
        self._serie(monkeypatch, [[D2, 1070.0]])
        intra = [[D2 + 7200, 1070.0]]
        assert wd.nao_reparados("X", {D2 // DIA: 1070.0}, intra) == 0

    def test_sem_intradiario_nao_alarma(self, monkeypatch):
        self._serie(monkeypatch, [[D2, 1070.0]])
        assert wd.nao_reparados("X", {D2 // DIA: 1070.0}, []) == 0


class TestPontosFantasma:
    def test_devolve_so_as_fantasmas_da_corrida(self):
        res = _diario([
            (D1, 9.0, 9.0, 9.0, 9.0, 0),                    # fantasma FORA da janela
            (D2, 7.0, 7.0, 7.0, 7.0, 0),                    # dentro
            (D3, 1.0, 2.0, 0.5, 1.5, 10),                   # real, dentro
        ])
        assert wd.pontos_fantasma(res, D2, D3) == {D2 // DIA: 7.0}
