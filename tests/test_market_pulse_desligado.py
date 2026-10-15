"""Market Pulse desligado (2026-10-15, decisão do usuário) — e fácil de religar.

Os quadros "Overnight" e "At the Open" saíram da tela em 09/09; os robôs que os alimentavam
seguiam rodando. Desligados: a foto/placar no cron do pulse_daily.yml, o gatilho do hunt-loop
(pulse_tick), o treino de domingo e o vigia (que, sem foto, mandaria alarme falso todo dia).
A RÉGUA do termômetro de notícias (news_pulse, último passo do pulse_daily.yml) continua.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")   # PyYAML não é dependência do robô, só do teste

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
import scripts.pulse_tick as tick  # noqa: E402


def _wf(nome: str) -> dict:
    return yaml.safe_load((RAIZ / ".github/workflows" / nome).read_text(encoding="utf-8"))


def _gatilhos(wf: dict) -> dict:
    on = wf.get("on", wf.get(True))       # PyYAML lê a chave `on:` como True (YAML 1.1)
    return on if isinstance(on, dict) else {}


def test_tick_desligado_nao_consulta_o_banco_nem_imprime(monkeypatch, capsys):
    assert tick.PULSE_LIGADO is False
    monkeypatch.setattr(tick, "corte_pendente",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("consultou o banco")))
    assert tick.main() == 0
    assert capsys.readouterr().out == ""      # stdout vazio = o hunt-loop não dispara nada


def test_foto_do_pulse_daily_so_roda_a_mao():
    wf = _wf("pulse_daily.yml")
    assert "schedule" in _gatilhos(wf)                     # o cron segue — para a régua
    passos = {p.get("name"): p for p in wf["jobs"]["pulse"]["steps"]}
    foto = passos["Tirar a foto e pontuar"]
    assert "workflow_dispatch" in foto.get("if", "")
    regua = next(p for p in passos.values() if "hunter.news_pulse" in str(p.get("run", "")))
    assert "workflow_dispatch" not in str(regua.get("if", ""))   # a régua continua no cron


def test_treino_e_vigia_do_pulse_sem_cron():
    for nome in ("pulse_train.yml", "pulse_watchdog.yml"):
        g = _gatilhos(_wf(nome))
        assert "schedule" not in g, nome
        assert "workflow_dispatch" in g, nome     # dá para rodar à mão / religar
