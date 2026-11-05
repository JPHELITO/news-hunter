"""
As regras de TELA do site que o exportador passou a resolver (hub_export/exporters/news_reference.py)
e as peças puras da Onda 1b: filtro de URL, semanas ISO, item de notícia, calendário e relatórios.

O que estes testes TRAVAM, e por quê:
  1) os MESMOS conjuntos do site (`_LABEL_ALIAS`, `_SM_SOURCES`, `_PP_SOURCES`, `_PRIMARY_SOURCES`,
     `_PULSE_CO` — index.html 1930-1971 e 2268-2276): fonte nova de setor único no site sem a cópia
     aqui = o setor exibido do hub diverge do site;
  2) a janela do Market Pulse com os 12 casos do teste do próprio site (tests/test_news_pulse.html
     41-55) e a segunda-feira de ~79 h;
  3) o filtro de URL de TODO link publicado (só http(s); sem credencial na consulta; sem Storage/
     Supabase): o arquivo é público e os links do admin são digitados à mão;
  4) a semana ISO em UTC com o ano ISO (2026-w53 vai de 28/12/2026 a 03/01/2027);
  5) o item de notícia campo a campo: trecho sem tags e cortado em 200, fonte PAGA sem trecho
     (decisão definitiva do usuário, 05/11), take só da lista, empresas e temas separados por ";".
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hub_export import weeks as W  # noqa: E402
from hub_export.exporters import calendar as cal  # noqa: E402
from hub_export.exporters import news_common as NC  # noqa: E402
from hub_export.exporters import news_reference as R  # noqa: E402
from hub_export.urls import safe_url, url_problem  # noqa: E402


def utc(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


# ── 1) os conjuntos do site ─────────────────────────────────────────────────────────────────────
def test_conjuntos_iguais_aos_do_site():
    assert R.LABEL_ALIAS == {
        "CVM": "CVM filing", "ASX": "ASX filing", "Bloomberg.com": "Bloomberg",
        "Reuters Brasil": "Reuters", "Kallanish Commodities": "Kallanish",
        "Kallanish Steel": "Kallanish", "Shanghai Metals Market": "SMM",
        "World Steel Association": "World Steel", "O Estado de S. Paulo": "Estadão",
        "Folha de São Paulo": "Folha de S.Paulo", "IBRAM - Mineração do Brasil": "IBRAM",
        "S&P Global": "S&P Platts", "S&P Global Commodity Insights": "S&P Platts"}
    assert R.SM_SOURCES == {
        "S&P Platts", "Platts", "Notícias de Mineração", "IBRAM", "ANM", "Brasil Mineral",
        "Mining.com", "Australian Mining", "SMM", "GMK Center", "Siderurgia Brasil", "SteelRadar",
        "Instituto Aço Brasil", "ASX filing", "Aclara Resources", "Fastmarkets Insights",
        "Northern Miner", "Financial Times", "Poços Já"}
    assert R.PP_SOURCES == {"Fastmarkets", "PaperAge", "Portal Celulose", "ABTCP",
                            "China Pulp & Paper", "Papnews", "Ibá"}
    assert R.PRIMARY_SOURCES == {"CVM", "ASX", "Aclara Resources"}
    assert len(R.PULSE_CO) == 16 and len({c for c, *_ in R.PULSE_CO}) == 16


@pytest.mark.parametrize("source, domain, esperado", [
    ("CVM", "rad.cvm.gov.br", "CVM filing"),
    ("Shanghai Metals Market", "metal.com", "SMM"),
    ("Google News", "www.mining.com", "Mining"),          # 1ª parte do domínio, sem o www.
    ("Google Notícias", "valor.globo.com", "Valor"),
    ("", "www.reuters.com", "Reuters"),
    (None, "", "—"),                                      # sem nada
    ("Google News", None, "—"),
    ("Google News", "abc.www.x.com", "Abc"),              # tira só o 1º "www." (replace do JS)
    ("Valor Econômico", "valor.globo.com", "Valor Econômico"),
    ("Mining.com", "mining.com", "Mining.com"),
])
def test_rotulo_da_fonte(source, domain, esperado):
    assert R.label(source, domain) == esperado


@pytest.mark.parametrize("rotulo, setor, mesa", [
    ("S&P Platts", None, "sm"), ("SMM", "pp", "sm"),          # a fonte manda no setor
    ("Fastmarkets", "steel", "pp"), ("Ibá", None, "pp"),
    ("Valor Econômico", "steel", "sm"), ("Valor Econômico", "mining", "sm"),
    ("Valor Econômico", "pp", "pp"), ("Valor Econômico", None, "nr"), ("Reuters", "macro", "nr"),
    ("CVM filing", "mining", "sm"),                           # CVM não carimba: vale o setor
    ("Fastmarkets Insights", "pp", "sm"),                     # o RSS aberto é de terras raras
])
def test_setor_exibido(rotulo, setor, mesa):
    assert R.desk(rotulo, setor) == mesa


def test_primary_usa_o_nome_cru():
    assert R.is_primary("CVM") and R.is_primary("ASX") and R.is_primary("Aclara Resources")
    assert not R.is_primary("CVM filing") and not R.is_primary(None)


# ── 2) janela do Market Pulse ───────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("agora, inicio", [
    ("2026-09-08T11:00:00Z", "2026-09-07T20:00:00Z"),   # terça 08:00 SP → segunda 17:00
    ("2026-09-08T19:59:00Z", "2026-09-07T20:00:00Z"),   # terça 16:59 → ainda segunda
    ("2026-09-08T20:00:00Z", "2026-09-07T20:00:00Z"),   # terça 17:00 → AINDA segunda (vira à meia-noite)
    ("2026-09-09T02:30:00Z", "2026-09-07T20:00:00Z"),   # terça 23:30
    ("2026-09-09T03:30:00Z", "2026-09-08T20:00:00Z"),   # quarta 00:30 → virou
    ("2026-09-07T21:00:00Z", "2026-09-04T20:00:00Z"),   # segunda 18:00 → sexta 17:00
    ("2026-09-07T11:00:00Z", "2026-09-04T20:00:00Z"),   # segunda 08:00 → sexta
    ("2026-09-05T13:00:00Z", "2026-09-04T20:00:00Z"),   # sábado 10:00
    ("2026-09-05T23:00:00Z", "2026-09-04T20:00:00Z"),   # sábado 20:00
    ("2026-09-06T15:00:00Z", "2026-09-04T20:00:00Z"),   # domingo 12:00
    ("2026-09-07T00:00:00Z", "2026-09-04T20:00:00Z"),   # domingo 21:00
    ("2026-09-08T03:00:00Z", "2026-09-07T20:00:00Z"),   # meia-noite SP de terça (h23)
])
def test_janela_do_pulse_casos_do_site(agora, inicio):
    assert R.pulse_window_start(utc(agora)) == utc(inicio)


def test_janela_do_pulse_na_segunda_chega_a_79_horas():
    agora = utc("2026-11-10T02:59:00Z")                   # segunda 23:59 de Brasília
    inicio = R.pulse_window_start(agora)
    assert inicio == utc("2026-11-06T20:00:00Z")          # sexta 17:00
    assert timedelta(hours=78) < agora - inicio < timedelta(hours=79)


def test_janela_do_pulse_sem_base_de_fusos_usa_utc_menos_3(monkeypatch):
    monkeypatch.setattr(R, "ZoneInfo", None)
    assert R.pulse_window_start(utc("2026-09-07T11:00:00Z")) == utc("2026-09-04T20:00:00Z")


# ── 3) filtro de URL ────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("url", [
    "https://www.metal.com/newscontent/103581234",
    "http://valor.globo.com/x.ghtml?utm_source=rss",
    "https://core.spglobal.com/#platts/insightsArticle?articleID=abc&insightsType=News",
    "https://news.google.com/rss/articles/CBMi?oc=5",
    "https://cdn-api.markitdigital.com/apiman-gateway/ASX/asx-research/1.0/file/2924-0293",
    "https://www.b3.com.br/pt_br/noticias?keywords=vale",
])
def test_url_boa_passa(url):
    assert url_problem(url) is None and safe_url("  " + url + " ") == url


@pytest.mark.parametrize("url, motivo", [
    ("javascript:alert(1)", "esquema"), ("ftp://x.com/a", "esquema"), ("//x.com/a", "esquema"),
    ("", "vazio"), (None, "vazio"), ("https://", "sem host"),
    ("https://user:pass@x.com/a", "credencial"),
    ("https://x.com/a?token=abc", "credencial"), ("https://x.com/a?access_token=abc", "credencial"),
    ("https://x.com/a.pdf?X-Amz-Signature=abc&X-Amz-Credential=k", "credencial"),
    ("https://conta.blob.core.windows.net/c/a.pdf?sv=2024&se=2026&sp=r&sig=abc", "credencial"),
    ("https://x.com/a?key=AIzaSECRETA", "credencial"), ("https://x.com/a?accessToken=1", "credencial"),
    ("https://app.x.com/#access_token=abc&token_type=bearer", "credencial"),
    ("https://projeto.supabase.co/rest/v1/news_articles", "interno"),
    ("https://cdn.exemplo.com/storage/v1/object/sign/a.pdf", "storage"),
    ("http://localhost:8000/a", "interno"), ("http://192.168.0.10/a", "interno"),
    ("http://127.0.0.1/a", "interno"), ("https://x.com/a b", "caractere inválido"),
    ("https://x.com/" + "a" * 3000, "longo"),
])
def test_url_ruim_vira_null(url, motivo):
    assert url_problem(url) == motivo and safe_url(url) is None


@pytest.mark.parametrize("url", [
    # os três achados no calendário real (05/11): rastreio de clique e confirmação de inscrição
    "https://mailerurl.mziq.com/ls/click?upn=u001.ABCDEF-2BXYZ",
    "https://mail.closir.com/c/eJxckL2q3DAQhZ_G7mJGM9JIKlykiLlFuOSGQ",
    "https://cloud.sfmc.itau.com.br/confirmacao_externa_prod?AttendanceType=1&CampaignID=9",
    "https://empresa.us21.list-manage.com/track/click?u=abc&id=def&e=0123456789",
    "https://u123456.ct.sendgrid.net/ls/click?upn=XYZ", "https://url9999.empresa.com/ls/click?upn=Q",
    "https://click.e.empresa.com/?qs=abc", "https://clicks.empresa.com/f/a/b",
    "https://email.mg.empresa.com/c/eJyNkM1u", "https://empresa.com/track/click?id=1",
    "https://d1abc04.na1.hubspotlinks.com/Ctc/I8+113/xyz", "https://empresa.com/e3t/Ctc/abc",
    "https://r20.rs6.net/tn.jsp?f=001abc",
    "https://empresa.createsend1.com/t/r-l-abc", "https://cl.exct.net/?qs=abc",
    "https://mandrillapp.com/track/click/123", "https://empresa.cmail20.com/t/y-l-abc",
])
def test_redirecionador_de_clique_de_email_e_descartado_inteiro(url):
    """O endereço todo é o rastreio: não há o que limpar."""
    assert url_problem(url) == "rastreio" and safe_url(url) is None


@pytest.mark.parametrize("url, limpo", [
    # o relatório da Ternium (05/11): link do portal copiado de um e-mail do Marketing Cloud
    ("https://www.itau.com.br/itaubba-pt/portal/viewer/equity/2815389c-99c0?sfmc_id=123"
     "&utm_source=sfmc&utm_medium=email&utm_campaign=x&utm_term=y&utm_id=9",
     "https://www.itau.com.br/itaubba-pt/portal/viewer/equity/2815389c-99c0"),
    ("https://www.empresa.com/artigo?id=7&_hsenc=p2ANqtz&_hsmi=12&utm_medium=email",
     "https://www.empresa.com/artigo?id=7"),                      # o que não é rastreio fica
    ("https://empresa.com/a?mkt_tok=MzQ5", "https://empresa.com/a"),
    ("https://empresa.com/a?mc_cid=1&mc_eid=abc", "https://empresa.com/a"),
    ("https://empresa.com/a?elqTrackId=1&elq=abc&x=1", "https://empresa.com/a?x=1"),
    ("https://empresa.com/a?_ke=ZW1haWw#topo", "https://empresa.com/a#topo"),
    ("https://empresa.com/a?SFMC_SUB=1&Utm_Source=x", "https://empresa.com/a"),   # sem caixa
    ("https://empresa.com/a?upn=abc&p=2&ss_email_id=9", "https://empresa.com/a?p=2"),
    ("https://empresa.com/#/artigo?sfmc_sub=1&utm_source=x&p=2", "https://empresa.com/#/artigo?p=2"),
    ("https://empresa.com/a?q=a%20b&sfmc_id=1&z=%C3%A9", "https://empresa.com/a?q=a%20b&z=%C3%A9"),
])
def test_link_normal_com_parametro_do_destinatario_fica_limpo(url, limpo):
    assert url_problem(url) is None and safe_url(url) == limpo
    assert safe_url(limpo) == limpo                               # limpar de novo não muda nada


@pytest.mark.parametrize("url", [
    # os parâmetros e hosts que existem nas notícias reais (05/11) continuam passando
    "https://valor.globo.com/empresas/noticia/2026/11/05/x.ghtml?utm_source=rss&utm_medium=feed"
    "&utm_campaign=empresas",
    "https://redir.folha.com.br/redir/online/mercado/rss091/*https://www1.folha.uol.com.br/x.shtml",
    "https://www.rad.cvm.gov.br/ENETWEB/frmExibirArquivoIPEExterno.aspx?ID=1234567&flnk=1",
    "https://www.ft.com/content/e89a420d-3a93-402f-ab0d-461476cf23ce?syn-25a6b1a6=1",
    "https://www.steelradar.com/en/haber/kocaer?comment=1",
    "https://www.chinapulppaper.com/component/content/article/sun-paper?catid=12",
    "https://news.metal.com/newscontent/103581234", "https://www.clickpetroleoegas.com.br/noticia",
    "https://www.itau.com.br/itaubba-pt/portal/viewer/equity/2b7acd7a-579b-4602-82a1-021",
    "https://us06web.zoom.us/webinar/register/WN_wqvadIMnSAW1Z7oeZJf",
    "https://www.mail.com.br/noticia",                    # "mail." sem o redirecionador /c/
    "HTTPS://Exemplo.com/a?",                             # sem nada a tirar: byte a byte igual
])
def test_link_normal_de_noticia_continua_passando(url):
    assert url_problem(url) is None and safe_url(url) == url  # utm_* sem destinatário fica


# ── 4) semanas ISO ──────────────────────────────────────────────────────────────────────────────
def test_semana_iso_em_utc_com_ano_iso():
    assert W.label_of(utc("2027-01-01T10:00:00Z")) == "2026-w53"
    assert W.label_of(utc("2027-01-04T00:00:00Z")) == "2027-w01"
    assert W.label_of(utc("2026-11-03T18:05:12Z")) == "2026-w45"
    assert W.label_of(utc("2026-11-09T02:00:00Z")) == "2026-w46"   # segunda UTC (domingo em SP)
    assert W.bounds("2026-w53") == ("2026-12-28", "2027-01-03")
    assert W.file_name("2026-w53") == "news-week-2026-w53.json"
    assert W.shift("2026-w53", 1) == "2027-w01" and W.shift("2027-w01", -1) == "2026-w53"
    assert W.span("2026-w52", "2027-w02") == ["2026-w52", "2026-w53", "2027-w01", "2027-w02"]
    assert W.span("2026-w45", "2026-w44") == []
    assert W.parse("2025-w53") is None and W.parse("2026-W45") is None
    assert W.label_from_file("news-week-2026-w45.json") == "2026-w45"
    assert W.label_from_file("news-week-2026-W45.json") is None
    with pytest.raises(ValueError):
        W.monday("2026-45")


# ── 5) o item de notícia ────────────────────────────────────────────────────────────────────────
def _row(**kw):
    base = {"url": "https://www.metal.com/n/1", "title": " Iron ore falls ", "source_name": "SMM",
            "domain": "www.metal.com", "snippet": "<p>Iron <b>ore</b></p>\n\n fell", "found_at":
            "2026-11-03T17:58:03.123456+00:00", "published_at": None, "take_llm": "-",
            "take_reason": " motivo ", "sector": "mining", "take_covered_companies": " VALE ;CSN; ",
            "take_topics": "iron_ore;  pellets"}
    base.update(kw)
    return base


def test_item_campo_a_campo():
    item, prob = NC.item_from_row(_row())
    assert prob is None and list(item) == list(NC.ITEM_KEYS)
    assert item == {"id": NC.news_id("https://www.metal.com/n/1"), "url": "https://www.metal.com/n/1",
                    "title": "Iron ore falls", "source": "SMM", "domain": "metal.com",
                    "primary": False, "publishedAt": None, "foundAt": "2026-11-03T17:58:03Z",
                    "take": "-", "sector": "mining", "desk": "sm", "companies": ["VALE", "CSN"],
                    "topics": ["iron_ore", "pellets"], "snippet": "Iron ore fell", "reason": "motivo"}
    assert len(item["id"]) == 12 and "reason" not in NC.week_item(item)


def test_trecho_limpo_e_cortado_em_200():
    longo = "<div>" + "palavra " * 100 + "</div>"
    s = NC.clean_snippet(longo)
    assert len(s) <= 200 and "<" not in s and "  " not in s and not s.endswith(" ")
    assert NC.clean_snippet("   ") is None and NC.clean_snippet("<br/>") is None
    assert NC.clean_snippet(None) is None


def test_fontes_pagas_saem_so_com_o_titulo(monkeypatch):
    for sn, dom in (("S&P Platts", "core.spglobal.com"), ("Fastmarkets", "dashboard.fastmarkets.com"),
                    ("Outro nome", "core.spglobal.com")):
        item, _ = NC.item_from_row(_row(source_name=sn, domain=dom, snippet="trecho pago"))
        assert item["snippet"] is None and item["title"] == "Iron ore falls", sn
    aberto, _ = NC.item_from_row(_row(source_name="Fastmarkets Insights", domain="fastmarkets.com",
                                      snippet="texto aberto"))
    assert aberto["snippet"] == "texto aberto"
    assert NC.HIDE_PAID_SNIPPETS is True                      # ligada por padrão (definitivo)
    monkeypatch.setattr(NC, "HIDE_PAID_SNIPPETS", False)      # só se o usuário mudar de ideia
    item, _ = NC.item_from_row(_row(source_name="S&P Platts", snippet="trecho pago"))
    assert item["snippet"] == "trecho pago"


def test_take_so_da_lista_e_pendente_e_null():
    for raw, esperado in (("+", "+"), (" - ", "-"), ("=", "="), ("no take", "no take"),
                          ("review", "review"), (None, None), ("?", None), ("positivo", None)):
        assert NC.item_from_row(_row(take_llm=raw))[0]["take"] == esperado, raw


def test_linhas_que_nao_saem():
    assert NC.item_from_row(_row(url="javascript:x"))[1] == "url esquema"
    assert NC.item_from_row(_row(url="https://x.com/?token=1"))[1] == "url credencial"
    assert NC.item_from_row(_row(title="  "))[1] == "sem título"
    assert NC.item_from_row(_row(found_at=None))[1] == "sem found_at"


def test_data_de_referencia_e_data_esquisita():
    item = {"publishedAt": "2026-11-03T12:00:00Z", "foundAt": "2026-11-03T12:30:00Z"}
    assert NC.bucket_iso(item) == "2026-11-03T12:00:00Z" and not NC.is_weird(item)
    futuro = {"publishedAt": "2026-11-03T13:31:00Z", "foundAt": "2026-11-03T12:30:00Z"}
    assert NC.is_weird(futuro) and NC.bucket_iso(futuro) == "2026-11-03T12:30:00Z"
    antiga = {"publishedAt": "2026-10-26T12:29:00Z", "foundAt": "2026-11-03T12:30:00Z"}
    assert NC.is_weird(antiga) and NC.plain_iso(antiga) == "2026-10-26T12:29:00Z"
    tardia = {"publishedAt": "2026-10-26T12:31:00Z", "foundAt": "2026-11-03T12:30:00Z"}
    assert not NC.is_weird(tardia) and NC.bucket_iso(tardia) == "2026-10-26T12:31:00Z"
    sem = {"publishedAt": None, "foundAt": "2026-11-03T12:30:00Z"}
    assert NC.bucket_iso(sem) == "2026-11-03T12:30:00Z"


def test_campos_que_mudam_recalculam_o_setor_exibido():
    item, _ = NC.item_from_row(_row(source_name="Valor Econômico", sector="steel"))
    assert item["desk"] == "sm"
    assert NC.apply_mutable(item, {"take_llm": "+", "sector": "pp", "take_covered_companies": "SUZANO",
                                   "take_topics": None, "take_reason": None}) is True
    assert item["desk"] == "pp" and item["take"] == "+" and item["companies"] == ["SUZANO"]
    assert NC.apply_mutable(item, {"take_llm": "+", "sector": "pp", "take_covered_companies": "SUZANO",
                                   "take_topics": None, "take_reason": None}) is False


# ── calendário e relatórios (normalização) ──────────────────────────────────────────────────────
def test_calendario_intervalo_horas_e_regra():
    assert cal.date_range(utc("2026-11-05T12:00:00Z")) == ("2025-01-01", "2028-05-31")
    assert cal.date_range(utc("2026-01-01T02:00:00Z")) == ("2024-01-01", "2027-06-30")  # 31/12 em SP
    assert cal.hhmm("08:00:00") == "08:00" and cal.hhmm("8:05") == "08:05"
    assert cal.hhmm("25:00") is None and cal.hhmm(None) is None
    regra = {"freq": "weekly", "interval": 2, "byweekday": [1, 3], "until": "2027-01-01",
             "count": None, "whole_week": True, "bysetpos": [-1], "bymonthday": [5],
             "_debug": {"token": 1}, "tz": "x", "byweekday_bad": {"a": 1}}
    assert cal.recurrence(regra) == {k: regra[k] for k in cal.RECURRENCE_KEYS}
    assert cal.recurrence({"freq": "daily", "byweekday": [{"x": 1}]}) == {"freq": "daily"}
    assert cal.recurrence({"tz": 1}) is None and cal.recurrence("weekly") is None


def test_relatorios_id_setor_link_e_data():
    from hub_export.exporters import reports

    class Src:
        generated_at = "2026-11-03T18:05:12Z"

        def recent_reports(self):
            return [{"name": "A", "sector": "pp", "link": "https://x.com/a.pdf", "date": "2026-11-04",
                     "companies": ["SUZB3", " ", 3]},
                    {"name": "B", "sector": "ZZ", "link": "notes/b.docx", "date": "2026-02-30",
                     "companies": None},
                    {"name": "B", "sector": "SM", "link": "notes/b.docx", "date": None, "companies": []},
                    {"name": None, "sector": "SM", "link": None, "date": None, "companies": []}]
    items = reports.build(Src())["items"]
    assert [(i["name"], i["sector"], i["link"], i["date"]) for i in items] == [
        ("A", "PP", "https://x.com/a.pdf", "2026-11-04"), ("B", "SM", None, None), ("B", "SM", None, None)]
    assert items[0]["companies"] == ["SUZB3"] and items[1]["companies"] == []
    assert len({i["id"] for i in items}) == 3                  # chave repetida ganha id próprio
    import hashlib
    assert items[0]["id"] == hashlib.sha1(b"https://x.com/a.pdf").hexdigest()[:12]
