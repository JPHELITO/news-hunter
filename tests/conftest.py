"""Proteção para TODA a suíte (24/11/2026).

Os destaques do blast passaram a INSISTIR por até 7 minutos quando a IA não responde, e a
tradução do clipping faz uma 2ª passada depois de 15 s. Em teste, isso viraria espera de verdade
(a 1ª rodada levou 8 minutos). Aqui: uma volta só, nenhum sono e nenhuma chave da Groq vinda da
máquina. Os testes que exercitam a insistência põem relógio falso e paciência por conta própria.
"""
import pytest


@pytest.fixture(autouse=True)
def _ia_sem_espera_de_verdade(monkeypatch):
    try:
        from clipping import blast, build
    except Exception:                       # suíte de outra parte do robô: nada a proteger
        yield
        return
    monkeypatch.setattr(blast, "PACIENCIA_S", 0)
    monkeypatch.setattr(blast, "_dormir", lambda s: None)
    monkeypatch.setattr(build, "_TRANSLATE_RETRY_S", 0)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    yield
