"""
A família `history-<id>.json` + `history-index.json` (hub_export/history.py — PLANO, Apêndice
F.1–F.2; pesquisa 2-dados §4.3–4.6).

O que estes testes TRAVAM, e por quê:
  1) a JUNÇÃO (cópia publicada com `ts < v_cut` + série leve com `ts ≥ v_cut`) é IGUAL à série longa
     — é o que permite manter 55 históricos sem reler 2,43 MB por dia; por `ts`, não por dia (o
     pregão da ASX que abre à 00:00 UTC no dia do corte); o trecho recente vem da série leve (a
     revisão de um fechamento entra);
  2) split (`lastSplitTs` novo) e junção que não fecha (cópia sem um ponto antigo) → leitura
     inteira, NA HORA (é papel publicado), contada no orçamento;
  3) fim de semana (o append roda, os metadados não mudam) → nada montado, nada enviado, nenhuma
     cópia baixada;
  4) a CARGA inicial: na ordem do universo (cobertura, índices, pares), no máximo 2 por execução,
     no ritmo do orçamento que acumula em tempo de runner (~1 MB/24 h, começa vazio), com o gasto
     real contado por dia UTC; um papel caro segura os de trás (a prioridade não pula);
  5) o índice só lista o que foi de fato publicado, na ordem do universo; vai na hora quando entra
     papel novo; com o Mindassets parado e sem o índice publicado, a família ESPERA e o índice não
     sai (um índice vazio publicado faria a carga recomeçar do zero);
  6) a sonda de metadados só roda quando a série leve mudou (ou a cada 6 h);
  7) os 55 papéis do universo batem com a lista do site; os 5 da quote_history que a Market não
     mostra nunca saem;
  8) `--out --history`/`--dry-run`: só os pedidos, os mesmos bytes que seriam enviados; o dry não
     mexe no registro.
"""
from __future__ import annotations

import gzip
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import test_hub_export_fixtures as F  # noqa: E402
from hub_export import history as H  # noqa: E402
from hub_export import state as S  # noqa: E402
from hub_export.encode import gzip_bytes, to_json_bytes  # noqa: E402
from hub_export.exporters import reference  # noqa: E402
from hub_export.registry import FILES, spec_for  # noqa: E402
from hub_export.run import run_cycle  # noqa: E402
from hunter import prices  # noqa: E402

QH = {r["ticker"]: r for r in F.QUOTE_HISTORY}
MARKET_QH = [t for t in H.UNIVERSE if t in QH]       # os 7 da Market com histórico, na ordem
ONLY = "history,history-index"

# o KNOWN da market.html (origin/main f636222, linhas 778-801), sem os `@`: a lista do site
SITE_KNOWN = (["AURA33.SA", "SUZB3.SA", "VALE3.SA", "GMEXICOB.MX", "CMIN3.SA", "SCCO", "KLBN11.SA",
               "TX", "CSNA3.SA", "CMPC.SN", "USIM5.SA", "RANI3.SA", "GGBR4.SA", "COPEC.SN", "CS.TO",
               "ERO.TO", "HBM.TO", "LUN.TO", "ARA.TO", "VMM.AX"]
              + ["CSNA3.SA", "GGBR4.SA", "GGB", "MT", "NUE", "TX", "USIM5.SA", "CMC", "GOAU4.SA", "STLD"]
              + ["VALE", "CMIN3.SA", "FMG.AX", "RIO", "BHP", "AAL.L", "CAP.SN", "BRAP3.SA"]
              + ["AUGO", "ARIS", "BVN", "AEM", "B", "HOC.L"]
              + ["ERO.TO", "SCCO", "GMEXICOB.MX", "CS.TO", "B", "HBM.TO", "LUN.TO"]
              + ["MEI.AX", "VMM.AX", "ARA.TO"]
              + ["SW", "IP", "SUZB3.SA", "KLBN11.SA", "RANI3.SA", "CMPC.SN", "COPEC.SN", "UPM.HE",
                 "STERV.HE"]
              + ["GDX", "SPX", "MATB11.SA", "NASDAQ", "IBOV", "SPTSX", "ASX200", "MEXBOL", "IPSA",
                 "FTSE100", "OMXH25"])


def world(qh=None, **kw) -> F.FakeHTTP:
    http = F.FakeHTTP(**kw)
    http.database["quote_history"] = F.quote_history() if qh is None else qh
    return http


def run(http, path, now=F.NOW, only=ONLY, **kw):
    kw.setdefault("memory", F.memory())
    return {r.name: r for r in run_cycle(env=F.env(), http=http, now=now, state_file=path,
                                         only=only, **F.run_kw(**kw))}


def longas(http):
    return [c.params["ticker"][3:] for c in http.supa_calls("quote_history")
            if "daily" in c.params["select"]]


def sondas(http):
    return [c for c in http.supa_calls("quote_history") if "daily" not in c.params["select"]]


def doc(raw: bytes) -> dict:
    return json.loads(gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw)


def publish(qh_rows, index_meta=None) -> dict:
    """As cópias publicadas de um mundo em que estes históricos já foram enviados: um arquivo por
    papel da Market + o history-index.json (com `index_meta[t]` por cima dos metadados, quando o
    teste quer o registro diferente da cópia)."""
    http = world(qh_rows)
    src, _ = F.make_sources(http=http, state=S.empty_state())
    store = src.history()
    out = {}
    for r in qh_rows:
        name = H.file_name(r["ticker"])
        if name is None:
            continue
        obj = spec_for(name).builder(src)
        store.after(r["ticker"], obj, "enviado")
        out[name] = gzip_bytes(to_json_bytes(obj))
    idx = FILES["history-index.json"].builder(src)
    for it in idx["items"]:
        it.update((index_meta or {}).get(it["ticker"], {}))
    out["history-index.json"] = to_json_bytes(idx)
    return out


# ── universo ──────────────────────────────────────────────────────────────────────────────────
def test_universo_sao_os_55_da_market():
    assert len(H.UNIVERSE) == 55 and len(set(H.UNIVERSE)) == 55
    assert set(H.UNIVERSE) == set(SITE_KNOWN)                       # a lista do site
    grupos = {t for _id, _pt, _en, ts in reference.HEATMAP_GROUPS for t in ts}
    assert set(H.UNIVERSE) == grupos | {"VALE3.SA", "AURA33.SA"}    # F.1 / O17
    assert set(H.UNIVERSE) <= {q[0] for q in prices.QUOTES_LIST}   # todos têm série leve
    # a ordem é a prioridade da carga: os 20 da cobertura da Market, os índices, os pares
    assert list(H.UNIVERSE[:20]) == SITE_KNOWN[:20]
    assert list(H.UNIVERSE[20:31]) == reference.G_INDEX
    fora = [q[0] for q in prices.QUOTES_LIST if q[0] not in H.UNIVERSE]
    assert sorted(fora) == ["BHP.AX", "GLEN.L", "MT.AS", "RIO.L", "SP500_FUT"]


def test_serie_leve_das_fixtures_e_a_reducao_da_longa():
    """O mundo falso é coerente com o `append_quote_history`: a série leve de cada papel é a
    redução EXATA da longa (é a premissa da junção)."""
    for t in MARKET_QH:
        q = next(r for r in F.QUOTES if r["ticker"] == t)
        assert F.light_of(QH[t]["daily"], q["daily_updated_at"]) == q["daily"], t
        assert QH[t]["updated_at"] == q["daily_updated_at"]          # a mesma transação


# ── junção ────────────────────────────────────────────────────────────────────────────────────
def test_juncao_por_ts_igual_a_longa_inclusive_o_pregao_da_asx():
    dua = "2026-11-03T00:05:00Z"                                      # v_cut = 27/10/2025 00:05
    asx = 1761523200                                                  # 27/10/2025 00:00 UTC: antes do corte, no MESMO dia
    longa = [[1761264000, 0.39], [asx, 0.40], [1793365200, 0.58]]
    leve = F.light_of(longa, dua)
    assert leve == [[asx, 0.40], [1793365200, 0.58]]                  # o pregão é o "mensal" de outubro
    publicada = longa[:-1]                                            # a cópia de ontem
    assert H.junction(publicada, leve, dua) == longa
    # cortar por DIA (< dia do corte) perderia o pregão da ASX — a conferência não fecharia
    cut_day = (F._epoch(dua) - H.CUT_S) // 86400
    por_dia = [p for p in publicada if p[0] // 86400 < cut_day] + [p for p in leve if p[0] >= F._epoch(dua) - H.CUT_S]
    assert len(por_dia) == 2 and not H.fits(por_dia, {"points": 3, "firstTs": longa[0][0], "lastTs": longa[-1][0]})
    assert H.junction(publicada, leve, None) is None


def test_atualizacao_diaria_pela_copia_publicada_sem_ler_a_longa(tmp_path):
    vale = QH["VALE3.SA"]
    ontem = dict(vale, daily=[list(p) for p in vale["daily"][:-1]], points=7,
                 last_ts=vale["daily"][-2][0], updated_at="2026-11-01T23:59:00+00:00")
    pub = publish([ontem])
    # a cópia de ontem tinha o fechamento de 31/10 "em formação": a série leve de hoje manda
    copia = doc(pub["history-vale3.sa.json"])
    copia["pts"][-1][1] = 59.0
    pub["history-vale3.sa.json"] = gzip_bytes(to_json_bytes(copia))
    http = world(published=pub)
    res = run(http, tmp_path / "st.json")
    assert res["history-vale3.sa.json"].action == "enviado"
    assert longas(http) == []                                        # nenhuma leitura da longa
    assert len(sondas(http)) == 1                                    # só a sonda de metadados
    out = doc(http.uploads["history-vale3.sa.json"])
    assert out["pts"] == vale["daily"]                               # = a série longa de hoje
    assert (out["points"], out["firstTs"], out["lastTs"], out["lastSplitTs"]) == \
        (8, vale["first_ts"], vale["last_ts"], None)
    assert out["updatedAt"] == "2026-11-02T23:59:02Z"
    assert list(out) == ["schemaVersion", "generatedAt", "ticker", "updatedAt", "firstTs",
                         "lastTs", "points", "lastSplitTs", "pts"]
    # o registro anda: no ciclo seguinte nada a fazer para a VALE
    res = run(world(published=pub), tmp_path / "st.json", now=F.NOW + timedelta(minutes=5))
    assert "history-vale3.sa.json" not in res


def test_split_rele_a_longa_na_hora(tmp_path):
    scco = QH["SCCO"]
    antes = dict(scco, last_split_ts=0, daily=[[p[0], p[1] * 2] for p in scco["daily"]])
    pub = publish([antes])
    assert doc(pub["history-scco.json"])["lastSplitTs"] is None
    http = world(published=pub)
    res = run(http, tmp_path / "st.json")                            # partida a frio, orçamento vazio
    assert longas(http) == ["SCCO"]                                  # papel publicado: não espera
    out = doc(http.uploads["history-scco.json"])
    assert out["lastSplitTs"] == F.SPLIT_SCCO and out["pts"] == scco["daily"]
    assert res["history-scco.json"].action == "enviado"
    st = S.load(tmp_path / "st.json")
    assert st["history"]["bucket"]["spent"] > 0                     # contado no orçamento


def test_juncao_que_nao_fecha_rele_a_longa(tmp_path):
    vale = QH["VALE3.SA"]
    pub = publish([vale], index_meta={"VALE3.SA": {"points": 7, "lastTs": vale["daily"][-2][0]}})
    copia = doc(pub["history-vale3.sa.json"])
    del copia["pts"][2]                                               # a cópia perdeu 15/09/2025
    pub["history-vale3.sa.json"] = gzip_bytes(to_json_bytes(copia))
    http = world(published=pub)
    run(http, tmp_path / "st.json")
    assert longas(http) == ["VALE3.SA"]
    assert doc(http.uploads["history-vale3.sa.json"])["pts"] == vale["daily"]
    assert S.load(tmp_path / "st.json")["history"]["needLong"] == []  # resolvido pela longa


def test_fim_de_semana_sem_ponto_novo_nao_monta_nem_envia(tmp_path):
    pub = publish(F.quote_history())
    http = world(published=pub)
    # sábado: o append rodou (os carimbos andaram nos dois lados), nenhum ponto novo
    for r in http.database["quote_history"]:
        r["updated_at"] = "2026-11-07T01:00:00+00:00"
    for r in http.database["quotes"]:
        r["daily_updated_at"] = "2026-11-07T01:00:00+00:00"
    sat = datetime(2026, 11, 7, 2, 0, tzinfo=timezone.utc)
    res = run(http, tmp_path / "st.json", now=sat)
    assert [n for n in res if n.startswith("history-") and n != "history-index.json"] == []
    assert res["history-index.json"].action == "igual"
    assert http.feeder_calls() == []
    baixados = [c.url.rsplit("/", 1)[1] for c in http.calls if c.url.startswith(F.PUBLIC_BASE)]
    assert "history-index.json" in baixados                          # a semente do registro…
    assert [b for b in baixados if H.ticker_from_file(b)] == []      # …e nenhum histórico baixado


# ── carga inicial ─────────────────────────────────────────────────────────────────────────────
def test_carga_na_ordem_do_universo_no_ritmo_do_orcamento(tmp_path):
    p = tmp_path / "st.json"
    http = world()
    res = run(http, p)                                               # runner novo: orçamento vazio
    assert longas(http) == [] and res["history-index.json"].action == "enviado"
    assert doc(http.uploads["history-index.json"])["items"] == []
    http = world()
    res = run(http, p, now=F.NOW + timedelta(minutes=5))             # ~3,6 KB acumulados
    assert longas(http) == ["VALE3.SA"]                              # o 1º da cobertura
    assert res["history-vale3.sa.json"].action == "enviado"
    assert res["history-index.json"].action == "enviado"             # papel novo: o índice vai já
    assert [i["ticker"] for i in doc(http.uploads["history-index.json"])["items"]] == ["VALE3.SA"]
    b = S.load(p)["history"]["bucket"]
    assert b["daySpent"] == b["spent"] > 0 and b["day"] == "2026-11-03"
    http = world()
    run(http, p, now=F.NOW + timedelta(hours=6))                     # orçamento de sobra: o teto manda
    assert longas(http) == ["SCCO", "CMPC.SN"]
    for k in range(1, 4):
        run(world(), p, now=F.NOW + timedelta(hours=6, minutes=5 * k))
    st = S.load(p)["history"]
    assert list(st["published"]) == ["VALE3.SA", "SCCO", "CMPC.SN", "HBM.TO", "VMM.AX", "IBOV",
                                     "AAL.L"]
    http = world()
    res = run(http, p, now=F.NOW + timedelta(hours=7))
    assert longas(http) == [] and not [n for n in res if n.startswith("history-") and
                                       n != "history-index.json"]   # carga completa: nada a fazer
    idx = [i["ticker"] for i in doc(http.uploads["history-index.json"])["items"]] \
        if "history-index.json" in http.uploads else None
    assert idx in (None, MARKET_QH)                                  # na ordem do universo


def test_papel_caro_segura_a_fila_e_o_gasto_e_contado(tmp_path):
    qh = F.quote_history()
    vale = next(r for r in qh if r["ticker"] == "VALE3.SA")
    antigos = [[86400 * d + 46800, 1.0] for d in range(3650, 6650)]  # 3.000 pontos de 1980–1988
    vale["daily"] = antigos + vale["daily"]
    vale.update(points=len(vale["daily"]), first_ts=vale["daily"][0][0])
    p = tmp_path / "st.json"
    run(world(qh), p)
    custo = H.estimate({"points": vale["points"]})                   # ~21,8 KB
    falta = custo * 86400 / H.LOAD_BYTES_PER_DAY                     # segundos de orçamento
    http = world(qh)
    run(http, p, now=F.NOW + timedelta(seconds=falta - 60))
    assert longas(http) == []                                        # a VALE (cara) segura a fila
    http = world(qh)
    run(http, p, now=F.NOW + timedelta(seconds=falta + 60))
    assert longas(http) == ["VALE3.SA"]
    b = S.load(p)["history"]["bucket"]
    assert b["spent"] > 0 and b["spent"] == b["daySpent"]            # o gasto REAL da longa
    assert len(http.supa_calls("quote_history")) == 1                # a sonda ficou da 1ª execução


# ── Mindassets parado ─────────────────────────────────────────────────────────────────────────
def _live_velho() -> bytes:
    return gzip_bytes(to_json_bytes({"schemaVersion": 1, "generatedAt": "2026-10-31T09:00:00Z",
                                     "quotes": {"items": []}}))


def test_mindassets_parado_sem_indice_a_familia_espera(tmp_path):
    http = world(published={"live.json": _live_velho()})            # parado há 3 dias; índice 404
    res = run(http, tmp_path / "st.json", now=F.NOW + timedelta(hours=6))
    assert sondas(http) == [] and longas(http) == []                 # nem a sonda
    assert res["history-index.json"].action == "erro"                # índice parcial nunca
    assert "history-index.json" not in http.uploads
    assert not S.load(tmp_path / "st.json")["history"]["seeded"]

    class SemRede(F.FakeHTTP):
        def request(self, method, url, **kw):
            if url.startswith(F.PUBLIC_BASE):
                self.calls.append(F.Call(method, url, {}, {}, None, kw.get("timeout")))
                raise requests.ConnectTimeout("timeout")
            return super().request(method, url, **kw)
    http = SemRede()
    http.database["quote_history"] = F.quote_history()
    res = run(http, tmp_path / "b.json")
    assert res["history-index.json"].action == "erro" and sondas(http) == []
    # o Mindassets voltou publicando (live.json fresco, índice ainda 404 = nunca publicado): carga
    live = gzip_bytes(to_json_bytes({"schemaVersion": 1, "generatedAt": F.GENERATED}))
    http = world(published={"live.json": live})
    res = run(http, tmp_path / "st.json", now=F.NOW + timedelta(minutes=1))
    assert res["history-index.json"].action == "enviado" and len(sondas(http)) == 1


def test_indice_so_com_o_que_foi_de_fato_publicado(tmp_path):
    p = tmp_path / "st.json"
    run(world(), p)
    http = world(feeder_script={"PUT": [403]})                       # o envio da VALE falha
    res = run(http, p, now=F.NOW + timedelta(minutes=5))
    assert res["history-vale3.sa.json"].action == "erro"
    assert "VALE3.SA" not in S.load(p)["history"]["published"]
    http = world()
    res = run(http, p, now=F.NOW + timedelta(minutes=10))            # tenta de novo, na frente
    assert longas(http)[0] == "VALE3.SA" and res["history-vale3.sa.json"].action == "enviado"


# ── sonda ─────────────────────────────────────────────────────────────────────────────────────
def test_sonda_so_quando_a_serie_leve_muda(tmp_path):
    p = tmp_path / "st.json"
    pub = publish(F.quote_history())
    run(world(published=pub), p, only="quotes-daily," + ONLY)
    for m in (5, 10, 65):                                            # mesmo diário: nenhuma sonda
        http = world(published=pub)
        run(http, p, now=F.NOW + timedelta(minutes=m), only="quotes-daily," + ONLY)
        assert sondas(http) == [], m
    # a VALE ganhou o fechamento de 03/11: o quotes-daily relê o diário → a sonda roda → junção
    http = world(published=pub)
    novo = [1793710800, 61.0]
    http.database["quote_history"][0]["daily"].append(novo)
    http.database["quote_history"][0].update(points=9, last_ts=novo[0],
                                             updated_at="2026-11-04T00:20:00+00:00")
    vale_q = next(r for r in http.database["quotes"] if r["ticker"] == "VALE3.SA")
    vale_q.update(daily=vale_q["daily"] + [novo], daily_updated_at="2026-11-04T00:20:00+00:00")
    res = run(http, p, now=datetime(2026, 11, 4, 1, 10, tzinfo=timezone.utc),
              only="quotes-daily," + ONLY)
    assert len(sondas(http)) == 1 and longas(http) == []
    assert doc(http.uploads["history-vale3.sa.json"])["pts"][-1] == novo
    assert res["history-vale3.sa.json"].action == "enviado"
    # e a cada 6 h, mesmo sem mudança
    http = world(published=pub)
    run(http, p, now=datetime(2026, 11, 4, 7, 20, tzinfo=timezone.utc), only=ONLY)
    assert len(sondas(http)) == 1


# ── ordem, nomes, modos ───────────────────────────────────────────────────────────────────────
def test_familia_por_ultimo_no_cold_e_os_de_fora_nunca_saem(tmp_path):
    p = tmp_path / "st.json"
    run(world(), p)
    http = world()
    res = run(http, p, now=F.NOW + timedelta(hours=6), only=None)
    nomes = list(res)
    hist = [n for n in nomes if n.startswith("history-") and n != "history-index.json"]
    assert hist and nomes.index(hist[0]) > nomes.index("ratings.json")
    assert nomes[-1] == "history-index.json"
    for _ in range(4):
        run(world(), p, now=F.NOW + timedelta(hours=7))
    st = S.load(p)["history"]
    assert "RIO.L" not in st["published"] and set(st["published"]) == set(MARKET_QH)
    assert not [u for u in http.uploads if "rio" in u]


def test_pedido_pelo_nome_no_envio(tmp_path):
    http = world()
    res = run(http, tmp_path / "st.json", only="history-ibov")
    assert list(res) == ["history-ibov.json"] and res["history-ibov.json"].action == "enviado"
    assert longas(http) == ["IBOV"]                                  # explícito: sem esperar orçamento
    assert doc(http.uploads["history-ibov.json"])["pts"] == QH["IBOV"]["daily"]
    assert S.load(tmp_path / "st.json")["history"]["published"]["IBOV"]["points"] == 4


def test_out_com_history_os_mesmos_bytes_e_o_indice_da_pasta(tmp_path):
    enviados = world()
    run(enviados, tmp_path / "a.json", only="history-vale3.sa,history-scco")
    out = tmp_path / "saida"
    http = world()
    res = {r.name: r for r in run_cycle(env=F.env(), http=http, now=F.NOW, mode="out", out_dir=out,
                                         state_file=tmp_path / "b.json", only=ONLY,
                                         history=["VALE3.SA", "SCCO"], **F.run_kw())}
    assert set(res) == {"history-vale3.sa.json", "history-scco.json", "history-index.json"}
    for n in ("history-vale3.sa.json", "history-scco.json"):
        assert (out / n).read_bytes() == enviados.uploads[n]         # gzip, os mesmos bytes
    idx = doc((out / "history-index.json").read_bytes())
    assert [i["ticker"] for i in idx["items"]] == ["VALE3.SA", "SCCO"]
    assert idx["items"][1] == {"ticker": "SCCO", "file": "history-scco.json", "firstTs": 1793367000,
                               "lastTs": 1793629800, "points": 2, "lastSplitTs": F.SPLIT_SCCO}
    assert http.feeder_calls() == [] and S.load(tmp_path / "b.json")["files"] == {}
    # sem --history o --out não monta histórico (o índice lista a pasta: vazia aqui)
    res = {r.name: r for r in run_cycle(env=F.env(), http=world(), now=F.NOW, mode="out",
                                         out_dir=tmp_path / "s2", state_file=tmp_path / "c.json",
                                         only=ONLY, **F.run_kw())}
    assert list(res) == ["history-index.json"]
    assert doc((tmp_path / "s2" / "history-index.json").read_bytes())["items"] == []


def test_dry_run_simula_a_fila_sem_mexer_no_registro(tmp_path):
    p = tmp_path / "st.json"
    run_cycle(env=F.env(), http=world(), now=F.NOW, mode="dry", state_file=p, only=ONLY, **F.run_kw())
    http = world()
    res = {r.name: r for r in run_cycle(env=F.env(), http=http, now=F.NOW + timedelta(hours=6),
                                         mode="dry", state_file=p, only=ONLY, **F.run_kw())}
    assert [n for n in res if n != "history-index.json"] == ["history-vale3.sa.json",
                                                            "history-scco.json"]
    assert {r.action for r in res.values()} == {"enviaria"}
    idx = res["history-index.json"]
    assert idx.items == "2 históricos publicados"                    # o que ENVIARIA
    assert S.load(p)["history"]["published"] == {} and http.feeder_calls() == []


def test_leitura_inteira_que_falha_volta_de_hora_em_hora(tmp_path):
    qh = F.quote_history()
    vale = next(r for r in qh if r["ticker"] == "VALE3.SA")
    vale["daily"] = []                                               # linha inconsistente no banco
    p = tmp_path / "st.json"
    run(world(qh), p)
    http = world(qh)
    res = run(http, p, now=F.NOW + timedelta(minutes=5))
    assert longas(http) == ["VALE3.SA"] and res["history-vale3.sa.json"].action == "erro"
    http = world(qh)
    run(http, p, now=F.NOW + timedelta(minutes=10))
    assert "VALE3.SA" not in longas(http)                            # não relê a cada 5 min…
    http = world(qh)
    run(http, p, now=F.NOW + timedelta(minutes=70))
    assert longas(http)[0] == "VALE3.SA"                             # …mas tenta de novo depois de 1 h


def test_papel_publicado_sem_serie_leve_vai_pela_longa(tmp_path):
    vale = QH["VALE3.SA"]
    pub = publish([vale], index_meta={"VALE3.SA": {"points": 7, "lastTs": vale["daily"][-2][0]}})
    http = world(published=pub)
    http.database["quotes"] = [q for q in http.database["quotes"] if q["ticker"] != "VALE3.SA"]
    res = run(http, tmp_path / "st.json")
    assert longas(http) == ["VALE3.SA"] and res["history-vale3.sa.json"].action == "igual"


def test_copia_indisponivel_pelo_mindassets_espera_em_vez_de_reler_a_longa(tmp_path):
    """Registro semeado (o índice publicado respondeu), mas as cópias dos papéis não: com o
    Mindassets parado isso é o publicador, não a falta da cópia — reler a longa de todos de uma vez
    custaria ~2,4 MB numa noite. Espera. Com o Mindassets publicando, 404 é falta de verdade."""
    vale, ibov = QH["VALE3.SA"], QH["IBOV"]
    pub = publish([vale, ibov], index_meta={"VALE3.SA": {"points": 7}, "IBOV": {"points": 3}})
    so_indice = {"history-index.json": pub["history-index.json"], "live.json": _live_velho()}
    http = world(published=so_indice)
    res = run(http, tmp_path / "a.json")
    assert longas(http) == [] and not [n for n in res if H.ticker_from_file(n)]
    live = gzip_bytes(to_json_bytes({"schemaVersion": 1, "generatedAt": F.GENERATED}))
    http = world(published={"history-index.json": pub["history-index.json"], "live.json": live})
    run(http, tmp_path / "b.json")
    assert longas(http) == ["VALE3.SA", "IBOV"]                      # falta de verdade: a longa
