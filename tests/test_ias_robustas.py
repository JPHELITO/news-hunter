"""
IAs dos takes mais robustas — o disjuntor de lotação, o motivo de cada queda e o 2º Gemini.

O QUE ESTAVA ACONTECENDO (medido em 21/10/2026, 45 dias de banco)
-----------------------------------------------------------------
6.890 takes: Gemini 94%, GLM 6%. Os 420 do GLM caíam quase todos entre 10h e 15h de Brasília,
em dia útil. Sondas nesse dia: o gemini-3.1-flash-lite respondeu "503 This model is currently
experiencing high demand" (lotação do Google, não cota nossa) várias vezes na mesma tarde; o
gemini-3.5-flash-lite respondeu 250 de 250. O GLM também respondeu "1305 overloaded".

O QUE A MEDIÇÃO DE QUALIDADE MUDOU NO PLANO
-------------------------------------------
Julgamento às cegas pelo Claude, contra o próprio prompt, das 100 manchetes que o GLM classificou
em produção: nas 31 em que os três modelos divergiram, o GLM acertou 22, o 3.1 16 e o 3.5 só 10.
Por isso: (a) NÃO existe "refazer pelo Gemini" o que saiu do GLM — pioraria; (b) o 3.5 entra por
ÚLTIMO, como rede de segurança quando Gemini e GLM caem juntos.

O QUE ESTES TESTES TRAVAM
-------------------------
1. A cadeia padrão: gemini → zai → gemini_b (o 2º Gemini por último, na mesma chave). A Mistral
   saiu em 21/10: conta grátis com limite zero, passou a exigir cartão.
2. Lotação (5xx) não segura a rodada: 1 nova tentativa por manchete e, depois de 2 manchetes
   seguidas, o provedor sai da rodada. O --ping do vigia continua com as 3 tentativas de sempre.
3. O motivo de cada queda fica registrado mesmo quando outro degrau salva a manchete.
4. Resumo da hora e tradução do clipping ganham o 2º Gemini (texto, não julgamento de take).

Só o `requests` é falso; nenhuma IA é chamada.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hunter import llm_take     # noqa: E402

OK_JSON = '{"take": "+", "reason": "r", "confidence": 0.8}'
MSG_503 = "This model is currently experiencing high demand. Spikes in demand are usually temporary."
GLM = "glm-4.5-flash"


class Resp:
    def __init__(self, status=200, payload=None, headers=None):
        self.status_code = status
        self.ok = 200 <= status < 300
        self._payload = payload if payload is not None else {}
        self.headers = headers or {}
        self.text = str(self._payload)

    def json(self):
        return self._payload


def gemini_ok(take="+"):
    return Resp(200, {"candidates": [{"content": {"parts": [{"text": OK_JSON.replace('"+"', f'"{take}"')}]}}]})


def gemini_503():
    return Resp(503, {"error": {"code": 503, "message": MSG_503, "status": "UNAVAILABLE"}})


def openai_ok(take="="):
    return Resp(200, {"choices": [{"message": {"content": OK_JSON.replace('"+"', f'"{take}"')}}]})


def zai_lotado():
    return Resp(429, {"error": {"code": "1305", "message": "The service may be temporarily overloaded"}},
                headers={"retry-after": "3600"})


M1 = llm_take.PROVIDERS["gemini"]["model"]
M2 = llm_take.PROVIDERS["gemini_b"]["model"]


@pytest.fixture
def motor(monkeypatch):
    """Cadeia gemini → zai → gemini_b com chaves falsas, sem espera e sem prompt do banco."""
    for p in ("gemini", "gemini_b", "zai"):
        monkeypatch.setitem(llm_take.PROVIDERS[p], "key", "k")
    monkeypatch.setitem(llm_take.PROVIDERS["zai"], "model", GLM)
    monkeypatch.setattr(llm_take, "CHAIN", ["gemini", "zai", "gemini_b"])
    monkeypatch.setattr(llm_take, "_SYSTEM", "sys")
    monkeypatch.setattr(llm_take, "_FEWSHOT", [])
    monkeypatch.setattr(llm_take, "_throttle", lambda p: None)
    monkeypatch.setattr(llm_take.time, "sleep", lambda s: None)
    llm_take.reset_run_skips()
    llm_take.LAST_DETAIL.clear()
    yield
    llm_take.reset_run_skips()


def roteiro(respostas_por_modelo):
    """requests.post falso: devolve, por modelo, a próxima resposta da lista (a última se repete)."""
    chamadas = []

    def post(url, json=None, headers=None, timeout=None):
        modelo = next(m for m in respostas_por_modelo if m in url or (json or {}).get("model") == m)
        chamadas.append(modelo)
        fila = respostas_por_modelo[modelo]
        return fila.pop(0) if len(fila) > 1 else fila[0]

    return post, chamadas


# ── 1. a cadeia ─────────────────────────────────────────────────────────────────

def test_cadeia_padrao_poe_o_segundo_gemini_por_ultimo():
    src = Path(llm_take.__file__).read_text(encoding="utf-8")
    assert '"LLM_CHAIN", "gemini,zai,gemini_b"' in src
    assert llm_take.PROVIDERS["gemini_b"]["style"] == "gemini"
    assert M2 != M1


def test_segundo_gemini_usa_a_mesma_chave_do_titular():
    # mesma conta/projeto: a cota grátis é por MODELO, não se cria projeto novo para isto
    src = Path(llm_take.__file__).read_text(encoding="utf-8")
    bloco = src.split('"gemini_b":', 1)[1].split("},", 1)[0]
    assert 'os.environ.get("GEMINI_API_KEY"' in bloco


def test_lotacao_do_titular_cai_no_glm_antes_do_segundo_gemini(motor):
    post, chamadas = roteiro({M1: [gemini_503()], GLM: [openai_ok("-")], M2: [gemini_ok()]})
    with mock.patch.object(llm_take.requests, "post", post):
        res = llm_take.classify("Iron ore falls")
    assert res["provider"] == "zai" and res["take"] == "-"
    assert M2 not in chamadas


def test_segundo_gemini_salva_quando_titular_e_glm_caem_juntos(motor):
    post, chamadas = roteiro({M1: [gemini_503()], GLM: [zai_lotado()], M2: [gemini_ok("=")]})
    with mock.patch.object(llm_take.requests, "post", post):
        res = llm_take.classify("x")
    assert res["provider"] == "gemini_b" and res["model"] == M2


# ── 2. lotação ──────────────────────────────────────────────────────────────────

def test_503_tenta_so_mais_uma_vez_por_manchete(motor):
    post, chamadas = roteiro({M1: [gemini_503()], GLM: [openai_ok()]})
    with mock.patch.object(llm_take.requests, "post", post):
        llm_take.classify("x")
    assert chamadas.count(M1) == 2          # 1 + RETRIES_5XX (era 3)


def test_lotacao_em_duas_manchetes_seguidas_tira_o_titular_da_rodada(motor):
    post, chamadas = roteiro({M1: [gemini_503()], GLM: [openai_ok()]})
    with mock.patch.object(llm_take.requests, "post", post):
        for _ in range(4):
            assert llm_take.classify("x")["provider"] == "zai"
    assert chamadas.count(M1) == 4          # 2 manchetes × 2 tentativas; depois sai da rodada
    assert "gemini" in llm_take._RUN_SKIP


def test_um_acerto_zera_a_contagem_de_lotacao(motor):
    # 503,503 (1ª manchete cai) → ok (2ª) → 503,503 (3ª cai): não são 2 SEGUIDAS
    post, _ = roteiro({M1: [gemini_503(), gemini_503(), gemini_ok(), gemini_503(), gemini_503(), gemini_ok()],
                       GLM: [openai_ok()]})
    with mock.patch.object(llm_take.requests, "post", post):
        provs = [llm_take.classify("x")["provider"] for _ in range(3)]
    assert provs == ["zai", "gemini", "zai"]
    assert "gemini" not in llm_take._RUN_SKIP


def test_ping_do_vigia_continua_com_3_tentativas_em_503(motor):
    post, chamadas = roteiro({M1: [gemini_503()]})
    with mock.patch.object(llm_take.requests, "post", post):
        res, why = llm_take._try_provider("gemini", "x", max_retries=2)
    assert res is None and why == "http 503" and chamadas.count(M1) == 3


def test_429_continua_tirando_da_rodada_na_primeira(motor):
    cota = Resp(429, {"error": {"message": "Quota exceeded", "details": [
        {"violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}]}},
        headers={"retry-after": "3600"})
    post, _ = roteiro({M1: [cota], GLM: [openai_ok()]})
    with mock.patch.object(llm_take.requests, "post", post):
        llm_take.classify("x")
    assert "gemini" in llm_take._RUN_SKIP
    assert "PerDay" in llm_take.LAST_DETAIL["gemini"]      # diz QUAL cota estourou


# ── 3. motivo registrado ────────────────────────────────────────────────────────

def test_queda_fica_registrada_mesmo_quando_outro_degrau_salva(motor):
    post, _ = roteiro({M1: [gemini_503()], GLM: [openai_ok()]})
    with mock.patch.object(llm_take.requests, "post", post):
        assert llm_take.classify("x") is not None
    resumo = llm_take.run_fail_summary()
    assert "gemini: http 503 ×1" in resumo and "high demand" in resumo


def test_resumo_vazio_quando_nada_caiu(motor):
    post, _ = roteiro({M1: [gemini_ok()]})
    with mock.patch.object(llm_take.requests, "post", post):
        llm_take.classify("x")
    assert llm_take.run_fail_summary() == ""


def test_reset_da_rodada_zera_quedas_e_disjuntores(motor):
    post, _ = roteiro({M1: [gemini_503()], GLM: [openai_ok()]})
    with mock.patch.object(llm_take.requests, "post", post):
        llm_take.classify("x"); llm_take.classify("x")
    assert llm_take.RUN_FAILS and "gemini" in llm_take._RUN_SKIP
    llm_take.reset_run_skips()
    assert not llm_take.RUN_FAILS and not llm_take._RUN_SKIP and not llm_take._RUN_5XX


# ── 4. tradução do clipping ─────────────────────────────────────────────────────

def test_traducao_tenta_o_segundo_gemini_do_projeto_dedicado_antes_do_glm(monkeypatch):
    from clipping import build
    monkeypatch.setenv("CLIPPING_TRANSLATE_KEY", "kc")
    monkeypatch.setenv("ZAI_API_KEY", "kz")
    monkeypatch.setenv("GEMINI_API_KEY", "kt")
    monkeypatch.delenv("CLIPPING_TRANSLATE_CHAIN", raising=False)
    cadeia = build._translate_chain()
    assert [c["key"] for c in cadeia] == ["kc", "kc", "kz", "kt"]
    assert cadeia[1]["model"] == M2 != cadeia[0]["model"]


def test_traducao_sem_chave_dedicada_continua_como_antes(monkeypatch):
    from clipping import build
    monkeypatch.delenv("CLIPPING_TRANSLATE_KEY", raising=False)
    monkeypatch.setenv("ZAI_API_KEY", "kz")
    monkeypatch.setenv("GEMINI_API_KEY", "kt")
    monkeypatch.delenv("CLIPPING_TRANSLATE_CHAIN", raising=False)
    assert [c["key"] for c in build._translate_chain()] == ["kz", "kt"]


def test_mistral_fora_da_cadeia_e_dos_workflows():
    src = Path(llm_take.__file__).read_text(encoding="utf-8")
    padrao = src.split('os.environ.get("LLM_CHAIN", "', 1)[1].split('"', 1)[0]
    assert "mistral" not in padrao.split(",")
    raiz = Path(llm_take.__file__).resolve().parent.parent / ".github" / "workflows"
    for wf in ("hunt-loop.yml", "takes_watchdog.yml"):
        assert "MISTRAL_API_KEY" not in (raiz / wf).read_text(encoding="utf-8")
