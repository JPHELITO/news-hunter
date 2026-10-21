"""
Testes do "resumo da última hora" (hunter/digest.py) — a Home mostra um texto AUTOMÁTICO para o
cliente, então o que estes testes travam é, nesta ordem:

  1) a IA só fala do que está nos títulos: número, ticker ou tamanho fora da regra = texto recusado;
  2) o robô de notícias nunca cai por causa do resumo, e não gasta IA se a tabela não existe;
  3) a cota: primeiro o projeto Gemini do clipping; o dos takes só com folga; hora vazia não chama IA;
  4) o relógio: 13 janelas de hora (7h–20h) + a madrugada, prontas aos 10 min, lista depois dos 25.

Nenhum teste chama IA de verdade (regra do projeto: avaliação nunca usa as IAs grátis da produção).
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hunter import digest as D      # noqa: E402

BRT = D.BRT


def at(h, m=0, day=21):
    return datetime(2026, 10, day, h, m, tzinfo=BRT)


def art(title, take="+", covs="", src="S&P Platts", minute=5, url=None, hour=10):
    return {"url": url or f"https://x/{abs(hash(title))}", "title": title, "take_llm": take,
            "take_covered_companies": covs or None, "source_name": src,
            "found_at": at(hour, minute).astimezone(timezone.utc).isoformat()}


# ── 4) o relógio ─────────────────────────────────────────────────────────────────────────────────
class TestRelogio:
    def test_hora_fica_pronta_aos_10_minutos(self):
        assert D.due_slots(at(9, 9)) == []
        (kind, s, e, fase), = D.due_slots(at(9, 10))
        assert (kind, s.hour, e.hour, fase) == ("hour", 8, 9, "ai")

    def test_depois_dos_25_minutos_sai_so_a_lista(self):
        (_, _, _, fase), = D.due_slots(at(9, 26))
        assert fase == "list"

    def test_depois_dos_40_minutos_a_janela_e_pulada(self):
        assert D.due_slots(at(9, 41)) == []

    def test_primeira_e_ultima_janela_do_dia(self):
        (k1, s1, e1, _), = D.due_slots(at(8, 12))
        assert (k1, s1.hour, e1.hour) == ("hour", 7, 8)
        (k2, s2, e2, _), = D.due_slots(at(20, 12))
        assert (k2, s2.hour, e2.hour) == ("hour", 19, 20)
        assert D.due_slots(at(21, 12)) == []           # 20h–21h é madrugada: sai junto às 7h10
        assert D.due_slots(at(6, 12)) == []

    def test_madrugada_sai_as_7h10_e_cobre_das_20h_da_vespera(self):
        (kind, s, e, fase), = D.due_slots(at(7, 12))
        assert kind == "overnight" and fase == "ai"
        assert (s.day, s.hour, e.day, e.hour) == (20, 20, 21, 7)

    def test_catorze_janelas_por_dia(self):
        vistas = set()
        t = at(0, 0)
        while t < at(23, 59):
            for kind, s, e, fase in D.due_slots(t):
                vistas.add((kind, s.isoformat()))
            t += timedelta(minutes=5)
        assert len(vistas) == 14
        assert sum(1 for k, _ in vistas if k == "overnight") == 1


# ── a regra fixa ─────────────────────────────────────────────────────────────────────────────────
class TestRegra:
    def test_coberta_com_take_vem_primeiro(self):
        its = [art("Asian HRC prices rise on cost support", "+"),
               art("O que impulsiona a alta de 5% da CSN (CSNA3) nesta terça", "+", "CSN", "Money Times"),
               art("Turkish scrap price inches up", "=")]
        out = D.select(its)
        assert out[0]["tickers"] == ["CSNA3"] and out[0]["take"] == "+"

    def test_no_take_de_fonte_unica_fica_de_fora(self):
        its = [art("Suzano e SENAI oferecem cursos gratuitos na Bahia", "no take", "SUZANO", "Portal Celulose")]
        assert D.select(its) == []

    def test_sem_take_e_sem_coberta_nao_entra(self):
        assert D.select([art("Some industry conference opens in Lisbon", None, src="Some Blog")]) == []

    def test_mesma_historia_em_duas_fontes_vira_uma_so_e_ganha_ponto(self):
        its = [art("Copper hits record high as supply tightens", "+", src="Mining.com"),
               art("Copper hits a record high on tight supply", "+", src="Reuters")]
        out = D.select(its)
        assert len(out) == 1 and "Mining.com" in out[0]["source"] and "Reuters" in out[0]["source"]

    def test_nao_repete_o_que_saiu_nas_janelas_anteriores(self):
        its = [art("Vale nega decisão sobre IPO da unidade de metais básicos", "=", "VALE", "Estadão")]
        anteriores = ["Vale (VALE3) nega ter decisão sobre IPO da unidade de metais básicos; entenda"]
        assert D.select(its, anteriores) == []

    def test_boletim_diario_sem_noticia_cai(self):
        assert D.select([art("[SMM Coking Coal and Coke Daily Brief] 20260909", "-", src="SMM")]) == []

    def test_no_maximo_quatro_por_hora_e_cinco_na_madrugada(self):
        titulos = ("Asian HRC prices rise on firmer cost support", "Copper hits record high in London",
                   "Pulp NBSK index broadly stable in Europe", "China steel exports jump in August",
                   "Iron ore futures climb in Dalian", "Kraftliner buyers report US supply issues",
                   "Turkish scrap import price inches up")
        its = [art(t, "+", src=f"Fonte{i}") for i, t in enumerate(titulos)]
        assert len(D.select(its, kind="hour")) == 4
        assert len(D.select(its, kind="overnight")) == 5


# ── 1) a IA só fala do que está nos títulos ──────────────────────────────────────────────────────
ITEMS = [{"title": "O que impulsiona a alta de 5% da CSN (CSNA3) nesta terça", "tickers": ["CSNA3"]},
         {"title": "Minério de ferro avança 1,36% na bolsa de Dalian", "tickers": []},
         {"title": "Copper set to test $15,000 as investors await US data", "tickers": []},
         {"title": "Produção de veículos sobe 9,4% em agosto, diz Anfavea", "tickers": []}]


class TestConferencia:
    def test_texto_fiel_passa(self):
        s = ("CSN (CSNA3) shares rose 5% and iron ore gained 1.36% in Dalian. Copper looks set to test $15,000, "
             "and Brazil's vehicle output rose 9.4% in August (Anfavea).")
        assert D.check_summary(s, ITEMS) is None

    def test_numero_inventado_e_recusado(self):
        assert "número" in D.check_summary("CSN (CSNA3) shares rose 7% as iron ore gained in Dalian today.", ITEMS)

    def test_ano_inventado_e_recusado(self):
        assert D.check_summary("Iron ore gained 1.36% in Dalian, its best day since 2019 for the market.", ITEMS)

    def test_ticker_nao_dado_e_recusado(self):
        assert "ticker" in D.check_summary("Vale (VALE3) and CSN (CSNA3) rose 5% with iron ore in Dalian.", ITEMS)

    def test_ticker_de_coberta_fora_do_padrao_b3_tambem(self):
        assert "ticker" in D.check_summary("Southern Copper (SCCO) gained as copper looks set to test $15,000.", ITEMS)

    def test_digitos_do_ticker_nao_contam_como_numero(self):
        assert D.check_summary("CSN (CSNA3) shares rose 5% on Tuesday as iron ore climbed in Dalian.", ITEMS) is None

    def test_texto_longo_demais_e_recusado(self):
        assert "palavras" in D.check_summary("word " * 75, ITEMS)

    def test_molde_do_raciocinio_e_recusado(self):
        assert D.check_summary("...", ITEMS) == "curto demais"

    def test_mil_vira_milhar(self):
        its = [{"title": "Cobre pode testar US$ 15 mil por tonelada", "tickers": []}]
        assert D.check_summary("Copper may test USD 15,000 per tonne, the headline said today.", its) is None


class TestResposta:
    def test_json_puro(self):
        assert D.parse_answer('{"summary": "Iron ore rose."}') == "Iron ore rose."

    def test_json_depois_do_raciocinio(self):
        raw = '<think>maybe {"summary": "..."}</think>\n{"summary": "Copper hit a record high."}'
        assert D.parse_answer(raw) == "Copper hit a record high."

    def test_json_em_cerca(self):
        assert D.parse_answer('```json\n{"summary": "Pulp prices fell."}\n```') == "Pulp prices fell."

    def test_lixo(self):
        assert D.parse_answer("sorry, I cannot") is None and D.parse_answer("") is None

    def test_prompt_tem_titulos_tickers_e_a_hora(self):
        p = D.build_prompt([{"title": "CSN sobe 5%", "tickers": ["CSNA3"], "take": "+", "source": "Money Times"}],
                           "hour", "11:00-12:00", previous="Iron ore rose in Asia.")
        assert "CSN sobe 5%" in p and "tickers: CSNA3" in p and "11:00-12:00" in p and "Iron ore rose in Asia." in p
        assert "at most 45 words" in p

    def test_prompt_leva_os_dois_ultimos_resumos(self):
        p = D.build_prompt([{"title": "S&P corta nota da CSN para B-", "tickers": ["CSNA3"], "take": "-", "source": "Valor"}],
                           "hour", "08:00-09:00", previous=["Vale plans panda bonds.", "S&P cut CSN to B-.", "terceiro"])
        assert '"Vale plans panda bonds."' in p and '"S&P cut CSN to B-."' in p and "terceiro" not in p
        assert "Do not repeat them" in p


# ── 3) a cota ────────────────────────────────────────────────────────────────────────────────────
def _gem_ok(text="CSN (CSNA3) shares rose 5% as iron ore gained 1.36% in Dalian today."):
    r = mock.Mock(ok=True)
    r.json.return_value = {"candidates": [{"content": {"parts": [{"text": json.dumps({"summary": text})}]}}],
                           "usageMetadata": {"promptTokenCount": 312, "candidatesTokenCount": 41}}
    return r


class TestCota:
    def test_usa_primeiro_o_projeto_do_clipping(self):
        with mock.patch.dict("os.environ", {"CLIPPING_TRANSLATE_KEY": "kc", "GEMINI_API_KEY": "kt"}), \
             mock.patch.object(D.requests, "post", return_value=_gem_ok()) as post, \
             mock.patch.object(D, "_takes_have_room") as room:
            got = D.write_summary(ITEMS, "hour", "11:00-12:00", None)
        assert got and "clipping" in got["model"] and got["tokens_in"] == 312
        assert post.call_args.kwargs["headers"]["x-goog-api-key"] == "kc"
        room.assert_not_called()                      # nem pergunta pela cota dos takes

    def test_projeto_dos_takes_so_com_folga(self):
        falha = mock.Mock(ok=False, status_code=429)
        with mock.patch.dict("os.environ", {"CLIPPING_TRANSLATE_KEY": "kc", "GEMINI_API_KEY": "kt"}), \
             mock.patch.object(D.requests, "post", return_value=falha) as post, \
             mock.patch.object(D, "_takes_have_room", return_value=False):
            assert D.write_summary(ITEMS, "hour", "11:00-12:00", None) is None
        assert post.call_count == 1                   # só o do clipping

    def test_cai_para_o_dos_takes_quando_ha_folga(self):
        falha = mock.Mock(ok=False, status_code=429)
        with mock.patch.dict("os.environ", {"CLIPPING_TRANSLATE_KEY": "kc", "GEMINI_API_KEY": "kt"}), \
             mock.patch.object(D.requests, "post", side_effect=[falha, _gem_ok()]), \
             mock.patch.object(D, "_takes_have_room", return_value=True):
            got = D.write_summary(ITEMS, "hour", "11:00-12:00", None)
        assert got and "takes" in got["model"]

    def test_sem_a_chave_do_clipping_usa_o_dos_takes(self):
        # o incidente de 21/10: o job do hunt-loop ainda não tinha a env nova e a 1ª versão não chamava ninguém
        with mock.patch.dict("os.environ", {"CLIPPING_TRANSLATE_KEY": "", "GEMINI_API_KEY": "kt"}), \
             mock.patch.object(D.requests, "post", return_value=_gem_ok()) as post, \
             mock.patch.object(D, "_takes_have_room", return_value=True):
            got = D.write_summary(ITEMS, "hour", "11:00-12:00", None)
        assert got and "takes" in got["model"]
        assert post.call_args.kwargs["headers"]["x-goog-api-key"] == "kt"

    def test_sem_chave_nenhuma_nao_chama_ninguem(self):
        with mock.patch.dict("os.environ", {"CLIPPING_TRANSLATE_KEY": "", "GEMINI_API_KEY": ""}), \
             mock.patch.object(D.requests, "post") as post:
            assert D.write_summary(ITEMS, "hour", "11:00-12:00", None) is None
        post.assert_not_called()

    def test_texto_recusado_nao_e_publicado(self):
        with mock.patch.dict("os.environ", {"CLIPPING_TRANSLATE_KEY": "kc", "GEMINI_API_KEY": ""}), \
             mock.patch.object(D.requests, "post", return_value=_gem_ok("CSN (CSNA3) jumped 12% on the day.")):
            assert D.write_summary(ITEMS, "hour", "11:00-12:00", None) is None


# ── 2) o robô nunca cai, e não gasta IA à toa ────────────────────────────────────────────────────
ENV = {"LLM_TAKES_ENABLED": "1", "GITHUB_ACTIONS": "true", "SUPABASE_URL": "https://sb.test",
       "SUPABASE_SERVICE_KEY": "svc", "CLIPPING_TRANSLATE_KEY": "kc"}


def _resp(ok=True, status=200, data=None, text=""):
    r = mock.Mock(ok=ok, status_code=status, text=text)
    r.json.return_value = data if data is not None else []
    r.raise_for_status = mock.Mock()
    return r


class TestPasso:
    def test_desligado_fora_do_hunt_loop(self, tmp_path):
        with mock.patch.dict("os.environ", {"GITHUB_ACTIONS": "", "RUNNER_TEMP": str(tmp_path)}), \
             mock.patch.object(D.requests, "get") as get:
            D.run_digest(at(9, 12))
        get.assert_not_called()

    def test_sem_tabela_nao_chama_ia(self, tmp_path):
        with mock.patch.dict("os.environ", {**ENV, "RUNNER_TEMP": str(tmp_path)}), \
             mock.patch.object(D.requests, "get", return_value=_resp(False, 404, text='relation "news_digest" does not exist')), \
             mock.patch.object(D.requests, "post") as post:
            D.run_digest(at(9, 12))
        post.assert_not_called()

    def test_hora_sem_nada_relevante_grava_quieta_sem_ia(self, tmp_path):
        gets = [_resp(data=[]),                                   # janela ainda não gravada
                _resp(data=[art("Some conference opens", None, src="Blog", hour=8)]),   # notícias
                _resp(data=[])]                                   # janelas anteriores
        with mock.patch.dict("os.environ", {**ENV, "RUNNER_TEMP": str(tmp_path)}), \
             mock.patch.object(D.requests, "get", side_effect=gets), \
             mock.patch.object(D.requests, "post", return_value=_resp()) as post:
            D.run_digest(at(9, 12))
        (call,) = post.call_args_list
        assert "news_digest" in call.args[0] and call.kwargs["json"][0]["status"] == "quiet"

    def test_hora_com_noticia_publica_o_texto(self, tmp_path):
        news = [art("O que impulsiona a alta de 5% da CSN (CSNA3) nesta terça", "+", "CSN", "Money Times", hour=8),
                art("Minério de ferro avança 1,36% na bolsa de Dalian", "+", src="Valor Econômico", hour=8)]
        gets = [_resp(data=[]), _resp(data=news), _resp(data=[])]
        posts = [_gem_ok(), _resp()]                               # IA, depois a gravação
        with mock.patch.dict("os.environ", {**ENV, "RUNNER_TEMP": str(tmp_path)}), \
             mock.patch.object(D.requests, "get", side_effect=gets), \
             mock.patch.object(D.requests, "post", side_effect=posts) as post:
            D.run_digest(at(9, 12))
        row = post.call_args_list[-1].kwargs["json"][0]
        assert row["status"] == "ok" and row["summary"].startswith("CSN (CSNA3)") and row["tokens_out"] == 41
        assert row["kind"] == "hour" and len(row["items"]) == 2
        # a mesma janela não é refeita na rodada seguinte (memória do job)
        with mock.patch.dict("os.environ", {**ENV, "RUNNER_TEMP": str(tmp_path)}), \
             mock.patch.object(D.requests, "get") as get2:
            D.run_digest(at(9, 17))
        get2.assert_not_called()

    def test_ia_fora_na_fase_da_ia_nao_grava_e_tenta_depois(self, tmp_path):
        news = [art("O que impulsiona a alta de 5% da CSN (CSNA3) nesta terça", "+", "CSN", "Money Times", hour=8)]
        gets = [_resp(data=[]), _resp(data=news), _resp(data=[])]
        with mock.patch.dict("os.environ", {**ENV, "RUNNER_TEMP": str(tmp_path)}), \
             mock.patch.object(D.requests, "get", side_effect=gets), \
             mock.patch.object(D.requests, "post", return_value=mock.Mock(ok=False, status_code=503)) as post, \
             mock.patch.object(D, "_takes_have_room", return_value=False):
            D.run_digest(at(9, 12))
        assert all("news_digest" not in c.args[0] for c in post.call_args_list)

    def test_depois_dos_25_minutos_grava_a_lista_sem_chamar_ia(self, tmp_path):
        news = [art("O que impulsiona a alta de 5% da CSN (CSNA3) nesta terça", "+", "CSN", "Money Times", hour=8)]
        gets = [_resp(data=[]), _resp(data=news), _resp(data=[])]
        with mock.patch.dict("os.environ", {**ENV, "RUNNER_TEMP": str(tmp_path)}), \
             mock.patch.object(D.requests, "get", side_effect=gets), \
             mock.patch.object(D.requests, "post", return_value=_resp()) as post:
            D.run_digest(at(9, 30))
        (call,) = post.call_args_list
        assert call.kwargs["json"][0]["status"] == "list" and call.kwargs["json"][0]["summary"] is None

    def test_qualquer_erro_vira_log_nunca_excecao(self, tmp_path):
        with mock.patch.dict("os.environ", {**ENV, "RUNNER_TEMP": str(tmp_path)}), \
             mock.patch.object(D.requests, "get", side_effect=RuntimeError("rede caiu")):
            D.run_digest(at(9, 12))                                # não levanta

    def test_desligavel_pela_env(self, tmp_path):
        with mock.patch.dict("os.environ", {**ENV, "DIGEST_ENABLED": "0", "RUNNER_TEMP": str(tmp_path)}), \
             mock.patch.object(D.requests, "get") as get:
            D.run_digest(at(9, 12))
        get.assert_not_called()
