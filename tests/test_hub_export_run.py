"""
O ciclo do exportador do hub (hub_export/run.py), o leitor do Supabase (supa.py) e o CLI.

Este código entra no ar ≤ 5 min depois de chegar ao master (o hunt-loop baixa o código a cada
ciclo). O que estes testes TRAVAM, e por quê:
  1) na vez + mudou → envia; igual → não envia (nenhuma chamada ao Feeder);
  2) erro num arquivo NÃO impede os outros, fica registrado no estado e o arquivo tenta de novo
     no ciclo seguinte (não espera a cadência inteira);
  3) a memória do ciclo é USADA: com ela (e o estado), nenhuma leitura de cotações/macro no
     Supabase — aferido pelo registro de chamadas do HTTP falso. Na partida a frio, só o papel
     que a memória não trouxe é lido, uma vez;
  4) o orçamento de 90 s: arquivo que não coube fica "adiado" e entra no próximo ciclo;
  5) o contador de bytes do Supabase (Content-Length quando há, senão o corpo) e a linha de
     tráfego no log; `select=*` é recusado;
  6) "dry" e "out" nunca mexem no registro de envio; o "out" grava EXATAMENTE os bytes que
     seriam enviados (gzip inclusive);
  7) sem as variáveis do Feeder, o passo do loop não faz nada (nem lê o banco); o CLI explica
     o que falta sem mostrar valor nenhum; código de saída ≠ 0 quando um arquivo falha;
  8) nada de segredo no log do ciclo, nem quando dá erro.
"""
from __future__ import annotations

import gzip
import json
import logging
import sys
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import test_hub_export_fixtures as F  # noqa: E402
import hub_export.__main__ as cli  # noqa: E402
from hub_export import state as S  # noqa: E402
from hub_export.feeder import FeederClient  # noqa: E402
from hub_export.registry import FILES, resolve  # noqa: E402
from hub_export.run import Result, run_cycle  # noqa: E402
from hub_export.supa import SupaError, SupaReader  # noqa: E402


def run(http, path, **kw):
    kw.setdefault("memory", F.memory())
    kw.setdefault("now", F.NOW)
    return {r.name: r for r in run_cycle(env=F.env(), http=http, state_file=path, **kw)}


def quote_price_reads(http):
    return [c for c in http.supa_calls("quotes") if "intraday" in c.params["select"]
            or "price" in c.params["select"]]


# ── enviar / pular ────────────────────────────────────────────────────────────────────────────
def test_na_vez_e_mudou_envia_igual_pula(tmp_path):
    p = tmp_path / "st.json"
    http = F.FakeHTTP()
    res = run(http, p)
    assert {n: r.action for n, r in res.items()} == {n: "enviado" for n in FILES}
    assert sorted(http.uploads) == sorted(FILES)
    st = S.load(p)
    for n, r in res.items():
        assert st["files"][n]["hash"] == r.hash and st["files"][n]["revision"] == r.revision
        assert st["files"][n]["sentAt"] == F.GENERATED == st["files"][n]["builtAt"]
    # mesmo dado, ciclo seguinte: nada vai ao Feeder
    http = F.FakeHTTP()
    res = run(http, p)
    assert res["live.json"].action == "igual" and res["intraday.json"].action == "igual"
    assert http.feeder_calls() == []
    # o preço da VALE mudou na memória: vão o live e o intraday
    mem = F.memory()
    mem["quotes"][1]["price"] = 61.5
    mem["quotes"][1]["intraday"]["pts"].append([1793711700, 61.5])
    http = F.FakeHTTP()
    res = run(http, p, memory=mem)
    assert res["live.json"].action == "enviado" and res["live.json"].reason == "mudou"
    assert res["intraday.json"].action == "enviado"
    assert sorted(http.uploads) == ["intraday.json", "live.json"]


def test_registro_bate_com_o_contrato():
    """Cadência, gzip, pulsação, meta de tamanho e tipo de cada arquivo (PLANO §2.5, 03/11/2026)."""
    tabela = {n: (s.cadence, s.gzip, s.pulse, s.max_bytes, s.content_type) for n, s in FILES.items()}
    assert tabela == {
        "config.json":       (900, False, None, 20 * 1024, "application/json"),
        "live.json":         (0, True, 600, 80 * 1024, "application/gzip"),
        "intraday.json":     (0, True, None, 150 * 1024, "application/gzip"),
        "quotes-daily.json": (3600, True, None, 350 * 1024, "application/gzip"),
    }


def test_live_intraday_e_quotes_daily_em_gzip_config_em_texto(tmp_path):
    """O Mindassets não comprime na entrega: quem precisa ir pequeno vai comprimido EM REPOUSO,
    com o nome .json (o hub reconhece pelos bytes 1f 8b), gzip determinístico."""
    http = F.FakeHTTP()
    run(http, tmp_path / "st.json")
    up = http.uploads
    for n in ("live.json", "intraday.json", "quotes-daily.json"):
        assert up[n][:2] == b"\x1f\x8b" and up[n][4:8] == b"\x00\x00\x00\x00", n   # mtime=0
        corpo = gzip.decompress(up[n])
        assert corpo.startswith(b'{"schemaVersion":1,"generatedAt":"2026-11-03T18:05:12Z"'), n
    assert up["config.json"].startswith(b'{"schemaVersion":1,"generatedAt":"2026-11-03T18:05:12Z"')
    tipos = {json.loads(c.data)["name"]: json.loads(c.data)["contentType"]
             for c in http.feeder_calls() if c.method == "POST" and c.url.endswith("/uploads")}
    assert tipos == {"config.json": "application/json", "live.json": "application/gzip",
                     "intraday.json": "application/gzip", "quotes-daily.json": "application/gzip"}
    puts = {c.url.split("/bucket/")[1].split("?")[0]: c.headers for c in http.feeder_calls()
            if c.method == "PUT"}
    assert puts["live.json"] == {"content-type": "application/gzip"}
    assert puts["config.json"] == {"content-type": "application/json"}


# ── erro num arquivo ──────────────────────────────────────────────────────────────────────────
def test_erro_num_arquivo_nao_impede_os_outros_e_tenta_de_novo(tmp_path):
    p = tmp_path / "st.json"
    http = F.FakeHTTP(supa_error={"commodities": 503})
    res = run(http, p)
    assert res["live.json"].action == "erro" and "HTTP 503" in res["live.json"].reason
    assert {res[n].action for n in ("intraday.json", "config.json", "quotes-daily.json")} == \
        {"enviado"}
    st = S.load(p)
    assert "HTTP 503" in st["files"]["live.json"]["lastError"]
    assert "builtAt" not in st["files"]["live.json"]             # tenta de novo já no próximo
    res = run(F.FakeHTTP(), p)
    assert res["live.json"].action == "enviado"
    assert S.load(p)["files"]["live.json"]["lastError"] is None


def test_falha_no_envio_e_erro_e_o_arquivo_volta_no_proximo_ciclo(tmp_path):
    p = tmp_path / "st.json"
    http = F.FakeHTTP(feeder_script={"PUT": [403]})
    feeder = FeederClient(F.FEEDER_URL, F.PROJECT_ID, F.FEEDER_KEY, http=http, retry_wait=0,
                          sleep=lambda s: None)
    res = run(http, p, feeder=feeder, only="config")
    assert res["config.json"].action == "erro" and "PUT: HTTP 403" in res["config.json"].reason
    res = run(F.FakeHTTP(), p, only="config")                     # não espera os 15 min
    assert res["config.json"].action == "enviado"


def test_commodities_caem_mas_o_live_sai_com_a_ultima_leitura_boa(tmp_path):
    p = tmp_path / "st.json"
    run(F.FakeHTTP(), p)
    mem = F.memory()
    mem["quotes"][1]["price"] = 62.0
    res = run(F.FakeHTTP(supa_error={"commodities": requests.ConnectionError("x")}), p, memory=mem)
    assert res["live.json"].action == "enviado"


# ── memória do ciclo ──────────────────────────────────────────────────────────────────────────
def test_memoria_do_ciclo_dispensa_ler_cotacoes_e_macro(tmp_path):
    p = tmp_path / "st.json"
    S.save(p, F.previous_state())
    http = F.FakeHTTP()
    res = run(http, p, only="live,intraday")
    assert res["live.json"].action == "enviado"
    assert quote_price_reads(http) == []
    assert http.supa_calls("macro_indicators") == []
    assert [c.table for c in http.supa_calls()] == ["commodities", "commodities"]  # leve + spark


def test_partida_a_frio_le_so_quem_a_memoria_nao_trouxe_e_uma_vez(tmp_path):
    p = tmp_path / "st.json"
    http = F.FakeHTTP()
    run(http, p, only="live")
    leituras = quote_price_reads(http)
    assert len(leituras) == 1
    pedidos = leituras[0].params["ticker"]
    assert F.MISSING_IN_CYCLE in pedidos and "VALE3.SA" not in pedidos
    q = {i["ticker"]: i for i in
         json.loads(gzip.decompress(http.uploads["live.json"]))["quotes"]["items"]}
    assert q["CMPC.SN"]["price"] == 1350.0
    http = F.FakeHTTP()
    run(http, p, only="live")
    assert quote_price_reads(http) == []                           # já conferidos: não relê


def test_sem_memoria_sonda_antes_de_ler_tudo(tmp_path):
    p = tmp_path / "st.json"
    http = F.FakeHTTP()
    run(http, p, only="intraday", memory={"quotes": None, "macro": None})
    sel = [c.params["select"] for c in http.supa_calls("quotes")]
    assert sel == ["updated_at", "ticker,price,change_pct,change_abs,updated_at,intraday"]
    assert http.supa_calls("quotes")[0].params["limit"] == "1"
    http = F.FakeHTTP()
    run(http, p, only="intraday", memory={"quotes": None, "macro": None}, force=True)
    assert [c.params["select"] for c in http.supa_calls("quotes")] == ["updated_at"]  # nada mudou


def test_memoria_velha_ou_ausente_e_ignorada(monkeypatch):
    import time as _t
    from hub_export.sources import cycle_memory
    from hunter import prices
    monkeypatch.setattr(prices, "LAST_QUOTE_ROWS", (_t.time() - 3600, [{"ticker": "X"}]))
    monkeypatch.setattr(prices, "LAST_MACRO_ROWS", None)
    assert cycle_memory() == {"quotes": None, "macro": None}
    monkeypatch.setattr(prices, "LAST_QUOTE_ROWS", (_t.time() - 5, [{"ticker": "X"}]))
    assert cycle_memory()["quotes"] == [{"ticker": "X"}]


def test_update_quotes_e_update_macro_guardam_a_memoria(monkeypatch):
    """As 2 variáveis novas do prices.py: as linhas montadas, com o instante — nada mais muda."""
    from hunter import prices
    gravado = {}
    monkeypatch.setattr(prices, "LAST_QUOTE_ROWS", None)     # o monkeypatch devolve o original
    monkeypatch.setattr(prices, "LAST_MACRO_ROWS", None)
    monkeypatch.setattr(prices, "_supa_upsert", lambda t, rows: gravado.setdefault(t, rows) and len(rows))
    monkeypatch.setattr(prices, "fetch_yahoo", lambda syms, *a, **k: {
        s: {"price": 10.0, "change_pct": 1.0, "change_abs": 0.1, "series": [[1, 10.0]],
            "prev": 9.9, "quote_time": 1793729041, "session": [1, 2]} for s in syms})
    monkeypatch.setattr(prices, "fetch_bcb", lambda sid: {"value": 15.0, "change_pct": 0.0})
    assert prices.update_quotes() == len(prices.QUOTES_LIST)
    assert prices.update_macro() == len(prices.MACRO_YAHOO) + len(prices.MACRO_BCB)
    at, rows = prices.LAST_QUOTE_ROWS
    assert rows is gravado["quotes"] and isinstance(at, float)
    assert prices.LAST_MACRO_ROWS[1] is gravado["macro_indicators"]
    from hub_export.sources import cycle_memory
    assert cycle_memory()["quotes"] is rows


# ── orçamento de tempo ────────────────────────────────────────────────────────────────────────
def test_orcamento_de_tempo_respeitado(tmp_path):
    p = tmp_path / "st.json"
    relogio = {"t": 0.0}

    class Lento(F.FakeHTTP):
        def _feeder(self, call):
            if call.url.endswith("/complete"):
                relogio["t"] += 60.0                               # cada envio "leva" 60 s
            return super()._feeder(call)

    http = Lento()
    res = run(http, p, clock=lambda: relogio["t"])
    assert [res[n].action for n in ("live.json", "intraday.json", "config.json",
                                    "quotes-daily.json")] == ["enviado", "enviado", "adiado",
                                                              "adiado"]
    assert sorted(http.uploads) == ["intraday.json", "live.json"]
    st = S.load(p)
    assert "config.json" not in st["files"]                        # nem montado: entra no próximo
    res = run(F.FakeHTTP(), p)
    assert res["config.json"].action == "enviado" and res["quotes-daily.json"].action == "enviado"


# ── Supabase: bytes, select, erros ───────────────────────────────────────────────────────────
def test_contador_de_bytes_e_linha_de_trafego(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    http = F.FakeHTTP()
    run(http, tmp_path / "st.json")
    linha = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Supabase:")]
    assert len(linha) == 1
    n = len(http.supa_calls())
    assert f"Supabase: {n} chamada(s)" in linha[0] and "memória do ciclo: 7 cotações, 2 macro" in linha[0]
    for nome in FILES:                                              # uma linha por arquivo
        assert any(r.getMessage().startswith(nome + " ") for r in caplog.records)


def test_supa_conta_content_length_ou_corpo_e_exige_select():
    class Um:
        def __init__(self, resp):
            self.resp = resp

        def request(self, *a, **k):
            return self.resp
    r = SupaReader(F.SUPA_URL, F.SUPA_KEY, http=Um(F.FakeResp(200, [{"a": 1}],
                                                                headers={"Content-Length": "999"})))
    assert r.select("t", "a") == [{"a": 1}]
    assert (r.calls, r.wire_bytes, r.body_bytes) == (1, 999, len(b'[{"a": 1}]'))
    r = SupaReader(F.SUPA_URL, F.SUPA_KEY, http=Um(F.FakeResp(200, [{"a": 1}])))
    r.select("t", "a")
    assert r.wire_bytes == r.body_bytes == len(b'[{"a": 1}]')
    for ruim in ("*", "", "a,*", " * "):
        with pytest.raises(ValueError, match="select explícito"):
            r.select("t", ruim)


def test_supa_pede_gzip_e_manda_a_chave_so_no_cabecalho():
    http = F.FakeHTTP()
    SupaReader(F.SUPA_URL, F.SUPA_KEY, http=http).select("quotes", "ticker", limit=1)
    c = http.calls[0]
    assert c.headers["Accept-Encoding"] == "gzip" and c.headers["apikey"] == F.SUPA_KEY
    assert F.SUPA_KEY not in c.url and all(F.SUPA_KEY not in str(v) for v in c.params.values())


def test_supa_erros_sem_url_nem_chave():
    for falha in (500, requests.ConnectionError(f"{F.SUPA_URL}/rest/v1/quotes?apikey={F.SUPA_KEY}")):
        http = F.FakeHTTP(supa_error={"quotes": falha})
        with pytest.raises(SupaError) as exc:
            SupaReader(F.SUPA_URL, F.SUPA_KEY, http=http).select("quotes", "ticker")
        assert "supabase" not in str(exc.value) and F.SUPA_KEY not in str(exc.value)
        assert exc.value.__cause__ is None
    with pytest.raises(SupaError, match="ausentes"):
        SupaReader.from_env({})


def test_supa_le_o_corpo_cru_comprimido_do_requests():
    """Com o `requests` de verdade (sem rede): o corpo gzip é contado comprimido."""
    import io
    import urllib3
    corpo = json.dumps([{"ticker": "VALE3.SA"}] * 50).encode()
    gz = gzip.compress(corpo)
    resp = requests.Response()
    resp.status_code = 200
    resp.headers["Content-Encoding"] = "gzip"
    resp.raw = urllib3.response.HTTPResponse(body=io.BytesIO(gz), headers={"Content-Encoding": "gzip"},
                                             status=200, preload_content=False)

    class Um:
        def request(self, *a, **k):
            return resp
    r = SupaReader(F.SUPA_URL, F.SUPA_KEY, http=Um())
    assert len(r.select("quotes", "ticker")) == 50
    assert r.wire_bytes == len(gz) and r.body_bytes == len(corpo)


# ── modos dry / out ───────────────────────────────────────────────────────────────────────────
def test_dry_run_diz_o_que_enviaria_e_nao_mexe_no_registro(tmp_path):
    p = tmp_path / "st.json"
    http = F.FakeHTTP()
    res = run(http, p, mode="dry")
    assert {r.action for r in res.values()} == {"enviaria"}
    assert all(r.due and r.changed for r in res.values())
    assert http.feeder_calls() == []
    st = S.load(p)
    assert st["files"] == {}                                       # registro de envio intocado
    assert st["daily"]["rows"] and st["spark"]                     # caches de dados, sim
    run(F.FakeHTTP(), p)                                           # envio de verdade
    antes = S.load(p)["files"]
    mem = F.memory()
    mem["quotes"][1]["price"] = 70.0
    res = run(F.FakeHTTP(), p, mode="dry", memory=mem)
    assert res["live.json"].action == "enviaria" and res["live.json"].changed is True
    assert res["intraday.json"].action == "igual"
    assert res["config.json"].action == "fora da vez" and res["config.json"].bytes
    assert S.load(p)["files"] == antes


def test_out_grava_exatamente_os_bytes_que_seriam_enviados(tmp_path):
    enviados = F.FakeHTTP()
    run(enviados, tmp_path / "a.json")
    out = tmp_path / "saida"
    http = F.FakeHTTP()
    res = run(http, tmp_path / "b.json", mode="out", out_dir=out)
    assert {r.action for r in res.values()} == {"gravado"}
    for n in FILES:
        assert (out / n).read_bytes() == enviados.uploads[n], n
    assert http.feeder_calls() == [] and S.load(tmp_path / "b.json")["files"] == {}
    for n in ("live.json", "intraday.json", "quotes-daily.json"):
        assert res[n].bytes == res[n].gz_bytes, n                 # vai comprimido
        assert (out / n).read_bytes()[:2] == b"\x1f\x8b", n
    assert res["config.json"].bytes == res["config.json"].raw_bytes   # vai em texto


# ── configuração / CLI ───────────────────────────────────────────────────────────────────────
def test_sem_variaveis_do_feeder_o_passo_do_loop_nao_faz_nada(tmp_path, caplog):
    http = F.FakeHTTP()
    env = F.env(DASHBOARD_FEEDER_API_KEY="")
    with caplog.at_level(logging.WARNING):
        assert run_cycle(env=env, http=http, memory=F.memory(), state_file=tmp_path / "s") == []
    assert http.calls == [] and "DASHBOARD_FEEDER_API_KEY" in caplog.text


def test_resolve_nomes():
    assert resolve(None) == resolve("all") == resolve("ALL") == list(FILES)
    assert resolve("config,live") == ["config.json", "live.json"]
    assert resolve("quotes-daily.json, intraday") == ["quotes-daily.json", "intraday.json"]
    with pytest.raises(ValueError, match="desconhecido"):
        resolve("news")


def test_log_do_ciclo_sem_segredo_mesmo_com_erro(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    http = F.FakeHTTP(supa_error={"commodities": requests.ConnectionError(
        f"{F.SUPA_URL}/rest/v1/commodities?apikey={F.SUPA_KEY}")},
        feeder_script={"PUT": [F.connection_error()] * 8})
    feeder = FeederClient(F.FEEDER_URL, F.PROJECT_ID, F.FEEDER_KEY, http=http, retry_wait=0,
                          sleep=lambda s: None)
    res = run(http, tmp_path / "st.json", feeder=feeder)
    assert {r.action for r in res.values()} == {"erro"}
    texto = caplog.text + json.dumps(json.loads((tmp_path / "st.json").read_text(encoding="utf-8")))
    for segredo in (F.SUPA_KEY, F.FEEDER_KEY, "projeto-secreto", "ASSINATURA-SECRETA", "X-Amz"):
        assert segredo not in texto


def _stub(monkeypatch, results):
    pedidos = {}

    def falso(**kw):
        pedidos.update(kw)
        return results
    monkeypatch.setattr("hub_export.run.run_cycle", falso)
    return pedidos


def _limpa_env(monkeypatch):
    for n in ("SUPABASE_URL", "SUPABASE_SERVICE_KEY", "DASHBOARD_FEEDER_URL",
              "DASHBOARD_FEEDER_PROJECT_ID", "DASHBOARD_FEEDER_API_KEY"):
        monkeypatch.delenv(n, raising=False)


def test_cli_envio_sem_feeder_explica_o_que_falta_sem_valores(monkeypatch, tmp_path, capsys):
    _limpa_env(monkeypatch)
    envf = tmp_path / ".env"
    envf.write_text(f"SUPABASE_URL={F.SUPA_URL}\nSUPABASE_SERVICE_KEY={F.SUPA_KEY}\n",
                    encoding="utf-8")
    pedidos = _stub(monkeypatch, [])
    assert cli.main(["--env-file", str(envf), "--all"]) == 2
    err = capsys.readouterr().err
    assert "DASHBOARD_FEEDER_URL" in err and "DASHBOARD_FEEDER_API_KEY" in err
    assert F.SUPA_KEY not in err and F.SUPA_URL not in err and pedidos == {}


def test_cli_out_e_dry_run_e_codigo_de_saida(monkeypatch, tmp_path, capsys):
    _limpa_env(monkeypatch)
    envf = tmp_path / ".env"
    envf.write_text(f"SUPABASE_URL={F.SUPA_URL}\nSUPABASE_SERVICE_KEY={F.SUPA_KEY}\n",
                    encoding="utf-8")
    pedidos = _stub(monkeypatch, [Result("live.json", "gravado", bytes=10, raw_bytes=10,
                                         gz_bytes=8, hash="ab" * 32, due=True)])
    assert cli.main(["--env-file", str(envf), "--only", "live,config.json",
                     "--out", str(tmp_path / "o")]) == 0
    assert pedidos["mode"] == "out" and pedidos["only"] == ["live.json", "config.json"]
    assert "live.json" in capsys.readouterr().out
    pedidos = _stub(monkeypatch, [Result("config.json", "erro", reason="HTTP 500")])
    assert cli.main(["--env-file", str(envf), "--dry-run", "--force"]) == 1
    assert pedidos["mode"] == "dry" and pedidos["force"] is True
    assert cli.main(["--env-file", str(envf), "--only", "news", "--dry-run"]) == 2
    assert cli.main(["--env-file", str(tmp_path / "nao-existe.env"), "--dry-run"]) == 2
