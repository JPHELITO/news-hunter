# -*- coding: utf-8 -*-
"""SESSION_STORE_READONLY — o run one-shot usa a sessão, mas não a regrava no store.

Contexto: hunt-once.yml (botão "Buscar novas agora" do Clipping) roda em concurrency
group PRÓPRIO, então PODE acontecer junto com a corrente do hunt-playwright. Sem esta
trava, o one-shot faria last-write-wins por cima da sessão que a corrente acabou de
renovar. A escrita LOCAL continua (o run precisa da sessão) — só o push remoto para.
"""
import types

import pytest

from hunter import playwright_session as ps


class _FakeResp:
    ok = True
    status_code = 200
    text = ""


@pytest.fixture
def spy_post(monkeypatch):
    import requests
    calls = []
    monkeypatch.setattr(requests, "post", lambda *a, **k: calls.append((a, k)) or _FakeResp())
    monkeypatch.setenv("SUPABASE_URL", "https://exemplo.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "chave-de-teste")
    return calls


def test_readonly_nao_regrava_no_store(spy_post, monkeypatch):
    monkeypatch.setenv("SESSION_STORE_READONLY", "1")
    ps._push_session_to_store("platts", '{"cookies": []}')
    assert spy_post == [], "com SESSION_STORE_READONLY=1 não pode haver POST no store"


def test_sem_a_flag_regrava_normalmente(spy_post, monkeypatch):
    monkeypatch.delenv("SESSION_STORE_READONLY", raising=False)
    ps._push_session_to_store("platts", '{"cookies": []}')
    assert len(spy_post) == 1, "sem a flag, o roll-forward normal tem que continuar"
    assert ps.SESSIONS_TABLE in spy_post[0][0][0]


def test_save_state_grava_local_mesmo_em_readonly(spy_post, monkeypatch, tmp_path):
    """A trava é só do store REMOTO — o arquivo local é o que o run usa."""
    monkeypatch.setenv("SESSION_STORE_READONLY", "1")
    monkeypatch.setenv("COOKIES_DIR", str(tmp_path))
    ctx = types.SimpleNamespace(storage_state=lambda: {"cookies": [{"name": "x"}]})
    ps.save_state(ctx, "platts")
    assert (tmp_path / "platts_state.json").exists()
    assert spy_post == []


def test_o_one_shot_NAO_liga_mais_a_flag():
    """A trava saiu do hunt-once.yml em 2026-09-09 e não pode voltar sem pensar.

    Com ela ligada, o app renova o token dentro do navegador durante o run, o servidor
    QUEIMA o token guardado (rotação) e o novo é descartado por não poder ser gravado —
    o store fica com um token morto. Ou seja: um clique do analista em "Buscar novas
    agora" podia derrubar a sessão da Platts. O que a trava protegia (one-shot atrasado
    sobrescrevendo a sessão da corrente) hoje é resolvido pelo save_state MONOTÔNICO.
    """
    from pathlib import Path
    yml = (Path(__file__).resolve().parent.parent
           / ".github" / "workflows" / "hunt-once.yml").read_text(encoding="utf-8")
    ligada = [l for l in yml.splitlines()
              if "SESSION_STORE_READONLY" in l and not l.lstrip().startswith("#")]
    assert not ligada, f"o one-shot voltou a ligar a trava: {ligada}"
