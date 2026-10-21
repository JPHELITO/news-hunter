"""
Respostas de IA "embrulhadas" — o JSON vem cercado de raciocínio, cercas ou rascunho.

O DEFEITO (achado em 21/10/2026, num estudo sobre o resumo de hora em hora da Home)
-----------------------------------------------------------------------------------
Os "News highlights" do blast (`clipping/blast.py::_chamar`) e a tradução do clipping
(`clipping/build.py::_translate_call`) tinham uma rede de segurança para quando a IA
devolve o JSON embrulhado: `next(c for c in _json_candidates(raw) if isinstance(c, dict) ...)`.
Só que `_json_candidates` devolve TEXTOS (a resposta inteira, o trecho depois do </think>,
o miolo das cercas, os blocos {…}) — nunca um dict. A condição nunca era verdadeira, e a
rede nunca pegou nada. Quando o Gemini caía e a reserva (o GLM da Z.AI, um modelo
"pensante") respondia com <think>…</think> antes do JSON, o destaque ou a tradução sumia
sem aviso, e a cadeia ia gastar a próxima IA à toa.

Os testes de antes não pegavam porque trocavam o `_chamar` inteiro por um falso: a leitura
do JSON nunca foi exercitada. Aqui ela é: só o `requests.post` é falso, e as respostas são
escritas à mão no formato que cada provedor devolve. NENHUMA IA é chamada.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clipping import blast                  # noqa: E402
from clipping import build                  # noqa: E402
from hunter import llm_take                 # noqa: E402


# ── Respostas de exemplo, escritas à mão ─────────────────────────────────────────────

# O GLM da Z.AI pensando em voz alta antes de responder. Repare que o raciocínio repete
# o MODELO de resposta que o prompt pede ({"destaques": ["...", "..."]}) — é JSON válido.
DESTAQUES_PENSANTE = (
    "<think>\nO analista quer até 3 destaques no formato {\"destaques\": [\"...\", \"...\"]}.\n"
    "Minério e celulose são os temas do dia.\n</think>\n"
    '{"destaques": ["Minério 62% sobe 2,1% WoW a USD 104/ton com reposição de estoques na China;", '
    '"Suzano anuncia aumento de USD 20/ton na fibra curta para a Ásia em outubro."]}'
)
DESTAQUES_ESPERADOS = [
    "Minério 62% sobe 2,1% WoW a USD 104/ton com reposição de estoques na China",
    "Suzano anuncia aumento de USD 20/ton na fibra curta para a Ásia em outubro",
]

DESTAQUES_CERCA = ('Claro! Seguem os destaques:\n```json\n'
                   '{"destaques": ["Minério sobe", "Celulose estável"]}\n```\n'
                   'Qualquer ajuste, é só pedir.')

DESTAQUES_RASCUNHO = ('Rascunho: {"destaques": ["versão fraca"]}\n'
                      'Versão final: {"destaques": ["versão final 1", "versão final 2"]}')

# O pior caso: o raciocínio tem o modelo do prompt e a resposta de verdade, depois do
# </think>, veio quebrada (aspas sem escape dentro do texto). Não há resposta — e o modelo
# do prompt ("...") NUNCA pode virar destaque.
DESTAQUES_SO_MODELO = (
    '<think>Formato pedido: {"destaques": ["...", "..."]}. Vou escrever.</think>\n'
    '{"destaques": ["Minério sobe 2,1% com "restocking" na China"]}'
)

TRADUCAO_PENSANTE = (
    "<think>\nThe user wants {\"title\": \"...\", \"paragraphs\": [\"...\", \"...\"]}. "
    "Keep numbers and names.\n</think>\n"
    "```json\n"
    '{"title": "Vale raises its 2026 iron ore guidance", '
    '"paragraphs": ["Vale raised its 2026 output guidance.", '
    '"The company cited the Northern System ramp-up."]}\n'
    "```"
)
TRADUCAO_ESPERADA = ("Vale raises its 2026 iron ore guidance",
                     ["Vale raised its 2026 output guidance.",
                      "The company cited the Northern System ramp-up."])

# Raciocínio com o modelo do prompt + resposta cortada no meio (estourou o limite).
TRADUCAO_SO_MODELO = (
    '<think>Output format: {"title": "...", "paragraphs": ["...", "..."]}</think>\n'
    '{"title": "Vale raises its 2026 iron ore guidance", "paragraphs": ["Vale raised'
)


# ── Um requests.post de mentira: devolve o envelope de cada provedor ─────────────────

class _Resp:
    def __init__(self, status, payload=None):
        self.status_code = status
        self.ok = 200 <= status < 300
        self._payload = payload

    def json(self):
        return self._payload


def _envelope(url, texto):
    if "generativelanguage" in url:                          # Gemini
        return {"candidates": [{"content": {"parts": [{"text": texto}]}}]}
    return {"choices": [{"message": {"content": texto}}]}    # OpenAI-compatível (Mistral, Z.AI)


def _post_falso(respostas, chamadas=None):
    """respostas: trecho da URL -> texto da IA (ou um número = HTTP de erro)."""
    def post(url, json=None, headers=None, timeout=None, **kw):
        for trecho, resp in respostas.items():
            if trecho in url:
                if chamadas is not None:
                    chamadas.append(trecho)
                if isinstance(resp, int):
                    return _Resp(resp)
                return _Resp(200, _envelope(url, resp))
        raise AssertionError(f"chamada inesperada para {url}")
    return post


ZAI_BLAST = {"rotulo": "z.ai (GLM)", "kind": "openai", "key": "k", "model": "glm-4.5-flash",
             "url": "https://api.z.ai/api/paas/v4/chat/completions"}
GEMINI_BLAST = {"rotulo": "gemini (projeto clipping)", "kind": "gemini", "key": "k",
                "model": "gemini-3.1-flash-lite"}
ZAI_TRAD = {"name": "zai", "kind": "openai", "key": "k", "model": "glm-4.5-flash",
            "url": "https://api.z.ai/api/paas/v4/chat/completions"}

CHAVES = {"CLIPPING_TRANSLATE_KEY": "k1", "GEMINI_API_KEY": "k2",
          "MISTRAL_API_KEY": "k3", "ZAI_API_KEY": "k4"}
ZERADO = {"gemini_clipping": 0, "gemini_takes": 0, "mistral": 0, "zai": 0}


# ── 1) A peça que lê: hunter.llm_take._parse_json_dict ───────────────────────────────

class TestLeituraDoJson:
    def test_json_puro(self):
        assert llm_take._parse_json_dict('{"destaques": ["a"]}', "destaques") == {"destaques": ["a"]}

    def test_raciocinio_antes_do_json(self):
        d = llm_take._parse_json_dict(DESTAQUES_PENSANTE, "destaques")
        assert d["destaques"][0].startswith("Minério 62% sobe")

    def test_cerca_json(self):
        assert llm_take._parse_json_dict(DESTAQUES_CERCA, "destaques") == {
            "destaques": ["Minério sobe", "Celulose estável"]}

    def test_cerca_sem_a_palavra_json(self):
        texto = 'Resposta:\n```\n{"title": "X", "paragraphs": ["y"]}\n```'
        assert llm_take._parse_json_dict(texto, "title") == {"title": "X", "paragraphs": ["y"]}

    def test_rascunho_antes_da_resposta_vale_a_ultima(self):
        d = llm_take._parse_json_dict(DESTAQUES_RASCUNHO, "destaques")
        assert d == {"destaques": ["versão final 1", "versão final 2"]}

    def test_pula_o_objeto_que_nao_tem_a_chave(self):
        texto = '{"destaques": ["a"]} e depois {"nota": "sem destaques aqui"}'
        assert llm_take._parse_json_dict(texto, "destaques") == {"destaques": ["a"]}

    def test_resposta_sem_a_chave_e_nada(self):
        assert llm_take._parse_json_dict('{"highlights": ["a"]}', "destaques") is None

    def test_o_raciocinio_nao_e_resposta(self):
        """Resposta quebrada depois do </think>: nada. O modelo do prompt que o raciocínio
        repetiu ({"destaques": ["...", "..."]}) não pode ser devolvido como se fosse ela."""
        assert llm_take._parse_json_dict(DESTAQUES_SO_MODELO, "destaques") is None
        assert llm_take._parse_json_dict(TRADUCAO_SO_MODELO, "title") is None

    def test_think_sem_resposta_depois_e_nada(self):
        assert llm_take._parse_json_dict('<think>{"destaques": ["a"]}</think>', "destaques") is None

    @pytest.mark.parametrize("raw", [None, "", "   ", 42, ["a"], "sem json nenhum", "{{{{", "</think>"])
    def test_nunca_levanta(self, raw):
        assert llm_take._parse_json_dict(raw, "destaques") is None


# ── 2) Blast: os News highlights do WhatsApp ─────────────────────────────────────────

class TestDestaquesDoBlast:
    def _chamar(self, cfg, texto):
        with mock.patch.object(blast.requests, "post", _post_falso({"api.z.ai": texto,
                                                                    "generativelanguage": texto})):
            return blast._chamar(cfg, "prompt")

    def test_glm_pensante_entrega_os_destaques(self):
        assert self._chamar(ZAI_BLAST, DESTAQUES_PENSANTE) == DESTAQUES_ESPERADOS

    def test_resposta_em_cerca(self):
        assert self._chamar(ZAI_BLAST, DESTAQUES_CERCA) == ["Minério sobe", "Celulose estável"]

    def test_rascunho_antes_da_resposta(self):
        assert self._chamar(ZAI_BLAST, DESTAQUES_RASCUNHO) == ["versão final 1", "versão final 2"]

    def test_json_puro_do_gemini_segue_igual(self):
        assert self._chamar(GEMINI_BLAST, '{"destaques": ["a;", "b."]}') == ["a", "b"]

    def test_so_raciocinio_nao_vira_destaque_e_avisa(self, caplog):
        with caplog.at_level(logging.WARNING, logger=blast.log.name):
            assert self._chamar(ZAI_BLAST, DESTAQUES_SO_MODELO) is None
        assert "z.ai (GLM)" in caplog.text

    def test_conteudo_vazio_nao_derruba(self):
        assert self._chamar(ZAI_BLAST, None) is None
        assert self._chamar(ZAI_BLAST, "") is None

    def test_gemini_fora_do_ar_e_a_reserva_pensante_salva_o_blast(self):
        """O caso real: os dois Gemini e a Mistral falham, sobra o GLM — que responde
        embrulhado. Antes, o blast saía sem destaque nenhum."""
        post = _post_falso({"generativelanguage": 503, "api.mistral.ai": 429,
                            "api.z.ai": DESTAQUES_PENSANTE})
        with mock.patch.dict(os.environ, CHAVES, clear=False), \
             mock.patch.object(blast, "_carga_hoje", return_value=ZERADO), \
             mock.patch.object(blast.requests, "post", post):
            r = blast.highlights([{"title": "x"}], n=3)
        assert r["provedor"] == "z.ai (GLM)"
        assert r["destaques"] == DESTAQUES_ESPERADOS and r["erro"] is None


# ── 3) Tradução do clipping ──────────────────────────────────────────────────────────

class TestTraducaoDoClipping:
    def _traduzir(self, texto):
        with mock.patch("requests.post", _post_falso({"api.z.ai": texto})):
            return build._translate_call(ZAI_TRAD, "Vale eleva projeção", ["a", "b"], "Portuguese")

    def test_glm_pensante_com_cerca_entrega_a_traducao(self):
        assert self._traduzir(TRADUCAO_PENSANTE) == TRADUCAO_ESPERADA

    def test_texto_em_volta_do_json(self):
        texto = ('Here is the translation:\n'
                 '{"title": "Vale raises guidance", "paragraphs": ["One.", "Two."]}\nHope it helps!')
        assert self._traduzir(texto) == ("Vale raises guidance", ["One.", "Two."])

    def test_so_raciocinio_nao_vira_traducao(self):
        """O modelo do prompt ({"title": "..."}) não pode entrar no Word como título traduzido."""
        assert self._traduzir(TRADUCAO_SO_MODELO) is None

    def test_conteudo_vazio_nao_derruba(self):
        assert self._traduzir(None) is None

    def test_reserva_pensante_traduz_sem_gastar_a_cota_dos_takes(self):
        """Gemini dedicado sem cota (429), o GLM responde embrulhado: a tradução sai dele, e o
        Gemini COMPARTILHADO (que é a cota dos takes) nem é chamado."""
        chamadas = []
        post = _post_falso({"generativelanguage": 429, "api.z.ai": TRADUCAO_PENSANTE}, chamadas)
        with mock.patch.dict(os.environ, CHAVES, clear=False), \
             mock.patch("requests.post", post):
            os.environ.pop("CLIPPING_TRANSLATE_CHAIN", None)      # a ordem padrão da cadeia
            r = build._translate_via_llm("Vale eleva projeção", ["a", "b"], "Portuguese")
        assert r == TRADUCAO_ESPERADA
        assert chamadas == ["generativelanguage", "api.z.ai"]
