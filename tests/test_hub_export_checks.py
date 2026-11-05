"""
O leitor do Supabase na Onda 1b (contagem barata, bytes por fonte e de cabeçalho, código do
erro), as conferências do `--check` e as opções novas do CLI (`--rebuild`, `--weeks`, `--check`).

O que estes testes TRAVAM, e por quê:
  1) o plano grátis conta os CABEÇALHOS (~0,75 KB por resposta, medido em 05/11): a linha de
     tráfego tem de mostrá-los, por fonte, senão a estimativa de MB/dia sai pela metade;
  2) a contagem barata é um HEAD (sem corpo) e a sonda "contagem + max" é UMA chamada;
  3) o `--check` descobre o tipo das colunas de data sem ler linha nenhuma (o filtro com texto
     qualquer é recusado por timestamptz) e nunca imprime valor, URL ou chave;
  4) `--weeks` só com --out/--dry-run (no envio, quem escolhe as semanas é o exportador);
     `--check` só com --dry-run; `--rebuild` chega ao ciclo.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import test_hub_export_fixtures as F  # noqa: E402
import hub_export.__main__ as cli  # noqa: E402
from hub_export.checks import run_checks  # noqa: E402
from hub_export.run import run_cycle  # noqa: E402
from hub_export.supa import SupaError, SupaReader  # noqa: E402


def reader(http=None):
    return SupaReader(F.SUPA_URL, F.SUPA_KEY, http=http or F.FakeHTTP())


def test_contagem_por_head_e_sonda_numa_chamada():
    r = reader()
    assert r.count("news_articles", "url", include_in_report="eq.true") == 14
    c = r.http.calls[-1]
    assert c.method == "HEAD" and c.headers["Prefer"] == "count=exact"
    rows, total = r.select_count("exec_calendar_events", "updated_at", is_visible="eq.true",
                                 order="updated_at.desc.nullslast", limit=1)
    assert total == 4 and rows == [{"updated_at": "2026-11-02T13:00:00.5+00:00"}]
    assert r.calls == 2


def test_bytes_por_fonte_com_cabecalhos():
    class Um:
        def request(self, *a, **k):
            return F.FakeResp(200, [{"a": 1}], headers={"Content-Length": "100",
                                                        "Content-Range": "0-0/1",
                                                        "X-Pesado": "x" * 500})
    r = SupaReader(F.SUPA_URL, F.SUPA_KEY, http=Um())
    r.select("t", "a", tag="news:sweep")
    r.select("t", "a", tag="news:sweep")
    r.rpc("f", "a")
    assert r.by_source["news:sweep"]["calls"] == 2 and r.by_source["rpc/f"]["calls"] == 1
    assert r.wire_bytes == 300 and r.header_bytes > 3 * 500
    assert r.by_source["news:sweep"]["headers"] == 2 * r.by_source["rpc/f"]["headers"]


def test_erro_leva_status_e_codigo_mas_nao_o_texto():
    r = reader()
    with pytest.raises(SupaError) as exc:
        r.select("news_articles", "url", found_at="eq.nao-e-data")
    e = exc.value
    assert (e.status, e.code) == (400, "22007") and str(e) == "news_articles: HTTP 400 (22007)"
    assert "timestamp" in e.detail and "timestamp" not in str(e)
    with pytest.raises(SupaError) as exc:
        r.select("news_articles", "created_at")
    assert exc.value.code == "42703"


def test_paginacao_por_offset_com_ordem_estavel():
    r = reader()
    rows = r.select_all("news_articles", "url", order="url.asc", page=4)
    assert len(rows) == 15 and len({x["url"] for x in rows}) == 15
    assert [c.params["offset"] for c in r.http.calls] == ["0", "4", "8", "12"]
    with pytest.raises(SupaError, match="interrompida"):
        r.select_all("news_articles", "url", order="url.asc", page=1, max_pages=3)


def test_check_descobre_tipos_e_permissao_sem_valores():
    linhas = run_checks(reader())
    texto = "\n".join(linhas)
    assert "news_articles.published_at: timestamp with time zone" in texto
    assert "news_articles.found_at: timestamp with time zone" in texto
    assert "news_articles.take_llm_at: timestamp with time zone" in texto
    assert "news_articles.id: ausente" in texto and "news_articles.created_at: ausente" in texto
    assert "14 incluídas" in texto and "rpc get_recent_reports_v3 (service key): HTTP 200" in texto
    assert "exec_calendar_events: 4 visível(is)" in texto and "flags: home_digest=True" in texto
    for segredo in (F.SUPA_KEY, F.SUPA_URL, "projeto-secreto", "http"):
        assert segredo not in texto


def test_check_ve_coluna_de_texto_e_rpc_sem_permissao():
    http = F.FakeHTTP(supa_error={"get_recent_reports_v3": 401})
    for r in http.database["news_articles"]:
        r["take_llm_at"] = None
    import test_hub_export_fixtures as fx
    orig = fx.TIMESTAMP_COLS
    fx.TIMESTAMP_COLS = orig - {"take_llm_at"}                # simula uma coluna `text`
    try:
        texto = "\n".join(run_checks(reader(http)))
    finally:
        fx.TIMESTAMP_COLS = orig
    assert "news_articles.take_llm_at: text" in texto
    assert "get_recent_reports_v3 (service key): rpc/get_recent_reports_v3: HTTP 401" in texto


def test_linha_de_trafego_tem_cabecalhos_e_fontes(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    run_cycle(env=F.env(), http=F.FakeHTTP(), memory=F.memory(), now=F.NOW,
              state_file=tmp_path / "st.json", **F.run_kw())
    (linha,) = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Supabase:")]
    assert "de cabeçalhos pela rede" in linha and "por fonte:" in linha
    for fonte in ("news:horizonte", "news:carga", "digest", "reports", "calendar:sonda",
                  "calendar", "commodities", "config"):
        assert f"{fonte} " in linha, fonte
    assert any(r.getMessage().startswith("notícias: ") for r in caplog.records)


def _limpa_env(monkeypatch):
    for n in ("SUPABASE_URL", "SUPABASE_SERVICE_KEY", "DASHBOARD_FEEDER_URL",
              "DASHBOARD_FEEDER_PROJECT_ID", "DASHBOARD_FEEDER_API_KEY"):
        monkeypatch.delenv(n, raising=False)


def test_cli_rebuild_weeks_e_check(monkeypatch, tmp_path, capsys):
    _limpa_env(monkeypatch)
    envf = tmp_path / ".env"
    envf.write_text(f"SUPABASE_URL={F.SUPA_URL}\nSUPABASE_SERVICE_KEY={F.SUPA_KEY}\n",
                    encoding="utf-8")
    pedidos = {}

    def falso(**kw):
        pedidos.clear()
        pedidos.update(kw)
        return []
    monkeypatch.setattr("hub_export.run.run_cycle", falso)
    assert cli.main(["--env-file", str(envf), "--out", str(tmp_path / "o")]) == 0
    assert pedidos["weeks"] == 2 and pedidos["budget_s"] == cli.MANUAL_BUDGET_S  # padrão do --out
    assert cli.main(["--env-file", str(envf), "--dry-run", "--weeks", "all", "--rebuild"]) == 0
    assert pedidos["weeks"] == "all" and pedidos["rebuild"] is True and pedidos["mode"] == "dry"
    assert cli.main(["--env-file", str(envf), "--dry-run"]) == 0
    assert pedidos["weeks"] is None                              # dry sem --weeks: simula o loop
    assert cli.main(["--env-file", str(envf), "--only", "news-week-2026-w30", "--dry-run"]) == 0
    assert pedidos["only"] == ["news-week-2026-w30.json"]
    pedidos.clear()
    assert cli.main(["--env-file", str(envf), "--weeks", "3"]) == 2          # envio: não vale
    assert cli.main(["--env-file", str(envf), "--check", "--out", "x"]) == 2   # só com --dry-run
    with pytest.raises(SystemExit):
        cli.main(["--env-file", str(envf), "--dry-run", "--weeks", "0"])
    assert pedidos == {}
    monkeypatch.setattr("hub_export.checks.run_checks", lambda supa: ["linha 1", "linha 2"])
    assert cli.main(["--env-file", str(envf), "--dry-run", "--check"]) == 0
    out = capsys.readouterr().out
    assert "linha 1\nlinha 2" in out and pedidos == {}            # não montou arquivo nenhum


def test_check_conta_as_incluidas_antes_do_arquivo():
    http = F.FakeHTTP()
    http.database["news_articles"] += [
        F._n("https://x.com/maio-31", "Antes do arquivo", "Mining.com", "mining.com",
             "2026-05-31T10:00:00+00:00", "2026-06-02T12:00:00+00:00", take="+"),
        F._n("https://x.com/abril", "Antiga achada em junho (data esquisita)", "Mining.com",
             "mining.com", "2026-04-01T10:00:00+00:00", "2026-06-03T12:00:00+00:00", take="+")]
    texto = "\n".join(run_checks(reader(http)))
    assert "1 incluída(s) com data de referência antes da 2026-w23" in texto
    assert "2 candidata(s) lidas" in texto


def test_linha_final_conta_o_que_as_semanas_descartaram(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    http = F.FakeHTTP()
    http.database["news_articles"].append(
        F._n("javascript:void(0)", "Link hostil na semana 43", "Mining.com", "mining.com",
             "2026-10-21T11:00:00+00:00", "2026-10-21T11:05:00+00:00", take="+"))
    run_cycle(env=F.env(), http=http, memory=F.memory(), now=F.NOW, state_file=tmp_path / "s.json",
              only="news-week", **F.run_kw())
    assert "notícias (semanas): descartadas: url esquema 1" in caplog.text
