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
Onda 2 (WP2.1B):
  • `quote_history` (a série LONGA) de 7 papéis da Market + 1 de fora (RIO.L), montada de modo que
    a série leve das cotações acima seja EXATAMENTE a redução dela (`light_of`, o porte do
    `append_quote_history`; um teste confere): VALE3.SA desde 2020 com pontos que a redução mensal
    descarta, IBOV, SCCO com split, os curtos; AUGO sem histórico. A tabela nasce VAZIA no `db()`
    (os testes de 1a/1b andam o relógio e não podem ver a carga dos históricos começar): quem testa
    a família põe `quote_history()` no banco;
  • `commodities.daily` das 3 de número livre (IRON_ORE_62 do TE, COPPER e GOLD do Yahoo) E das
    pagas (IRON_ORE 61% Platts, HRC China, BHKP China) — as pagas nunca podem sair;
  • histórico de recomendações (tabela, empresas do Stock Guide e a RPC `get_rating_history`, que
    só devolve com a flag `market_ratings` ligada e esconde a empresa oculta), com `note`, `source`,
    ids e carimbos de usuário que não podem vazar.
Onda 2 (WP2.4B, Market Watch — as tabelas `mw_*` e as RPCs `get_mw_*`, que escondem a empresa
oculta como as de verdade):
  • 7 empresas, uma OCULTA (CSNA3, com dado em todas as tabelas que nunca pode sair), fora de ordem
    na tabela; um papel PNA/PNB (USIM5, `float_by_class`), uma UNIT (KLBN11, ÷ 5), um BDR (AURA33,
    total), ON (VALE3, RANI3) e PN (BRAP4); RANI3 SEM capital; AURA33 sem insiders;
  • aluguel semanal de 16/07 a 29/10 + 02/11/2026 (o último pregão gravado), 12 papéis, o USIM6 só
    nos 3 últimos, `bdi_pdf` antes de agosto; colunas internas (`qty_registro/d0/d1`) que não saem;
  • recompras: em andamento, encerrada com prazo no futuro (o status manda), SEM status com prazo no
    futuro (vale o prazo), em dólar (Aura), sem data de decisão; `reason` com e-mail e telefone;
  • insiders: MAIS DE 1.000 linhas na janela de 14 meses (o PostgREST corta em 1.000), saldos e
    negócios — dentro e fora dos 12 meses, "Exercício opção de venda" (conta como venda na regra
    do site), meses fora da janela, `group_type` nulo; `move_desc` com e-mail e telefone e com a
    data colada (`20082026`, não é telefone);
  • comunicados: com link do ENET, SEM link, com link recusado pelo filtro (credencial), política
    de fato relevante (R), trecho com e-mail/telefone (+55 (21) …, 55 21 ….…, 0800) e com o e-mail
    CORTADO no fim do trecho; `published_at` com fuso -03:00 (como o robô grava).
Onda 3 (WP3.1B, setores — os bancos SQLite do repositório do site, servidos por um GitHub FALSO):
  • os dois bancos PEQUENOS de fixture ficam em `hub_export/samples/sectors/` (`steel_sm_web.db.gz` e
    `pulp_paper.db`, com os esquemas de verdade): bytes fixos no git, então o SHA do blob (que vai no
    índice dourado) não depende da versão do SQLite de quem roda; `sector_fixture_dbs()` os gera (o
    teste confere que o conteúdo bate e o `UPDATE_GOLDEN=1` regrava). Os casos: país com 3 meses
    vazios (fica no trecho, como `null`) e com 4 (trecho novo), linha com volume 0 × mês sem linha,
    valor minúsculo (1e-06), nomes com acento (ordem de código Unicode: "Áustria" e "Índia" depois de
    "Japão"), mês faltando no fim de uma série (o eixo comum), o `Wire Rod` só na linha laranja, o
    GACC com os primeiros meses de volume 0 (o corte) e o último também (o "Data through" pelo
    `volume_bdmt > 0`), porto com 3 e 4 meses vazios, `exprev_*`/`iba_pulp`/`company_q` que só a
    base bruta leva, colunas NULL;
  • o `FakeGitHub` (sonda `contents/`, download `git/blobs/<sha>`, `commits?path=`), com falha sob
    encomenda (status, exceção, limite de taxa) e o conteúdo adulterado (SHA que não confere).

Nada aqui fala com a rede: o `FakeHTTP` faz o papel do `requests.Session` para o Supabase (um
mini-PostgREST: select com alias, `->>` e `->` com chave ou índice (inclusive negativo:
`last:daily->-1`); filtros `eq./neq./gt./gte./lt./lte./is./in./like.`, `not.` e as árvores
`or=(…)`/`and=(…)`, também sobre o resultado de RPC; `order` com várias colunas, `limit`, `offset`
e o corte de 1.000 linhas por resposta; HEAD e `Prefer: count=exact` com `Content-Range`; RPC com
argumentos; colunas e tipos conferidos onde o banco real recusaria), para o Feeder (roteiro de
respostas por etapa, com falhas sob encomenda), para a cópia publicada no Mindassets e para a API do
GitHub (os bancos dos setores).
"""
from __future__ import annotations

import atexit
import base64
import copy
import gzip
import hashlib
import json
import re
import shutil
import sqlite3
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hub_export import sectors as SE  # noqa: E402
from hub_export import state as hub_state  # noqa: E402
from hub_export.github import API as GITHUB_API  # noqa: E402
from hub_export.github import OWNER as GITHUB_OWNER  # noqa: E402
from hub_export.github import REPO as GITHUB_REPO  # noqa: E402
from hub_export.github import git_blob_sha  # noqa: E402

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


D_1030, D_1102, D_1103 = 1793318400, 1793577600, 1793664000      # 00:00 UTC de 30/10, 02/11, 03/11
COMMODITIES = [
    # PAGA (Platts 61%): o robô ACUMULA o assessment em `daily` — a série nunca pode sair
    {"code": "IRON_ORE", "name": "IO Fines 61%", "unit": "USD/dmt", "price": 99.05,
     "change_pct": -0.4, "assessed_at": "2026-11-02", "updated_at": "2026-11-03T17:32:39.736573+00:00",
     "spark": _spark("2026-11-02"),
     "daily": [[D_1030, 98.5], [D_1102, 99.05]], "daily_updated_at": "2026-11-03T01:00:00.5+00:00"},
    {"code": "COPPER", "name": "Copper", "unit": "USD/lb", "price": 5.1234, "change_pct": 0.8,
     "assessed_at": "2026-11-03", "updated_at": "2026-11-03T18:04:05.1+00:00",
     "spark": _spark("2026-11-03", ph=0),
     "daily": [[1631232000, 4.31], [D_1030, 5.02], [D_1102, 5.08], [D_1103, 5.1234]],
     "daily_updated_at": "2026-11-03T01:12:00.123+00:00"},
    {"code": "HRC_CHINA", "name": "HRC China", "unit": "USD/mt", "price": 455.0,
     "change_pct": None, "assessed_at": "2026-10-31", "updated_at": "2026-11-03T17:32:39.7+00:00",
     "spark": None, "daily": [[D_1030, 450.0], [D_1102, 455.0]],
     "daily_updated_at": "2026-11-03T01:00:00+00:00"},
    # chave de depuração dentro do spark: a lista branca tem de barrar
    {"code": "PULP_BHKP_CHINA", "name": "BHKP China Net", "unit": "USD/t", "price": 560.0,
     "change_pct": 0.0, "assessed_at": "2026-10-30", "updated_at": "2026-11-03T11:00:00+00:00",
     "spark": _spark("2026-10-30", extra={"_debug": {"token": "x"}}),
     "daily": [[1792800000, 555.0], [D_1030, 560.0]], "daily_updated_at": "2026-11-03T11:00:00+00:00"},
    # LIVRES (Onda 2): minério 62% do Trading Economics (~10 anos) e ouro do Yahoo (~5 anos)
    {"code": "IRON_ORE_62", "name": "Iron Ore 62% CFR China", "unit": "USD/t", "price": 104.6,
     "change_pct": 0.35, "assessed_at": "2026-11-03", "updated_at": "2026-11-03T14:38:50.5+00:00",
     "spark": None, "daily": [[1478995200, 72.5], [D_1030, 103.9], [D_1102, 104.25], [D_1103, 104.6]],
     "daily_updated_at": "2026-11-03T14:38:50.5+00:00"},
    {"code": "GOLD", "name": "Gold", "unit": "USD/oz", "price": 4012.3, "change_pct": -0.2,
     "assessed_at": "2026-11-03", "updated_at": "2026-11-03T15:24:00+00:00", "spark": None,
     "daily": [[1631232000, 1794.5], [D_1102, 4020.4], [D_1103, 4012.3]],
     "daily_updated_at": "2026-11-03T15:24:00.25+00:00"},
]
FREE_CODES = ("IRON_ORE_62", "COPPER", "GOLD")
PAID_CODES = ("IRON_ORE", "HRC_CHINA", "PULP_BHKP_CHINA")

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
    {"key": "market_ratings", "enabled": True},  # Onda 2: o ratings.json é fail-closed também
    # Onda 4: fail-closed no dado; `stock_guide_peers` AUSENTE = ligada (fail-open, como no site)
    {"key": "stock_guide", "enabled": True},
    {"key": "stock_guide_sensitivity", "enabled": True},
    {"key": "data_page", "enabled": True},
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
    # Onda 4 (H.3): texto livre com contato (mascarado) e links — o de rastreio de e-mail (FICTÍCIO) sai,
    # o sem rótulo fica com `label: null`
    _ev("e-5", "IBBA Mining Day", "2027-03-10", cat="c-data", end="2027-03-11",
        location="São Paulo", description="Dúvidas: eventos.ficticio@example.com ou (11) 3000-0000.",
        links=[{"label": "Inscrição", "url": "https://events.itaubba.com/mining"},
               {"label": "confirmar", "url": "https://mailerurl.mziq.com/ls/click?upn=FICTICIO"},
               {"label": "  ", "url": "https://www.example.org/agenda-ficticia"}],
        updated_at="2026-11-02T13:00:00.5+00:00"),
]


# ── Onda 2: a série LONGA (quote_history) ─────────────────────────────────────────────────────
def _epoch(iso: str) -> int:
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())


def light_of(long_pts, dua: str) -> list[list]:
    """A série LEVE que o `append_quote_history` (site, admin/supabase_quote_history.sql) deriva da
    longa: o último pregão de cada mês UTC antes de `v_cut` + todos os pontos ≥ `v_cut`, com
    `v_cut` = `daily_updated_at` − 372 dias."""
    cut = _epoch(dua) - 372 * 86400
    monthly = {}
    for ts, v in long_pts:
        if ts < cut:
            d = datetime.fromtimestamp(ts, timezone.utc)
            ym = (d.year, d.month)
            if ym not in monthly or ts > monthly[ym][0]:
                monthly[ym] = [ts, v]
    return sorted(monthly.values()) + [[ts, v] for ts, v in long_pts if ts >= cut]


def _qh(ticker, daily, split=0):
    q = next((r for r in QUOTES if r["ticker"] == ticker), None)
    dua = q["daily_updated_at"] if q else "2026-11-02T23:10:00+00:00"
    return {"ticker": ticker, "daily": daily, "points": len(daily), "first_ts": daily[0][0],
            "last_ts": daily[-1][0], "last_split_ts": split, "source": "yahoo", "updated_at": dua}


SPLIT_SCCO = 1793716200
QUOTE_HISTORY = [
    # 01/01/2020 e 15/09/2025: a redução mensal descarta (não são o último pregão do mês)
    _qh("VALE3.SA", [[1577883600, 39.5], [1577970000, 40.0], [1757941200, 54.0], [1759168800, 55.0],
                     [1759323600, 56.0], [1793365200, 59.9], [1793451600, 60.1], [1793624400, 60.48]]),
    _qh("IBOV", [[1577883600, 114500.0], [1577970000, 115000.0], [1793365200, 127100.0],
                 [1793624400, 127785.82]]),
    _qh("SCCO", [[1793367000, 110.0], [1793629800, 113.36]], split=SPLIT_SCCO),
    _qh("CMPC.SN", [[1793365200, 1340.0], [1793624400, 1354.0]]),
    _qh("VMM.AX", [[1793365200, 0.58]]),
    _qh("HBM.TO", [[1793367000, 24.9]]),
    _qh("AAL.L", [[1793365200, 2300.0]]),
    # insumo do blast, FORA do universo da Market: nunca vira arquivo
    _qh("RIO.L", [[1793365200, 6000.0]]),
]


def quote_history() -> list[dict]:
    return copy.deepcopy(QUOTE_HISTORY)


# ── Onda 2: histórico de recomendações (Stock Guide) ──────────────────────────────────────────
def _sgc(ticker, name, symbol, *, hidden=False, model_url=None, updated_by=None, upd, order, sector,
         rec="OP", tp=10.0, shares=100.0, trade="USD", base="USD", fx_base=1.0, fx_usd=1.0, y1=None,
         y2=None):
    """Uma linha de `stock_guide_companies` (o mundo falso usa `is_hidden`; a coluna de verdade é
    `is_visible` — as duas vão na linha) com as colunas que a tela não usa e que nunca saem."""
    row = {"ticker": ticker, "company_name": name, "yahoo_symbol": symbol, "is_hidden": hidden,
           "is_visible": not hidden, "model_url": model_url, "updated_by": updated_by, "updated_at": upd,
           "display_order": order, "sector": sector, "recommendation": rec, "target_price": tp,
           "shares_outstanding": shares, "trade_ccy": trade, "base_ccy": base, "fx_to_base": fx_base,
           "fx_to_usd": fx_usd, "last_update": "2026-11-05", "volume_unit": "Mt", "capex_y1": 999.0,
           "capex_y2": 998.0, "net_revenues_y1": 5000.0, "net_revenues_y2": 5100.0, "fcfe_y1": None,
           "fcfe_y2": None, "volumes_y1": 300.0, "volumes_y2": 310.0}
    for y, vals in (("y1", y1), ("y2", y2)):
        for col, v in zip(("ebitda", "net_debt", "ev_adjustment", "net_income", "cash_earnings",
                           "dividends", "ocf"), vals or (None,) * 7):
            row[f"{col}_{y}"] = v
    return row


SG_COMPANIES = [
    # números FICTÍCIOS (pequenos, determinísticos); o ruído binário de propósito (o arquivo leva como está)
    _sgc("VALE", "Vale", "VALE", model_url="https://tenant.sharepoint.com/modelo-vale.xlsm",
         updated_by="uuid-admin", upd="2026-11-06T15:39:00.5+00:00", order=1, sector="iron_ore",
         rec="MP", tp=14, shares=4255.762795000001, y1=(14826.1, 16341.6, 974.0, 5407.1, 6123.7, 4794, 8437.8),
         y2=(14112.2, 15100.0, 974.0, 5100.0, 5900.0, 4500.0, 8000.0)),
    _sgc("AUGO", "Aura", "AUGO", updated_by="uuid-admin", upd="2026-11-06T15:30:00+00:00", order=5,
         sector="gold", rec="Buy", tp=98.0, shares=74.0, y1=(700.0, -100.0, 0.0, 400.0, 450.0, 250.0, 500.0),
         y2=(750.0, -150.0, None, 420.0, 470.0, 260.0, 520.0)),
    # negocia em CAD com o modelo em US$ (o fator do modelo ≠ 1); EBITDA negativo (desenvolvedora)
    _sgc("ARA", "Aclara Resources", "ARA.TO", upd="2026-11-06T15:20:00+00:00", order=15,
         sector="rare_earths", tp=4.37, shares=230.5, trade="CAD", fx_base=0.708, fx_usd=0.708,
         y1=(-12.0, -40.0, None, -15.0, -14.0, None, -10.0), y2=(-8.0, -30.0, None, -9.0, -8.5, None, -6.0)),
    # oculta no Stock Guide: a RPC de cliente devolve só nome, visibilidade e ordem
    _sgc("CSNA3", "CSN", "CSNA3.SA", hidden=True, upd="2026-11-01T10:00:00+00:00", order=6,
         sector="steel", rec="UP", tp=8.0, shares=1326.0, trade="BRL", base="BRL",
         y1=(9000.0, 30000.0, 0.0, -500.0, 1500.0, 0.0, 6000.0)),
]
SG_CONFIG = [{"id": 1, "y1_label": "2026E", "y2_label": "2027E", "assumptions_note": "nota interna do editor",
              "comps_footnote": "", "updated_at": "2026-06-23T12:00:00+00:00", "updated_by": "uuid-admin"}]


def _sgp(company, order, country, sector, symbol, ev, nd, pe, pce, dy):
    """Um par de `stock_guide_global_peers` (div. yield em FRAÇÃO, como o banco guarda)."""
    row = {"company": company, "display_order": order, "country": country, "sector": sector,
           "yahoo_symbol": symbol, "is_aggregate": False, "is_live": True, "mkt_cap_usd": 12345.6,
           "updated_at": "2026-11-06T15:39:10+00:00"}
    for i, y in enumerate(("y1", "y2")):
        row.update({f"ev_ebitda_{y}": ev[i], f"net_debt_ebitda_{y}": nd[i], f"pe_{y}": pe[i],
                    f"pce_{y}": pce[i], f"div_yield_{y}": dy[i]})
    return row


SG_PEERS = [                                       # fora de ordem: a RPC ordena por display_order
    _sgp("RIO", 3, "UK", "iron_ore", "RIO", (5.9, 5.6), (0.4, 0.3), (10.2, 9.8), (None, None),
         (0.0612, 0.0598)),
    _sgp("CAP", 1, "CL", "steel", "CAP.SN", (6.216729651245153, 5.1), (2.4868222891566263, 2.0), (None, None),
         (27.951849852000375, 20.0), (0.018177375411990001, None)),
    _sgp("BHP", 2, "AU", "iron_ore", "BHP", (6.1, 5.8), (0.5, 0.4), (12.0, 11.5), (None, None),
         (0.049, 0.051)),
]


def _sens_table(tid, metric, year, title, *, pair=None, rows=(1.0, 2.0), cols=(10.0, 20.0, 30.0),
                values=None, col_label="Avg. Iron Ore Price 2027 (USD/t)", row_label="Avg. FX 2027 (USD/BRL)"):
    vals = values or [[round(r * c, 4) for c in cols] for r in (rows or (1.0,))]
    return {"id": tid, "metric": metric, "year": year, "title": title, "pairId": pair, "colLabel": col_label,
            "rowLabel": row_label if rows else None, "colLevels": list(cols),
            "rowLevels": list(rows) if rows else None, "values": vals, "title_note": "corrigido à mão",
            "title_original": "título original do Excel", "corner": "x"}


def _analysis(key, label, price):
    # `mktcap_model_nav` nulo GRAVADO, como o /admin faz em toda análise importada desde 25/11
    # (sgMergeSens2dAnalysis); no banco de 26/11 só as 4 de cobre têm a chave (as outras: ausente = null)
    return {"key": key, "label": label, "source_file": f"Modelo Ficticio_Sensitivity_{key.upper()}.xlsx",
            "model_date": "2026-11-09", "currency": "USD", "model_price": price, "mktcap_model": 50000.5,
            "mktcap_model_nav": None, "shares_mn": 4255.76,
            "assumptions": [{"label": "Avg. Iron Ore Price 2027", "value": 97.5},
                            {"label": "Avg. FX 2027", "value": 5.5}],
            "assumptions_note": "premissa conferida", "import_ticker": "VALE3"}


def _vale_sheet(key, label, price):
    """Uma aba de análise da Vale: EBITDA + EV/EBITDA (o par) desenhados; dividend yield guardado e
    ESCONDIDO pela tela."""
    return {"name": label, "analysis": _analysis(key, label, price),
            "tables": [_sens_table(f"{key}-t1", "ebitda", 2027, "Proforma EBITDA 2027 (USD million)"),
                       _sens_table(f"{key}-t2", "ev_ebitda", 2027, "EV/EBITDA 2027 (x)", pair=f"{key}-t1"),
                       _sens_table(f"{key}-t3", "div_yield", 2027, "Dividend Yield 2027 (%)")]}


def _cu_table(tid, metric, year, title, *, pair=None, values=None, long_term=False):
    """Uma grade 3×3 de uma mineradora de cobre (números fictícios): cobre nas linhas × ouro nas colunas,
    em termos reais de 2027 (o rótulo do cobre com a quebra de linha, como o analista escreveu) ou, no P/NAV,
    os preços de longo prazo."""
    if long_term:
        return _sens_table(tid, metric, year, title, pair=pair, values=values, rows=(5.2, 5.6, 6.0),
                           cols=(3450.0, 4250.0, 5050.0), col_label="Long-Term Gold Price (USD/oz)",
                           row_label="Long-Term Copper Price (USD/lb)")
    return _sens_table(tid, metric, year, title, pair=pair, values=values, rows=(5.0, 5.4, 5.8),
                       cols=(3600.0, 4400.0, 5200.0), col_label="Average Gold Real-Term Price 2027E (USD/oz)",
                       row_label="Average Copper Real-Term Price \n2027E (USD/lb)")


def _sens(sid, ticker, order, definition, upd):
    return {"id": sid, "title": f"Sensibilidade {ticker}", "value_mode": "absolute", "metric_label": "",
            "unit": "", "companies": [ticker], "definition": definition, "display_order": order,
            "updated_at": upd, "updated_by": "uuid-admin"}


SG_SENS = [
    # o exemplo fictício antigo (formato de antes: sem `kind`) — a RPC devolve, o arquivo nunca
    _sens(1, "VALE", 0, {"panel": "Exemplo", "ticker": "VALE", "tables": [{"title": "Exemplo"}]},
          "2026-10-21T10:00:00+00:00"),
    # Vale: 3 análises (uma por arquivo), cada uma com 2 grades desenhadas e 1 escondida
    _sens(7, "VALE", 1, {"kind": "sens2d", "ticker": "VALE", "currency": "USD", "model_date": "2026-11-09",
                         "source_file": "Modelo Ficticio_Sensitivity_FX.xlsx", "model_price": 11.97,
                         "mktcap_model": 50000.5, "shares_mn": 4255.76, "panel": "Vale", "corner": "x",
                         "import_ticker": "VALE3", "mktcap_note": "FCF ÷ yield",
                         "assumptions": [{"label": "Avg. Iron Ore Price 2027", "value": 97.5}],
                         "sheets": [_vale_sheet("fx", "Iron ore × FX", 11.97),
                                    _vale_sheet("freight", "Iron ore × Freight", 11.90),
                                    _vale_sheet("copper", "Iron ore × Copper", 11.97)]},
          "2026-11-11T18:41:30.25+00:00"),
    # Gerdau: 2027 de preferência (a de 2026 sai), margem nunca; a grade SEM ano fica
    _sens(4, "GGBR4", 7, {"kind": "sens2d", "ticker": "GGBR4", "currency": "BRL", "model_date": "2026-10-28",
                          "source_file": "Modelo Ficticio Gerdau_Sensitivity.xlsx", "model_price": "24.0",
                          "mktcap_model": 47083.25, "assumptions": [{"label": "Avg. FX 2027", "value": 5.4},
                                                                    {"value": 1.0}],
                          "sheets": [{"name": "Sensitivity ", "tables": [
                              _sens_table("t0", "ebitda", 2026, "EBITDA 2026 (BRL million)"),
                              _sens_table("t1", "ebitda", "2027", "EBITDA 2027 (BRL million)"),
                              _sens_table("t2", "fcf", 2027, "FCF 2027 (BRL million)"),
                              _sens_table("t3", "fcf_yield", 2027, "FCF Yield 2027 (%)", pair="t2",
                                          values=[[0.051, None, 0.07], [0.06, 0.065, 0.071]]),
                              _sens_table("t4", "fcf", None, "FCF Ex-Prepayments (BRL million)"),
                              _sens_table("t5", "ebitda_margin", 2027, "EBITDA Margin 2027 (%)")]}]},
          "2026-11-03T10:00:00+00:00"),
    # desenvolvedora (Aclara): sem as 4 de sempre → DCF upside e P/NAV; o P/NAV em grade 1-D
    _sens(16, "ARA", 15, {"kind": "sens2d", "ticker": "ARA", "currency": "USD", "model_date": "2026-10-16",
                          "source_file": "Modelo Ficticio Aclara_Sensitivity.xlsx", "model_price": 3.0,
                          "mktcap_model": None, "assumptions": [],
                          "sheets": [{"name": "Sensitivity", "tables": [
                              _sens_table("u1", "upside", 2027, "DCF Upside (%)", col_label="Dy price (US$/kg)",
                                          row_label="Y price (US$/kg)"),
                              _sens_table("p1", "p_nav", None, "P/NAV (x)", rows=None,
                                          col_label="Dy price (US$/kg)")]}]},
          "2026-11-11T15:40:00+00:00"),
    # empresa OCULTA: a RPC de cliente não devolve a linha
    _sens(20, "CSNA3", 6, {"kind": "sens2d", "ticker": "CSNA3", "currency": "BRL",
                           "sheets": [{"name": "Sensitivity", "tables": [
                               _sens_table("c1", "ebitda", 2027, "EBITDA 2027 (BRL million)")]}]},
          "2026-11-12T09:00:00+00:00"),
    # COBRE (Hudbay; o molde dos 4 arquivos de 22/11): EBITDA, EV/EBITDA, FCF e FCF yield de 2027 + o P/NAV de
    # longo prazo (sem ano). A tela desenha o conjunto do cobre (_S2_SHOW_EMPRESA, stock-guide.html:816-820,
    # a92c19f): o EV/EBITDA fica GUARDADO e não sai; o P/NAV entra. A base do P/NAV (`mktcap_model_nav`, do
    # quadro "Implicit Cu Prices") a ±1 % da do FCF yield; as notas do conserto à mão nunca saem
    _sens(18, "HBM", 5, {"kind": "sens2d", "ticker": "HBM", "import_ticker": "HBM", "currency": "USD",
                         "model_date": "2026-11-22", "source_file": "Modelo Ficticio Hudbay_Sensitivity.xlsx",
                         "model_price": 16.25, "mktcap_model": 6420.5, "mktcap_model_nav": 6388.75,
                         "mktcap_model_nav_note": "base do P/NAV refeita à mão (nota interna)",
                         "shares_mn": 395.06, "panel": "Sensitivity", "model_price_original": 29.5,
                         "model_price_note": "D12 apontava o preço-alvo (conserto à mão)",
                         "tables_omitted": [{"title": "Grade fictícia omitida", "why": "sinais trocados no EV"}],
                         "assumptions": [{"label": "Copper Real-Term Price 2027E (USD/lb)", "value": 5.4},
                                         {"label": "Gold Real-Term Price 2027E (USD/oz)", "value": 4400.0},
                                         {"label": "(=) Market Cap (USD mn)", "value": 6388.75}],
                         "sheets": [{"name": "Sensitivity", "tables": [
                             _cu_table("t1", "ebitda", 2027, "EBITDA 2027E (USD million)"),
                             _cu_table("t2", "ev_ebitda", 2027, "EV/EBITDA 2027E (x)", pair="t1"),
                             _cu_table("t3", "fcf", 2027, "FCF Ex-Growth 2027E (USD million)"),
                             _cu_table("t4", "fcf_yield", 2027, "FCF Ex-Growth Yield (%) 2027E", pair="t3",
                                       values=[[0.081, 0.09, 0.099], [0.1, 0.108, 0.117], [0.12, 0.127, 0.135]]),
                             _cu_table("t5", "p_nav", None, "P/NAV (x)", long_term=True,
                                       values=[[0.8, 0.9, 1.0], [0.85, 0.95, 1.05], [0.9, 1.0, 1.1]])]}]},
          "2026-11-11T14:00:00+00:00"),
]
SENS_DRAWN = {"VALE": 6, "HBM": 4, "GGBR4": 4, "ARA": 2}     # as grades que a tela desenha (as do arquivo)


def _dss(key, label, sector, cadence, state, last, nxt, *, auto=True, text="on schedule (10d)",
         checked="2026-11-11T19:46:12.123456+00:00"):
    return {"key": key, "label": label, "client_desc": f"{label}: monthly data from the source.",
            "client_cadence": "Monthly · first ~10 days" if cadence == "monthly" else "Continuous",
            "sector": sector, "cadence": cadence, "how_pulled": "humano baixa o arquivo e arrasta no _inbox",
            "auto": auto, "state": state, "status_text": text, "last_period": last, "next_expected": nxt,
            "checked_at": checked, "updated_at": "2026-09-02T12:00:00+00:00"}


DATA_STATUS = [
    _dss("secex_steel", "Brazil steel foreign trade (SECEX)", "steel", "monthly", "green", "2026-10",
         "2026-11"),
    _dss("gacc", "China woodchip imports (GACC)", "pulp", "monthly", "amber", "2026-09", "2026-10",
         auto=False, text="waiting for release (41d)"),
    _dss("quotes", "Share prices", "live", "live", "green", "11/11 19:42 UTC", "contínuo",
         text="live ( 4 min ago)"),
    _dss("korea", "Korea customs (black line)", "steel", "monthly", "grey", None, None, text=None,
         checked="2026-11-11T19:40:00+00:00"),
]


def _rh(rid, ticker, day, rating, target, ccy, listing, *, note="Import BBG: comentário interno",
        source="import", upd="2026-11-06T15:07:53.123+00:00"):
    return {"id": rid, "ticker": ticker, "effective_date": day, "rating": rating,
            "target_price": target, "currency": ccy, "listing": listing, "note": note, "source": source,
            "created_at": "2026-11-06T15:00:00+00:00", "updated_at": upd, "updated_by": "uuid-admin"}


RATING_HISTORY = [
    _rh(1, "VALE", "2025-10-28", "MP", 13, "USD", "VALE"),
    _rh(2, "VALE", "2026-08-17", "OP", 18, "USD", None, source="stock_guide"),    # listing nulo → símbolo
    _rh(4, "AUGO", "2021-08-10", "MP", 26.67, "BRL", "AURA33.SA"),
    _rh(3, "AUGO", "2024-11-04", "NR", None, None, "AURA33.SA"),                  # NR: sem alvo e moeda
    _rh(5, "AUGO", "2026-11-20", "OP", 98, "USD", "AUGO", note=None, source="manual"),  # no futuro: o hub descarta
    _rh(6, "CSNA3", "2026-01-10", "UP", 8, "BRL", "CSNA3.SA"),                    # empresa oculta
    _rh(7, "ARA", "2026-10-16", "OP", 4.37, "CAD", "ARA.TO", upd="2026-11-05T12:00:00+00:00"),
]


def _rpc_rating_history(database, args):
    """O corpo de `get_rating_history()`: com a service key (não é admin) só devolve linhas com a
    flag `market_ratings` ligada; empresa oculta no Stock Guide fica de fora; ordem (ticker, data).
    Devolve também `note` (a tabela da RPC tem) — o `select` do exportador não pede."""
    flags = {r["key"]: r["enabled"] for r in database["rpc"]["get_dashboard_flags"]}
    if flags.get("market_ratings") is not True:
        return []
    cos = {c["ticker"]: c for c in database["stock_guide_companies"]}
    out = []
    for r in database["stock_guide_rating_history"]:
        c = cos.get(r["ticker"])
        if c is None or c["is_hidden"]:
            continue
        out.append({"ticker": r["ticker"], "company_name": c["company_name"],
                    "yahoo_symbol": c["yahoo_symbol"], "effective_date": r["effective_date"],
                    "rating": r["rating"], "target_price": r["target_price"],
                    "currency": r["currency"], "listing": r["listing"], "note": r["note"]})
    return sorted(out, key=lambda r: (r["ticker"], r["effective_date"]))


_SG_MASKED = ("sector", "volume_unit", "yahoo_symbol", "shares_outstanding", "net_debt_y1", "net_debt_y2",
              "last_update", "target_price", "recommendation", "ebitda_y1", "ebitda_y2", "net_income_y1",
              "net_income_y2", "dividends_y1", "dividends_y2", "model_url", "cash_earnings_y1",
              "cash_earnings_y2", "ocf_y1", "ocf_y2", "capex_y1", "capex_y2", "net_revenues_y1",
              "net_revenues_y2", "ev_adjustment_y1", "ev_adjustment_y2", "fx_to_base", "fx_to_usd",
              "base_ccy", "trade_ccy", "volumes_y1", "volumes_y2", "fcfe_y1", "fcfe_y2")


def _rpc_sg_comps(database, args):
    """O corpo de `get_stock_guide_comps()` para quem NÃO é admin (a chave de serviço): a oculta vem só
    com nome, visibilidade e ordem; por (display_order, ticker)."""
    out = []
    for c in database["stock_guide_companies"]:
        row = {k: v for k, v in c.items() if k not in ("is_hidden", "updated_by", "updated_at")}
        if not c.get("is_visible"):
            row.update({k: None for k in _SG_MASKED})
        out.append(row)
    return sorted(out, key=lambda r: (r.get("display_order") or 0, r["ticker"]))


def _rpc_sg_config(database, args):
    return [{k: c[k] for k in ("y1_label", "y2_label", "assumptions_note", "comps_footnote")}
            for c in database["stock_guide_config"] if c.get("id") == 1]


def _rpc_sg_peers(database, args):
    return sorted(({k: v for k, v in p.items() if k != "updated_at"} for p in database["stock_guide_global_peers"]),
                  key=lambda p: (p["display_order"], p["company"]))


def _rpc_sg_sens(database, args):
    """`get_stock_guide_sensitivity_tables()` de cliente: tira a linha de empresa oculta; por
    (display_order, id). Devolve o exemplo antigo também (o filtro `kind` é de quem pede)."""
    hidden = {c["ticker"] for c in database["stock_guide_companies"] if not c.get("is_visible")}
    rows = [{k: s[k] for k in ("id", "title", "value_mode", "metric_label", "unit", "companies",
                               "definition", "display_order")}
            for s in database["stock_guide_sensitivities"] if not set(s["companies"]) <= hidden]
    return sorted(rows, key=lambda s: (s["display_order"], s["id"]))


def _rpc_data_status(database, args):
    rank = {"steel": 1, "pulp": 2}
    return sorted(database["data_source_status"], key=lambda r: (rank.get(r["sector"], 3), r["label"]))


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


# ── Onda 2 (WP2.4B): Market Watch ─────────────────────────────────────────────────────────────
def _mwc(ticker, name, cls, lending, symbol, sector, order, visible=True):
    return {"ticker": ticker, "name": name, "cnpj": "00.000.000/0001-91", "cvm_code": "009999",
            "b3_code": ticker[:4], "share_class": cls, "lending_tickers": lending,
            "yahoo_symbol": symbol, "sector": sector, "is_visible": visible, "display_order": order,
            "updated_at": "2026-09-02T22:06:16.449534+00:00"}


MW_COMPANIES = [                                   # fora de ordem: a RPC ordena por display_order
    _mwc("AURA33", "Aura Minerals", "BDR", ["AURA33"], "AURA33.SA", "gold", 90),
    _mwc("VALE3", "Vale", "ON", ["VALE3"], "VALE3.SA", "iron_ore", 10),
    _mwc("KLBN11", "Klabin", "UNT", ["KLBN3", "KLBN4", "KLBN11"], "KLBN11.SA", "pulp_paper", 60),
    _mwc("BRAP4", "Bradespar", "PN", ["BRAP3", "BRAP4"], "BRAP4.SA", "iron_ore", 25),
    _mwc("CSNA3", "CSN", "ON", ["CSNA3"], "CSNA3.SA", "steel", 30, visible=False),     # OCULTA
    _mwc("USIM5", "Usiminas", "PNA", ["USIM3", "USIM5", "USIM6"], "USIM5.SA", "steel", 50),
    _mwc("RANI3", "Irani", "ON", ["RANI3"], "RANI3.SA", "pulp_paper", 80),
]
MW_VISIBLE = ["VALE3", "BRAP4", "USIM5", "KLBN11", "RANI3", "AURA33"]      # a ordem da RPC
MW_HIDDEN = "CSNA3"


def _cap(co, on, pn, f_on, f_pn, *, total=None, f_total=None, by=None, pct=None):
    return {"company": co, "ref_date": "2026-12-31", "doc_version": 3, "shares_on": on,
            "shares_pn": pn, "shares_total": total if total is not None else (on or 0) + (pn or 0),
            "float_on": f_on, "float_pn": f_pn,
            "float_total": f_total if f_total is not None else (f_on or 0) + (f_pn or 0),
            "pct_float_total": pct, "float_by_class": by or {},
            "updated_at": "2026-09-02T12:00:00+00:00"}


MW_CAPITAL = [                                     # RANI3 não tem
    _cap("VALE3", 4539007580, None, 4268000000, None, pct=94.03),
    _cap("BRAP4", 122523049, 236003738, 2000000, 230000000, pct=64.7),
    _cap("CSNA3", 1326093947, None, 727421210, None, pct=54.85),
    _cap("USIM5", 705260684, 547690000, 300000000, 518315018,
         by={"PNB": 66261, "PNA": 518248757}, pct=65.0),          # fora de ordem: sai A→Z
    _cap("KLBN11", 2198000000, 3889000000, 1100000000, 3800000000, pct=80.5),
    _cap("AURA33", None, None, None, None, total=74000000, f_total=30000000, pct=40.5),
]

# o aluguel: semanal (às quintas) de 16/07 a 29/10 + 02/11/2026, o último pregão gravado
MW_LENDING_DAYS = ["2026-07-16", "2026-07-23", "2026-07-30", "2026-08-06", "2026-08-13",
                   "2026-08-20", "2026-08-27", "2026-09-03", "2026-09-10", "2026-09-17",
                   "2026-09-24", "2026-10-01", "2026-10-08", "2026-10-15", "2026-10-22",
                   "2026-10-29", "2026-11-02"]
MW_LENDING_TICKERS = [("VALE3", "VALE3", 150_000_000), ("BRAP3", "BRAP4", 600_000),
                      ("BRAP4", "BRAP4", 30_000_000), ("USIM3", "USIM5", 1_200_000),
                      ("USIM5", "USIM5", 70_000_000), ("USIM6", "USIM5", 3),
                      ("KLBN3", "KLBN11", 4_000_000), ("KLBN4", "KLBN11", 9_000_000),
                      ("KLBN11", "KLBN11", 160_000_000), ("RANI3", "RANI3", 5_000_000),
                      ("AURA33", "AURA33", 1_900_000), ("CSNA3", "CSNA3", 114_000_000)]


def mw_lending_row(ticker, company, day, k, base, *, qty=None):
    q = qty if qty is not None else int(base * (1 + 0.01 * ((k * 7 + len(ticker)) % 13)))
    price = round(10 + len(company) * 1.5 + k * 0.1, 2)
    return {"ticker": ticker, "company": company, "ref_date": day, "qty_total": q,
            "value_brl": round(q * price, 2), "avg_price": price, "qty_registro": q // 2,
            "qty_d0": q // 4, "qty_d1": q - q // 2 - q // 4,
            "rate_taker_avg": round(0.05 + 0.01 * ((k + len(ticker)) % 9), 4),
            "rate_taker_min": 0.01, "rate_taker_max": round(1 + 0.1 * len(ticker), 2),
            "rate_donor_avg": round(0.04 + 0.01 * ((k + len(ticker)) % 7), 4),
            "contracts_day": 10 + k, "qty_day": q // 100, "value_day": round(q // 100 * price, 2),
            "source": "bdi_pdf" if day < "2026-08-01" else "bdi_api",
            "updated_at": f"{day}T21:30:00.123+00:00"}


def _mw_lending() -> list[dict]:
    rows = []
    for t, co, base in MW_LENDING_TICKERS:
        days = MW_LENDING_DAYS[-3:] if t == "USIM6" else MW_LENDING_DAYS
        for k, d in enumerate(days):
            rows.append(mw_lending_row(t, co, d, k, base))
    return rows


MW_LENDING = _mw_lending()


def _bb(pid, co, decided, expires, status, operation, purpose, q_on, q_pn, c_on, c_pn, *,
        reason=None, usd=None, brokers=()):
    return {"program_id": pid, "company": co, "company_name": "RAZAO SOCIAL INTERNA S.A.",
            "decided_on": decided, "expires_on": expires, "status": status, "operation": operation,
            "reason": reason, "purpose": purpose, "qty_on": q_on, "qty_pn": q_pn, "qty_circ_on": c_on,
            "qty_circ_pn": c_pn, "authorized_usd": usd, "brokers": list(brokers),
            "updated_at": "2026-09-03T12:10:00.5+00:00"}


MW_BUYBACKS = [
    _bb(1500, "VALE3", "2024-01-10", "2025-07-10", "Encerrado", "Compra",
        "AS AÇÕES VÃO FICAR EM TESOURARIA.", 50000000, None, 4400000000, None),
    _bb(1973, "VALE3", "2026-04-30", "2027-10-29", "Em Andamento", "Compra",
        "AS AÇÕES PODERÃO SER CANCELADAS.", 100000000, None, 4300000000, None,
        reason="Maximizar o valor ao acionista. Dúvidas: ri@vale-exemplo.com.br ou (21) 3485-0000.",
        brokers=["BTG PACTUAL CTVM S.A.", " J.P. MORGAN CCVM S.A. ", ""]),
    _bb(1800, "BRAP4", "2025-06-01", "2027-01-01", "Encerrado", "Compra", None,       # o status manda
        None, 5000000, None, 230000000),
    _bb(1900, "KLBN11", "2026-03-01", "2027-03-01", None, None,                     # sem status: o prazo
        "AS AÇÕES PODERÃO SER CANCELADAS.", 31250000, 125000000, 1100000000, 3800000000),
    _bb(-1, "AURA33", "2026-05-18", "2027-05-18", "Em Andamento", None,             # em US$, à mão
        "PARA MANUTENCAO EM TESOURARIA, POSTERIOR CANCELAMENTO OU ALIENACAO", None, None, None, None,
        usd=200000000),
    _bb(1700, "USIM5", None, None, "Encerrado", "Venda", None, 1000, 2000, None, None),  # sem data: no fim
    _bb(1600, "CSNA3", "2026-08-01", "2027-08-01", "Em Andamento", "Compra",           # empresa OCULTA
        "AS AÇÕES PODERÃO SER CANCELADAS.", 30000000, None, 700000000, None),
]

MW_GROUPS = ["Controlador ou Vinculado", "Conselho de Administração ou Vinculado",
             "Diretor ou Vinculado", "Conselho Fiscal ou Vinculado", "Órgão Estatutário ou Vinculado"]
MW_ENTITY = {"VALE3": "VALE S.A.", "BRAP4": "BRADESPAR S.A.", "USIM5": "USINAS SID MINAS GERAIS S.A.",
             "KLBN11": "KLABIN S.A.", "RANI3": "IRANI PAPEL E EMBALAGEM S.A.", "CSNA3": "CIA SIDERURGICA NAC"}
# 14 meses no banco (ago/2025 a set/2026); a janela do "agora" (03/11/2026) começa em out/2025
MW_MONTHS = [f"2025-{m:02d}-01" for m in range(8, 13)] + [f"2026-{m:02d}-01" for m in range(1, 10)]


def _mid(*parts) -> str:
    import hashlib
    return hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()[:24]


def mw_insider(co, month, group, move, *, qty=None, price=None, day=None, desc=None, asset="ON",
               version=1, broker=None, seq=0, entity_type="Companhia", upd=None):
    trade = "compra" in move.lower() or "venda" in move.lower()
    return {"id": _mid(co, month, version, group, move, asset, seq), "company": co, "ref_month": month,
            "doc_version": version, "entity_type": entity_type, "entity": MW_ENTITY.get(co),
            "group_type": group, "move_type": move, "move_desc": desc,
            "operation": "Débito" if "venda" in move.lower() or "saída" in move.lower() else "Crédito",
            "asset_type": "Ações", "asset_class": asset, "broker": broker, "move_date": day,
            "qty": qty, "unit_price": price if trade else None,
            "volume": round(qty * price, 2) if trade and qty and price else None,
            "is_balance": move.lower().startswith("saldo"),
            "updated_at": upd or f"{month[:7]}-25T12:00:00.25+00:00"}


def _mw_insiders() -> list[dict]:
    rows = []
    for mi, month in enumerate(MW_MONTHS):
        for ci, co in enumerate(("VALE3", "BRAP4", "USIM5", "KLBN11", "RANI3", "CSNA3")):
            for gi, group in enumerate(MW_GROUPS):
                for asset in ("ON", "PN", None):
                    rows.append(mw_insider(co, month, group, "Saldo Inicial", asset=asset,
                                           qty=1000 * (gi + 1) * (ci + 1) + mi))
            for k, move in enumerate(("Outras Entradas", "Outras Saídas")):
                rows.append(mw_insider(co, month, MW_GROUPS[2], move, day=f"{month[:8]}1{k}",
                                       qty=10 + mi, seq=k))
    rows += [
        # VALE3: dois negócios nos 12 meses (+1.000 −400) e um em out/2025 (na janela de 14 meses,
        # fora dos 12)
        mw_insider("VALE3", "2026-09-01", "Diretor ou Vinculado", "Compra à vista", qty=1000,
                   price=60.1, day="2026-09-12", broker="XP INVESTIMENTOS CCTVM S.A.",
                   desc="Compra em bolsa. Contato: ri@vale-exemplo.com.br, tel. (21) 3485-0000",
                   upd="2026-10-19T09:00:00.75+00:00"),
        mw_insider("VALE3", "2026-08-01", "Conselho de Administração ou Vinculado", "Venda à vista",
                   qty=400, price=58.0, day="2026-08-20", desc="Operação liquidada em 20082026"),
        mw_insider("VALE3", "2025-10-01", "Diretor ou Vinculado", "Compra à vista", qty=9999,
                   price=55.0, day="2025-10-15"),
        # KLBN11: venda do controlador e o "Exercício opção de venda" (casa /venda/: venda no site)
        mw_insider("KLBN11", "2026-07-01", "Controlador ou Vinculado", "Venda à vista", qty=5000,
                   price=20.0, day="2026-07-14", asset="KLBN11"),
        mw_insider("KLBN11", "2026-06-01", "Diretor ou Vinculado", "Exercício opção de venda",
                   qty=100, price=19.0, day="2026-06-30", asset="KLBN11"),
        # BRAP4: "Compra" (sem "à vista") também é negócio; group_type nulo (existe no real)
        mw_insider("BRAP4", "2026-05-01", None, "Compra", qty=50, price=21.5, day="2026-05-06",
                   asset="PN"),
        # empresa OCULTA: nunca sai
        mw_insider("CSNA3", "2026-09-01", "Diretor ou Vinculado", "Compra à vista", qty=777,
                   price=13.0, day="2026-09-02"),
    ]
    return rows


MW_INSIDERS = _mw_insiders()
ENET = "https://www.rad.cvm.gov.br/ENET/frmExibirArquivoIPEExterno.aspx?ID={}&flnk"


def _fl(fid, co, headline, category, doc_date, published, *, url="enet", newsworthy=False,
        flag=None, title=None, excerpt=None, upd=None):
    return {"id": fid, "company": co, "b3_code": co[:4], "headline": headline, "category": category,
            "doc_date": doc_date, "published_at": published, "flag": flag,
            "cvm_url": ENET.format(fid - 2000000) if url == "enet" else url,
            "is_newsworthy": newsworthy, "doc_title": title, "doc_excerpt": excerpt,
            "updated_at": upd or published[:10] + "T23:00:00.5+00:00"}


MW_FILINGS = [
    _fl(3545500, "VALE3", "VALE (VALE-NM) - Apresentacao a analistas/agentes mercado - 26/10/26",
        "Apresentacao a analistas/agentes mercado", "2026-10-26", "2026-10-26T18:00:00-03:00"),
    _fl(3546251, "VALE3", "VALE (VALE-NM) - Fato Relevante - 02/11/26", "Fato Relevante",
        "2026-11-02", "2026-11-02T19:32:00-03:00", newsworthy=True,
        title="Vale informa sobre o projeto Serra Norte",
        excerpt=("Vale informa sobre o projeto Serra Norte, aprovado para o período 2026-2030. "
                 "Relações com Investidores: ri@vale-exemplo.com.br | Tel.: +55 (21) 3485-0000 | "
                 "Fax.: 55 21 3485.0001 | CNPJ 33.592.510/0001-54 | NIRE 33.300.019.766"),
        upd="2026-11-03T01:10:00.5+00:00"),
    _fl(3546100, "KLBN11", "KLABIN S.A. (KLBN) - Outros Comunicados ao Mercado - 01/11/26",
        "Outros Comunicados ao Mercado", "2026-11-01", "2026-11-01T10:05:00-03:00", url=None,
        newsworthy=True, excerpt="Safra 2025-2026: 1.234.567 toneladas; a data-base é 06.11.2026."),
    _fl(3546050, "USIM5", "USIMINAS (USIM-N1) - Ata Reuniao do Conselho de Administracao - 30/10/26",
        "Ata Reuniao do Conselho de Administracao", "2026-10-30", "2026-10-30T18:00:00-03:00",
        url="https://www.rad.cvm.gov.br/ENET/frmExibirArquivoIPEExterno.aspx?ID=1&token=SEGREDO"),
    _fl(3545900, "BRAP4", "BRADESPAR (BRAP-N1) - Polit. Divulgacao Ato ou Fato Relevante - 29/10/26 (R)",
        "Polit. Divulgacao Ato ou Fato Relevante", "2026-10-29", "2026-10-29T09:00:00-03:00",
        flag="R", newsworthy=True),
    _fl(3545800, "AURA33", "AURA MINERALS (AURA) - Aviso aos Acionistas - 28/10/26",
        "Aviso aos Acionistas", "2026-10-28", "2026-10-28T20:00:00-03:00", newsworthy=True,
        title="Aura anuncia dividendos",
        excerpt="Aura anuncia dividendos de US$ 0,25 por ação. Dúvidas: ri@aura-exemplo.co"),
    _fl(3545700, "RANI3", "IRANI (RANI-NM) - Dados Financeiros - Press-release - 27/10/26",
        "Dados Financeiros - Press-release", "2026-10-27", "2026-10-27T19:00:00-03:00",
        newsworthy=True,
        excerpt=("Receita de R$ 1.234.567,89 no 3T26. SAC 0800 891 0000. Sede: Rua Exemplo, 100, "
                 "CEP 90010-150. NIRE 43300002578.")),
    _fl(3545600, "CSNA3", "CSN (CSNA-N1) - Fato Relevante - 27/10/26", "Fato Relevante",   # OCULTA
        "2026-10-27", "2026-10-27T12:00:00-03:00", newsworthy=True),
]


MW_FAMILY_FILES = [f"mw-short-{co.lower()}.json" for co in MW_VISIBLE]
MW_FIXED = ["mw-companies.json", "mw-short-interest.json", "mw-buybacks.json", "mw-insiders.json",
            "mw-filings.json"]


def no_mw_cap(monkeypatch) -> None:
    """Desliga o teto de envios por execução do grupo `mw` (registry.GROUP_CAPS). Os testes das
    Ondas 1a/1b/2.1B andam ciclo a ciclo com TODOS os arquivos; o teto espalharia os 11 do Market
    Watch por vários ciclos, o que eles não examinam — o teto tem testes próprios
    (test_hub_export_mw.py)."""
    monkeypatch.setattr("hub_export.run.GROUP_CAPS", {})


def _mw_visible(database) -> set:
    return {c["ticker"] for c in database["mw_companies"] if c["is_visible"]}


def _rpc_mw_companies(database, args):
    return sorted((c for c in database["mw_companies"] if c["is_visible"]),
                  key=lambda c: (c["display_order"], c["ticker"]))


def _rpc_mw_short_interest(database, args):
    vis, co, frm = _mw_visible(database), args.get("p_company"), args.get("p_from")
    rows = [r for r in database["mw_short_interest"] if r["company"] in vis
            and (co is None or r["company"] == co) and (frm is None or r["ref_date"] >= frm)]
    return sorted(rows, key=lambda r: (r["ref_date"], r["ticker"]))


def _rpc_mw_short_latest(database, args):
    """O corpo de `get_mw_short_latest()`: o último dia de cada papel visível, a posição do último
    pregão ≤ 30 dias antes e quantos dias o papel tem na tabela."""
    from datetime import date as _d, timedelta as _td
    vis = _mw_visible(database)
    by: dict = {}
    for r in database["mw_short_interest"]:
        by.setdefault(r["ticker"], []).append(r)
    out = []
    for t, rows in by.items():
        rows = sorted(rows, key=lambda r: r["ref_date"])
        last = rows[-1]
        if last["company"] not in vis:
            continue
        lim = (_d.fromisoformat(last["ref_date"]) - _td(days=30)).isoformat()
        m1 = [r for r in rows if r["ref_date"] <= lim]
        out.append({"ticker": t, "company": last["company"], "ref_date": last["ref_date"],
                    "qty_total": last["qty_total"], "value_brl": last["value_brl"],
                    "avg_price": last["avg_price"], "rate_taker_avg": last["rate_taker_avg"],
                    "qty_1m": m1[-1]["qty_total"] if m1 else None,
                    "rate_1m": m1[-1]["rate_taker_avg"] if m1 else None, "days": len(rows)})
    return sorted(out, key=lambda r: (r["company"], r["ticker"]))


def _rpc_mw_share_capital(database, args):
    vis = _mw_visible(database)
    return [k for k in database["mw_share_capital"] if k["company"] in vis]


def _rpc_mw_buybacks(database, args):
    vis, co = _mw_visible(database), args.get("p_company")
    rows = [b for b in database["mw_buyback_programs"] if b["company"] in vis
            and (co is None or b["company"] == co)]
    rows.sort(key=lambda b: b["program_id"], reverse=True)
    rows.sort(key=lambda b: (b["decided_on"] is not None, b["decided_on"] or ""), reverse=True)
    return rows


def _rpc_mw_insiders(database, args):
    vis, co, frm = _mw_visible(database), args.get("p_company"), args.get("p_from")
    rows = [m for m in database["mw_insider_moves"] if m["company"] in vis
            and (co is None or m["company"] == co) and (frm is None or m["ref_month"] >= frm)]
    rows.sort(key=lambda m: m["id"])
    rows.sort(key=lambda m: (m["group_type"] is None, m["group_type"] or ""))
    rows.sort(key=lambda m: (m["move_date"] is not None, m["move_date"] or ""), reverse=True)
    rows.sort(key=lambda m: m["ref_month"], reverse=True)
    return rows


def _rpc_mw_filings(database, args):
    vis, co = _mw_visible(database), args.get("p_company")
    lim = max(1, min(int(args.get("p_limit") or 200), 2000))
    rows = [f for f in database["mw_filings"] if f["company"] in vis
            and (co is None or f["company"] == co)]
    rows.sort(key=lambda f: _ts(f["published_at"], "published_at"), reverse=True)
    return rows[:lim]


def db() -> dict:
    out = _db_small()
    # as tabelas grandes do Market Watch têm só valores escalares por linha: a cópia rasa de cada
    # linha basta (o deepcopy de ~1.400 linhas custava 60 ms por banco falso, centenas de vezes)
    out["mw_short_interest"] = [dict(r) for r in MW_LENDING]
    out["mw_insider_moves"] = [dict(r) for r in MW_INSIDERS]
    return out


def _db_small() -> dict:
    return copy.deepcopy({"quotes": QUOTES, "macro_indicators": MACRO, "commodities": COMMODITIES,
                          "news_articles": NEWS, "news_digest": DIGEST,
                          "exec_calendar_events": CAL_EVENTS,
                          "exec_calendar_categories": CAL_CATEGORIES,
                          "exec_calendar_data_windows": CAL_WINDOWS,
                          "quote_history": [],          # vazia: quem testa a família põe quote_history()
                          "stock_guide_rating_history": RATING_HISTORY,
                          "stock_guide_companies": SG_COMPANIES,
                          "stock_guide_config": SG_CONFIG,
                          "stock_guide_global_peers": SG_PEERS,
                          "stock_guide_sensitivities": SG_SENS,
                          "data_source_status": DATA_STATUS,
                          "mw_companies": MW_COMPANIES, "mw_share_capital": MW_CAPITAL,
                          "mw_buyback_programs": MW_BUYBACKS, "mw_filings": MW_FILINGS,
                          "rpc_rows": {"reports": REPORTS},
                          "rpc": {"get_dashboard_flags": FLAGS,
                                  "get_heatmap_tickers": HEATMAP_ADMIN,
                                  "get_commodity_groups": COMMODITY_GROUPS,
                                  "get_recent_reports_v3": _rpc_reports,
                                  "get_exec_calendar_categories": _rpc_categories,
                                  "get_exec_calendar_data_windows": _rpc_windows,
                                  "get_exec_calendar_events": _rpc_events,
                                  "get_rating_history": _rpc_rating_history,
                                  "get_stock_guide_comps": _rpc_sg_comps,
                                  "get_stock_guide_config": _rpc_sg_config,
                                  "get_stock_guide_global_peers": _rpc_sg_peers,
                                  "get_stock_guide_sensitivity_tables": _rpc_sg_sens,
                                  "get_data_source_status": _rpc_data_status,
                                  "get_mw_companies": _rpc_mw_companies,
                                  "get_mw_short_interest": _rpc_mw_short_interest,
                                  "get_mw_short_latest": _rpc_mw_short_latest,
                                  "get_mw_share_capital": _rpc_mw_share_capital,
                                  "get_mw_buybacks": _rpc_mw_buybacks,
                                  "get_mw_insiders": _rpc_mw_insiders,
                                  "get_mw_filings": _rpc_mw_filings}})


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


# ── Onda 3: os bancos dos setores (SQLite do repositório do site) ──────────────────────────────
SECTOR_SAMPLES = Path(__file__).resolve().parents[1] / "hub_export" / "samples" / "sectors"
SECTOR_FILE = {SE.MM: "steel_sm_web.db.gz", SE.PP: "pulp_paper.db"}
SECTOR_COMMITS = {SE.MM: (("c0ffee" * 7)[:40], "2026-11-06T18:51:02Z"),
                  SE.PP: (("beef01" * 7)[:40], "2026-11-06T23:15:29Z")}
GH_TOKEN = "ghs_TOKEN-FALSO-NAO-PODE-VAZAR-123"

# os esquemas como estão nos bancos de verdade (`sqlite_master`, f636222)
STEEL_DDL = [
    """CREATE TABLE iabr_production (
        period     TEXT PRIMARY KEY,
        year       INTEGER, month INTEGER,
        crude_steel REAL, flat REAL, long_prod REAL,
        semi REAL, slabs REAL, billets REAL, pig_iron REAL,
        updated_at TEXT
    )""",
    """CREATE TABLE iabr_consumption (
        period TEXT PRIMARY KEY,
        year INTEGER, month INTEGER,
        total REAL, flat REAL, long_prod REAL,
        updated_at TEXT
    )""",
    """CREATE TABLE iabr_exports (
        period TEXT PRIMARY KEY,
        year INTEGER, month INTEGER,
        flat_ktons REAL, long_ktons REAL, semi_ktons REAL,
        total_ktons REAL, total_usd_mn REAL,
        updated_at TEXT
    )""",
    """CREATE TABLE iabr_imports (
        period TEXT PRIMARY KEY,
        year INTEGER, month INTEGER,
        flat_ktons REAL, long_ktons REAL, semi_ktons REAL,
        total_ktons REAL, total_usd_mn REAL,
        updated_at TEXT
    )""",
    """CREATE TABLE iabr_domestic_sales (
        period TEXT PRIMARY KEY,
        year INTEGER, month INTEGER,
        total REAL, flat REAL, long_prod REAL, semi REAL,
        updated_at TEXT
    , slabs REAL, billets REAL)""",
    """CREATE TABLE secex_exports (
        period   TEXT,
        category TEXT,
        revenue_usd_mn REAL, volume_ktons REAL,
        price_usd_ton  REAL, working_days INTEGER,
        yoy_revenue REAL,   yoy_volume REAL,
        updated_at TEXT,
        PRIMARY KEY (period, category)
    )""",
    """CREATE TABLE secex_imports (
        period   TEXT,
        category TEXT,
        revenue_usd_mn REAL, volume_ktons REAL,
        price_usd_ton  REAL, working_days INTEGER,
        yoy_revenue REAL,   yoy_volume REAL,
        updated_at TEXT,
        PRIMARY KEY (period, category)
    )""",
    """CREATE TABLE inda_distribution (
        period TEXT PRIMARY KEY,
        year INTEGER, month INTEGER,
        inventories REAL, inv_months REAL,
        purchases REAL, sales REAL,
        working_days INTEGER, inv_yoy REAL,
        hist_avg REAL, sales_ltm REAL, sales_ma3 REAL,
        updated_at TEXT
    )""",
    """CREATE TABLE import_prediction (
        period  TEXT,
        product TEXT,
        country TEXT,
        value_usd REAL,
        volume_kg REAL,
        updated_at TEXT,
        PRIMARY KEY (period, product, country)
    )""",
    """CREATE TABLE pred_exports (
        period     TEXT,
        country    TEXT,
        product    TEXT,
        value_usd  REAL,
        volume_kg  REAL,
        updated_at TEXT,
        PRIMARY KEY (period, country, product)
    )""",
    'CREATE TABLE "secex_country" ("period" TEXT, "direction" TEXT, "category" TEXT, "country" TEXT, '
    '"volume_ktons" REAL, "revenue_usd_mn" REAL, "updated_at" TEXT, PRIMARY KEY ("direction", '
    '"category", "period", "country")) WITHOUT ROWID',
]
_IBA_PAPER_COLS = ([f"{f}_{g}" for f in ("prod", "dom", "exp", "imp")
                    for g in ("total", "packaging", "pw", "newsprint", "cardboard", "other")]
                   + ["app_cons", "exprev_total", "exprev_latam", "exprev_europe", "exprev_namerica",
                      "exprev_africa", "exprev_asia", "exprev_china"])
_IBA_PULP_COLS = ["prod_total", "prod_hw", "prod_sw", "prod_hy", "dom_total", "dom_hw", "dom_sw",
                  "dom_hy", "exp_total", "exp_hw", "exp_sw", "exp_hy", "imp_total", "imp_hw", "imp_sw",
                  "imp_hy", "app_cons", "exprev_total", "exprev_latam", "exprev_europe",
                  "exprev_namerica", "exprev_africa", "exprev_asia", "exprev_china", "imprev_total"]
_COMPANY_Q_COLS = ["klabin_packaging", "klabin_corr_mi", "klabin_corr_me", "klabin_sacks_mi",
                   "klabin_sacks_me", "klabin_cardboard", "klabin_card_mi", "klabin_card_me",
                   "suzano_total", "suzano_pw", "suzano_pw_mi", "suzano_pw_me", "suzano_cardboard",
                   "suzano_card_mi", "suzano_card_me", "suzano_tissue", "iba_packaging", "iba_pw",
                   "iba_cardboard", "iba_tissue"]
PULP_DDL = [
    "CREATE TABLE iba_paper (period TEXT, year INT, month INT, "
    + ", ".join(f"{c} REAL" for c in _IBA_PAPER_COLS) + ")",
    "CREATE TABLE iba_pulp (period TEXT, year INT, month INT, "
    + ", ".join(f"{c} REAL" for c in _IBA_PULP_COLS) + ")",
    "CREATE TABLE secex_pulp_port (period TEXT, year INT, month INT, port TEXT, volume_ktons REAL, "
    "revenue_usd_mn REAL)",
    "CREATE TABLE company_q (quarter TEXT, year INT, q INT, "
    + ", ".join(f"{c} REAL" for c in _COMPANY_Q_COLS) + ")",
    "CREATE TABLE calendar (period TEXT, year INT, month INT, working_days INT)",
    "CREATE TABLE empapel (period TEXT, year INT, month INT, shipments_kton REAL, working_days REAL, "
    "exp_per_day REAL, ltm_shipments REAL)",
    "CREATE TABLE gacc_woodchips (period TEXT, year INT, month INT, fibre TEXT, country TEXT, "
    "volume_bdmt REAL, revenue_usd_mn REAL)",
    """CREATE TABLE secex_pulp_country (
        period TEXT NOT NULL, year INT, month INT, country TEXT NOT NULL, product TEXT NOT NULL,
        volume_ktons REAL, revenue_usd_mn REAL,
        PRIMARY KEY (period, country, product)) WITHOUT ROWID""",
]

# os meses: índice 0 = 2022-01
SECTOR_FROM = "2022-01"


def mon(i: int) -> str:
    return f"{2022 + i // 12}-{i % 12 + 1:02d}"


def _num(*key, scale=1000.0, places=4) -> float:
    """Um número determinístico (o mesmo em toda máquina) a partir da chave."""
    h = int(hashlib.sha1(repr(key).encode("utf-8")).hexdigest()[:8], 16)
    return round((h % 900_000 + 100_000) / 1_000_000 * scale, places)


# secex_country: (direção, categoria FOLHA, país, meses com linha). Os pais (flat, long, semi) são a
# soma das folhas por (direção, mês, país), como no banco de verdade.
COUNTRY_SERIES = [
    ("imp", "hrc", "China", range(18)),
    ("imp", "hrc", "Coreia do Sul", [0, 1, 2, 3, 7, 8, 9, 14, 15, 16, 17]),   # 3 vazios / 4 vazios
    ("imp", "crc", "Alemanha", [0, 1, 2, 4, 5]),                               # mês 2 = 0; 3 sem linha
    ("imp", "coated", "Japão", [16, 17]),
    ("imp", "rebar", "China", range(0, 18, 2)),
    ("imp", "bar", "Índia", [9]),
    ("imp", "placa", "China", range(12, 18)),
    ("exp", "hrc", "Estados Unidos", range(18)),
    ("exp", "heavy_plate", "Estados Unidos", [3, 4, 5]),
    ("exp", "coated", "Japão", [5]),                                           # valor minúsculo
    ("exp", "wire_rod", "Áustria", [0, 17]),
    ("exp", "shapes", "China", range(6, 12)),
    ("exp", "ingot_billet", "Estados Unidos", range(10, 18)),
    ("exp", "placa", "Áustria", [1, 2, 3]),
]
ZERO_ROW = ("imp", "crc", "Alemanha", 2)
TINY_ROW = ("exp", "coated", "Japão", 5)
PARENT = {c: p for p, cs in SE.COUNTRY_TABS.items() if p != "total" for c in cs if c != p}
STAMP_OLD, STAMP_NEW = "2026-10-07T18:20:13.5", "2026-11-06T18:49:41.212755"


def _steel_rows(con) -> None:
    leaves: dict = {}
    for d, cat, ctry, months in COUNTRY_SERIES:
        for i in months:
            v = 0.0 if (d, cat, ctry, i) == ZERO_ROW else (
                1e-06 if (d, cat, ctry, i) == TINY_ROW else _num(d, cat, ctry, i, scale=90.0, places=6))
            leaves[(d, cat, ctry, i)] = v
    parents: dict = {}
    for (d, cat, ctry, i), v in leaves.items():
        k = (d, PARENT[cat], ctry, i)
        parents[k] = parents.get(k, 0.0) + v
    for (d, cat, ctry, i), v in {**leaves, **parents}.items():
        con.execute('INSERT INTO "secex_country" VALUES (?,?,?,?,?,?,?)',
                    (mon(i), d, cat, ctry, v, round(v * _num(cat, scale=1.5, places=3), 6),
                     STAMP_NEW if i >= 16 else STAMP_OLD))
    for table, stamp_ in (("secex_imports", "2026-11-06T18:49:41.212755"),
                          ("secex_exports", "2026-11-06T18:49:40.1")):
        vols: dict = {}
        for cat in ("total", "flat", "long", "semi", "hrc", "rebar"):
            for i in range(18):
                if table == "secex_imports" and ((cat == "rebar" and 3 <= i <= 5)
                                                 or (cat == "semi" and i == 17)):
                    continue                                  # mês sem linha (o eixo comum leva null)
                vol = _num(table, cat, i, scale=900.0)
                rev = round(vol * _num(table, cat, scale=1.2, places=3), 4)
                vols[(cat, i)] = (vol, rev)
                prev = vols.get((cat, i - 12))
                yoy_v = round(vol / prev[0] - 1, 4) if prev else None
                yoy_r = round(rev / prev[1] - 1, 4) if prev else None
                con.execute(f"INSERT INTO {table} VALUES (?,?,?,?,?,?,?,?,?)",
                            (mon(i), cat, rev, vol, round(rev * 1000 / vol, 2), None, yoy_r, yoy_v,
                             stamp_))
    for i in range(12, 18):                                   # IABr: 2023-01 … 2023-06
        p, y, m = mon(i), 2022 + i // 12, i % 12 + 1
        st = "2026-10-15T20:39:52" if i == 17 else "2026-10-15T20:39:51.5"
        con.execute("INSERT INTO iabr_production VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (p, y, m, *(_num("prod", c, i) for c in ("crude", "flat", "long", "semi",
                                                              "slabs", "billets", "pig")), st))
        if i < 17:                                            # o consumo para 1 mês antes
            con.execute("INSERT INTO iabr_consumption VALUES (?,?,?,?,?,?,?)",
                        (p, y, m, *(_num("cons", c, i) for c in ("total", "flat", "long")), st))
        con.execute("INSERT INTO iabr_domestic_sales (period, year, month, total, flat, long_prod, "
                    "semi, updated_at, slabs, billets) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (p, y, m, *(_num("dom", c, i) for c in ("total", "flat", "long", "semi")), st,
                     _num("dom", "slabs", i), _num("dom", "billets", i)))
        for t in ("iabr_exports", "iabr_imports"):
            con.execute(f"INSERT INTO {t} VALUES (?,?,?,?,?,?,?,?,?)",
                        (p, y, m, *(_num(t, c, i) for c in ("flat", "long", "semi", "total", "usd")),
                         st))
    for i in range(10, 18):                                   # INDA: 2022-11 … 2023-06
        con.execute("INSERT INTO inda_distribution VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (mon(i), 2022 + i // 12, i % 12 + 1, _num("inv", i),
                     _num("months", i, scale=5.0, places=1),
                     _num("buy", i), _num("sell", i), 20 + i % 3, _num("yoy", i, scale=0.5),
                     None if i == 13 else 812.4, None if i < 12 else _num("ltm", i, scale=9000.0),
                     _num("ma3", i), "2026-10-28T19:39:19.120000"))
    for i in range(9, 18):                                    # laranja: 2022-10 … 2023-06
        for prod in ("HRC", "CRC", "Coated", "Wire Rod"):
            for ctry in ("Korea", "China"):
                if (prod == "Wire Rod" and ctry == "Korea") or (prod, ctry, i) == ("HRC", "Korea", 11):
                    continue
                kg = _num("imp", prod, ctry, i, scale=4e7, places=1)
                con.execute("INSERT INTO import_prediction VALUES (?,?,?,?,?,?)",
                            (mon(i), prod, ctry, round(kg * 0.62, 1), kg,
                             STAMP_NEW if i == 17 else STAMP_OLD))
    for i in range(10, 17):                                   # preta: 2022-11 … 2023-05
        for ctry in ("Korea", "China"):
            for prod in ("HRC", "CRC", "Coated"):
                if (prod, ctry, i) == ("Coated", "Korea", 10):
                    continue                                  # a série da Coreia começa depois
                kg = _num("exp", prod, ctry, i, scale=4e7, places=1)
                con.execute("INSERT INTO pred_exports VALUES (?,?,?,?,?,?)",
                            (mon(i), ctry, prod, round(kg * 0.6, 1), kg, "2026-10-20T15:02:13.4"))


PORT_SERIES = [("Santos", range(18)), ("Paranaguá", [0, 1, 6, 7, 8]),   # 4 vazios: trecho novo
               ("Vitória", [0, 1, 2, 6, 7]), ("Barcarena", [5]), ("Rio Grande", [17])]
DEST_SERIES = [("China", "hardwood", range(18)), ("Netherlands", "hardwood", range(0, 18, 3)),
               ("United States", "softwood", range(6)), ("Italy", "ukp", [4]),
               ("Türkiye", "dissolving", [10, 11]), ("Belgium", "other", [17])]
GACC_SUPPLIERS = ("Vietnam", "Australia", "Chile", "Others")


def _pulp_rows(con) -> None:
    for port, months in PORT_SERIES:
        for i in months:
            zero = port == "Barcarena"
            v = 0.0 if zero else _num("port", port, i, scale=600.0)
            con.execute("INSERT INTO secex_pulp_port VALUES (?,?,?,?,?,?)",
                        (mon(i), 2022 + i // 12, i % 12 + 1, port, v,
                         0.0 if zero else round(v * 0.55, 4)))
    for ctry, prod, months in DEST_SERIES:
        for i in months:
            v = round(_num("dest", ctry, prod, i, scale=400.0), 3)
            con.execute("INSERT INTO secex_pulp_country VALUES (?,?,?,?,?,?,?)",
                        (mon(i), 2022 + i // 12, i % 12 + 1, ctry, prod, v, round(v * 0.52, 4)))
    for i in range(-1, 18):                                   # 2021-12 (fora do eixo) … 2023-06
        con.execute("INSERT INTO calendar VALUES (?,?,?,?)",
                    (mon(i), 2022 + i // 12, i % 12 + 1, 19 + (i % 4)))
    for i in range(-2, 6):                                    # GACC: 2021-11 … 2022-06
        for fibre in ("HW", "SW"):
            vols = {}
            for ctry in GACC_SUPPLIERS:
                zero = i < 0 or i == 5 or (fibre == "SW" and ctry == "Chile")
                vols[ctry] = (0.0, 0.0) if zero else (_num("gacc", fibre, ctry, i, scale=0.8, places=9),
                                                       _num("gaccrev", fibre, ctry, i, scale=90.0))
            vols["Total"] = (round(sum(v for v, _r in vols.values()), 9),
                             round(sum(r for _v, r in vols.values()), 4))
            for ctry in sorted(vols):                         # o robô grava em ordem alfabética
                con.execute("INSERT INTO gacc_woodchips VALUES (?,?,?,?,?,?,?)",
                            (mon(i), 2022 + i // 12, i % 12 + 1, fibre, ctry, *vols[ctry]))
    for i in range(7, 13):                                    # IBÁ papel: 2022-08 … 2023-01
        vals = [None if (c.startswith("exprev_") and i < 9) else _num("iba", c, i)
                for c in _IBA_PAPER_COLS]
        con.execute(f"INSERT INTO iba_paper VALUES (?,?,?,{','.join('?' * len(vals))})",
                    (mon(i), 2022 + i // 12, i % 12 + 1, *vals))
    for i in range(0, 3):                                     # IBÁ celulose (morto na tela)
        vals = [None if c.endswith("_hy") else _num("pulp", c, i) for c in _IBA_PULP_COLS]
        con.execute(f"INSERT INTO iba_pulp VALUES (?,?,?,{','.join('?' * len(vals))})",
                    (mon(i), 2022, i + 1, *vals))
    for i in range(4, 14):                                    # Empapel: 2022-05 … 2023-02
        ship = _num("empapel", i, scale=350.0, places=6)
        days = 24.0 + i % 3
        con.execute("INSERT INTO empapel VALUES (?,?,?,?,?,?,?)",
                    (mon(i), 2022 + i // 12, i % 12 + 1, ship, days, round(ship / days, 6),
                     _num("ltm", i, scale=4000.0, places=6)))
    for q in (1, 2):
        con.execute(f"INSERT INTO company_q VALUES (?,?,?,{','.join('?' * len(_COMPANY_Q_COLS))})",
                    (f"{q}T22", 2022, q, *(_num("cq", c, q) for c in _COMPANY_Q_COLS)))


def build_sector_db(path, sector: str, mutate=None) -> None:
    """Monta o banco de fixture do setor em `path` (página de 1 KB, compactado). `mutate(con)` roda
    no fim — é como os testes tiram uma coluna ou repetem uma linha."""
    path = Path(path)
    if path.exists():
        path.unlink()
    con = sqlite3.connect(path)
    con.execute("PRAGMA page_size = 1024")
    for ddl in (STEEL_DDL if sector == SE.MM else PULP_DDL):
        con.execute(ddl)
    (_steel_rows if sector == SE.MM else _pulp_rows)(con)
    if mutate is not None:
        mutate(con)
    con.commit()
    con.execute("VACUUM")
    con.close()


def sector_db_bytes(sector: str, mutate=None) -> bytes:
    """Os bytes do arquivo como ficam no REPOSITÓRIO do site (o banco do aço vai em gzip)."""
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "x.db"
        build_sector_db(p, sector, mutate)
        raw = p.read_bytes()
    return gzip.compress(raw, compresslevel=9, mtime=0) if SE.DBS[sector].gz else raw


_SECTOR_BYTES: dict = {}


def sector_files() -> dict[str, bytes]:
    """caminho no repositório → bytes: os bancos de fixture GUARDADOS em hub_export/samples/sectors."""
    if not _SECTOR_BYTES:
        for s in SE.SECTORS:
            _SECTOR_BYTES[SE.DBS[s].path] = (SECTOR_SAMPLES / SECTOR_FILE[s]).read_bytes()
    return dict(_SECTOR_BYTES)


def site_files() -> dict[str, bytes]:
    """O repositório do site no GitHub falso: os dois bancos dos setores e o glossário (Onda 4)."""
    return {**sector_files(), GLOSSARY_PATH: glossary_bytes()}


# ── Onda 4: o glossário do site (`admin/glossary.json`), FICTÍCIO, no repositório do GitHub falso ─────
GLOSSARY_PATH = "admin/glossary.json"
GLOSSARY_COMMIT = (("feed01" * 7)[:40], "2026-09-11T17:30:36Z")
_SM_PAGE, _PP_PAGE = "/Steel and Mining/steel_sm_dashboard.html", "/Pulp and Paper/pp_dashboard.html"


def _gi(term, where, *, unit="kt", freq="Monthly", source="Fonte fictícia", desc="Descrição fictícia."):
    return {"term": term, "unit": unit, "freq": freq, "source": source, "desc": desc, "where": where}


GLOSSARY_DOC = {
    "generated": "2026-09-02",
    "note": "Nota interna do editor: não publicar.",
    "sections": [
        {"id": "commodities", "title": "Commodity prices", "intro": "Preços de referência (fictício).",
         "items": [_gi("Minério fictício 61%", "/index.html", unit="USD/t", freq="Daily"),
                   _gi("Minério fictício 62%", "/market.html", unit="", freq=None)]},
        {"id": "steel", "title": "Steel & Mining data", "intro": None,
         "items": [_gi("Import volume (fictício)", _SM_PAGE + "#imports"),
                   # exceção por termo: o site aponta a página-mãe (Imports); o hub, o Modelo preditivo
                   _gi("Import model — orange line", _SM_PAGE + "#imports"),
                   _gi("Termo sem endereço", None),
                   _gi("Termo de página que sumiu", "/pagina-antiga.html"),
                   _gi("   ", "/index.html")]},
        {"id": "pulp", "title": "Pulp & Paper data", "intro": "Contato: analista.ficticio@example.com",
         "items": [_gi("China woodchip imports (GACC)", _PP_PAGE + "#pulp", unit="M BDMT"),
                   _gi("LTM shipments", _PP_PAGE + "#paper"),
                   _gi("Paper production (fictício)", _PP_PAGE + "#paper")]},
        {"id": "market_watch", "title": "Market Watch", "intro": "",
         "items": [_gi("Buyback programs", "/market-watch.html?tab=buybacks", unit=None),
                   _gi("Sensitivity tables", "/stock-guide.html#sens", unit=None)]},
    ],
}


def glossary_bytes(doc=None) -> bytes:
    """O arquivo como o site o guarda (JSON indentado, UTF-8): bytes fixos → SHA do blob fixo."""
    return (json.dumps(GLOSSARY_DOC if doc is None else doc, ensure_ascii=False, indent=2) + "\n").encode()


def glossary_blob(doc=None) -> str:
    return git_blob_sha(glossary_bytes(doc))


def sector_blob(sector: str) -> str:
    return git_blob_sha(sector_files()[SE.DBS[sector].path])


_CACHE_DIR = tempfile.mkdtemp(prefix="hub_export_test_")
atexit.register(shutil.rmtree, _CACHE_DIR, True)


def sector_objects(src) -> dict[str, dict]:
    """Os 36 arquivos dos setores montados direto pelos construtores, "como se publicados" (o índice
    só sai com a fila vazia)."""
    from hub_export.registry import FILES
    out = {}
    for s in SE.SECTORS:
        for name in SE.queue_of(s):
            obj = FILES[name].builder(src)
            FILES[name].after(src, obj, "enviado")
            out[name] = obj
        out[SE.INDEX[s]] = FILES[SE.INDEX[s]].builder(src)
    return out


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


_ARROW = re.compile(r"(->>|->)")


def _arrow(row: dict, expr: str):
    """`col->chave`, `col->índice` (negativo conta do fim, como no Postgres) e `->>` no último
    passo (devolve TEXTO: número vira string, booleano vira "true"/"false")."""
    parts = _ARROW.split(expr)
    val = row.get(parts[0].strip())
    text = False
    for i in range(1, len(parts), 2):
        op, key = parts[i], parts[i + 1].strip()
        if isinstance(val, dict):
            val = val.get(key)
        elif isinstance(val, list) and re.fullmatch(r"-?\d+", key):
            k = int(key)
            val = val[k] if -len(val) <= k < len(val) else None
        else:
            val = None
        text = op == "->>"
    if val is None or not text:
        return copy.deepcopy(val)
    if isinstance(val, bool):
        return "true" if val else "false"
    return val if isinstance(val, str) else json.dumps(val)


_SCALARS = (str, int, float, bool, type(None))


def _project(row: dict, select: str) -> dict:
    out = {}
    for item in select.split(","):
        item = item.strip()
        alias, expr = item.split(":", 1) if ":" in item else (item, item)
        if "->" in expr:
            out[alias] = _arrow(row, expr)
        else:
            v = row.get(expr)
            out[alias] = v if isinstance(v, _SCALARS) else copy.deepcopy(v)
    return out


# ── o mini-PostgREST ──────────────────────────────────────────────────────────────────────────
# colunas timestamptz (o banco real recusa um literal que não é data: HTTP 400, código 22007)
TIMESTAMP_COLS = frozenset({"published_at", "found_at", "take_llm_at", "slot_start", "slot_end",
                            "created_at", "updated_at", "daily_updated_at", "checked_at"})
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
    val = _arrow(row, col) if "->" in col else row.get(col)     # `definition->>kind=eq.sens2d`
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


GITHUB_REPO_URL = f"{GITHUB_API}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/"


@dataclass
class FakeGitHub:
    """A API do GitHub para os bancos dos setores. `files` = caminho → bytes do arquivo na `main`
    (trocar = um commit novo; os blobs antigos continuam baixáveis); `commits` = setor → (sha, data);
    `fail[etapa]` (sonda, download, commit) = status HTTP, exceção ou FakeResp pronto, a cada chamada;
    `corrupt` = o download devolve um byte trocado (o SHA não confere). Onda 4: o glossário do site
    (`admin/glossary.json`, commit em `commits[GLOSSARY_PATH]`); a sonda manda o arquivo inteiro em
    base64 (arquivo < 1 MB, como a API de verdade) e um `ETag`, e com `If-None-Match` igual responde 304
    sem corpo (`not_modified` conta quantas)."""
    files: dict = field(default_factory=site_files)
    commits: dict = field(default_factory=lambda: {**SECTOR_COMMITS, GLOSSARY_PATH: GLOSSARY_COMMIT})
    fail: dict = field(default_factory=dict)
    corrupt: bool = False
    blobs: dict = field(default_factory=dict)
    not_modified: int = 0

    def handle(self, call):
        rest = call.url[len(GITHUB_REPO_URL):]
        for data in self.files.values():
            self.blobs.setdefault(git_blob_sha(data), data)
        if rest.startswith("contents/"):
            step = "sonda"
        elif rest.startswith("git/blobs/"):
            step = "download"
        elif rest == "commits":
            step = "commit"
        else:
            raise AssertionError(f"rota do GitHub inesperada no teste: {rest}")
        err = self.fail.get(step)
        if isinstance(err, BaseException):
            raise err
        if isinstance(err, FakeResp):
            return err
        if isinstance(err, int):
            return FakeResp(err, {"message": "erro falso do GitHub"})
        if step == "sonda":
            path = unquote(rest[len("contents/"):])
            assert call.params.get("ref") == "main", call.params
            data = self.files.get(path)
            if data is None:
                return FakeResp(404, {"message": "Not Found"})
            sha = git_blob_sha(data)
            etag = f'"etag-{sha}"'
            if call.headers.get("If-None-Match") == etag:
                self.not_modified += 1
                return FakeResp(304, b"", headers={"ETag": etag})
            small = len(data) < 1024 * 1024                  # acima de 1 MB a API não manda o conteúdo
            return FakeResp(200, {"type": "file", "name": path.rsplit("/", 1)[-1], "path": path,
                                  "sha": sha, "size": len(data),
                                  "content": base64.b64encode(data).decode() if small else "",
                                  "encoding": "base64" if small else "none"}, headers={"ETag": etag})
        if step == "download":
            assert call.headers.get("Accept") == "application/vnd.github.raw+json", call.headers
            data = self.blobs.get(rest[len("git/blobs/"):])
            if data is None:
                return FakeResp(404, {"message": "Not Found"})
            if self.corrupt:
                data = data[:-1] + bytes([data[-1] ^ 1])
            return FakeResp(200, data)
        path = call.params.get("path")
        sector = next((s for s in SE.SECTORS if SE.DBS[s].path == path), None)
        assert (sector or path in self.commits) and call.params.get("sha") == "main" \
            and call.params.get("per_page") == "1"
        sha, when = self.commits[sector or path]
        return FakeResp(200, [{"sha": sha, "commit": {"committer": {"date": when},
                                                      "message": "data: robô do site"}}])


@dataclass
class FakeHTTP:
    """`requests.Session` falso. `feeder_script[etapa]` = lista de respostas (status ou exceção)
    consumidas na ordem; vazia = sucesso. Etapas: initialize, PUT, complete, failed.
    `max_rows` = o corte do PostgREST do Supabase (1.000 linhas por resposta, calado).
    `github` = a API do GitHub dos bancos dos setores (FakeGitHub)."""
    database: dict = field(default_factory=db)
    published: dict = field(default_factory=dict)          # nome → bytes (cópia no Mindassets)
    feeder_script: dict = field(default_factory=dict)
    supa_error: dict = field(default_factory=dict)          # tabela/rpc → status ou exceção
    calls: list = field(default_factory=list)
    uploads: dict = field(default_factory=dict)             # nome → bytes recebidos no PUT
    revision: int = 0
    max_rows: int | None = 1000
    github: FakeGitHub = field(default_factory=FakeGitHub)

    def request(self, method, url, headers=None, params=None, data=None, json=None,
                timeout=None, stream=False):
        call = Call(method, url, dict(params or {}), dict(headers or {}), data, timeout)
        self.calls.append(call)
        if url.startswith(SUPA_URL):
            return self._supa(call)
        if url.startswith(FEEDER_URL) or url.startswith(PUT_HOST):
            return self._feeder(call)
        if url.startswith(GITHUB_REPO_URL):
            return self.github.handle(call)
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
            # o PostgREST real só ORDENA resultado de RPC por coluna do select (medido em 10/11:
            # 42703 "column record.<col> does not exist"); o filtro fora do select funciona
            chosen = {i.strip().split(":", 1)[-1] for i in call.params["select"].split(",")}
            for part in (call.params.get("order") or "").split(","):
                col = part.strip().split(".", 1)[0]
                if col and col not in chosen:
                    raise PgError(400, "42703", f"column record.{col} does not exist")
        else:
            rows = self.database[name]
            known = NEWS_COLUMNS if name == "news_articles" else None
            if known is not None:
                asked = {_ARROW.split(i.split(":", 1)[-1])[0].strip()
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
        if self.max_rows is not None:
            rows = rows[:self.max_rows]
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

    def github_calls(self, step: str | None = None) -> list[Call]:
        """As chamadas à API do GitHub (`sonda`, `download`, `commit` ou todas)."""
        def kind(c):
            rest = c.url[len(GITHUB_REPO_URL):]
            return ("sonda" if rest.startswith("contents/") else
                    "download" if rest.startswith("git/blobs/") else "commit")
        return [c for c in self.calls if c.url.startswith(GITHUB_REPO_URL)
                and (step is None or kind(c) == step)]


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
    kw.setdefault("http", http)                    # o GitHub falso dos setores
    kw.setdefault("cache_dir", _CACHE_DIR)
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


def test_fixtures_onda2_cobrem_os_casos_do_brief():
    from hub_export import history as H
    qh = {r["ticker"]: r for r in QUOTE_HISTORY}
    assert qh["SCCO"]["last_split_ts"] == SPLIT_SCCO                      # split
    assert "RIO.L" in qh and "RIO.L" not in H.UNIVERSE                    # um de fora
    assert "AUGO" not in qh                                               # papel sem histórico
    for t, r in qh.items():                                               # metadados coerentes
        assert (r["points"], r["first_ts"], r["last_ts"]) == \
            (len(r["daily"]), r["daily"][0][0], r["daily"][-1][0]), t
    # a redução mensal descarta pontos da longa da VALE (é o que a junção precisa respeitar)
    vale_q = next(q for q in QUOTES if q["ticker"] == "VALE3.SA")
    assert len(qh["VALE3.SA"]["daily"]) > len(vale_q["daily"])
    codes = {c["code"]: c for c in COMMODITIES}
    assert all(codes[c]["daily"] for c in FREE_CODES + PAID_CODES)       # a paga também acumula
    assert any(c["is_hidden"] for c in SG_COMPANIES)
    assert any(r["note"] for r in RATING_HISTORY) and any(r["listing"] is None for r in RATING_HISTORY)
    assert {"market_ratings"} <= {f["key"] for f in FLAGS}


def test_mini_postgrest_corta_em_mil_linhas():
    http = FakeHTTP()
    http.database["quote_history"] = [{"ticker": f"T{i}", "points": i} for i in range(1500)]
    r = http.request("GET", SUPA_URL + "/rest/v1/quote_history",
                     params={"select": "ticker", "order": "points.asc"},
                     headers={"Prefer": "count=exact"})
    assert len(r.json()) == 1000 and r.headers["Content-Range"] == "0-999/1500"
    r = http.request("GET", SUPA_URL + "/rest/v1/quote_history",
                     params={"select": "ticker", "order": "points.asc", "offset": "1000"})
    assert len(r.json()) == 500
