"""Testes do histórico diário completo (hunter/quote_history.py + a manutenção em
hunter/prices.py::update_quote_history).

O que estes testes TRAVAM, e por quê:
  • que a série longa é pedida por period1/period2 e NUNCA por range=max — com range=max
    o Yahoo ignora o interval e devolve barras MENSAIS (medido: VALE3 = 320 pontos em
    26 anos), que foi justamente a limitação que este trabalho veio remover;
  • que a costura é por DIA e o ponto NOVO vence — o epoch do Yahoo carrega o horário de
    abertura do pregão e muda com horário de verão, então comparar epoch cru duplicaria
    o mesmo dia;
  • que um SPLIT novo força re-puxar a série INTEIRA em vez de append — o Yahoo reescreve
    o passado retroativamente (fixture real da NVDA, 10:1 em 10/06/2024) e um append
    deixaria metade da série em outra escala, com um degrau falso e nenhum erro no log;
  • que a sessão que a janela curta devolve CARIMBADA é reconstruída pela série horária
    (e papel parado continua buraco) — sem isso o apagão da SGO deixava gravado, como
    fechamento, o preço de minutos depois da abertura, para sempre (2026-10-19).

Rodar: python -m pytest tests/test_quote_history.py -v
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import hunter
import hunter.quote_history as qh
import hunter.prices as prices


DAY = 86400
# Um dia útil "às 13:30 UTC" e o MESMO dia "às 14:30 UTC" (horário de verão) — o caso
# que faria a costura por epoch cru gravar o dia duas vezes.
D1 = 10 * DAY + 13 * 3600 + 1800
D1_DST = 10 * DAY + 14 * 3600 + 1800
D2 = 11 * DAY + 13 * 3600 + 1800


# ───────────────────────── parsing do payload do Yahoo ─────────────────────────
def _payload(ts, closes, splits=None):
    res = {"timestamp": list(ts), "indicators": {"quote": [{"close": list(closes)}]}}
    if splits is not None:
        res["events"] = {"splits": {str(s): {"date": s, "numerator": 10.0,
                                             "denominator": 1.0, "splitRatio": "10:1"}}
                         for s in splits}
    return res


class TestParse:
    def test_descarta_close_nulo(self):
        pts = qh._closes(_payload([1, 2, 3], [10.0, None, 12.0]))
        assert pts == [[1, 10.0], [3, 12.0]]

    def test_ordena_crescente(self):
        pts = qh._closes(_payload([3, 1, 2], [12.0, 10.0, 11.0]))
        assert [p[0] for p in pts] == [1, 2, 3]

    def test_result_vazio_nao_explode(self):
        assert qh._closes(None) == []
        assert qh._splits(None) == []

    def test_le_split_do_events(self):
        # fixture com a forma REAL devolvida pelo Yahoo p/ o split 10:1 da NVDA
        assert qh._splits(_payload([1], [10.0], splits=[1718026200])) == [1718026200]

    def test_sem_events_nao_inventa_split(self):
        assert qh._splits(_payload([1], [10.0])) == []


# ───────────────────────────── costura por dia ─────────────────────────────
class TestMerge:
    def test_acrescenta_dia_novo(self):
        assert qh.merge([[D1, 10.0]], [[D2, 11.0]]) == [[D1, 10.0], [D2, 11.0]]

    def test_mesmo_dia_o_novo_vence(self):
        # fechamento de hoje ainda em formação, substituído pelo definitivo amanhã
        out = qh.merge([[D1, 10.0]], [[D1, 10.5]])
        assert out == [[D1, 10.5]]

    def test_mesmo_dia_em_horario_diferente_nao_duplica(self):
        # ⚠️ o caso do horário de verão: mesmo pregão, epoch diferente
        out = qh.merge([[D1, 10.0]], [[D1_DST, 10.7]])
        assert len(out) == 1 and out[0][1] == 10.7

    def test_saida_sempre_ordenada(self):
        out = qh.merge([[D2, 11.0]], [[D1, 10.0]])
        assert [p[0] for p in out] == [D1, D2]

    def test_listas_vazias(self):
        assert qh.merge([], []) == []
        assert qh.merge(None, [[D1, 9.0]]) == [[D1, 9.0]]


# ────────────────── a série longa NÃO pode ser pedida com range=max ──────────────────
class TestFetchFullUsaPeriod:
    def test_manda_period1_period2_e_nunca_range(self, monkeypatch):
        visto = {}

        def fake_chart(symbol, params, timeout=30):
            visto.update(params)
            return _payload([D1, D2], [1.0, 2.0])

        monkeypatch.setattr(qh, "_chart", fake_chart)
        qh.fetch_full("VALE3.SA")
        assert "period1" in visto and "period2" in visto
        assert visto.get("interval") == "1d"
        # o dia em que alguém trocar isto por range=max, o histórico vira mensal calado
        assert "range" not in visto

    def test_janela_curta_pede_eventos_de_split(self, monkeypatch):
        visto = {}

        def fake_chart(symbol, params, timeout=30):
            visto.update(params)
            return _payload([D2], [2.0], splits=[D2])

        monkeypatch.setattr(qh, "_chart", fake_chart)
        pts, splits = qh.fetch_recent("VALE3.SA", days=7)
        assert visto.get("range") == "7d"
        assert "split" in (visto.get("events") or "")
        assert pts == [[D2, 2.0]] and splits == [D2]


# ────────── sessão carimbada na janela curta: a série se cura sozinha ──────────
def _barras(barras):
    """[(ts, o, h, l, c, v), ...] -> payload `result` do Yahoo com OHLCV."""
    campos = ("open", "high", "low", "close", "volume")
    return {"timestamp": [b[0] for b in barras],
            "indicators": {"quote": [{k: [b[i + 1] for b in barras] for i, k in enumerate(campos)}]}}


class TestFetchRecentReconstroiFantasma:
    """O apagão da SGO (2026-10-19): a barra de hoje vem real durante o pregão e no dia
    seguinte vira carimbo; o robô ficava com o preço de minutos depois da abertura."""

    def _yahoo(self, monkeypatch, diario, horario):
        pedidos = []

        def fake_chart(symbol, params, timeout=30):
            pedidos.append(dict(params))
            return diario if params.get("interval") == "1d" else horario

        monkeypatch.setattr(qh, "_chart", fake_chart)
        return pedidos

    def test_sessao_carimbada_vem_do_intradiario(self, monkeypatch):
        diario = _barras([(D1, 1040.0, 1052.0, 1036.0, 1049.0, 900_000),     # real
                          (D2, 957.0, 957.0, 957.0, 957.0, 0)])               # CARIMBO
        horario = _barras([(D2 + 3600, 1060.0, 1061.0, 1050.0, 1055.0, 300_000),
                           (D2 + 6 * 3600, 1062.0, 1062.0, 1060.0, 1060.0, 290_000)])
        pedidos = self._yahoo(monkeypatch, diario, horario)
        pts, _ = qh.fetch_recent("CMPC.SN", days=7)
        assert pts == [[D1, 1049.0], [D2, 1060.0]]                # o ÚLTIMO candle da sessão
        assert pedidos[1]["interval"] == "1h" and pedidos[1]["range"] == "10d"

    def test_ultimo_candle_sem_volume_e_o_fechamento(self, monkeypatch):
        # CMPC em 13/10: o candle das 18:30 UTC veio com volume 0 e o fechamento certo.
        diario = _barras([(D2, 957.0, 957.0, 957.0, 957.0, 0)])
        horario = _barras([(D2 + 3600, 955.0, 958.0, 950.0, 955.0, 250_000),
                           (D2 + 5 * 3600, 965.0, 965.0, 965.0, 965.0, 0)])
        self._yahoo(monkeypatch, diario, horario)
        assert qh.fetch_recent("CMPC.SN")[0] == [[D2, 965.0]]

    def test_papel_parado_continua_buraco(self, monkeypatch):
        # VMM.AX em ago/26: halt na bolsa — sem NENHUM negócio no intradiário não existe
        # fechamento a recuperar, e carimbo não entra (armadilha 3).
        diario = _barras([(D1, 4.0, 4.4, 3.9, 4.3, 50_000),
                          (D2, 4.3, 4.3, 4.3, 4.3, 0)])
        horario = _barras([(D2 + 3600, 4.3, 4.3, 4.3, 4.3, 0)])
        self._yahoo(monkeypatch, diario, horario)
        assert qh.fetch_recent("VMM.AX")[0] == [[D1, 4.3]]

    def test_sem_carimbo_nao_pede_o_horario(self, monkeypatch):
        # O caso de todo dia em todas as outras bolsas: nenhuma requisição a mais.
        diario = _barras([(D1, 10.0, 11.0, 9.0, 10.5, 1000), (D2, 10.5, 11.0, 10.0, 10.8, 900)])
        pedidos = self._yahoo(monkeypatch, diario, None)
        assert qh.fetch_recent("VALE3.SA")[0] == [[D1, 10.5], [D2, 10.8]]
        assert len(pedidos) == 1

    def test_horario_fora_do_ar_nao_derruba(self, monkeypatch):
        diario = _barras([(D1, 1040.0, 1052.0, 1036.0, 1049.0, 900_000),
                          (D2, 957.0, 957.0, 957.0, 957.0, 0)])
        self._yahoo(monkeypatch, diario, None)                    # _chart devolveu None
        assert qh.fetch_recent("CMPC.SN")[0] == [[D1, 1049.0]]   # = o comportamento antigo


# ─────────── índice sem barra diária: o diário sai da série horária ───────────
class TestDiarioPeloHorario:
    """MSCI IPSA (MXIPSAPC.SN), 28/10/2026: interval=1d devolve SÓ a barra de hoje, em
    qualquer janela; a série de 1 h existe desde o lançamento (01/09/2026). Armadilha 5."""

    # Dois pregões da SGO (12:30–20:00 UTC) com candles de hora em hora — índice: volume 0.
    P1 = D1 - 3600                                   # 12:30 UTC do dia 10
    P2 = D2 - 3600                                   # 12:30 UTC do dia 11

    def _velas(self, *pregoes):
        ts, cs = [], []
        for abre, valores in pregoes:
            for i, v in enumerate(valores):
                ts.append(abre + i * 3600)
                cs.append(v)
        return {"timestamp": ts, "indicators": {"quote": [{"close": cs, "volume": [0] * len(ts)}]}}

    def _yahoo(self, monkeypatch, res):
        pedidos = []

        def fake_chart(symbol, params, timeout=30):
            pedidos.append(dict(params))
            return res

        monkeypatch.setattr(qh, "_chart", fake_chart)
        return pedidos

    def test_um_ponto_por_pregao_carimbo_da_abertura_valor_do_ultimo(self, monkeypatch):
        res = self._velas((self.P1, [5400.0, 5410.0, 5395.5]), (self.P2, [5380.0, None, 5346.97]))
        self._yahoo(monkeypatch, res)
        assert qh._diario_pelo_horario("MXIPSAPC.SN", 10) == [[self.P1, 5395.5], [self.P2, 5346.97]]

    def test_pregao_que_cruza_a_meia_noite_utc_e_um_so(self, monkeypatch):
        # ASX: 23:00 → 05:12 UTC. Agrupar pela data UTC partiria o pregão em dois.
        abre = 10 * DAY + 23 * 3600
        res = self._velas((abre, [8800.0, 8810.0, 8820.0, 8830.0, 8840.0, 8847.3]))
        self._yahoo(monkeypatch, res)
        assert qh._diario_pelo_horario("X", 10) == [[abre, 8847.3]]

    def test_carimbo_casa_com_a_barra_diaria_da_mesma_bolsa(self, monkeypatch):
        # a costura e a home comparam POR DIA: o índice tem de cair no mesmo dia das ações
        res = self._velas((self.P1, [1.0, 2.0]), (self.P2, [3.0]))
        self._yahoo(monkeypatch, res)
        assert [qh._day(t) for t, _ in qh._diario_pelo_horario("MXIPSAPC.SN", 10)] == [10, 11]

    def test_fetch_full_pede_o_horario_inteiro_e_nunca_o_diario(self, monkeypatch):
        pedidos = []

        def fake_chart(symbol, params, timeout=30):
            pedidos.append((symbol, dict(params)))
            return self._velas((self.P1, [1.0]), (self.P2, [2.0]))

        monkeypatch.setattr(qh, "_chart", fake_chart)
        assert len(qh.fetch_full("MXIPSAPC.SN")) == 2       # < 2 o robô descartaria a série
        do_indice = [p for s, p in pedidos if s == "MXIPSAPC.SN"]
        assert do_indice == [{"range": "730d", "interval": "1h"}]   # o ÍNDICE nunca pelo diário

    def test_fetch_recent_janela_com_folga_e_sem_split(self, monkeypatch):
        pedidos = self._yahoo(monkeypatch, self._velas((self.P2, [5401.84])))
        assert qh.fetch_recent("MXIPSAPC.SN", days=7) == ([[self.P2, 5401.84]], [])
        assert pedidos == [{"range": "10d", "interval": "1h"}]

    def test_horario_fora_do_ar_devolve_vazio(self, monkeypatch):
        self._yahoo(monkeypatch, None)
        assert qh.fetch_recent("MXIPSAPC.SN") == ([], [])
        assert qh.fetch_full("MXIPSAPC.SN") == []

    def test_outros_simbolos_seguem_no_diario(self, monkeypatch):
        pedidos = self._yahoo(monkeypatch, _payload([D1, D2], [1.0, 2.0]))
        qh.fetch_full("^GSPTSE")
        assert pedidos[0].get("interval") == "1d" and "period1" in pedidos[0]


# ─────────── a PONTE: o passado do IPSA antes de 01/09 vem do ETF IT NOW ───────────
class TestPonteHistorica:
    """Decisão do usuário (28/10/2026): antes do 1º dado do MSCI IPSA, os RETORNOS do ETF IT NOW
    IPSA, na escala do índice (mediana da razão nos primeiros pregões em comum)."""

    @staticmethod
    def _dias(pares):                         # [(dia, valor)] -> [[epoch 12:30 UTC do dia, valor]]
        return [[d * DAY + 45000, v] for d, v in pares]

    def test_emenda_na_escala_do_indice_e_o_indice_vence_na_sobreposicao(self):
        indice = self._dias([(10, 100.0), (11, 102.0), (12, 101.0)])
        ponte = self._dias([(5, 50.0), (8, 49.0), (10, 50.0), (11, 51.0), (12, 50.5)])
        assert qh._emendar(indice, ponte) == self._dias([(5, 100.0), (8, 98.0)]) + indice

    def test_preserva_os_retornos_do_substituto(self):
        indice = self._dias([(10, 5733.7), (11, 5700.0), (12, 5690.0)])
        ponte = self._dias([(3, 5000.0), (4, 5100.0), (10, 5263.0), (11, 5232.0), (12, 5223.0)])
        out = qh._emendar(indice, ponte)
        assert abs(out[1][1] / out[0][1] - 5100.0 / 5000.0) < 1e-6

    def test_escala_pela_mediana_um_negocio_torto_nao_move(self):
        # 3º pregão em comum com o ETF parado num preço velho (ilíquido): razão 3,0 — ignorada
        indice = self._dias([(10, 100.0), (11, 100.0), (12, 300.0), (13, 100.0), (14, 100.0)])
        ponte = self._dias([(9, 49.0), (10, 50.0), (11, 50.0), (12, 100.0), (13, 50.0), (14, 50.0)])
        assert qh._emendar(indice, ponte)[0] == self._dias([(9, 98.0)])[0]

    def test_usa_so_os_primeiros_pregoes_em_comum(self):
        indice = self._dias([(10, 100.0), (11, 100.0), (12, 100.0), (13, 300.0), (14, 300.0)])
        ponte = self._dias([(9, 10.0), (10, 50.0), (11, 50.0), (12, 50.0), (13, 50.0), (14, 50.0)])
        assert qh._emendar(indice, ponte, n=3)[0][1] == 20.0          # k = 2, não 6

    def test_sobreposicao_curta_fica_sem_ponte(self):
        indice = self._dias([(10, 100.0), (11, 101.0)])
        ponte = self._dias([(5, 50.0), (10, 50.0), (11, 50.5)])
        assert qh._emendar(indice, ponte) == indice                  # 2 pregões em comum < 3

    def test_sem_substituto_ou_sem_indice(self):
        indice = self._dias([(10, 100.0)])
        assert qh._emendar(indice, []) == indice
        assert qh._emendar([], self._dias([(5, 1.0)])) == []

    def test_serie_do_substituto_reconstroi_o_carimbo_recente(self, monkeypatch):
        hoje = int(time.time()) // DAY
        real = (hoje - 9) * DAY + 45000
        carimbo = (hoje - 8) * DAY + 45000
        diario = _barras([(real, 4900.0, 4950.0, 4890.0, 4940.0, 1200),
                          (carimbo, 4940.0, 4940.0, 4940.0, 4940.0, 0)])      # feed morto da SGO
        horario = _barras([(carimbo + 3600, 4950.0, 4960.0, 4945.0, 4955.0, 300),
                           (carimbo + 7200, 4955.0, 4975.0, 4950.0, 4971.5, 280)])

        def fake_chart(symbol, params, timeout=30):
            return diario if params.get("interval") == "1d" else horario

        monkeypatch.setattr(qh, "_chart", fake_chart)
        assert qh._serie_ponte("CFMITNIPSA.SN") == [[real, 4940.0], [carimbo, 4971.5]]

    def test_fetch_full_do_ipsa_vem_emendado(self, monkeypatch):
        hoje = int(time.time()) // DAY
        abre = lambda d: (hoje - d) * DAY + 45000
        etf = _barras([(abre(400), 1, 1, 1, 4000.0, 9), (abre(5), 1, 1, 1, 5000.0, 9),
                       (abre(4), 1, 1, 1, 5050.0, 9), (abre(3), 1, 1, 1, 5000.0, 9)])
        msci = {"timestamp": [abre(5), abre(4), abre(3)],
                "indicators": {"quote": [{"close": [5500.0, 5555.0, 5500.0], "volume": [0, 0, 0]}]}}

        def fake_chart(symbol, params, timeout=30):
            return msci if symbol == "MXIPSAPC.SN" else etf

        monkeypatch.setattr(qh, "_chart", fake_chart)
        out = qh.fetch_full("MXIPSAPC.SN")
        assert out[0] == [abre(400), 4400.0] and out[1:] == [[abre(5), 5500.0], [abre(4), 5555.0], [abre(3), 5500.0]]

    def test_so_o_ipsa_tem_ponte(self):
        assert qh.PONTE_HISTORICA == {"MXIPSAPC.SN": "CFMITNIPSA.SN"}
        assert set(qh.PONTE_HISTORICA) <= qh.DIARIO_PELO_HORARIO


# ─────────── benchmarks do heatmap relativo: um índice por bolsa ───────────
class TestBenchmarksDasBolsas:
    """O front escolhe o benchmark pelo `exchange` do papel (BENCH_BY_EXCH no index.html):
    o índice tem de estar no QUOTES_LIST com o MESMO código de bolsa das ações."""

    ESPERADO = {"IBOV": "B3", "SPX": "US", "SPTSX": "TSX", "ASX200": "ASX",
                "MEXBOL": "BMV", "IPSA": "BCS", "FTSE100": "LSE", "OMXH25": "HEL"}

    def test_cada_benchmark_existe_como_indice_na_bolsa_certa(self):
        lista = {q[0]: q for q in prices.QUOTES_LIST}
        for tk, bolsa in self.ESPERADO.items():
            assert tk in lista, tk
            assert lista[tk][2] == "index" and lista[tk][3] == bolsa, lista[tk]

    def test_toda_bolsa_com_acao_tem_benchmark(self):
        bolsas = {q[3] for q in prices.QUOTES_LIST if q[2] != "index"}
        # AMS (MT.AS) é insumo do blast das 06h e não aparece no heatmap; NASDAQ/NYSE → SPX
        sem = bolsas - set(self.ESPERADO.values()) - {"NYSE", "NASDAQ", "AMS"}
        assert not sem, sem

    def test_ipsa_e_o_da_msci_pelo_horario(self):
        # o ^IPSA da S&P morreu em 01/09/2026 (404 no Yahoo) — não voltar para ele
        lista = {q[0]: q for q in prices.QUOTES_LIST}
        assert lista["IPSA"][5] == "MXIPSAPC.SN" and "MXIPSAPC.SN" in qh.DIARIO_PELO_HORARIO


# ───────────── decisão do dia a dia: append x re-puxar a série inteira ─────────────
class _Espiao:
    """Substitui o módulo quote_history dentro do update_quote_history."""

    def __init__(self, estado, recent=(), splits=()):
        self.estado = estado
        self._recent = list(recent)
        self._splits = list(splits)
        self.fulls = []          # tickers cuja série INTEIRA foi puxada
        self.appends = []        # (ticker, n_pontos, replace)

    def load_state(self):
        return self.estado

    def fetch_recent(self, qsym, days=7):
        return list(self._recent), list(self._splits)

    def fetch_full(self, qsym):
        self.fulls.append(qsym)
        return [[D1, 1.0], [D2, 2.0]]

    def append(self, ticker, points, last_split_ts=None, replace=False):
        self.appends.append((ticker, len(points), replace))
        return {"n_points": len(points)}


def _roda(monkeypatch, espiao, tickers=(("VALE3.SA", "VALE3.SA"),)):
    """Executa update_quote_history com o Supabase e a lista de papéis mockados."""
    monkeypatch.setenv("SUPABASE_URL", "https://exemplo.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "chave")
    monkeypatch.setattr(prices, "QUOTES_LIST",
                        [(tk, "n", "steel", "B3", "yahoo", qs) for tk, qs in tickers])

    class _R:
        ok = True

        @staticmethod
        def json():
            return [{"ticker": tk, "daily_updated_at": None} for tk, _ in tickers]

    monkeypatch.setattr(prices.requests, "get", lambda *a, **k: _R())
    # `from hunter import quote_history` resolve o ATRIBUTO do pacote (o módulo já está
    # importado), não o sys.modules — então é o atributo que precisa ser trocado.
    monkeypatch.setattr(hunter, "quote_history", espiao)
    return prices.update_quote_history()


class TestManutencaoDiaria:
    def test_papel_sem_historico_puxa_serie_inteira(self, monkeypatch):
        e = _Espiao(estado={})
        _roda(monkeypatch, e)
        assert e.fulls == ["VALE3.SA"]
        assert e.appends and e.appends[0][2] is True      # replace=True

    def test_dia_comum_so_faz_append(self, monkeypatch):
        e = _Espiao(estado={"VALE3.SA": {"points": 6681, "last_split_ts": None}},
                    recent=[[D2, 2.0]])
        _roda(monkeypatch, e)
        assert e.fulls == []                              # NÃO re-baixa o passado
        assert e.appends == [("VALE3.SA", 1, False)]

    def test_split_novo_forca_serie_inteira(self, monkeypatch):
        e = _Espiao(estado={"VALE3.SA": {"points": 6681, "last_split_ts": None}},
                    recent=[[D2, 2.0]], splits=[1718026200])
        _roda(monkeypatch, e)
        assert e.fulls == ["VALE3.SA"]                    # o passado foi reescrito
        assert e.appends[0][2] is True                    # substitui, não costura

    def test_split_ja_conhecido_nao_repuxa(self, monkeypatch):
        e = _Espiao(estado={"VALE3.SA": {"points": 6681, "last_split_ts": 1718026200}},
                    recent=[[D2, 2.0]], splits=[1718026200])
        _roda(monkeypatch, e)
        assert e.fulls == []
        assert e.appends == [("VALE3.SA", 1, False)]

    def test_teto_de_series_inteiras_por_ciclo(self, monkeypatch):
        # 12 papéis sem histórico: o hunt-loop roda a cada 5 min e não pode engordar
        alvos = [(f"T{i}.SA", f"T{i}.SA") for i in range(12)]
        e = _Espiao(estado={})
        _roda(monkeypatch, e, tickers=alvos)
        assert len(e.fulls) == prices._FULL_PER_RUN
        # e quem ficou de fora NÃO entrou com série curta (senão nunca faria o full)
        assert len(e.appends) == prices._FULL_PER_RUN

    def test_sem_tabela_cai_no_caminho_antigo(self, monkeypatch):
        e = _Espiao(estado=None)                          # None = tabela não existe
        chamou = {}
        monkeypatch.setattr(prices, "_update_quotes_daily_legacy",
                            lambda stale, url, key: chamou.setdefault("n", len(stale)))
        _roda(monkeypatch, e)
        assert chamou.get("n") == 1                       # a home não fica sem série
        assert e.fulls == [] and e.appends == []
