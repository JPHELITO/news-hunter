"""
As notícias da Onda 1b (hub_export/news.py): o cache do horizonte e as leituras que mantêm os
arquivos certos sem `updated_at` no banco e dentro do orçamento do Supabase.

O que estes testes TRAVAM (pesquisa 1b-dados §3.4–3.7 e §9), e por quê:
  1) incremental com MARGEM: `found_at` é o relógio do raspador (o hunt-playwright insere minutos
     depois); a margem de 30 min pega a linha que chegou fora de ordem — e é UMA chamada por ciclo;
  2) a linha RETROATIVA (lote manual, found_at de horas atrás) escapa do incremental e é pega pela
     varredura de 80 h (a cada 3 h);
  3) EXCLUSÃO e REINCLUSÃO só aparecem na varredura (PATCH sem carimbo): a notícia retirada sai do
     news-latest e da semana; a reincluída volta, lida por inteiro;
  4) a semana ANTIGA (fora do horizonte) é corrigida SOBRE A CÓPIA PUBLICADA (Mindassets), sem
     reler a semana inteira do banco;
  5) o corte do news-latest é arredondado à hora: o arquivo não muda a cada ciclo só porque o
     relógio andou;
  6) a janela de segunda-feira (~79 h, desde sexta 17:00 de Brasília) e a semana ISO da virada do
     ano (2026-w53 com 01/01/2027);
  7) partida a frio sobre as CÓPIAS PUBLICADAS (sem ler o horizonte do banco) e `--rebuild`;
  8) carga inicial aos poucos (≤ 2 semanas por hora, da mais nova para a mais antiga, retomando
     pelo índice publicado, com teto de bytes por estado) e a conferência diária das antigas;
  9) o índice só lista semanas JÁ publicadas; a semana que saiu do horizonte ganha a última
     montagem e sai do cache; erro do banco derruba só os arquivos de notícia daquela execução.
"""
from __future__ import annotations

import gzip
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import test_hub_export_fixtures as F  # noqa: E402
from hub_export import news as N  # noqa: E402
from hub_export import state as S  # noqa: E402
from hub_export import weeks as W  # noqa: E402
from hub_export.encode import content_hash  # noqa: E402
from hub_export.exporters import news_latest  # noqa: E402
from hub_export.run import run_cycle  # noqa: E402


def utc(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def run(http, path, now=F.NOW, **kw):
    kw.setdefault("memory", F.memory())
    kw.setdefault("only", "news-latest,news-week,news-index")
    return {r.name: r for r in run_cycle(env=F.env(), http=http, now=now, state_file=path,
                                         **F.run_kw(**kw))}


def doc(http, name):
    raw = http.uploads[name]
    return json.loads(gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw)


def urls(obj):
    return [i["url"] for i in obj["items"]]


def kinds(http):
    """O tipo de cada leitura de notícias, pelo filtro (incremental, varredura, semana…)."""
    out = []
    for c in http.supa_calls("news_articles"):
        p = c.params
        if c.method == "HEAD":
            out.append("contagem")
        elif "take_llm_at.gte" in p.get("or", ""):
            out.append("incremental")
        elif "url" in p and p["url"].startswith("in."):
            out.append("por url")
        elif p.get("or", "").startswith("(and(published_at.gte"):
            out.append("semana")
        elif p["select"].startswith("url,published_at,found_at,take_llm"):
            out.append("varredura")
        else:
            out.append("horizonte")
    return out


def row(url, title="Nova manchete", **kw):
    kw.setdefault("take", "+")
    return F._n(url, title, kw.pop("source", "Mining.com"), kw.pop("domain", "mining.com"),
                kw.pop("published", None), kw.pop("found"), **kw)


# ── partida a frio ──────────────────────────────────────────────────────────────────────────────
def test_primeiro_envio_le_o_horizonte_uma_vez_e_depois_so_o_incremental(tmp_path):
    p = tmp_path / "st.json"
    http = F.FakeHTTP()
    res = run(http, p)
    assert {n: r.action for n, r in res.items()} == {
        "news-latest.json": "enviado", "news-week-2026-w43.json": "enviado",
        "news-week-2026-w44.json": "enviado", "news-week-2026-w45.json": "enviado",
        "news-index.json": "enviado"}
    assert kinds(http) == ["horizonte", "semana"]          # horizonte + a carga da semana 43
    assert urls(doc(http, "news-latest.json")) == F.LATEST_URLS
    for lab, esperado in F.WEEK_URLS.items():
        assert urls(doc(http, W.file_name(lab))) == esperado, lab
    http = F.FakeHTTP()
    run(http, p, now=F.NOW + timedelta(minutes=5))
    assert kinds(http) == ["incremental"]                  # o ciclo normal: UMA chamada
    c = http.supa_calls("news_articles")[0]
    assert c.params["or"] == ("(found_at.gte.2026-11-03T17:28:03Z,"
                              "take_llm_at.gte.2026-11-03T17:59:00Z)")
    assert c.params["include_in_report"] == "eq.true" and "*" not in c.params["select"]


def test_partida_a_frio_semeia_das_copias_publicadas(tmp_path):
    primeiro = F.FakeHTTP()
    run(primeiro, tmp_path / "a.json")
    # runner novo: estado vazio, mas o Mindassets tem as cópias
    http = F.FakeHTTP(published=dict(primeiro.uploads))
    res = run(http, tmp_path / "b.json", now=F.NOW + timedelta(minutes=5))
    assert {r.action for r in res.values()} == {"igual"}
    assert http.feeder_calls() == []
    # nada do horizonte inteiro: incremental + varredura de 80 h; a incluída SEM TÍTULO (que nunca
    # sai) é lida uma vez por estado e anotada
    assert kinds(http) == ["incremental", "varredura", "por url"]
    st = N.load_news(N.news_state_path(tmp_path / "b.json"))
    assert st["seededFrom"] == "publicada" and st["weeks"].keys() == {
        F.WEEK_OLD, F.WEEK_PREV, F.WEEK_CUR}               # registro semeado do índice publicado
    inc = http.supa_calls("news_articles")[0].params["or"]
    assert "take_llm_at.gte.2026-11-03T17:53:12Z" in inc    # generatedAt publicado − 10 − 2 min
    assert st["wmTake"] == "2026-11-03T18:01:00Z"           # e anda com o que o incremental leu
    assert st["dropped"] == {"https://www.mining.com/no-title/": "sem título"}
    http = F.FakeHTTP(published=dict(primeiro.uploads))
    run(http, tmp_path / "b.json", now=F.NOW + timedelta(hours=3, minutes=10))
    assert kinds(http) == ["incremental", "varredura"]      # a varredura seguinte não relê


def test_rebuild_ignora_as_copias_publicadas_e_rele_o_banco(tmp_path):
    primeiro = F.FakeHTTP()
    run(primeiro, tmp_path / "a.json")
    http = F.FakeHTTP(published=dict(primeiro.uploads))
    http.database["news_articles"][0]["title"] = "Iron ore futures fall in Dalian (título corrigido)"
    res = run(http, tmp_path / "b.json", rebuild=True)
    assert kinds(http) == ["horizonte"]
    assert res["news-latest.json"].action == "enviado"
    assert doc(http, "news-latest.json")["items"][0]["title"].endswith("(título corrigido)")


# ── incremental, linha retroativa, varredura ───────────────────────────────────────────────────
def test_incremental_com_margem_e_linha_retroativa(tmp_path):
    p = tmp_path / "st.json"
    run(F.FakeHTTP(), p)
    http = F.FakeHTTP()
    fora_de_ordem = row("https://www.mining.com/fora-de-ordem/", found="2026-11-03T17:40:00+00:00",
                        take_at="2026-11-03T17:41:00+00:00")
    retroativa = row("https://www.mining.com/lote-manual/", found="2026-11-03T14:00:00+00:00",
                     published="2026-11-03T13:50:00+00:00", take_at="2026-11-03T14:01:00+00:00")
    http.database["news_articles"] += [fora_de_ordem, retroativa]
    res = run(http, p, now=F.NOW + timedelta(minutes=5))
    latest = urls(doc(http, "news-latest.json"))
    assert res["news-latest.json"].reason == "mudou"
    assert fora_de_ordem["url"] in latest                   # 18 min antes da marca: a margem pega
    assert retroativa["url"] not in latest                  # 4 h antes: escapa do incremental
    http2 = F.FakeHTTP()
    http2.database = http.database
    run(http2, p, now=F.NOW + timedelta(hours=3, minutes=1))   # a varredura de 80 h (3 h depois)
    assert "varredura" in kinds(http2) and "por url" in kinds(http2)
    assert retroativa["url"] in urls(doc(http2, "news-latest.json"))


def test_take_da_ia_que_chega_depois_atualiza_pelo_take_llm_at(tmp_path):
    p = tmp_path / "st.json"
    run(F.FakeHTTP(), p)
    http = F.FakeHTTP()
    fm = next(r for r in http.database["news_articles"] if r["url"] == F.U_FM)
    fm.update(take_llm="+", take_llm_at="2026-11-03T18:07:00+00:00")
    run(http, p, now=F.NOW + timedelta(minutes=5))
    item = next(i for i in doc(http, "news-latest.json")["items"] if i["url"] == F.U_FM)
    assert item["take"] == "+"
    assert kinds(http) == ["incremental"]


def test_exclusao_e_reinclusao_pela_varredura(tmp_path):
    p = tmp_path / "st.json"
    run(F.FakeHTTP(), p)
    http = F.FakeHTTP()
    cvm = next(r for r in http.database["news_articles"] if r["url"] == F.U_CVM)
    cvm["include_in_report"] = False                       # retirada do relatório (PATCH sem carimbo)
    run(http, p, now=F.NOW + timedelta(minutes=5))
    assert "news-latest.json" not in http.uploads           # o incremental não vê a retirada…
    http = F.FakeHTTP()
    http.database["news_articles"] = [r for r in http.database["news_articles"]
                                      if r["url"] != F.U_CVM] + [dict(cvm)]
    t = F.NOW + timedelta(hours=3, minutes=5)
    res = run(http, p, now=t, only="news-latest,news-week-2026-w45,news-index")
    assert F.U_CVM not in urls(doc(http, "news-latest.json"))    # …a varredura vê
    assert F.U_CVM not in urls(doc(http, "news-week-2026-w45.json"))
    assert res["news-index.json"].action == "enviado"
    assert [w["count"] for w in doc(http, "news-index.json")["weeks"] if w["week"] == F.WEEK_CUR] == [6]
    # reinclusão: volta na varredura seguinte, lida por inteiro (url=in.)
    http = F.FakeHTTP()
    run(http, p, now=t + timedelta(hours=3, minutes=1), only="news-latest")
    assert "por url" in kinds(http) and F.U_CVM in urls(doc(http, "news-latest.json"))


def test_varredura_depois_de_mudar_as_regras_do_classificador(tmp_path):
    p = tmp_path / "st.json"
    regras = tmp_path / "hunter"
    regras.mkdir()
    (regras / "news_take_classifier.py").write_text("v1", encoding="utf-8")
    pub = S.PublishedCopies(http=F.FakeHTTP(), env=F.env())
    st = N.empty_news_state()
    sup = F.make_sources()[0].supa

    def store(now, st=st):
        return N.NewsStore(sup, pub, st, now=now, first_week=F.ARCHIVE_FIRST, hunter_dir=regras)
    store(F.NOW).sync()
    (regras / "news_take_classifier.py").write_text("v2", encoding="utf-8")   # deploy de regra
    s2 = store(F.NOW + timedelta(minutes=5))
    s2.sync()
    assert st["sweep"]["due"] == ["2026-11-03T18:20:12Z", "2026-11-03T18:50:12Z"]
    n = sup.calls
    store(F.NOW + timedelta(minutes=16)).sync()             # +10 min do deploy: varre
    assert sup.calls - n == 2 and st["sweep"]["due"] == ["2026-11-03T18:50:12Z"]


# ── semana antiga: correção sobre a cópia publicada ─────────────────────────────────────────────
def test_semana_antiga_corrigida_sobre_a_copia_publicada(tmp_path):
    p = tmp_path / "st.json"
    primeiro = F.FakeHTTP()
    run(primeiro, p)
    http = F.FakeHTTP(published=dict(primeiro.uploads))
    w43 = next(r for r in http.database["news_articles"] if r["url"] == F.U_W43)
    w43.update(take_llm="+", take_llm_at="2026-11-03T18:06:00+00:00")   # take dado à mão
    res = run(http, p, now=F.NOW + timedelta(minutes=61))
    assert res["news-week-2026-w43.json"].action == "enviado"
    assert doc(http, "news-week-2026-w43.json")["items"][0]["take"] == "+"
    assert "semana" not in kinds(http)                      # não releu a semana do banco
    assert [c.url for c in http.calls if c.url.endswith("news-week-2026-w43.json")]  # baixou a cópia


def test_linha_atrasada_de_semana_antiga_entra_pela_varredura(tmp_path):
    p = tmp_path / "st.json"
    primeiro = F.FakeHTTP()
    run(primeiro, p)
    http = F.FakeHTTP(published=dict(primeiro.uploads))
    atrasada = row("https://www.mining.com/atrasada-w43/", published="2026-10-25T12:00:00+00:00",
                   found="2026-11-02T11:00:00+00:00", take_at="2026-11-02T11:05:00+00:00")
    http.database["news_articles"].append(atrasada)
    run(http, p, now=F.NOW + timedelta(hours=3, minutes=1))
    assert urls(doc(http, "news-week-2026-w43.json")) == [atrasada["url"], F.U_W43]
    assert atrasada["url"] in urls(doc(http, "news-latest.json"))     # achada nas últimas 48 h


# ── janela do news-latest ───────────────────────────────────────────────────────────────────────
def test_corte_arredondado_a_hora(tmp_path):
    p = tmp_path / "st.json"
    http = F.FakeHTTP()
    http.database["news_articles"].append(row(
        "https://www.mining.com/borda/", published="2026-11-01T18:30:00+00:00",
        found="2026-11-01T18:35:00+00:00", take_at="2026-11-01T18:40:00+00:00"))
    run(http, p)
    h = {}
    for minutos in (0, 50):
        src, _ = F.make_sources(http=http, now=F.NOW + timedelta(minutes=minutos))
        obj = news_latest.build(src)
        h[minutos] = content_hash(obj)
        assert obj["windowStart"] == "2026-11-01T18:00:00Z"
    assert h[0] == h[50]                                    # 50 min depois, mesmo arquivo
    src, _ = F.make_sources(http=http, now=F.NOW + timedelta(minutes=56))   # 19:01: janela anda
    obj = news_latest.build(src)
    assert obj["windowStart"] == "2026-11-01T19:00:00Z"
    assert "https://www.mining.com/borda/" not in urls(obj)


def test_janela_de_segunda_feira_de_79_horas():
    segunda = utc("2026-11-10T02:59:00Z")                   # segunda 23:59 em Brasília
    http = F.FakeHTTP()
    http.database["news_articles"] = [
        row("https://www.mining.com/sexta-1730/", published="2026-11-06T20:30:00+00:00",
            found="2026-11-06T20:31:00+00:00"),
        row("https://www.mining.com/sexta-1630/", published="2026-11-06T19:30:00+00:00",
            found="2026-11-06T19:31:00+00:00")]
    src, _ = F.make_sources(http=http, now=segunda)
    obj = news_latest.build(src)
    assert obj["windowStart"] == "2026-11-06T20:00:00Z"     # sexta 17:00 de Brasília
    assert urls(obj) == ["https://www.mining.com/sexta-1730/"]
    assert timedelta(hours=78) < segunda - utc(obj["windowStart"]) < timedelta(hours=80)


def test_semana_iso_da_virada_do_ano(tmp_path):
    agora = utc("2027-01-02T12:00:00Z")
    http = F.FakeHTTP()
    http.database["news_articles"] = [
        row("https://www.mining.com/ano-novo/", published="2027-01-01T10:00:00+00:00",
            found="2027-01-01T10:05:00+00:00"),
        row("https://www.mining.com/natal/", published="2026-12-28T00:30:00+00:00",
            found="2026-12-28T00:35:00+00:00")]
    res = run(http, tmp_path / "st.json", now=agora, first_week="2026-w52")
    assert F.week_names(res) == ["news-week-2026-w52.json", "news-week-2026-w53.json"]
    w53 = doc(http, "news-week-2026-w53.json")
    assert (w53["week"], w53["from"], w53["to"]) == ("2026-w53", "2026-12-28", "2027-01-03")
    assert urls(w53) == ["https://www.mining.com/ano-novo/", "https://www.mining.com/natal/"]
    idx = doc(http, "news-index.json")
    assert [w["week"] for w in idx["weeks"]] == ["2026-w53", "2026-w52"]
    assert (idx["firstDate"], idx["lastDate"], idx["count"]) == ("2026-12-28", "2027-01-01", 2)


# ── carga inicial aos poucos e conferência das antigas ─────────────────────────────────────────
def test_carga_inicial_duas_por_hora_da_mais_nova_para_a_mais_antiga(tmp_path):
    p = tmp_path / "st.json"
    http = F.FakeHTTP()
    res = run(http, p, first_week="2026-w39")
    assert F.week_names(res) == ["news-week-2026-w42.json", "news-week-2026-w43.json",
                                 "news-week-2026-w44.json", "news-week-2026-w45.json"]
    res = run(F.FakeHTTP(), p, now=F.NOW + timedelta(minutes=30), first_week="2026-w39")
    assert F.week_names(res) == ["news-week-2026-w44.json", "news-week-2026-w45.json"]  # cota
    h3 = F.FakeHTTP()
    res = run(h3, p, now=F.NOW + timedelta(minutes=61), first_week="2026-w39")
    assert {n for n, r in res.items() if r.action == "enviado"} >= {
        "news-week-2026-w40.json", "news-week-2026-w41.json", "news-index.json"}
    assert [w["week"] for w in doc(h3, "news-index.json")["weeks"]] == [
        "2026-w45", "2026-w44", "2026-w43", "2026-w42", "2026-w41", "2026-w40"]
    # runner novo retoma do índice publicado: falta só a 39
    pub = {**http.uploads, **h3.uploads}
    h4 = F.FakeHTTP(published=pub)
    res = run(h4, tmp_path / "novo.json", now=F.NOW + timedelta(minutes=65), first_week="2026-w39")
    assert [n for n, r in res.items() if r.action == "enviado" and "w3" in n] == [
        "news-week-2026-w39.json"]


def test_carga_inicial_respeita_o_teto_de_bytes_por_estado(tmp_path, monkeypatch):
    monkeypatch.setattr(N, "ARCHIVE_STATE_BYTES", 1)
    p = tmp_path / "st.json"
    res = run(F.FakeHTTP(), p, first_week="2026-w39")
    assert "news-week-2026-w43.json" in res                 # a 1ª carga já passa do teto…
    res = run(F.FakeHTTP(), p, now=F.NOW + timedelta(hours=2), first_week="2026-w39")
    assert not [n for n in res if n in ("news-week-2026-w41.json", "news-week-2026-w40.json")]


def test_conferencia_diaria_corrige_semana_antiga_que_mudou(tmp_path):
    p = tmp_path / "st.json"
    primeiro = F.FakeHTTP()
    run(primeiro, p)
    madrugada = utc("2026-11-04T05:10:00Z")
    http = F.FakeHTTP(published=dict(primeiro.uploads))
    run(http, p, now=madrugada)
    assert "contagem" in kinds(http) and "news-week-2026-w43.json" not in http.uploads  # bateu
    # no dia seguinte a notícia da semana 43 é retirada (backfill): a contagem diverge
    http = F.FakeHTTP(published=dict(primeiro.uploads))
    next(r for r in http.database["news_articles"] if r["url"] == F.U_W43)["include_in_report"] = False
    res = run(http, p, now=madrugada + timedelta(days=1))
    assert res["news-week-2026-w43.json"].action == "enviado"
    assert urls(doc(http, "news-week-2026-w43.json")) == []
    assert [w["count"] for w in doc(http, "news-index.json")["weeks"] if w["week"] == F.WEEK_OLD] == [0]
    # fora da janela das 05 UTC não confere
    http = F.FakeHTTP(published=dict(primeiro.uploads))
    run(http, p, now=madrugada + timedelta(days=1, hours=2))
    assert "contagem" not in kinds(http)


# ── registro, índice e horizonte que anda ───────────────────────────────────────────────────────
def test_indice_so_lista_semana_publicada(tmp_path):
    p = tmp_path / "st.json"
    http = F.FakeHTTP(feeder_script={"PUT": [200, 403]})   # a 2ª semana (44) falha no PUT
    from hub_export.feeder import FeederClient
    feeder = FeederClient(F.FEEDER_URL, F.PROJECT_ID, F.FEEDER_KEY, http=http, retry_wait=0,
                          sleep=lambda s: None)
    res = run(http, p, only="news-week,news-index", feeder=feeder)
    assert res["news-week-2026-w44.json"].action == "erro"
    assert [w["week"] for w in doc(http, "news-index.json")["weeks"]] == ["2026-w45", "2026-w43"]
    res = run(F.FakeHTTP(), p, now=F.NOW + timedelta(minutes=5), only="news-week,news-index")
    assert res["news-week-2026-w44.json"].action == "enviado"   # tenta de novo no ciclo seguinte


def test_semana_que_sai_do_horizonte_ganha_a_ultima_montagem_e_sai_do_cache(tmp_path):
    p = tmp_path / "st.json"
    run(F.FakeHTTP(), p)
    segunda = utc("2026-11-09T00:20:00Z")                   # semana 46: a 44 virou antiga
    http = F.FakeHTTP()
    res = run(http, p, now=segunda)
    assert F.week_names(res) == ["news-week-2026-w44.json", "news-week-2026-w45.json",
                                 "news-week-2026-w46.json"]
    assert res["news-week-2026-w44.json"].action == "igual"
    st = N.load_news(N.news_state_path(p))
    assert st["completeFrom"] == "2026-w45"
    res = run(F.FakeHTTP(), p, now=segunda + timedelta(hours=1, minutes=5))
    assert F.week_names(res) == ["news-week-2026-w45.json", "news-week-2026-w46.json"]
    rows = N.load_news(N.news_state_path(p))["rows"]
    assert F.U_PREV not in rows and F.U_SUNDAY not in rows  # saíram do cache
    assert F.U_LATE in rows                                 # achada no horizonte: fica (atrasada)


def test_out_monta_o_indice_pelas_semanas_da_pasta(tmp_path):
    out = tmp_path / "saida"
    res = run(F.FakeHTTP(), tmp_path / "st.json", mode="out", out_dir=out, weeks=1)
    assert F.week_names(res) == ["news-week-2026-w45.json"]
    idx = json.loads((out / "news-index.json").read_bytes())
    assert [w["week"] for w in idx["weeks"]] == ["2026-w45"] and idx["count"] == 7
    res = run(F.FakeHTTP(), tmp_path / "st.json", mode="out", out_dir=out, weeks="all",
              only="news-week,news-index")
    idx = json.loads((out / "news-index.json").read_bytes())
    assert [w["week"] for w in idx["weeks"]] == ["2026-w45", "2026-w44", "2026-w43"]
    (out / "news-week-2026-w40.json").write_bytes(b"lixo")      # arquivo ruim fica de fora
    run(F.FakeHTTP(), tmp_path / "st.json", mode="out", out_dir=out, only="news-index")
    idx = json.loads((out / "news-index.json").read_bytes())
    assert "2026-w40" not in [w["week"] for w in idx["weeks"]]


def test_dry_run_simula_o_loop_e_nao_mexe_no_registro(tmp_path):
    p = tmp_path / "st.json"
    res = run(F.FakeHTTP(), p, mode="dry")
    assert {r.action for r in res.values()} == {"enviaria"}
    st = N.load_news(N.news_state_path(p))
    assert st["weeks"] == {} and st["seeded"] is True        # cache sim, registro de envio não
    assert len(st["archive"]["loads"]) == 1                  # a leitura da carga conta na cota


def test_erro_no_banco_derruba_so_as_noticias_e_o_ciclo_seguinte_recupera(tmp_path):
    p = tmp_path / "st.json"
    http = F.FakeHTTP(supa_error={"news_articles": 503})
    res = run(http, p, only=None)
    assert res["news-latest.json"].action == "erro" and "HTTP 503" in res["news-latest.json"].reason
    assert res["news-week"].action == "erro" and res["live.json"].action == "enviado"
    assert res["news-index.json"].action == "erro"
    res = run(F.FakeHTTP(), p, only=None, now=F.NOW + timedelta(minutes=5))
    assert res["news-latest.json"].action == "enviado"


def test_estado_das_noticias_mora_ao_lado_do_principal_e_sobrevive_a_corrupcao(tmp_path):
    p = tmp_path / "st.json"
    assert N.news_state_path(p) == tmp_path / "st_news.json"
    assert N.news_state_path(S.state_path({"RUNNER_TEMP": str(tmp_path)})).name == \
        "hub_export_state_news.json"
    N.news_state_path(p).write_text("{corrompido", encoding="utf-8")
    res = run(F.FakeHTTP(), p)
    assert res["news-latest.json"].action == "enviado"
    assert N.load_news(N.news_state_path(p))["seeded"] is True


def test_regras_do_disco_viram_um_hash_estavel(tmp_path):
    assert N.rules_hash(tmp_path) is None
    (tmp_path / "filter.py").write_text("x", encoding="utf-8")
    h = N.rules_hash(tmp_path)
    assert h and h == N.rules_hash(tmp_path)
    assert N.rules_hash() is not None                       # as regras de verdade do repositório


def test_cache_de_antes_de_um_filtro_mais_estrito_nao_publica_o_link_recusado(tmp_path):
    p = tmp_path / "st.json"
    run(F.FakeHTTP(), p)
    np_ = N.news_state_path(p)
    st = N.load_news(np_)
    item = dict(st["rows"][F.U_SMM])
    item["url"] = "https://mailerurl.mziq.com/ls/click?upn=destinatario"   # como se aceito antes
    st["rows"][item["url"]] = item
    N.save_news(np_, st)
    res = run(F.FakeHTTP(), p, now=F.NOW + timedelta(minutes=5))
    assert res["news-latest.json"].action == "igual"           # o item "aceito antes" não entra
    out = tmp_path / "saida"
    run(F.FakeHTTP(), p, now=F.NOW + timedelta(minutes=6), mode="out", out_dir=out, weeks=1)
    for nome in ("news-latest.json", "news-week-2026-w45.json"):
        assert b"mziq" not in gzip.decompress((out / nome).read_bytes()), nome


def test_noticia_com_parametro_do_destinatario_sai_limpa_sem_duplicar(tmp_path):
    """A chave do cache é o link publicado (limpo): a varredura não acha que a notícia sumiu nem
    a relê, e a partida a frio sobre a cópia publicada não a duplica."""
    from hub_export.exporters.news_common import news_id
    cru = "https://www.mining.com/artigo-email/?id=9&sfmc_id=destinatario&utm_source=sfmc"
    limpo = "https://www.mining.com/artigo-email/?id=9"
    http = F.FakeHTTP()
    http.database["news_articles"].append(row(cru, found="2026-11-03T17:00:00+00:00",
                                              take_at="2026-11-03T17:01:00+00:00"))
    p = tmp_path / "st.json"
    run(http, p)
    itens = [i for i in doc(http, "news-latest.json")["items"] if "artigo-email" in i["url"]]
    assert [(i["url"], i["id"]) for i in itens] == [(limpo, news_id(limpo))]
    assert "destinatario" not in str(doc(http, "news-week-2026-w45.json"))
    h2 = F.FakeHTTP()
    h2.database = http.database
    run(h2, p, now=F.NOW + timedelta(hours=3, minutes=1))         # varredura de 80 h
    assert "varredura" in kinds(h2) and "por url" not in kinds(h2)   # não a dá por nova
    h3 = F.FakeHTTP(published={**http.uploads, **h2.uploads})
    h3.database = http.database
    out = tmp_path / "saida"
    run(h3, tmp_path / "novo.json", now=F.NOW + timedelta(hours=3, minutes=5), mode="out",
        out_dir=out, weeks=1)
    lat = json.loads(gzip.decompress((out / "news-latest.json").read_bytes()))
    assert sum(1 for i in lat["items"] if "artigo-email" in i["url"]) == 1
