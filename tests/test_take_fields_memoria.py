"""push_take_fields só regrava o take que MUDOU — e, na dúvida, regrava tudo como antes.

Contexto (2026-10-15): o hunt.py fazia 1 PATCH por notícia da janela de 72h em TODA rodada
(hunt-loop de 5 em 5 min + hunt-playwright de 30 em 30) — ~100 mil chamadas por dia, ~95% de
todas as chamadas ao Supabase, o que estourava o Log Ingestion. Simulação contra o banco real:
de 283 regravações de uma rodada, 282 eram idênticas e NENHUMA mudava nada.

O que estes testes travam: (1) sem memória o comportamento é o de sempre; (2) com memória, só
vai PATCH quando o pacote muda; (3) toda falha (PATCH com erro, memória ilegível, velha ou de
outro job, URL repetida) cai no comportamento antigo — regravar.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hunter import sync  # noqa: E402


class _Resp:
    def __init__(self, status=204, text=""):
        self.status_code, self.text = status, text
        self.ok = 200 <= status < 300


def _art(i: int, take: str = "+", **extra) -> dict:
    a = {"url": f"https://exemplo.com/noticia-{i}?a=1&b=2", "title": f"Notícia {i}",
         "take": take, "take_reason": "motivo", "take_sector": "steel",
         "take_region": "brazil", "take_topics": "hrc; prices",
         "take_covered_companies": "GERDAU", "take_confidence": 0.7,
         "take_matched_rules": "R7", "include_in_report": True, "exclusion_reason": None}
    a.update(extra)
    return a


@pytest.fixture
def banco(monkeypatch):
    """Intercepta os PATCHes. `falhar` = conjunto de URLs que devolvem erro."""
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "k")
    estado = {"patches": [], "falhar": set(), "explodir": set(), "migracao": False}

    def fake_patch(url, json=None, headers=None, timeout=None):
        estado["patches"].append((url, json))
        alvo = next((u for u in estado["falhar"] | estado["explodir"]
                     if sync.requests.utils.quote(u, safe="") in url), None)
        if estado["migracao"]:
            return _Resp(400, 'column "take" does not exist')
        if alvo in estado["explodir"]:
            raise OSError("sem rede")
        if alvo in estado["falhar"]:
            return _Resp(500, "erro")
        return _Resp(204)
    monkeypatch.setattr(sync.requests, "patch", fake_patch)
    return estado


def _urls_patchadas(estado) -> list[str]:
    return sorted(u for u, _ in estado["patches"])


# ── sem memória: exatamente o comportamento antigo ───────────────────────────
def test_sem_memoria_regrava_tudo_em_toda_rodada(banco):
    arts = [_art(i) for i in range(5)]
    assert sync.push_take_fields(arts) == 5
    assert sync.push_take_fields(arts) == 5
    assert len(banco["patches"]) == 10


def test_o_patch_continua_identico_ao_de_antes(banco, tmp_path):
    """Mesma URL (codificada) e mesmo corpo do PATCH de sempre — só muda QUANDO ele vai."""
    a = _art(1)
    sync.push_take_fields([a], cache_path=tmp_path / "m.json")
    url, corpo = banco["patches"][0]
    assert url == ("https://x.supabase.co/rest/v1/news_articles?url=eq."
                   + sync.requests.utils.quote(a["url"], safe=""))
    assert corpo == {k: a[k] for k in sync._TAKE_FIELDS}


# ── com memória ──────────────────────────────────────────────────────────────
def test_segunda_rodada_igual_nao_regrava_nada(banco, tmp_path):
    mem = tmp_path / "m.json"
    arts = [_art(i) for i in range(5)]
    assert sync.push_take_fields(arts, cache_path=mem) == 5     # 1ª rodada do job: tudo
    banco["patches"].clear()
    assert sync.push_take_fields(arts, cache_path=mem) == 0     # nada mudou
    assert banco["patches"] == []


def test_take_que_mudou_e_regravado_com_o_valor_novo(banco, tmp_path):
    mem = tmp_path / "m.json"
    sync.push_take_fields([_art(i) for i in range(5)], cache_path=mem)
    banco["patches"].clear()
    nova_rodada = [_art(i) for i in range(5)]
    nova_rodada[2] = _art(2, take="-", include_in_report=False, exclusion_reason="x")
    assert sync.push_take_fields(nova_rodada, cache_path=mem) == 1
    (url, corpo), = banco["patches"]
    assert "noticia-2" in url
    assert corpo["take"] == "-" and corpo["include_in_report"] is False


def test_qualquer_campo_do_pacote_conta_como_mudanca(banco, tmp_path):
    """O 'entra ou não entra' (include_in_report) decide feed, clipping e fila da IA."""
    mem = tmp_path / "m.json"
    for campo, valor in [("include_in_report", False), ("exclusion_reason", "rationale_news"),
                         ("take_covered_companies", "GERDAU; USIMINAS"), ("take_confidence", 0.71)]:
        sync.push_take_fields([_art(1)], cache_path=mem)
        banco["patches"].clear()
        assert sync.push_take_fields([_art(1, **{campo: valor})], cache_path=mem) == 1, campo
        banco["patches"].clear()


def test_noticia_nova_no_meio_do_job_e_gravada(banco, tmp_path):
    mem = tmp_path / "m.json"
    sync.push_take_fields([_art(1), _art(2)], cache_path=mem)
    banco["patches"].clear()
    sync.push_take_fields([_art(1), _art(2), _art(3)], cache_path=mem)
    assert len(banco["patches"]) == 1 and "noticia-3" in banco["patches"][0][0]


# ── falhas caem no comportamento antigo (regravar) ───────────────────────────
def test_patch_com_erro_e_tentado_de_novo_na_rodada_seguinte(banco, tmp_path):
    mem = tmp_path / "m.json"
    arts = [_art(i) for i in range(3)]
    banco["falhar"].add(arts[1]["url"])
    assert sync.push_take_fields(arts, cache_path=mem) == 2
    banco["patches"].clear()
    banco["falhar"].clear()
    assert sync.push_take_fields(arts, cache_path=mem) == 1
    assert "noticia-1" in banco["patches"][0][0] and len(banco["patches"]) == 1


def test_queda_de_rede_no_patch_e_tentada_de_novo(banco, tmp_path):
    mem = tmp_path / "m.json"
    arts = [_art(i) for i in range(3)]
    banco["explodir"].add(arts[0]["url"])
    sync.push_take_fields(arts, cache_path=mem)
    banco["patches"].clear()
    banco["explodir"].clear()
    sync.push_take_fields(arts, cache_path=mem)
    assert len(banco["patches"]) == 1 and "noticia-0" in banco["patches"][0][0]


def test_migracao_pendente_nao_entra_na_memoria(banco, tmp_path):
    mem = tmp_path / "m.json"
    banco["migracao"] = True
    assert sync.push_take_fields([_art(1)], cache_path=mem) == 0
    banco["migracao"] = False
    banco["patches"].clear()
    assert sync.push_take_fields([_art(1)], cache_path=mem) == 1


def test_memoria_ilegivel_regrava_tudo(banco, tmp_path):
    mem = tmp_path / "m.json"
    mem.write_text("{isto não é json", encoding="utf-8")
    assert sync.push_take_fields([_art(i) for i in range(4)], cache_path=mem) == 4


def test_memoria_velha_e_ignorada(banco, tmp_path):
    """Memória com mais de 6h não é deste job (que dura 5h55) — não se confia nela."""
    mem = tmp_path / "m.json"
    arts = [_art(i) for i in range(3)]
    sync.push_take_fields(arts, cache_path=mem)
    velho = time.time() - sync.TAKE_CACHE_MAX_AGE_S - 60
    os.utime(mem, (velho, velho))
    banco["patches"].clear()
    assert sync.push_take_fields(arts, cache_path=mem) == 3


def test_url_repetida_na_rodada_vai_sempre(banco, tmp_path):
    """Dois pacotes para a mesma linha: não dá para saber qual ficou no banco → regrava."""
    mem = tmp_path / "m.json"
    arts = [_art(1), _art(1, take="-"), _art(2)]
    sync.push_take_fields(arts, cache_path=mem)
    banco["patches"].clear()
    sync.push_take_fields(arts, cache_path=mem)
    assert len(banco["patches"]) == 2
    assert all("noticia-1" in u for u, _ in banco["patches"])


def test_memoria_guarda_so_as_noticias_da_rodada(banco, tmp_path):
    mem = tmp_path / "m.json"
    sync.push_take_fields([_art(1), _art(2)], cache_path=mem)
    sync.push_take_fields([_art(2), _art(3)], cache_path=mem)
    guardadas = json.loads(mem.read_text(encoding="utf-8"))
    assert sorted(guardadas) == sorted([_art(2)["url"], _art(3)["url"]])


def test_noticia_que_saiu_e_voltou_e_regravada(banco, tmp_path):
    """Saiu da memória (não veio numa rodada) → na volta, regrava por garantia."""
    mem = tmp_path / "m.json"
    sync.push_take_fields([_art(1), _art(2)], cache_path=mem)
    sync.push_take_fields([_art(2)], cache_path=mem)
    banco["patches"].clear()
    sync.push_take_fields([_art(1), _art(2)], cache_path=mem)
    assert len(banco["patches"]) == 1 and "noticia-1" in banco["patches"][0][0]


# ── onde a memória mora ──────────────────────────────────────────────────────
def test_memoria_so_existe_no_actions(monkeypatch, tmp_path):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    assert sync.take_cache_path() is None          # computador do analista: como sempre
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    assert sync.take_cache_path() == tmp_path / sync.TAKE_CACHE_FILE


def test_hunt_passa_a_memoria_do_job():
    fonte = (Path(__file__).resolve().parent.parent / "hunt.py").read_text(encoding="utf-8")
    assert "push_take_fields(articles_filtered, cache_path=take_cache_path())" in fonte


def test_insert_de_noticia_nova_ja_leva_o_take(monkeypatch):
    """A memória pode pular uma notícia cujo PATCH 'deu certo' sem a linha existir ainda
    porque o INSERT (ignore-duplicates) grava o pacote de take COMPLETO na rodada em que a
    notícia entra. Se um dia o push_articles parar de mandar esses campos, isto quebra."""
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "k")
    enviados = []

    class _R:
        ok = True
        status_code = 201
        headers = {"content-range": "*/1"}
        text = ""

    def fake_post(url, json=None, headers=None, timeout=None):
        enviados.extend(json)
        return _R()
    monkeypatch.setattr(sync.requests, "post", fake_post)
    a = _art(9)
    sync.push_articles([a])
    assert all(enviados[0].get(k) == a[k] for k in sync._TAKE_FIELDS)
