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
    deixaria metade da série em outra escala, com um degrau falso e nenhum erro no log.

Rodar: python -m pytest tests/test_quote_history.py -v
"""
import sys
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
