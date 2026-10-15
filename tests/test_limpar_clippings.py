"""Limpeza mensal dos arquivos antigos do clipping (2026-10-15, decisão do usuário).

Apagar não tem volta, então o que estes testes travam são as TRAVAS: só `clippings/`, nunca
menos de 8 dias (os links valem 7), arquivo sem data fica, listagem que falha não apaga
nada, e sem --aplicar é só simulação.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
from scripts import limpar_clippings as lc  # noqa: E402

AGORA = datetime.now(timezone.utc)


def _dias(n: float) -> str:
    return (AGORA - timedelta(days=n)).isoformat()


class _Resp:
    def __init__(self, payload, status=200):
        self._p, self.status_code = payload, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._p


# Bucket de mentira: clippings/<job>/arquivo, com idades variadas.
_ARVORE = {
    "clippings/": [{"name": "job-velho", "id": None}, {"name": "job-medio", "id": None},
                   {"name": "job-novo", "id": None}, {"name": "job-sem-data", "id": None}],
    "clippings/job-velho/": [
        {"name": "NEWS - 08012026.docx", "id": "1", "created_at": _dias(75), "metadata": {"size": 1_000_000}},
        {"name": "NEWS - 08012026.eml", "id": "2", "created_at": _dias(75), "metadata": {"size": 2_000_000}}],
    "clippings/job-medio/": [
        {"name": "NEWS - 09102026.docx", "id": "3", "created_at": _dias(31), "metadata": {"size": 500_000}}],
    "clippings/job-novo/": [
        {"name": "NEWS - 10142026.docx", "id": "4", "created_at": _dias(1), "metadata": {"size": 700_000}},
        {"name": "NEWS - 09202026.docx", "id": "5", "created_at": _dias(29), "metadata": {"size": 700_000}}],
    "clippings/job-sem-data/": [
        {"name": "x.docx", "id": "6", "created_at": None, "metadata": {"size": 1}},
        {"name": "y.docx", "id": "7", "created_at": "lixo", "metadata": {"size": 1}}],
}


@pytest.fixture
def storage(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "k")
    est = {"apagados": [], "falha_lista": False, "falha_apagar": False}

    def fake_post(url, json=None, headers=None, timeout=None):
        assert url.endswith("/storage/v1/object/list/admin-uploads")
        if est["falha_lista"]:
            return _Resp({"error": "x"}, 500)
        return _Resp(_ARVORE.get(json["prefix"], []))

    def fake_delete(url, json=None, headers=None, timeout=None):
        assert url.endswith("/storage/v1/object/admin-uploads")
        if est["falha_apagar"]:
            return _Resp({"error": "x"}, 500)
        est["apagados"].append(list(json["prefixes"]))
        return _Resp([{"name": p} for p in json["prefixes"]])
    monkeypatch.setattr(lc.requests, "post", fake_post)
    monkeypatch.setattr(lc.requests, "delete", fake_delete)
    return est


def test_lista_descendo_nas_pastas(storage):
    todos = lc.listar("https://x.supabase.co", "k")
    assert len(todos) == 7
    assert all(a["caminho"].startswith("clippings/job-") for a in todos)


def test_so_os_de_mais_de_30_dias_e_sem_data_fica(storage):
    alvo = lc.velhos(lc.listar("https://x.supabase.co", "k"), 30, AGORA)
    assert sorted(a["caminho"] for a in alvo) == [
        "clippings/job-medio/NEWS - 09102026.docx",
        "clippings/job-velho/NEWS - 08012026.docx",
        "clippings/job-velho/NEWS - 08012026.eml"]


def test_fora_de_clippings_nunca_entra():
    outros = [{"caminho": "pred_exports/base.xlsx", "criado": _dias(400), "bytes": 1},
              {"caminho": "iba/x.pdf", "criado": _dias(400), "bytes": 1}]
    assert lc.velhos(outros, 30, AGORA) == []
    with pytest.raises(AssertionError):
        lc.apagar("https://x", "k", ["pred_exports/base.xlsx"])


def test_sem_aplicar_e_so_simulacao(storage, capsys):
    assert lc.main([]) == 0
    assert storage["apagados"] == []
    assert "SIMULAÇÃO" in capsys.readouterr().out


def test_aplicar_apaga_so_os_velhos(storage):
    assert lc.main(["--aplicar"]) == 0
    apagados = sum(storage["apagados"], [])
    assert len(apagados) == 3 and all("job-novo" not in p for p in apagados)


def test_apaga_em_lotes(storage, monkeypatch):
    monkeypatch.setattr(lc, "LOTE", 2)
    assert lc.main(["--aplicar"]) == 0
    assert [len(l) for l in storage["apagados"]] == [2, 1]


@pytest.mark.parametrize("dias", [0, 1, 7])
def test_recusa_menos_de_8_dias_sem_tocar_na_rede(storage, dias, monkeypatch):
    monkeypatch.setattr(lc.requests, "post", lambda *a, **k: pytest.fail("tocou na rede"))
    assert lc.main(["--dias", str(dias), "--aplicar"]) == 2


def test_listagem_falhou_nao_apaga_nada(storage):
    storage["falha_lista"] = True
    assert lc.main(["--aplicar"]) == 1
    assert storage["apagados"] == []


def test_erro_ao_apagar_deixa_o_job_vermelho(storage):
    storage["falha_apagar"] = True
    assert lc.main(["--aplicar"]) == 1


def test_workflow_mensal_apaga_no_cron_e_simula_a_mao():
    yaml = pytest.importorskip("yaml")
    wf = yaml.safe_load((RAIZ / ".github/workflows/limpar_clippings.yml").read_text(encoding="utf-8"))
    on = wf.get("on", wf.get(True))
    assert on["schedule"] == [{"cron": "30 7 1 * *"}]          # dia 1º, 04:30 BRT
    assert on["workflow_dispatch"]["inputs"]["modo"]["default"] == "simular"
    passo = wf["jobs"]["limpar"]["steps"][-1]
    assert "--aplicar" in passo["run"] and "--dias 30" in passo["run"]
    assert "schedule" in passo["env"]["MODO"]
