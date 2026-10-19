"""Testes do vigia dos takes — e-mail SÓ com estrago de verdade (2026-10-19).

O incidente: o vigia ficou vermelho em 16 de 40 dias com os takes saindo normalmente. O --ping
dava UMA tentativa por IA, a Mistral não responde (limite 0/min na conta) e o Gemini cai toda
tarde de dia útil (13-18h UTC) — justamente quando o cron atrasado do GitHub dispara o vigia.

O que estes testes TRAVAM, e por quê:
  • que soluço PASSAGEIRO (429, 5xx, rede) do titular não vira e-mail — o Gemini volta sozinho;
  • que chamada RECUSADA do titular (chave/assinatura/modelo) VIRA e-mail — é o incidente de
    06/08 (Mistral com 402), que não se cura sozinho;
  • que "ninguém respondeu" vira e-mail — os takes vão parar;
  • que o estrago do DIA é medido pela mistura: GLM > 20% ou titular < 50%, com amostra mínima
    (fim de semana tem 5-10 takes) — e a Mistral, reserva aceitável (4,7% de erro), não é estrago;
  • que o ping dá as MESMAS 3 tentativas da produção.

Rodar: python -m pytest tests/test_takes_watchdog.py -v
"""
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import scripts.takes_watchdog as wd


# ─────────────────────────── a natureza da falha ───────────────────────────
class TestClassificarFalha:
    def test_ok(self):
        assert wd.classificar_falha("ok") == "ok"

    def test_chamada_recusada_e_permanente(self):
        for why in ("auth 401", "auth 402", "auth 403", "http 400", "http 404", "http 413"):
            assert wd.classificar_falha(why) == "permanente", why

    def test_soluco_e_passageiro(self):
        for why in ("rate_limited", "http 500", "http 503", "rede: ReadTimeout",
                    "json inválido", "resposta vazia", "esgotou retries", "exceção: KeyError"):
            assert wd.classificar_falha(why) == "passageira", why


# ─────────────────────────────── o ping ───────────────────────────────
CADEIA = ["gemini", "mistral", "zai"]


class TestProblemasDoPing:
    def test_titular_com_soluco_e_reserva_viva_NAO_alarma(self):
        # O caso de 19/10: Gemini dando 503 à tarde, GLM respondendo, Mistral com 429.
        res = {"gemini": "http 503", "mistral": "rate_limited", "zai": "ok"}
        assert wd.problemas_do_ping(CADEIA, res) == []

    def test_so_o_titular_vivo_NAO_alarma(self):
        # Sem rede de segurança é risco, não estrago: vai para o relatório.
        res = {"gemini": "ok", "mistral": "rate_limited", "zai": "rede: ReadTimeout"}
        assert wd.problemas_do_ping(CADEIA, res) == []

    def test_reserva_recusada_NAO_alarma(self):
        res = {"gemini": "ok", "mistral": "auth 402", "zai": "ok"}
        assert wd.problemas_do_ping(CADEIA, res) == []

    def test_titular_recusado_ALARMA(self):
        # O incidente de 06/08: 402 de assinatura — não se cura sozinho.
        res = {"gemini": "auth 402", "mistral": "rate_limited", "zai": "ok"}
        probs = wd.problemas_do_ping(CADEIA, res)
        assert len(probs) == 1 and "RECUSOU" in probs[0] and "gemini" in probs[0]

    def test_ninguem_responde_ALARMA(self):
        res = {"gemini": "http 503", "mistral": "rate_limited", "zai": "rede: ReadTimeout"}
        probs = wd.problemas_do_ping(CADEIA, res)
        assert len(probs) == 1 and "NENHUM" in probs[0]

    def test_cadeia_vazia_ALARMA(self):
        assert "CHAIN vazia" in wd.problemas_do_ping([], {})[0]


# ──────────────────────── o estrago do dia (sem IA) ────────────────────────
class TestProblemasDeQuem:
    def test_dia_normal(self):
        assert wd.problemas_de_quem(Counter(gemini=180, glm=6)) == []

    def test_glm_acima_do_limiar_ALARMA(self):
        # 16/10: o GLM fez 71 de 245 (29%) — o limiar antigo de 50% nunca tocou.
        probs = wd.problemas_de_quem(Counter(gemini=174, glm=71))
        assert len(probs) == 1 and "FRACO" in probs[0]

    def test_glm_no_limiar_nao_alarma(self):
        assert wd.problemas_de_quem(Counter(gemini=80, glm=20)) == []

    def test_mistral_cobrindo_NAO_e_estrago(self):
        # Reserva aceitável (4,7% de erro): 40% do dia na Mistral não é e-mail.
        assert wd.problemas_de_quem(Counter(gemini=60, mistral=40)) == []

    def test_titular_fora_a_maior_parte_do_dia_ALARMA(self):
        # O 1º/ago ao contrário: o titular quase não classificou, mesmo com reserva decente.
        probs = wd.problemas_de_quem(Counter(gemini=45, mistral=55))
        assert len(probs) == 1 and "titular" in probs[0]

    def test_amostra_pequena_nao_julga(self):
        # Fim de semana: 4 do GLM em 10 takes seriam "40%".
        assert wd.problemas_de_quem(Counter(gemini=6, glm=4)) == []


# ───────────────── o ping dá as MESMAS 3 tentativas da produção ─────────────────
class _Resp:
    headers = {"content-range": "0-0/0"}


def test_ping_usa_3_tentativas_e_soluco_nao_derruba(monkeypatch):
    from hunter import llm_take
    monkeypatch.setenv("SUPABASE_URL", "https://exemplo.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "chave")
    linhas = ([{"take_llm": "+", "take_llm_model": "gemini-3.1-flash-lite", "source_name": "X"}] * 30
              + [{"take_llm": "=", "take_llm_model": "glm-4.5-flash", "source_name": "X"}] * 2)
    paginas = iter([linhas, []])
    monkeypatch.setattr(wd, "_get", lambda *a, **k: next(paginas))
    monkeypatch.setattr(wd.requests, "get", lambda *a, **k: _Resp())
    chamadas = []

    def fake_try(p, ut, max_retries=2):
        chamadas.append((p, max_retries))
        return ({"take": "="}, "ok") if p == "zai" else (None, "http 503")

    monkeypatch.setattr(llm_take, "CHAIN", list(CADEIA))
    monkeypatch.setattr(llm_take, "_load_prompts", lambda: None)
    monkeypatch.setattr(llm_take, "_user_text", lambda *a, **k: "Headline: x")
    monkeypatch.setattr(llm_take, "_try_provider", fake_try)
    monkeypatch.setattr(sys, "argv", ["takes_watchdog", "--ping"])
    assert wd.main() == 0                                   # Gemini com 503 + GLM vivo = verde
    assert chamadas == [(p, 2) for p in CADEIA]             # max_retries=2 = 3 tentativas
