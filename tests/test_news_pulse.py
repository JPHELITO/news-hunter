"""A régua do termômetro tem de contar EXATAMENTE como a home conta.

O card (index.html: _pulseTally/_pulseGaugeVal/_pulseWindowStartMs) e esta régua são duas
saídas da mesma conta; se divergirem, a calibração futura vai medir a diferença entre os
dois e chamar de "mercado". Estes testes travam a janela, a contagem, o portão e o formato
do lote que vai ao Supabase (chaves uniformes, senão o PostgREST recusa tudo).
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hunter import news_pulse as np_  # noqa: E402

UTC = dt.timezone.utc


def _utc(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s).replace(tzinfo=UTC)


# ── calendário e janelas ────────────────────────────────────────────────────────────────
def test_sessoes_ate_pula_fim_de_semana():
    qua = dt.date(2026, 9, 9)
    assert np_.sessoes_ate(qua, 5) == [dt.date(2026, 9, 3), dt.date(2026, 9, 4),
                                       dt.date(2026, 9, 7), dt.date(2026, 9, 8), qua]


def test_sessoes_ate_num_domingo_recua_para_sexta():
    assert np_.sessoes_ate(dt.date(2026, 9, 6), 1) == [dt.date(2026, 9, 4)]


def test_janela_close_vai_de_fechamento_a_fechamento():
    ini, fim = np_.janela(dt.date(2026, 9, 8), "close")          # terça
    assert ini == _utc("2026-09-07T20:00:00")                     # segunda 17:00 BRT
    assert fim == _utc("2026-09-08T20:00:00")                     # terça 17:00 BRT


def test_janela_de_segunda_comeca_na_sexta():
    ini, fim = np_.janela(dt.date(2026, 9, 7), "09")
    assert ini == _utc("2026-09-04T20:00:00")                     # sexta 17:00
    assert fim == _utc("2026-09-07T12:00:00")                     # segunda 09:00 BRT


def test_janelas_nao_sobrepoem():
    a = np_.janela(dt.date(2026, 9, 7), "close")
    b = np_.janela(dt.date(2026, 9, 8), "close")
    assert a[1] == b[0]


def test_assentado_exige_folga_para_a_ia():
    fim = _utc("2026-09-08T20:00:00")
    assert not np_.assentado(fim, fim + dt.timedelta(hours=1, minutes=59))
    assert np_.assentado(fim, fim + dt.timedelta(hours=2))


# ── a conta ─────────────────────────────────────────────────────────────────────────────
ROWS = [
    {"take_llm": "+", "take_llm_model": "gemini", "take_covered_companies": "VALE; CSN",
     "sector": "mining", "source_name": "Valor", "published_at": "2026-09-08T10:00:00+00:00"},
    {"take_llm": "-", "take_llm_model": "gemini", "take_covered_companies": "VALE",
     "sector": "mining", "source_name": "Platts", "published_at": "2026-09-08T11:00:00+00:00"},
    {"take_llm": "=", "take_llm_model": "mistral", "take_covered_companies": " vale ",
     "sector": "steel", "source_name": "Platts", "published_at": None, "found_at": "2026-09-08T12:00:00+00:00"},
    {"take_llm": "no take", "take_llm_model": "gemini", "take_covered_companies": "KLABIN",
     "sector": "pp", "source_name": "Fastmarkets", "published_at": "2026-09-08T13:00:00+00:00"},
    {"take_llm": None, "take_llm_model": None, "take_covered_companies": "SUZANO",
     "sector": "pp", "source_name": "Fastmarkets", "published_at": "2026-09-08T14:00:00+00:00"},
    {"take_llm": "+", "take_llm_model": "gemini", "take_covered_companies": "",
     "sector": "nr", "source_name": "Reuters", "published_at": "2026-09-08T15:00:00+00:00"},
]


def test_contar_espelha_a_home():
    c = np_.contar(ROWS)
    assert (c["pos"], c["neg"], c["neu"], c["notake"], c["pending"]) == (2, 1, 1, 1, 1)
    assert c["scored"] == 4 and c["n_items"] == 6
    assert c["covered"]["VALE"] == {"n": 3, "pos": 1, "neg": 1, "neu": 1, "notake": 0, "pending": 0}
    assert c["covered"]["CSN"]["n"] == 1                              # veio do "VALE; CSN"
    assert c["covered"]["KLABIN"]["notake"] == 1                      # presença sem tom
    assert c["covered"]["SUZANO"]["pending"] == 1                     # sem IA ainda


def test_impressao_digital_do_instrumento():
    c = np_.contar(ROWS)
    assert c["sources"] == {"Valor": 1, "Platts": 2, "Fastmarkets": 2, "Reuters": 1}
    assert c["models"] == {"gemini": 4, "mistral": 1}                 # pendente não tem modelo


def test_setores_espelham_os_chips():
    assert np_.setores_de({"sector": "steel"}) == {"all", "sm"}
    assert np_.setores_de({"sector": "mining"}) == {"all", "sm"}
    assert np_.setores_de({"sector": "pp"}) == {"all", "pp"}
    assert np_.setores_de({"sector": "nr"}) == {"all"}
    assert np_.setores_de({}) == {"all"}


def test_portao_de_amostra():
    assert np_.ponteiro({"pos": 10, "neg": 9, "neu": 0, "scored": 19}) is None
    assert np_.ponteiro({"pos": 10, "neg": 10, "neu": 0, "scored": 20}) == 50
    assert np_.ponteiro({"pos": 30, "neg": 0, "neu": 30, "scored": 60}) == 75   # neutro ancora
    assert np_.ponteiro({"pos": 0, "neg": 25, "neu": 0, "scored": 25}) == 0


# ── as linhas que vão ao banco ──────────────────────────────────────────────────────────
def test_linhas_so_cortes_assentados_e_chaves_uniformes():
    sessao = dt.date(2026, 9, 8)
    agora = _utc("2026-09-08T13:30:00")           # 10:30 BRT: 07h assentou (09:00), 09h não (11:00)
    rows = np_.linhas(ROWS, [sessao], agora)
    cortes = {r["cut"] for r in rows}
    assert cortes == {"07"}
    assert {r["sector"] for r in rows} == {"all", "sm", "pp"}
    chaves = [tuple(sorted(r)) for r in rows]
    assert len(set(chaves)) == 1                  # todas as linhas com as MESMAS colunas
    r = next(r for r in rows if r["sector"] == "all")
    assert r["window_start"] == "2026-09-07T20:00:00+00:00"
    assert r["window_end"] == "2026-09-08T10:00:00+00:00"   # 07:00 BRT
    assert r["formula"] == np_.FORMULA and r["gauge"] is None


def test_linhas_filtram_por_janela_e_setor():
    sessao = dt.date(2026, 9, 8)
    agora = _utc("2026-09-09T12:00:00")           # tudo assentado
    rows = np_.linhas(ROWS, [sessao], agora)
    close_all = next(r for r in rows if r["cut"] == "close" and r["sector"] == "all")
    assert close_all["n_items"] == 6 and close_all["scored"] == 4
    close_sm = next(r for r in rows if r["cut"] == "close" and r["sector"] == "sm")
    assert close_sm["n_items"] == 3 and set(close_sm["covered"]) == {"VALE", "CSN"}
    close_pp = next(r for r in rows if r["cut"] == "close" and r["sector"] == "pp")
    assert close_pp["n_items"] == 2 and set(close_pp["covered"]) == {"KLABIN", "SUZANO"}
    c07 = next(r for r in rows if r["cut"] == "07" and r["sector"] == "all")
    assert c07["n_items"] == 0                    # tudo foi publicado depois das 07:00 BRT


def test_linhas_ignoram_manchete_sem_tempo():
    rows = np_.linhas([{"take_llm": "+"}], [dt.date(2026, 9, 8)], _utc("2026-09-09T12:00:00"))
    assert all(r["n_items"] == 0 for r in rows)


# ── gravação: tabela ausente é aviso, erro de verdade é erro ───────────────────────────
class _Resp:
    def __init__(self, status: int, text: str = ""):
        self.status_code, self.text, self.ok = status, text, status < 400


def test_gravar_tabela_ausente_nao_derruba(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "k")
    monkeypatch.setattr(np_.requests, "post",
                        lambda *a, **k: _Resp(404, '{"code":"PGRST205","message":"Could not find the table"}'))
    assert np_.gravar([{"a": 1}]) == 0


def test_gravar_erro_real_levanta(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "k")
    monkeypatch.setattr(np_.requests, "post", lambda *a, **k: _Resp(500, "boom"))
    with pytest.raises(RuntimeError):
        np_.gravar([{"a": 1}])


def test_gravar_usa_on_conflict_da_chave(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "k")
    urls = []

    def post(url, **k):
        urls.append(url)
        assert "merge-duplicates" in k["headers"]["Prefer"]
        return _Resp(201)
    monkeypatch.setattr(np_.requests, "post", post)
    assert np_.gravar([{"a": 1}] * 450, lote=200) == 450
    assert len(urls) == 3 and all("on_conflict=session_date,sector,cut" in u for u in urls)
