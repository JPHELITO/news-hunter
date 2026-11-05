"""
O "banco falso" do exportador do hub (hub_export/) — e os testes que travam que ele continua
cobrindo os casos que o brief do WP1.2 exige.

Os outros `test_hub_export_*.py` importam daqui (sem conftest.py, convenção do repositório).
O mundo falso é PEQUENO e REALISTA, com os formatos que o PostgREST devolve de verdade (carimbos
com microssegundos e `+00:00`, `->>` devolvendo texto) e os formatos que o `update_quotes` monta
na memória do ciclo:

  • papéis de 7 bolsas (B3, NYSE, Nasdaq, Santiago, ASX, Toronto, Londres) + um índice;
  • CMPC.SN SEM preço no ciclo (o Yahoo não devolveu) — tem de sair o último valor do estado;
  • AUGO com `session` inválida (fecha antes de abrir) — tem de virar `null`;
  • VMM.AX sem velas no dia (`pts: []`); HBM.TO sem variação (`change_pct` nulo, nunca 0);
  • commodities com e sem `spark`, e um `spark` com chave de depuração que NÃO pode vazar;
  • /admin do heatmap com papel oculto (GGB e B), cobertura em ordem própria, símbolo
    desconhecido (ignorado) e um oculto marcado como cobertura (fica oculto);
  • flags com `hub_market` mandando no `market`, um `hub_` órfão e as internas do site
    (clipinator, research_team, tutorial) que a lista branca barra;
  • o diário com a cauda mensal antiga e pontos dos dois lados do corte de 400 dias;
  • grupos de commodities visíveis fora de ordem e um invisível.
Onda 1b (o "agora" é terça 03/11/2026 18:05 UTC: semana corrente 2026-w45, anterior 2026-w44;
janela do news-latest desde 01/11 18:00 UTC):
  • notícias de fonte com apelido (SMM), Google News (rótulo = domínio), CVM (PRIMARY), as duas
    PAGAS (Platts, Fastmarkets: trecho não pode sair), take pendente, descoberta tardia (semana
    44 dentro do news-latest), datas esquisitas (no futuro e muito antiga), semana antiga (43),
    domingo fora da janela, e as que nunca saem (fora do relatório, `javascript:`, link com
    `token=`, sem título) — com as colunas internas (take do robô, modelo da IA…) que não vazam;
  • o resumo da hora com `ok`, `quiet`, `list` (com link hostil) e a madrugada, com `model`,
    `tokens_*` e `items[].score` que não podem sair;
  • relatórios da RPC com link do SharePoint com token, sem link, sem data e sem nome;
  • calendário com categoria inativa, janela de dado inativa, evento da B3, recorrente (com chave
    de depuração na regra), oculto, antigo demais e links/descrição com endereço do Storage.

Nada aqui fala com a rede: o `FakeHTTP` faz o papel do `requests.Session` para o Supabase (um
mini-PostgREST: select com alias e `->>`; filtros `eq./neq./gt./gte./lt./lte./is./in./like.`,
`not.` e as árvores `or=(…)`/`and=(…)`; `order` com várias colunas, `limit`, `offset`; HEAD e
`Prefer: count=exact` com `Content-Range`; RPC com argumentos; colunas e tipos conferidos onde o
banco real recusaria), para o Feeder (roteiro de respostas por etapa, com falhas sob encomenda) e
para a cópia publicada no Mindassets.
"""
from __future__ import annotations

import copy
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hub_export import state as hub_state  # noqa: E402

# ── endereços e segredos FALSOS (os testes conferem que nunca aparecem em log) ─────────────────
SUPA_URL = "https://projeto-secreto.supabase.co"
SUPA_KEY = "service-key-SECRETA-0123456789"
FEEDER_URL = "https://feeder.example.test"
PROJECT_ID = "proj-123"
FEEDER_KEY = "rdf_pub.CHAVE-SECRETA-NAO-PODE-VAZAR"
PUT_HOST = "https://oci.example.test"
PUBLIC_BASE = "https://public.example.test/base"

NOW = datetime(2026, 11, 3, 18, 5, 12, 345678, tzinfo=timezone.utc)
GENERATED = "2026-11-03T18:05:12Z"

# janelas de pregão de 03/11/2026 (epoch, s)
B3_SESS = [1793710800, 1793736000]         # 13:00–20:00 UTC
NY_SESS = [1793716200, 1793739600]         # 14:30–21:00 UTC
LSE_SESS = [1793692800, 1793723400]        # 08:00–16:30 UTC
ASX_SESS = [1793746800, 1793768400]        # 23:00–05:00 UTC (o pregão de 04/11 em Sydney)
BCS_SESS = [1793709000, 1793732400]        # 12:30–19:00 UTC


def env(**extra) -> dict:
    e = {"SUPABASE_URL": SUPA_URL, "SUPABASE_SERVICE_KEY": SUPA_KEY,
         "DASHBOARD_FEEDER_URL": FEEDER_URL, "DASHBOARD_FEEDER_PROJECT_ID": PROJECT_ID,
         "DASHBOARD_FEEDER_API_KEY": FEEDER_KEY, "HUB_EXPORT_PUBLIC_BASE": PUBLIC_BASE}
    e.update(extra)
    return e


# ── linhas de `quotes` como o PostgREST devolve ────────────────────────────────────────────────
def _q(ticker, name, sector, exchange, price, chg, chg_abs, upd, intraday, daily, daily_upd):
    return {"ticker": ticker, "name": name, "sector": sector, "exchange": exchange,
            "price": price, "change_pct": chg, "change_abs": chg_abs, "updated_at": upd,
            "intraday": intraday, "daily": daily, "daily_updated_at": daily_upd}


QUOTES = [
    _q("IBOV", "Ibovespa", "index", "B3", 128450.12, 0.52, 664.3,
       "2026-11-03T18:04:01.123456+00:00",
       {"prev": 127785.82, "pts": [[1793710800, 127800.5], [1793711100, 128100.25]],
        "traded_at": 1793729041, "session": B3_SESS},
       [[1577970000, 115000.0], [1793365200, 127100.0], [1793624400, 127785.82]],
       "2026-11-03T00:29:08.879604+00:00"),
    _q("VALE3.SA", "Vale", "iron_ore", "B3", 61.23, 1.234, 0.75,
       "2026-11-03T18:04:01.123457+00:00",
       {"prev": 60.48, "pts": [[1793710800, 60.5], [1793711100, 60.7], [1793711400, 61.23]],
        "traded_at": 1793729041, "session": B3_SESS},
       # cauda mensal de 2020 e um ponto 5 min ANTES do corte de 400 dias: os dois ficam fora do
       # quotes-daily.json; o de 01/10/2025 fica dentro
       [[1577970000, 40.0], [1759168800, 55.0], [1759323600, 56.0],
        [1793365200, 59.9], [1793451600, 60.1], [1793624400, 60.48]],
       "2026-11-02T23:59:02.685735+00:00"),
    _q("SCCO", "Southern Copper", "copper", "NYSE", 112.4, -0.85, -0.96,
       "2026-11-03T18:04:01.5+00:00",
       {"prev": 113.36, "pts": [[1793716200, 113.1], [1793716500, 112.4]],
        "traded_at": 1793729100, "session": NY_SESS},
       [[1793367000, 110.0], [1793629800, 113.36]], "2026-11-02T23:55:46.537336+00:00"),
    # sessão inválida (fecha antes de abrir) → session null
    _q("AUGO", "Aura Minerals (Nasdaq)", "gold", "NASDAQ", 24.1, 2.1, 0.5,
       "2026-11-03T18:04:02+00:00",
       {"prev": 23.6, "pts": [[1793716200, 23.9]], "traded_at": 1793729000,
        "session": [NY_SESS[1], NY_SESS[0]]},
       [], "2026-11-02T23:50:00+00:00"),
    # sem preço no ciclo de agora: este é o valor "do banco" (= o do estado anterior)
    _q("CMPC.SN", "CMPC", "pp", "BCS", 1350.0, -0.3, -4.0,
       "2026-11-03T17:49:00.000001+00:00",
       {"prev": 1354.0, "pts": [[1793709000, 1352.0]], "traded_at": 1793725000,
        "session": BCS_SESS},
       [[1793365200, 1340.0], [1793624400, 1354.0]], "2026-11-03T00:10:00+00:00"),
    # Sydney fechada, sem vela do dia (pts []), último negócio de ontem
    _q("VMM.AX", "Viridis Mining", "rare_earths", "ASX", 0.62, 3.33, 0.02,
       "2026-11-03T18:04:03+00:00",
       {"prev": 0.6, "pts": [], "traded_at": 1793682000, "session": ASX_SESS},
       [[1793365200, 0.58]], "2026-11-03T00:05:00+00:00"),
    # variação ausente (Yahoo sem fechamento anterior) → null, nunca 0
    _q("HBM.TO", "Hudbay Minerals", "copper", "TSX", 25.4, None, None,
       "2026-11-03T18:04:03.25+00:00",
       {"prev": None, "pts": [[1793716200, 25.4]], "traded_at": 1793729050, "session": NY_SESS},
       [[1793367000, 24.9]], "2026-11-02T23:40:00+00:00"),
    _q("AAL.L", "Anglo American", "iron_ore", "LSE", 2380.5, 0.4, 9.5,
       "2026-11-03T18:04:03.75+00:00",
       {"prev": 2371.0, "pts": [[1793692800, 2372.0], [1793710800, 2380.5]],
        "traded_at": 1793723400, "session": LSE_SESS},
       [[1793365200, 2300.0]], "2026-11-02T23:30:00+00:00"),
]
MISSING_IN_CYCLE = "CMPC.SN"

MACRO = [
    {"code": "USD_BRL", "name": "USD/BRL", "unit": "R$", "value": 5.4948, "change_pct": 1.071,
     "updated_at": "2026-11-03T18:04:11.95015+00:00"},
    {"code": "US10Y", "name": "US 10Y Yield", "unit": "%", "value": 4.123, "change_pct": None,
     "updated_at": "2026-11-03T18:04:11.950183+00:00"},
    # variação ZERO legítima (fica 0.0) — e o BCB não respondeu no ciclo de agora
    {"code": "SELIC", "name": "SELIC Target", "unit": "%", "value": 15.0, "change_pct": 0.0,
     "updated_at": "2026-11-03T09:00:00+00:00"},
]
MACRO_MISSING_IN_CYCLE = "SELIC"


def _spark(asof, v=2, ph=None, extra=None):
    sp = {"asof": asof, "v": v,
          "d": {"c": -0.4, "s": [0, 250, 1000, 600], "b": 2},
          "w": {"c": 1.2, "s": [100, 0, 400, 1000, 600], "b": 0},
          "m": {"c": None, "s": [500, 500, 1000], "b": 0},
          "ytd": {"c": 12.5, "s": [0, 1000], "b": 0},
          "y": {"c": -3.0, "s": [1000, 0, 300], "b": 0}}
    if ph is not None:
        sp["ph"] = ph
    sp.update(extra or {})
    return sp


COMMODITIES = [
    {"code": "IRON_ORE", "name": "IO Fines 61%", "unit": "USD/dmt", "price": 99.05,
     "change_pct": -0.4, "assessed_at": "2026-11-02", "updated_at": "2026-11-03T17:32:39.736573+00:00",
     "spark": _spark("2026-11-02")},
    {"code": "COPPER", "name": "Copper", "unit": "USD/lb", "price": 5.1234, "change_pct": 0.8,
     "assessed_at": "2026-11-03", "updated_at": "2026-11-03T18:04:05.1+00:00",
     "spark": _spark("2026-11-03", ph=0)},
    {"code": "HRC_CHINA", "name": "HRC China", "unit": "USD/mt", "price": 455.0,
     "change_pct": None, "assessed_at": "2026-10-31", "updated_at": "2026-11-03T17:32:39.7+00:00",
     "spark": None},
    # chave de depuração dentro do spark: a lista branca tem de barrar
    {"code": "PULP_BHKP_CHINA", "name": "BHKP China Net", "unit": "USD/t", "price": 560.0,
     "change_pct": 0.0, "assessed_at": "2026-10-30", "updated_at": "2026-11-03T11:00:00+00:00",
     "spark": _spark("2026-10-30", extra={"_debug": {"token": "x"}})},
]

FLAGS = [
    {"key": "heatmap", "enabled": True},
    {"key": "commodities", "enabled": True},
    {"key": "market", "enabled": True},
    {"key": "hub_market", "enabled": False},     # o hub manda: market → false
    {"key": "news_feed", "enabled": False},
    {"key": "hub_orphan", "enabled": True},      # sem "orphan" no /admin: não aparece
    {"key": "quarterly", "enabled": False},
    {"key": "hub_quarterly", "enabled": True},   # o hub manda: quarterly → true
    {"key": "home_digest", "enabled": True},     # o resumo da hora é fail-closed: só com true
    # internas do site: fora da lista branca, nunca saem (nem com hub_)
    {"key": "clipinator", "enabled": True},
    {"key": "research_team", "enabled": True},
    {"key": "hub_research_team", "enabled": True},
    {"key": "tutorial", "enabled": False},
]

HEATMAP_ADMIN = [
    {"symbol": "GGB", "is_hidden": True, "in_coverage": False, "sort_order": 0},
    {"symbol": "ZZZZ.SA", "is_hidden": False, "in_coverage": True, "sort_order": 0},  # desconhecido
    {"symbol": "VALE", "is_hidden": False, "in_coverage": True, "sort_order": 1},
    {"symbol": "SUZB3.SA", "is_hidden": False, "in_coverage": True, "sort_order": 2},
    {"symbol": "B", "is_hidden": True, "in_coverage": True, "sort_order": 3},          # oculto vence
]

COMMODITY_GROUPS = [
    {"id": "77474b40-f8b1-4181-bc24-10a6413d5894", "title": "Iron Ore + Pellet",
     "codes": ["IRON_ORE", "IOPRM00"], "sort_order": 10, "is_visible": True},
    {"id": "2054d74f-5aec-4abf-b749-9370605c889c", "title": "Base & Precious Metals",
     "codes": ["GOLD", "COPPER"], "sort_order": 5, "is_visible": True},
    {"id": "15fea25d-e224-4a60-b5d9-4a44b5deb005", "title": "Escondido",
     "codes": ["PULP_BHKP_CHINA"], "sort_order": 1, "is_visible": False},
]


# ── Onda 1b: news_articles como o PostgREST devolve (com as colunas internas que não saem) ─────
ARCHIVE_FIRST = "2026-w43"     # nos testes, o arquivo começa na semana 43 (no código: 2026-w23)
WEEK_CUR, WEEK_PREV, WEEK_OLD = "2026-w45", "2026-w44", "2026-w43"
WINDOW_START = "2026-11-01T18:00:00Z"


def _n(url, title, source, domain, published, found, *, take=None, take_at=None, reason=None,
       sector=None, companies=None, topics=None, snippet="", include=True):
    return {"url": url, "domain": domain, "source_name": source, "title": title, "snippet": snippet,
            "published_at": published, "found_at": found, "matched_keywords": ["minério"],
            "sector": sector, "news_type": "price", "sentiment": 0.3, "tickers": ["VALE3"],
            "take": "+", "take_reason": reason, "take_topics": topics,
            "take_covered_companies": companies, "take_sector": "steel", "take_region": "br",
            "take_confidence": 0.8, "take_matched_rules": "regra-interna",
            "include_in_report": include,
            "exclusion_reason": None if include else "institutional_admin",
            "take_llm": take, "take_llm_model": "gemini-x" if take else None,
            "take_llm_at": take_at, "take_llm_attempts": 1}


U_SMM = "https://www.metal.com/newscontent/103581234"
U_GNEWS = "https://news.google.com/rss/articles/CBMiCOPPER?oc=5"
U_CVM = "https://www.rad.cvm.gov.br/ENETWEB/frmExibirArquivoIPEExterno.aspx?ID=1234567"
U_PLATTS = "https://core.spglobal.com/#platts/insightsArticle?articleID=abc123&insightsType=News"
U_FM = "https://dashboard.fastmarkets.com/a/98765"
U_PREV = "https://valor.globo.com/empresas/noticia/2026/10/30/suzano-reajuste.ghtml"
U_LATE = "https://www.reuters.com/markets/commodities/steel-tariffs-2026-10-27/"
U_FUTURE = "https://www.mining.com/gold-results-future-date/"
U_OLDPUB = "https://gmk.center/en/news/old-article/"
U_W43 = "https://www.steelorbis.com/steel-news/w43-rebar.htm"
U_SUNDAY = "https://www.example.org/sunday-note"

NEWS = [
    _n(U_SMM, "Iron ore futures fall in Dalian", "Shanghai Metals Market", "metal.com",
       None, "2026-11-03T17:58:03.123456+00:00", take="-", take_at="2026-11-03T18:01:00.5+00:00",
       reason="Coberta citada com queda de preço", sector="mining", companies="VALE; CSN",
       topics="iron_ore; pellets",
       snippet="<p>Iron ore <b>futures</b> fell</p>\n\n  in Dalian on   Tuesday"),
    _n(U_GNEWS, "Copper hits a record on supply worries", "Google News", "www.mining.com",
       "2026-11-03T12:00:00+00:00", "2026-11-03T12:30:00.25+00:00", take="+",
       take_at="2026-11-03T12:31:00+00:00", sector="mining", topics="copper",
       snippet="Copper rose " + "x" * 300),
    _n(U_CVM, "Vale: Fato Relevante sobre a mina de Brucutu", "CVM", "rad.cvm.gov.br",
       "2026-11-02T22:00:00+00:00", "2026-11-02T22:05:00+00:00", take="=",
       take_at="2026-11-02T22:10:00+00:00", sector="mining", companies="VALE",
       snippet="Comunicado " * 40),
    _n(U_PLATTS, "Platts: China HRC holds steady", "S&P Platts", "core.spglobal.com",
       "2026-11-03T09:00:00+00:00", "2026-11-03T09:10:00+00:00", take="no take",
       take_at="2026-11-03T09:20:00+00:00", sector="steel",
       snippet="Conteúdo pago do terminal da Platts que não pode ir ao arquivo público."),
    _n(U_FM, "BHKP prices edge up in China", "Fastmarkets", "dashboard.fastmarkets.com",
       None, "2026-11-02T14:00:00+00:00", sector="pp", companies="SUZANO",
       snippet="Conteúdo pago da Fastmarkets."),
    _n(U_PREV, "Suzano anuncia reajuste da celulose", "Valor Econômico", "valor.globo.com",
       "2026-10-30T10:00:00+00:00", "2026-10-30T10:20:00+00:00", take="+",
       take_at="2026-10-30T10:25:00+00:00", sector="pp", companies="SUZANO", topics="pulp"),
    # descoberta tardia (7 dias depois: ainda não é data esquisita) → semana 44 E no news-latest
    _n(U_LATE, "Steel tariffs widen in the US", None, "www.reuters.com",
       "2026-10-27T08:00:00+00:00", "2026-11-03T08:00:00+00:00", take="-",
       take_at="2026-11-03T08:05:00+00:00", sector="steel", topics="tariffs", reason="Tarifa"),
    # datas esquisitas: no futuro (> found + 1 h) e antiga demais (< found − 8 d) → valem pelo found
    _n(U_FUTURE, "Gold miner results (date in the future)", "Mining.com", "mining.com",
       "2026-11-05T10:00:00+00:00", "2026-11-03T15:00:00+00:00", take="+",
       take_at="2026-11-03T15:05:00+00:00", sector="mining", companies="AURA", topics="gold"),
    _n(U_OLDPUB, "Old article found today", "GMK Center", "gmk.center",
       "2026-09-01T00:00:00+00:00", "2026-11-02T09:00:00+00:00", take="=",
       take_at="2026-11-02T09:05:00+00:00", sector="steel"),
    # semana antiga (43)
    _n(U_W43, "Rebar prices fall in Turkey", "SteelOrbis", "steelorbis.com",
       "2026-10-21T10:00:00+00:00", "2026-10-21T10:30:00+00:00", take="-",
       take_at="2026-10-21T10:40:00+00:00", sector="steel", topics="rebar"),
    # domingo 01/11 17:00: semana 44, fora da janela do news-latest (rótulo sem domínio = "—")
    _n(U_SUNDAY, "Sunday note", "Google Notícias", "", "2026-11-01T17:00:00+00:00",
       "2026-11-01T17:05:00+00:00", take="no take", take_at="2026-11-01T17:10:00+00:00"),
    # nunca saem: fora do relatório, link hostil, link com credencial, sem título
    _n("https://www.bloomberg.com/crypto-rally", "Crypto rally", "Bloomberg.com", "bloomberg.com",
       None, "2026-11-03T17:00:00+00:00", include=False, take="+",
       take_at="2026-11-03T17:02:00+00:00"),
    _n("javascript:alert(1)", "Hostile link", "Valor Econômico", "valor.globo.com", None,
       "2026-11-03T16:00:00+00:00", take="+", take_at="2026-11-03T16:02:00+00:00"),
    _n("https://cdn.example.com/a.pdf?token=SEGREDO-DO-LINK", "Signed link", "IBRAM", "ibram.org.br",
       None, "2026-11-03T16:30:00+00:00", take="=", take_at="2026-11-03T16:32:00+00:00"),
    _n("https://www.mining.com/no-title/", "   ", "Mining.com", "mining.com", None,
       "2026-11-03T16:40:00+00:00"),
]
NEWS_COLUMNS = frozenset(NEWS[0])
LATEST_URLS = [U_SMM, U_FUTURE, U_GNEWS, U_PLATTS, U_LATE, U_CVM, U_FM, U_OLDPUB]   # foundAt ↓
WEEK_URLS = {WEEK_CUR: [U_SMM, U_FUTURE, U_GNEWS, U_PLATTS, U_CVM, U_FM, U_OLDPUB],
             WEEK_PREV: [U_SUNDAY, U_PREV, U_LATE], WEEK_OLD: [U_W43]}

DIGEST = [
    {"slot_start": "2026-11-03T16:00:00+00:00", "slot_end": "2026-11-03T17:00:00+00:00",
     "kind": "hour", "status": "ok",
     "summary": "Iron ore fell in Dalian while copper hit a record; Vale (VALE3) cited.",
     "items": [{"url": U_SMM, "title": "Iron ore futures fall in Dalian", "source": "SMM, Reuters",
                "take": "-", "tickers": ["VALE3", "CSNA3"], "at": "2026-11-03T16:21:09.5+00:00",
                "score": 7}],
     "model": "gemini-3.1-flash-lite · clipping", "tokens_in": 320, "tokens_out": 48,
     "created_at": "2026-11-03T17:10:31.123+00:00"},
    {"slot_start": "2026-11-03T15:00:00+00:00", "slot_end": "2026-11-03T16:00:00+00:00",
     "kind": "hour", "status": "quiet", "summary": None, "items": [], "model": None,
     "tokens_in": None, "tokens_out": None, "created_at": "2026-11-03T16:10:05+00:00"},
    {"slot_start": "2026-11-03T14:00:00+00:00", "slot_end": "2026-11-03T15:00:00+00:00",
     "kind": "hour", "status": "list", "summary": None,
     "items": [{"url": "javascript:alert(1)", "title": "Hostile", "source": "X", "take": "+",
                "tickers": [], "at": "2026-11-03T14:30:00+00:00", "score": 3},
               {"url": U_PREV, "title": "Valor note", "source": "Valor Econômico", "take": None,
                "tickers": [], "at": "2026-11-03T14:40:00+00:00", "score": 2}],
     "model": None, "tokens_in": None, "tokens_out": None,
     "created_at": "2026-11-03T15:40:00+00:00"},
    {"slot_start": "2026-11-02T23:00:00+00:00", "slot_end": "2026-11-03T10:00:00+00:00",
     "kind": "overnight", "status": "ok", "summary": "Overnight: CVM filing from Vale (VALE3).",
     "items": [{"url": U_CVM, "title": "Vale: Fato Relevante", "source": "CVM", "take": "=",
                "tickers": ["VALE3"], "at": "2026-11-02T22:05:00+00:00", "score": 6}],
     "model": "gemini-3.1-flash-lite · clipping", "tokens_in": 300, "tokens_out": 40,
     "created_at": "2026-11-03T10:10:00+00:00"},
]

REPORTS = [
    {"name": "Vale 3Q26: strong volumes", "sector": "SM",
     "link": "https://www.itaubba.com.br/research/vale-3q26.pdf", "date": "2026-11-04",
     "companies": ["VALE3"]},
    {"name": "Suzano: pulp price preview", "sector": "PP",
     "link": "https://tenant.sharepoint.com/:b:/s/research/Eabc?token=SEGREDO-DO-SHAREPOINT",
     "date": "2026-11-03", "companies": ["SUZB3", "KLBN11"]},
    {"name": "Macro: China steel demand", "sector": "NR", "link": None, "date": None,
     "companies": []},
    {"name": "  ", "sector": "SM", "link": "https://www.itaubba.com.br/x.pdf", "date": "2026-11-01",
     "companies": []},
]

CAL_CATEGORIES = [
    {"id": "c-earn", "name": "Earnings / Conf. Call", "color": "#1565C0", "kind": "earnings",
     "sort_order": 10, "is_active": True, "updated_at": "2026-08-01T12:00:00+00:00",
     "updated_by": "11111111-uuid-admin"},
    {"id": "c-data", "name": "Data Release", "color": "#00838F", "kind": "data_release",
     "sort_order": 20, "is_active": True, "updated_at": "2026-08-01T12:00:00+00:00",
     "updated_by": "11111111-uuid-admin"},
    {"id": "c-old", "name": "Retired", "color": "#999999", "kind": None, "sort_order": 5,
     "is_active": False, "updated_at": "2026-08-01T12:00:00+00:00", "updated_by": None},
]
CAL_WINDOWS = [
    {"id": "w-1", "source_key": "secex_steel", "label": "SECEX — Steel foreign trade",
     "from_day": 1, "to_day": 10, "lag_months": 1, "note": "MDIC/Comex Stat (~4º dia útil).",
     "is_active": True, "updated_at": "2026-08-01T12:00:00+00:00", "updated_by": "11111111"},
    {"id": "w-2", "source_key": "gacc", "label": "GACC — China woodchips", "from_day": 18,
     "to_day": 31, "lag_months": 1, "note": None, "is_active": False,
     "updated_at": "2026-08-01T12:00:00+00:00", "updated_by": "11111111"},
]


def _ev(eid, title, start, *, cat="c-earn", end=None, all_day=True, start_time=None,
        end_time=None, company=None, location=None, description=None, links=None,
        recurrence=None, visible=True, source=None, updated_at="2026-10-01T23:20:00+00:00"):
    return {"id": eid, "title": title, "category_id": cat, "start_date": start, "end_date": end,
            "all_day": all_day, "start_time": start_time, "end_time": end_time, "company": company,
            "location": location, "description": description, "links": links or [],
            "recurrence": recurrence, "is_visible": visible, "source": source,
            "external_id": f"b3:{company}:2026:ITR3" if source == "b3" else None, "ics_seq": 2,
            "locked": False, "updated_at": updated_at, "updated_by": "11111111-uuid-admin"}


CAL_EVENTS = [
    _ev("e-1", "VALE3.SA | 3Q26 Earnings Release (B3)", "2026-10-23", all_day=False,
        start_time="08:00:00", company="VALE3.SA", source="b3",
        description="Detalhes em https://ri.vale.com/3q26 e o anexo "
                    "https://abc.supabase.co/storage/v1/object/public/x.pdf",
        links=[{"label": "Cronograma B3", "url": "https://www.b3.com.br/cronograma"},
               {"label": "anexo", "url": "https://abc.supabase.co/storage/v1/object/public/x.pdf",
                "email": "analista@itaubba.com"}]),
    _ev("e-2", "SECEX monthly data", "2025-03-05", cat="c-data",
        recurrence={"freq": "monthly", "interval": 1, "bymonthday": [5], "until": None,
                    "_debug": {"token": "x"}}, updated_at="2026-09-10T12:00:00+00:00"),
    _ev("e-3", "Hidden draft", "2026-11-10", visible=False),
    _ev("e-4", "Too old", "2023-05-01"),
    _ev("e-5", "IBBA Mining Day", "2027-03-10", cat="c-data", end="2027-03-11",
        location="São Paulo", links=[{"label": "Inscrição", "url": "https://events.itaubba.com/mining"}],
        updated_at="2026-11-02T13:00:00.5+00:00"),
]


def _rpc_reports(database, args):
    return database["rpc_rows"]["reports"][:max(1, min(int(args.get("p_limit") or 10), 20))]


def _rpc_categories(database, args):
    return sorted(database["exec_calendar_categories"], key=lambda c: (c["sort_order"], c["name"]))


def _rpc_windows(database, args):
    return sorted((w for w in database["exec_calendar_data_windows"] if w["is_active"]),
                  key=lambda w: (w["from_day"], w["label"]))


def _rpc_events(database, args):
    """O corpo de `get_exec_calendar_events(p_from, p_to)`: visíveis (a service key não é admin)
    que cruzam o intervalo, mais TODOS os recorrentes, por início."""
    lo, hi = args["p_from"], args["p_to"]
    rows = [e for e in database["exec_calendar_events"] if e["is_visible"]
            and (e["recurrence"] is not None
                 or (e["start_date"] <= hi and (e["end_date"] or e["start_date"]) >= lo))]
    return sorted(rows, key=lambda e: (e["start_date"], e["start_time"] or ""))


def db() -> dict:
    return copy.deepcopy({"quotes": QUOTES, "macro_indicators": MACRO, "commodities": COMMODITIES,
                          "news_articles": NEWS, "news_digest": DIGEST,
                          "exec_calendar_events": CAL_EVENTS,
                          "exec_calendar_categories": CAL_CATEGORIES,
                          "exec_calendar_data_windows": CAL_WINDOWS,
                          "rpc_rows": {"reports": REPORTS},
                          "rpc": {"get_dashboard_flags": FLAGS,
                                  "get_heatmap_tickers": HEATMAP_ADMIN,
                                  "get_commodity_groups": COMMODITY_GROUPS,
                                  "get_recent_reports_v3": _rpc_reports,
                                  "get_exec_calendar_categories": _rpc_categories,
                                  "get_exec_calendar_data_windows": _rpc_windows,
                                  "get_exec_calendar_events": _rpc_events}})


# ── a memória do ciclo, como o update_quotes/update_macro monta ───────────────────────────────
def memory_quotes() -> list[dict]:
    rows = []
    for r in QUOTES:
        if r["ticker"] == MISSING_IN_CYCLE:
            continue
        rows.append({k: copy.deepcopy(r[k]) for k in
                     ("ticker", "name", "sector", "exchange", "price", "change_pct", "change_abs",
                      "intraday", "updated_at")})
    return rows


def memory_macro() -> list[dict]:
    return [copy.deepcopy(r) for r in MACRO if r["code"] != MACRO_MISSING_IN_CYCLE]


def memory() -> dict:
    return {"quotes": memory_quotes(), "macro": memory_macro()}


def previous_state() -> dict:
    """O estado deixado pelo ciclo anterior: já conhece CMPC.SN e SELIC (que faltam agora) e já
    consultou o banco pelos papéis que ninguém trouxe (`checked`) — como fica depois do 1º ciclo."""
    from hub_export.sources import _macro_row, _quote_row, macro_universe, quote_universe
    st = hub_state.empty_state()
    cmpc = next(r for r in QUOTES if r["ticker"] == MISSING_IN_CYCLE)
    st["quotes"]["rows"][MISSING_IN_CYCLE] = _quote_row(cmpc)
    st["quotes"]["checked"] = sorted(t for t, _n, _e in quote_universe())
    selic = next(r for r in MACRO if r["code"] == MACRO_MISSING_IN_CYCLE)
    st["macro"]["rows"][MACRO_MISSING_IN_CYCLE] = _macro_row(selic)
    st["macro"]["checked"] = sorted(c for c, _n, _u in macro_universe())
    return st


# ── o HTTP falso ──────────────────────────────────────────────────────────────────────────────
class FakeResp:
    def __init__(self, status: int = 200, body=b"", headers: dict | None = None):
        self.status_code = status
        self.content = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        self.headers = dict(headers or {})

    def json(self):
        return json.loads(self.content)


@dataclass
class Call:
    method: str
    url: str
    params: dict
    headers: dict
    data: bytes | None
    timeout: float | None

    @property
    def table(self) -> str | None:
        if self.url.startswith(SUPA_URL + "/rest/v1/"):
            return self.url[len(SUPA_URL + "/rest/v1/"):]
        return None


def _arrow(row: dict, expr: str):
    col, key = expr.split("->>", 1)
    obj = row.get(col)
    val = obj.get(key) if isinstance(obj, dict) else None
    if val is None:
        return None
    if isinstance(val, bool):
        return "true" if val else "false"
    return val if isinstance(val, str) else json.dumps(val)


def _project(row: dict, select: str) -> dict:
    out = {}
    for item in select.split(","):
        item = item.strip()
        alias, expr = item.split(":", 1) if ":" in item else (item, item)
        out[alias] = _arrow(row, expr) if "->>" in expr else copy.deepcopy(row.get(expr))
    return out


# ── o mini-PostgREST ──────────────────────────────────────────────────────────────────────────
# colunas timestamptz (o banco real recusa um literal que não é data: HTTP 400, código 22007)
TIMESTAMP_COLS = frozenset({"published_at", "found_at", "take_llm_at", "slot_start", "slot_end",
                            "created_at", "updated_at", "daily_updated_at"})
_DT = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")
_PARAMS = ("select", "order", "limit", "offset")


class PgError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def _ts(v, col):
    if isinstance(v, datetime):
        return v
    s = str(v)
    if not _DT.match(s):
        raise PgError(400, "22007",
                      f'invalid input syntax for type timestamp with time zone: "{s}"')
    return datetime.fromisoformat(s.replace("Z", "+00:00").replace(" ", "T"))


def _split_top(s: str) -> list[str]:
    """Separa por vírgula no nível de cima, respeitando parênteses e aspas (PostgREST)."""
    out, cur, depth, quoted, i = [], "", 0, False, 0
    while i < len(s):
        c = s[i]
        if quoted:
            cur += c
            if c == "\\" and i + 1 < len(s):
                cur += s[i + 1]
                i += 2
                continue
            if c == '"':
                quoted = False
        elif c == '"':
            quoted, cur = True, cur + c
        elif c == "(":
            depth, cur = depth + 1, cur + c
        elif c == ")":
            depth, cur = depth - 1, cur + c
        elif c == "," and depth == 0:
            out.append(cur)
            cur = ""
        else:
            cur += c
        i += 1
    if cur:
        out.append(cur)
    return out


def _unquote(v: str) -> str:
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] == '"':
        return re.sub(r"\\(.)", r"\1", v[1:-1])
    return v


def _cmp(row: dict, col: str, spec: str) -> bool:
    neg = spec.startswith("not.")
    if neg:
        spec = spec[4:]
    op, _, raw = spec.partition(".")
    val = row.get(col)
    if op == "is":
        res = {"null": val is None, "true": val is True, "false": val is False}[raw]
    elif op == "in":
        assert raw.startswith("(") and raw.endswith(")"), raw
        values = [_unquote(x) for x in _split_top(raw[1:-1])]
        res = val is not None and str(val) in values
    elif op in ("eq", "neq", "gt", "gte", "lt", "lte"):
        if col in TIMESTAMP_COLS:
            b = _ts(raw, col)
            a = _ts(val, col) if val is not None else None
        elif isinstance(val, bool):
            a, b = val, raw == "true"
        elif isinstance(val, (int, float)):
            a, b = val, float(raw)
        else:
            a, b = (None if val is None else str(val)), raw
        if a is None:
            res = False                                   # NULL não compara com nada
        else:
            res = {"eq": a == b, "neq": a != b, "gt": a > b, "gte": a >= b, "lt": a < b,
                   "lte": a <= b}[op]
    elif op in ("like", "ilike"):
        pat = "^" + re.escape(raw).replace(r"\*", ".*") + "$"
        res = val is not None and re.match(pat, str(val), re.I if op == "ilike" else 0) is not None
    else:
        raise AssertionError(f"filtro não suportado pelo falso: {col}={spec}")
    return not res if neg else res


def _tree(row: dict, term: str) -> bool:
    term = term.strip()
    neg = term.startswith("not.")
    if neg:
        term = term[4:]
    for op in ("and", "or"):
        if term.startswith(op + "(") and term.endswith(")"):
            parts = [_tree(row, t) for t in _split_top(term[len(op) + 1:-1])]
            res = all(parts) if op == "and" else any(parts)
            return not res if neg else res
    col, _, spec = term.partition(".")
    res = _cmp(row, col, spec)
    return not res if neg else res


def _matches(row: dict, params: dict) -> bool:
    for k, v in params.items():
        if k in _PARAMS:
            continue
        if k in ("or", "and"):
            if not _tree(row, k + v):
                return False
        elif not _cmp(row, k, v):
            return False
    return True


def _filter_cols(params: dict) -> set[str]:
    cols = set()

    def walk(term):
        term = term.strip()
        if term.startswith("not."):
            term = term[4:]
        for op in ("and", "or"):
            if term.startswith(op + "(") and term.endswith(")"):
                for t in _split_top(term[len(op) + 1:-1]):
                    walk(t)
                return
        cols.add(term.partition(".")[0])
    for k, v in params.items():
        if k in _PARAMS:
            continue
        if k in ("or", "and"):
            walk(k + v)
        else:
            cols.add(k)
    return cols


def _order(rows: list[dict], spec: str) -> list[dict]:
    out = list(rows)
    for part in reversed([p for p in spec.split(",") if p]):
        bits = part.split(".")
        col, desc = bits[0], "desc" in bits
        nullslast = "nullslast" in bits or (not desc and "nullsfirst" not in bits)
        present = [r for r in out if r.get(col) is not None]
        absent = [r for r in out if r.get(col) is None]
        key = (lambda r: _ts(r[col], col)) if col in TIMESTAMP_COLS else (lambda r: r[col])
        present.sort(key=key, reverse=desc)
        out = present + absent if nullslast else absent + present
    return out


@dataclass
class FakeHTTP:
    """`requests.Session` falso. `feeder_script[etapa]` = lista de respostas (status ou exceção)
    consumidas na ordem; vazia = sucesso. Etapas: initialize, PUT, complete, failed."""
    database: dict = field(default_factory=db)
    published: dict = field(default_factory=dict)          # nome → bytes (cópia no Mindassets)
    feeder_script: dict = field(default_factory=dict)
    supa_error: dict = field(default_factory=dict)          # tabela/rpc → status ou exceção
    calls: list = field(default_factory=list)
    uploads: dict = field(default_factory=dict)             # nome → bytes recebidos no PUT
    revision: int = 0

    def request(self, method, url, headers=None, params=None, data=None, json=None,
                timeout=None, stream=False):
        call = Call(method, url, dict(params or {}), dict(headers or {}), data, timeout)
        self.calls.append(call)
        if url.startswith(SUPA_URL):
            return self._supa(call)
        if url.startswith(FEEDER_URL) or url.startswith(PUT_HOST):
            return self._feeder(call)
        if url.startswith(PUBLIC_BASE):
            name = url[len(PUBLIC_BASE) + 1:]
            if name in self.published:
                return FakeResp(200, self.published[name])
            return FakeResp(404, b"<Error>NoSuchKey</Error>")
        raise AssertionError(f"URL inesperada no teste: {url}")

    # Supabase ─────────────────────────────────────────────────────────────────────────────
    def _supa(self, call: Call):
        path = call.url[len(SUPA_URL):]
        name = path.rsplit("/", 1)[1]
        err = self.supa_error.get(name)
        if isinstance(err, BaseException):
            raise err
        if isinstance(err, int):
            return FakeResp(err, {"message": "erro falso"})
        try:
            return self._postgrest(call, path, name)
        except PgError as e:
            return FakeResp(e.status, {"code": e.code, "message": e.message})

    def _postgrest(self, call: Call, path: str, name: str):
        is_rpc = path.startswith("/rest/v1/rpc/")
        if is_rpc:
            src = self.database["rpc"][name]
            rows = src(self.database, json.loads(call.data or b"{}")) if callable(src) else src
        else:
            rows = self.database[name]
            known = NEWS_COLUMNS if name == "news_articles" else None
            if known is not None:
                asked = {i.split(":", 1)[-1].split("->>")[0].strip()
                         for i in call.params["select"].split(",")} | _filter_cols(call.params)
                for col in sorted(asked - known):
                    raise PgError(400, "42703", f"column {name}.{col} does not exist")
        rows = [r for r in rows if _matches(r, call.params)]
        if "order" in call.params:
            rows = _order(rows, call.params["order"])
        total = len(rows)
        off = int(call.params.get("offset") or 0)
        rows = rows[off:]
        if "limit" in call.params:
            rows = rows[:int(call.params["limit"])]
        headers = {}
        if "count=exact" in (call.headers.get("Prefer") or ""):
            headers["Content-Range"] = (f"{off}-{off + len(rows) - 1}/{total}" if rows
                                        else f"*/{total}")
        if call.method == "HEAD":
            if headers:
                headers["Content-Range"] = f"*/{total}"
            return FakeResp(200, b"", headers=headers)
        return FakeResp(200, [_project(r, call.params["select"]) for r in rows], headers=headers)

    # Feeder ───────────────────────────────────────────────────────────────────────────────
    def _next(self, step):
        queue = self.feeder_script.get(step) or []
        return queue.pop(0) if queue else 200

    def _feeder(self, call: Call):
        if call.method == "PUT":
            step = "PUT"
        elif call.url.endswith("/complete"):
            step = "complete"
        elif call.url.endswith("/failed"):
            step = "failed"
        else:
            step = "initialize"
        outcome = self._next(step)
        if isinstance(outcome, BaseException):
            raise outcome
        if outcome != 200:
            return FakeResp(outcome, {"error": f"{step} falhou"})
        if step == "initialize":
            body = json.loads(call.data)
            self.revision += 1
            upd = f"upd-{self.revision}"
            ctype = body["contentType"]
            return FakeResp(200, {"update": {"id": upd, "status": "AWAITING_UPLOAD"},
                                  "upload": {"url": f"{PUT_HOST}/bucket/{body['name']}"
                                                    f"?X-Amz-Signature=ASSINATURA-SECRETA-{upd}",
                                             "requiredHeaders": {"content-type": ctype}}})
        if step == "PUT":
            name = call.url.split("/bucket/", 1)[1].split("?", 1)[0]
            self.uploads[name] = call.data
            return FakeResp(200, b"")
        if step == "complete":
            return FakeResp(200, {"update": {"id": call.url.split("/")[-2],
                                             "revision": self.revision}})
        return FakeResp(200, {"update": {"status": "UPLOAD_FAILED"}})

    # leitura dos registros ───────────────────────────────────────────────────────────────
    def supa_calls(self, table: str | None = None) -> list[Call]:
        return [c for c in self.calls if c.table is not None
                and (table is None or c.table == table)]

    def feeder_calls(self) -> list[Call]:
        return [c for c in self.calls if c.url.startswith((FEEDER_URL, PUT_HOST))]


def connection_error():
    return requests.ConnectionError(f"HTTPSConnectionPool: falhou {PUT_HOST}/x?X-Amz-Signature=SIG")


def make_sources(http=None, state=None, mem="default", published=None, now=NOW, **kw):
    """Sources ligado ao banco falso. `mem="default"` = a memória do ciclo das fixtures. O arquivo
    de notícias começa em ARCHIVE_FIRST (semana 43), salvo pedido em contrário."""
    from hub_export.sources import Sources
    from hub_export.supa import SupaReader
    http = http or FakeHTTP()
    supa = SupaReader(SUPA_URL, SUPA_KEY, http=http)
    st = hub_state.empty_state() if state is None else state
    kw.setdefault("first_week", ARCHIVE_FIRST)
    return Sources(supa, st, now=now, memory=memory() if mem == "default" else mem,
                   published=published, **kw), http


def run_kw(**kw) -> dict:
    """Os argumentos comuns do run_cycle nos testes (arquivo de notícias desde a semana 43)."""
    kw.setdefault("first_week", ARCHIVE_FIRST)
    return kw


def week_names(results) -> list[str]:
    """Os nomes semanais de uma lista de Results (ou de um dict nome → Result)."""
    names = results if isinstance(results, dict) else [r.name for r in results]
    return sorted(n for n in names if n.startswith("news-week-"))


# ── os testes do próprio banco falso ─────────────────────────────────────────────────────────
def test_fixtures_cobrem_os_casos_do_brief():
    bolsas = {r["exchange"] for r in QUOTES}
    assert len(bolsas) >= 6
    assert MISSING_IN_CYCLE not in {r["ticker"] for r in memory_quotes()}
    sess = next(r for r in QUOTES if r["ticker"] == "AUGO")["intraday"]["session"]
    assert sess[1] < sess[0]                                    # sessão inválida
    assert any(r["spark"] is None for r in COMMODITIES)
    assert any(r["spark"] for r in COMMODITIES)
    assert any(r["is_hidden"] for r in HEATMAP_ADMIN)
    assert any(r["in_coverage"] and not r["is_hidden"] for r in HEATMAP_ADMIN)


def test_mini_postgrest_entende_alias_seta_filtro_e_ordem():
    http = FakeHTTP()
    r = http.request("GET", SUPA_URL + "/rest/v1/commodities",
                     params={"select": "code,asof:spark->>asof,v:spark->>v",
                             "code": 'in.("COPPER","HRC_CHINA")', "order": "code"})
    assert r.json() == [{"code": "COPPER", "asof": "2026-11-03", "v": "2"},
                        {"code": "HRC_CHINA", "asof": None, "v": None}]
    r = http.request("GET", SUPA_URL + "/rest/v1/quotes",
                     params={"select": "updated_at", "order": "updated_at.desc.nullslast",
                             "limit": "1"})
    assert r.json() == [{"updated_at": "2026-11-03T18:04:03.75+00:00"}]


def test_fixtures_1b_cobrem_os_casos_do_brief():
    by = {r["url"]: r for r in NEWS}
    assert by[U_PLATTS]["source_name"] == "S&P Platts" and by[U_FM]["source_name"] == "Fastmarkets"
    assert by[U_SMM]["published_at"] is None and by[U_FM]["take_llm"] is None      # pendente
    assert any(not r["include_in_report"] for r in NEWS)
    assert any(r["url"].startswith("javascript:") for r in NEWS)
    assert any("token=" in r["url"] for r in NEWS)
    assert {d["status"] for d in DIGEST} == {"ok", "quiet", "list"}
    assert any(d["kind"] == "overnight" for d in DIGEST)
    assert any(not c["is_active"] for c in CAL_CATEGORIES)
    assert any(e["recurrence"] for e in CAL_EVENTS) and any(not e["is_visible"] for e in CAL_EVENTS)
    assert sum(len(v) for v in WEEK_URLS.values()) == 11


def test_mini_postgrest_entende_or_and_datas_contagem_e_rpc():
    http = FakeHTTP()
    sel = {"select": "url", "include_in_report": "eq.true",
           "or": "(published_at.gte.2026-11-03T12:00:00Z,and(published_at.is.null,"
                 "found_at.gte.2026-11-03T17:00:00Z))", "order": "url.asc"}
    got = {r["url"] for r in http.request("GET", SUPA_URL + "/rest/v1/news_articles",
                                          params=sel).json()}
    assert got == {U_GNEWS, U_SMM, U_FUTURE}           # a de 17:00 fora do relatório não vem
    r = http.request("HEAD", SUPA_URL + "/rest/v1/news_articles",
                     params={"select": "url", "include_in_report": "eq.true"},
                     headers={"Prefer": "count=exact"})
    assert r.content == b"" and r.headers["Content-Range"] == "*/14"
    r = http.request("GET", SUPA_URL + "/rest/v1/news_articles",
                     params={"select": "url", "order": "found_at.desc,url.asc", "limit": "2",
                             "offset": "1"}, headers={"Prefer": "count=exact"})
    assert [x["url"] for x in r.json()] == ["https://www.bloomberg.com/crypto-rally",
                                            "https://www.mining.com/no-title/"]    # 2ª e 3ª
    assert r.headers["Content-Range"] == "1-2/15"
    r = http.request("GET", SUPA_URL + "/rest/v1/news_articles",
                     params={"select": "url", "found_at": "eq.nao-e-data"})
    assert r.status_code == 400 and r.json()["code"] == "22007"
    r = http.request("GET", SUPA_URL + "/rest/v1/news_articles", params={"select": "created_at"})
    assert r.status_code == 400 and r.json()["code"] == "42703"
    r = http.request("POST", SUPA_URL + "/rest/v1/rpc/get_recent_reports_v3",
                     params={"select": "name"}, data=json.dumps({"p_limit": 2}).encode())
    assert [x["name"] for x in r.json()] == ["Vale 3Q26: strong volumes", "Suzano: pulp price preview"]
    r = http.request("GET", SUPA_URL + "/rest/v1/news_articles",
                     params={"select": "url", "url": f'in.("{U_SMM}","{U_CVM}")'})
    assert {x["url"] for x in r.json()} == {U_SMM, U_CVM}
