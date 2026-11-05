"""
As fontes da Onda 1b fora das notícias (hub_export/sources.py): o resumo da hora, o calendário,
os relatórios e as flags — cada uma pagando o mínimo de Supabase.

O que estes testes TRAVAM, e por quê:
  1) o resumo da hora é IMUTÁVEL depois de gravado: o exportador só lê nas janelas de gravação
     (E+10…E+45 de Brasília, E = 7…20) e só o que é novo (`slot_start > maior`); na partida a
     frio, semeia da cópia publicada. Fora das janelas: zero chamada;
  2) FAIL-CLOSED: `home_digest` desligada ou ausente → `windows: []` (o arquivo é público) e nem
     lê a tabela; `hub_home_digest` manda nela; a flag vale 15 min (não custa uma RPC por ciclo);
  3) calendário: a sonda "contagem + max(updated_at)" (UMA chamada) a cada 15 min; leitura
     completa quando ela muda — o DELETE do admin aparece na contagem — e uma vez por dia depois
     das 23:45 UTC, que pega o robô da B3 (upsert/ocultação SEM mexer em `updated_at`);
     categorias e janelas a cada 60 min;
  4) relatórios só pela RPC, com `p_limit` 20 — a tabela de origem tem dados pessoais e NUNCA é
     lida; o filtro de URL vale para o link digitado no Clipping.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import test_hub_export_fixtures as F  # noqa: E402
from hub_export import state as S  # noqa: E402
from hub_export.encode import gzip_bytes, to_json_bytes  # noqa: E402
from hub_export.exporters import calendar as cal_exp  # noqa: E402
from hub_export.exporters import news_digest, reports  # noqa: E402
from hub_export.sources import calendar_daily_mark, digest_window_open  # noqa: E402


def utc(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


# ── resumo da hora ──────────────────────────────────────────────────────────────────────────────
def test_janelas_de_gravacao_do_resumo():
    abre = [("2026-11-03T10:10:00Z", True),    # 07:10 de Brasília: a madrugada
            ("2026-11-03T10:09:00Z", False), ("2026-11-03T10:45:00Z", False),
            ("2026-11-03T18:15:00Z", True),    # 15:15
            ("2026-11-03T23:44:00Z", True),    # 20:44: a última de hora em hora (19–20)
            ("2026-11-04T00:15:00Z", False),   # 21:15: não há janela
            ("2026-11-03T09:30:00Z", False)]   # 06:30
    for t, esperado in abre:
        assert digest_window_open(utc(t)) is esperado, t


def test_resumo_lido_uma_vez_e_so_nas_janelas(tmp_path):
    st = S.empty_state()
    src, http = F.make_sources(state=st, now=utc("2026-11-03T18:05:12Z"))   # 15:05: fora
    w = news_digest.build(src)["windows"]
    assert [x["slotStart"] for x in w] == ["2026-11-03T16:00:00Z", "2026-11-03T15:00:00Z",
                                           "2026-11-03T14:00:00Z", "2026-11-02T23:00:00Z"]
    (leitura,) = http.supa_calls("news_digest")                 # partida a frio: lê uma vez
    assert leitura.params["order"] == "slot_start.desc" and leitura.params["limit"] == "48"
    assert leitura.params["select"] == "slot_start,slot_end,kind,status,summary,items,created_at"
    src, http = F.make_sources(state=st, now=utc("2026-11-03T18:06:12Z"))   # fora: zero
    assert news_digest.build(src)["windows"] == w and not http.supa_calls("news_digest")
    http = F.FakeHTTP()
    http.database["news_digest"].append(
        {"slot_start": "2026-11-03T17:00:00+00:00", "slot_end": "2026-11-03T18:00:00+00:00",
         "kind": "hour", "status": "quiet", "summary": None, "items": [], "model": None,
         "tokens_in": None, "tokens_out": None, "created_at": "2026-11-03T18:10:00+00:00"})
    src, http = F.make_sources(http=http, state=st, now=utc("2026-11-03T18:15:00Z"))  # 15:15
    w2 = news_digest.build(src)["windows"]
    (leitura,) = http.supa_calls("news_digest")
    assert leitura.params["slot_start"] == "gt.2026-11-03T16:00:00Z"        # só o novo
    assert w2[0]["slotStart"] == "2026-11-03T17:00:00Z" and len(w2) == 5


def test_resumo_semeado_da_copia_publicada_e_sem_campos_internos():
    publicado = {"schemaVersion": 1, "generatedAt": "2026-11-03T17:20:00Z",
                 "windows": [news_digest.from_row(r) for r in F.DIGEST]}
    http = F.FakeHTTP(published={"news-digest.json": gzip_bytes(to_json_bytes(publicado))})
    pub = S.PublishedCopies(http=http, env=F.env())
    src, http = F.make_sources(http=http, published=pub)
    obj = news_digest.build(src)
    assert http.supa_calls("news_digest")[0].params["slot_start"] == "gt.2026-11-03T16:00:00Z"
    assert len(obj["windows"]) == 4 and obj["updatedAt"] == "2026-11-03T17:10:31Z"
    texto = str(obj)
    for proibido in ("model", "tokens_in", "tokens_out", "score", "gemini"):
        assert proibido not in texto
    assert list(obj["windows"][0]) == ["slotStart", "slotEnd", "kind", "status", "summary",
                                       "createdAt", "items"]
    assert list(obj["windows"][0]["items"][0]) == ["url", "title", "source", "take", "tickers", "at"]
    hostil = obj["windows"][2]["items"][0]
    assert hostil["url"] is None and hostil["title"] == "Hostile"


def test_resumo_guarda_so_as_ultimas_48():
    http = F.FakeHTTP()
    base = utc("2026-11-03T16:00:00Z")
    http.database["news_digest"] = [
        {"slot_start": (base - timedelta(hours=i)).isoformat(), "slot_end": None, "kind": "hour",
         "status": "quiet", "summary": None, "items": [], "created_at": None} for i in range(60)]
    src, _ = F.make_sources(http=http)
    w = news_digest.build(src)["windows"]
    assert len(w) == 48 and w[0]["slotStart"] == "2026-11-03T16:00:00Z"


def test_resumo_fail_closed():
    for flags in ([{"key": "home_digest", "enabled": False}], [],
                  [{"key": "home_digest", "enabled": True}, {"key": "hub_home_digest", "enabled": False}],
                  [{"key": "home_digest", "enabled": "true"}]):            # texto não é true
        http = F.FakeHTTP()
        http.database["rpc"]["get_dashboard_flags"] = flags
        src, http = F.make_sources(http=http)
        obj = news_digest.build(src)
        assert obj["windows"] == [] and obj["updatedAt"] is None, flags
        assert not http.supa_calls("news_digest"), flags                 # nem lê a tabela
    http = F.FakeHTTP()
    http.database["rpc"]["get_dashboard_flags"] = [{"key": "home_digest", "enabled": False},
                                                  {"key": "hub_home_digest", "enabled": True}]
    src, _ = F.make_sources(http=http)
    assert len(news_digest.build(src)["windows"]) == 4                     # o hub manda


def test_flag_do_resumo_vale_15_minutos(tmp_path):
    st = S.empty_state()
    src, http = F.make_sources(state=st)
    news_digest.build(src)
    assert len(http.supa_calls("rpc/get_dashboard_flags")) == 1
    src, http = F.make_sources(state=st, now=F.NOW + timedelta(minutes=10))
    news_digest.build(src)
    assert http.supa_calls("rpc/get_dashboard_flags") == []               # do estado
    src, http = F.make_sources(state=st, now=F.NOW + timedelta(minutes=16))
    news_digest.build(src)
    assert len(http.supa_calls("rpc/get_dashboard_flags")) == 1           # venceu: relê


def test_resumo_desligado_e_religado_rele_o_que_perdeu():
    st = S.empty_state()
    src, _ = F.make_sources(state=st)
    news_digest.build(src)                                                 # ligado: semeou
    http = F.FakeHTTP()
    http.database["rpc"]["get_dashboard_flags"] = []
    src, _ = F.make_sources(http=http, state=st, now=F.NOW + timedelta(minutes=20))
    assert news_digest.build(src)["windows"] == []
    assert st["digest"]["seeded"] is False                                 # religada → relê
    src, http = F.make_sources(state=st, now=F.NOW + timedelta(minutes=40))   # fora da janela
    assert len(news_digest.build(src)["windows"]) == 4
    assert len(http.supa_calls("news_digest")) == 1


# ── calendário ──────────────────────────────────────────────────────────────────────────────────
def cal_calls(http):
    return [c.table for c in http.supa_calls() if "calendar" in c.table]


def test_calendario_forma_e_regras():
    src, http = F.make_sources()
    c = cal_exp.build(src)
    assert list(c) == ["schemaVersion", "generatedAt", "updatedAt", "timezone", "from", "to",
                       "categories", "dataWindows", "events"]
    assert (c["timezone"], c["from"], c["to"]) == ("America/Sao_Paulo", "2025-01-01", "2028-05-31")
    assert c["updatedAt"] == "2026-11-02T13:00:00Z"                    # max(updated_at) dos visíveis
    assert [x["id"] for x in c["categories"]] == ["c-old", "c-earn", "c-data"]   # inclusive inativa
    assert c["categories"][0]["active"] is False
    assert [x["sourceKey"] for x in c["dataWindows"]] == ["secex_steel"]           # só as ativas
    assert [e["id"] for e in c["events"]] == ["e-2", "e-1", "e-5"]     # sem oculto e sem o antigo
    e1 = c["events"][1]
    assert list(e1) == ["id", "title", "categoryId", "startDate", "endDate", "allDay", "startTime",
                        "endTime", "company", "recurrence", "origin"]
    assert (e1["startTime"], e1["origin"], e1["allDay"]) == ("08:00", "b3", False)
    assert c["events"][0]["recurrence"] == {"freq": "monthly", "interval": 1, "bymonthday": [5],
                                            "until": None}               # sem a chave de depuração
    sel = {c.table: c.params["select"] for c in http.supa_calls()}
    for t, s in sel.items():
        assert "updated_by" not in s and "external_id" not in s and "ics_seq" not in s, t
        for livre in ("location", "description", "links"):          # nem lidos do banco
            assert livre not in s.split(","), (t, livre)
    ev = next(c for c in http.supa_calls() if c.table == "rpc/get_exec_calendar_events")
    assert ev.data and b'"p_from": "2025-01-01"' in ev.data and b'"p_to": "2028-05-31"' in ev.data


TEXTO_LIVRE = ("location", "description", "links")


def test_eventos_nunca_levam_local_descricao_nem_links():
    """Texto livre do time, com links copiados de e-mails recebidos (rastreio de clique que
    identifica quem recebeu, confirmação de inscrição): fora até a Onda 4, que decide a regra."""
    http = F.FakeHTTP()
    for e in http.database["exec_calendar_events"]:
        e.update(location="Sala 3", description="Inscrição: https://mailerurl.mziq.com/ls/click?upn=x",
                 links=[{"label": "confirmar", "url":
                         "https://cloud.sfmc.itau.com.br/confirmacao_externa_prod?CampaignID=1"},
                        {"label": "webinar", "url": "https://mail.closir.com/c/eJxck"}])
    src, _ = F.make_sources(http=http)
    c = cal_exp.build(src)
    assert c["events"] and all(k not in e for e in c["events"] for k in TEXTO_LIVRE)
    texto = str(c)
    for pedaco in ("mziq", "sfmc", "closir", "Sala 3", "Inscrição"):
        assert pedaco not in texto
    # um cache de estado de antes desta regra (com as 3 chaves) também não chega ao arquivo
    st = S.empty_state()
    src, _ = F.make_sources(state=st)
    cal_exp.build(src)
    for e in st["calendar"]["events"]:
        e.update(location="Sala 3", description="velha", links=[{"label": "x", "url": "https://x.com"}])
    src, http = F.make_sources(state=st, now=F.NOW + timedelta(minutes=15))
    c = cal_exp.build(src)
    assert "rpc/get_exec_calendar_events" not in cal_calls(http)      # veio do cache…
    assert all(k not in e for e in c["events"] for k in TEXTO_LIVRE)  # …e saiu sem as 3 chaves


def test_calendario_sonda_e_leitura_completa(tmp_path):
    st = S.empty_state()
    src, http = F.make_sources(state=st)
    cal_exp.build(src)
    assert cal_calls(http) == ["exec_calendar_events", "rpc/get_exec_calendar_categories",
                               "rpc/get_exec_calendar_data_windows", "rpc/get_exec_calendar_events"]
    probe = http.supa_calls("exec_calendar_events")[0]
    assert probe.headers.get("Prefer") == "count=exact" and probe.params["limit"] == "1"
    assert probe.params["is_visible"] == "eq.true"
    # 15 min depois, nada mudou: só a sonda
    src, http = F.make_sources(state=st, now=F.NOW + timedelta(minutes=15))
    cal_exp.build(src)
    assert cal_calls(http) == ["exec_calendar_events"]
    # admin editou (updated_at sobe): leitura completa
    http = F.FakeHTTP()
    http.database["exec_calendar_events"][0].update(title="VALE3 | 3Q26 (data nova)",
                                                    updated_at="2026-11-03T18:20:00+00:00")
    src, http = F.make_sources(http=http, state=st, now=F.NOW + timedelta(minutes=30))
    c = cal_exp.build(src)
    assert "rpc/get_exec_calendar_events" in cal_calls(http)
    assert c["events"][1]["title"] == "VALE3 | 3Q26 (data nova)"
    # admin apagou um evento (some da contagem, o max não muda): leitura completa
    http = F.FakeHTTP()
    http.database["exec_calendar_events"][0].update(title="VALE3 | 3Q26 (data nova)",
                                                    updated_at="2026-11-03T18:20:00+00:00")
    http.database["exec_calendar_events"] = [e for e in http.database["exec_calendar_events"]
                                             if e["id"] != "e-5"]
    src, http = F.make_sources(http=http, state=st, now=F.NOW + timedelta(minutes=45))
    c = cal_exp.build(src)
    assert "rpc/get_exec_calendar_events" in cal_calls(http)
    assert [e["id"] for e in c["events"]] == ["e-2", "e-1"]


def test_robo_da_b3_sem_updated_at_e_pego_pela_leitura_diaria():
    st = S.empty_state()
    src, _ = F.make_sources(state=st, now=utc("2026-11-03T20:00:00Z"))
    cal_exp.build(src)

    def mudou_sem_carimbo():
        http = F.FakeHTTP()
        e = http.database["exec_calendar_events"][0]
        e["start_date"] = "2026-10-29"                       # o robô mexe na data, updated_at igual
        return http
    src, http = F.make_sources(http=mudou_sem_carimbo(), state=st, now=utc("2026-11-03T23:40:00Z"))
    c = cal_exp.build(src)
    assert "rpc/get_exec_calendar_events" not in cal_calls(http)   # a sonda não vê
    assert c["events"][1]["startDate"] == "2026-10-23"
    src, http = F.make_sources(http=mudou_sem_carimbo(), state=st, now=utc("2026-11-03T23:50:00Z"))
    c = cal_exp.build(src)                                    # depois das 23:45 UTC: relê tudo
    assert "rpc/get_exec_calendar_events" in cal_calls(http)
    assert [e["startDate"] for e in c["events"] if e["id"] == "e-1"] == ["2026-10-29"]
    src, http = F.make_sources(http=mudou_sem_carimbo(), state=st, now=utc("2026-11-04T00:05:00Z"))
    cal_exp.build(src)
    assert cal_calls(http) == ["exec_calendar_events"]       # uma vez por dia
    assert calendar_daily_mark(utc("2026-11-04T00:05:00Z")) == "2026-11-03T23:45:00Z"


def test_categorias_e_janelas_a_cada_hora_e_leitura_que_falha_nao_estraga_o_estado():
    st = S.empty_state()
    src, _ = F.make_sources(state=st)
    cal_exp.build(src)
    src, http = F.make_sources(state=st, now=F.NOW + timedelta(minutes=60))
    cal_exp.build(src)
    assert cal_calls(http) == ["exec_calendar_events", "rpc/get_exec_calendar_categories",
                               "rpc/get_exec_calendar_data_windows"]
    antes = dict(st["calendar"])
    http = F.FakeHTTP(supa_error={"get_exec_calendar_events": 500})
    http.database["exec_calendar_events"][0]["updated_at"] = "2026-11-03T19:00:00+00:00"
    src, _ = F.make_sources(http=http, state=st, now=F.NOW + timedelta(minutes=75))
    try:
        cal_exp.build(src)
        assert False, "deveria falhar"
    except Exception as e:
        assert "HTTP 500" in str(e)
    assert st["calendar"]["probe"] == antes["probe"]         # a próxima rodada relê


# ── relatórios ──────────────────────────────────────────────────────────────────────────────────
def test_relatorio_com_link_de_email_marketing_sai_com_o_link_limpo():
    import hashlib
    http = F.FakeHTTP()
    rows = http.database["rpc_rows"]["reports"]
    rows[0]["link"] = ("https://www.itau.com.br/itaubba-pt/portal/viewer/equity/2815389c"
                       "?sfmc_id=123&utm_source=sfmc&utm_id=9")
    rows[2]["link"] = "https://mailerurl.mziq.com/ls/click?upn=abc"   # redirecionador: fora
    src, _ = F.make_sources(http=http)
    items = reports.build(src)["items"]
    assert items[0]["link"] == "https://www.itau.com.br/itaubba-pt/portal/viewer/equity/2815389c"
    assert items[2]["link"] is None and "sfmc" not in str(items) and "mziq" not in str(items)
    assert items[0]["id"] == hashlib.sha1(rows[0]["link"].encode()).hexdigest()[:12]  # id estável


def test_resumo_relido_do_estado_passa_pelo_filtro_de_url_de_novo():
    st = S.empty_state()
    src, _ = F.make_sources(state=st)
    news_digest.build(src)
    st["digest"]["rows"][0]["items"][0]["url"] = "https://mailerurl.mziq.com/ls/click?upn=x"
    st["digest"]["rows"][3]["items"][0]["url"] = F.U_CVM + "&sfmc_id=destinatario&utm_source=sfmc"
    src, http = F.make_sources(state=st, now=F.NOW + timedelta(minutes=1))   # fora da janela
    w = news_digest.build(src)["windows"]
    assert not http.supa_calls("news_digest") and w[0]["items"][0]["url"] is None
    assert w[3]["items"][0]["url"] == F.U_CVM                         # limpo, sem o destinatário


def test_relatorios_so_pela_rpc():
    src, http = F.make_sources()
    obj = reports.build(src)
    assert [c.table for c in http.supa_calls()] == ["rpc/get_recent_reports_v3"]
    (c,) = http.supa_calls()
    assert c.data == b'{"p_limit": 20}' and c.params["select"] == "name,sector,link,date,companies"
    assert [i["name"] for i in obj["items"]] == ["Vale 3Q26: strong volumes",
                                                 "Suzano: pulp price preview",
                                                 "Macro: China steel demand"]
    assert obj["items"][1]["link"] is None                    # SharePoint com token: fora
    assert list(obj) == ["schemaVersion", "generatedAt", "items"]
    assert all(list(i) == ["id", "name", "sector", "link", "date", "companies"] for i in obj["items"])
    assert not [t for t in (c.table for c in http.supa_calls()) if "clipping" in t]
