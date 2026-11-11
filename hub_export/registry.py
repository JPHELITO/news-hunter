"""Os arquivos públicos da Commodities Datahouse e como cada um sai (PLANO §2.5 e Apêndice E).

`max_bytes` é a meta do arquivo BRUTO (JSON antes do gzip). Estourou → aviso no log e vai assim
mesmo: cortar dado em silêncio é pior que um arquivo maior.
Nomes: minúsculas, hífen, sem barra (regra do Feeder). O gzip vai com o nome `.json` (o hub
descobre pelos bytes 1f 8b — é o que Saúde e Óleo & Gás já fazem pelo mesmo Feeder).

Gzip em repouso (decisão de 03/11/2026, com o tamanho medido): live, intraday e quotes-daily; na
Onda 1b, news-latest, news-week-*, news-digest e calendar (o Mindassets não comprime na entrega e
o cliente relê os quentes a cada 2 min). config, reports e news-index vão em texto.

FAMÍLIA `news-week`: um arquivo por semana ISO (`news-week-2026-w45.json`). O dicionário FILES é
estático; a família entra na lista como o marcador "news-week" e o run.py a expande na vez dela
(news.py decide quais semanas: anterior e corrente, correções, carga inicial). Também aceita o
nome de uma semana (`news-week-2026-w45`), que é relida do banco.

ORDEM dentro da mesma cadência = a de ORDER: o news-index.json vem DEPOIS das semanas (lista só
as já publicadas, inclusive as desta execução).

Onda 2 (Apêndice F): quotes-monthly, commodities-daily e ratings (gzip) e history-index (texto),
todos COLD; e a FAMÍLIA `history` (`history-<fileId>.json`, um por papel do universo da Market,
nome pela regra F.0 de names.py), no molde da `news-week`: o marcador "history" em ORDER, que o
run.py expande na vez dele (history.py diz quais papéis: a fila do loop, no máximo 2 por execução,
ou os pedidos pelo nome `history-vale3.sa`). A família vem por ÚLTIMO no COLD, antes do índice: se
o orçamento de 90 s acabar, quem espera é ela. O arquivo de um papel tem cadência HOT no Spec porque
a cadência da família é a da fila (history.py), não a do arquivo.

Market Watch (Onda 2, WP2.4B, Apêndice F.6–F.11): mw-companies (texto) e mw-short-interest,
mw-buybacks, mw-insiders e mw-filings (gzip), COLD; e a FAMÍLIA `mw-short` (`mw-short-<fileId>.json`,
uma por empresa visível, gzip), cada arquivo COLD pelo seu último build (mw.py diz quais empresas). O
mw-buybacks é montado de hora em hora, mas o dado só é RELIDO 2×/dia (mw.py: o `updated_at` dessas
tabelas não serve de sonda). Os 5 fixos e a família formam o GRUPO `mw`, com TETO de envios por
execução (GROUP_CAPS, O28): o que passa do teto fica "na fila" e vai no ciclo seguinte.

Onda 3 (Apêndice G, WP3.1B; sectors.py): os 36 arquivos dos setores — Steel & Mining (`mm-*`) e Pulp &
Paper (`pp-*`): os 15 de dado da tela, os 19 da base bruta (`*-raw-<tabela>`, G.11) e os 2 índices —,
ENTRADAS FIXAS (nomes fechados: os 8 de país são um conjunto, não família) com cadência EVENT: nunca
"na vez" pelo relógio, só pelo `urgent` (blob novo no repositório do site, ou arquivo na fila). Grupo
`sectors` com teto de 4 envios por execução (T10); vêm DEPOIS de tudo (se o orçamento de 90 s acabar,
quem espera são eles) e, em cada setor, o índice por último. Lidos dos bancos SQLite do site pela
API do GitHub (zero Supabase).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import partial
from typing import Callable

from . import history as H
from . import mw as MW
from . import sectors as SE
from . import weeks as W
from .exporters import (calendar, commodities_daily, config, history_file, history_index, intraday,
                        live, mm_countries, mm_domestic, mm_prediction, mm_trade, mw_buybacks,
                        mw_companies, mw_filings, mw_insiders, mw_short_file, mw_short_interest,
                        news_digest, news_index, news_latest, news_week, pp_corrugated, pp_paper,
                        pp_pulp_exports, pp_woodchips, quotes_daily, quotes_monthly, ratings,
                        raw_table, reports, sector_index)
from .schedule import COLD, EVENT, HOT, PULSE_LIVE, WARM

KB = 1024
MB = 1024 * KB
WEEK_FAMILY = W.PREFIX          # "news-week"
HISTORY_FAMILY = H.PREFIX       # "history"
MW_FAMILY = MW.PREFIX           # "mw-short"
MW_GROUP = "mw"
SECTOR_GROUP = SE.GROUP         # "sectors"
# envios por execução, por grupo (O28: a carga inicial do Market Watch são 16 arquivos — o Feeder pega
# 10 revisões por ~60 s de TODOS os times; uma rajada nossa atrasaria as dos outros). Setores (T10):
# o mês novo do SECEX (~15 envios com a base bruta) sai em 4 ciclos; a carga inicial (36), em 9.
GROUP_CAPS = {MW_GROUP: 4, SECTOR_GROUP: 4}
EVENT_ORDER = 10 ** 9           # a "cadência" de ordenação dos EVENT: depois de tudo


@dataclass(frozen=True)
class Spec:
    builder: Callable
    cadence: int          # segundos (schedule.HOT/WARM/COLD/DAILY)
    gzip: bool            # conteúdo gzip (nome continua .json)
    pulse: int | None     # reenvia sem mudança depois disso (só o live.json)
    max_bytes: int        # meta do JSON bruto
    after: Callable | None = None    # (src, obj, ação) depois da decisão de enviar
    urgent: Callable | None = None   # (src) → True = na vez fora da cadência
    forbidden: frozenset = field(default_factory=frozenset)   # chaves proibidas SÓ neste arquivo
    group: str | None = None         # grupo com teto de envios por execução (GROUP_CAPS)

    @property
    def content_type(self) -> str:
        return "application/gzip" if self.gzip else "application/json"


FILES: dict[str, Spec] = {
    "config.json":       Spec(config.build,       WARM, False, None,       20 * KB),
    "live.json":         Spec(live.build,         HOT,  True,  PULSE_LIVE, 80 * KB),
    "intraday.json":     Spec(intraday.build,     HOT,  True,  None,       150 * KB),
    "quotes-daily.json": Spec(quotes_daily.build, COLD, True,  None,       350 * KB),
    "news-latest.json":  Spec(news_latest.build,  HOT,  True,  None,       300 * KB),
    "news-digest.json":  Spec(news_digest.build,  HOT,  True,  None,       80 * KB),
    "calendar.json":     Spec(calendar.build,     WARM, True,  None,       300 * KB),
    "reports.json":      Spec(reports.build,      COLD, False, None,       20 * KB),
    "news-index.json":   Spec(news_index.build,   COLD, False, None,       20 * KB,
                              urgent=news_index.urgent),
    "quotes-monthly.json":    Spec(quotes_monthly.build,    COLD, True,  None, 400 * KB),
    "commodities-daily.json": Spec(commodities_daily.build, COLD, True,  None, 150 * KB),
    "ratings.json":           Spec(ratings.build,           COLD, True,  None, 100 * KB,
                                   forbidden=ratings.FORBIDDEN),
    "history-index.json":     Spec(history_index.build,     COLD, False, None, 20 * KB,
                                   urgent=history_index.urgent),
    "mw-companies.json":      Spec(mw_companies.build,      COLD, False, None, 20 * KB,
                                   forbidden=mw_companies.FORBIDDEN, group=MW_GROUP),
    "mw-short-interest.json": Spec(mw_short_interest.build, COLD, True,  None, 60 * KB,
                                   forbidden=mw_short_interest.FORBIDDEN, group=MW_GROUP),
    "mw-buybacks.json":       Spec(mw_buybacks.build,       COLD, True,  None, 100 * KB,
                                   forbidden=mw_buybacks.FORBIDDEN, group=MW_GROUP),
    "mw-insiders.json":       Spec(mw_insiders.build,       COLD, True,  None, 500 * KB,
                                   forbidden=mw_insiders.FORBIDDEN, group=MW_GROUP),
    # comunicados: ~1 KB cru por item com o trecho (medido em 10/11: 188 KB para 181) → ~620 KB nos 600
    "mw-filings.json":        Spec(mw_filings.build,        COLD, True,  None, 700 * KB,
                                   forbidden=mw_filings.FORBIDDEN, group=MW_GROUP),
}
WEEK_SPEC = dict(cadence=COLD, gzip=True, pulse=None, max_bytes=1024 * KB)
HISTORY_SPEC = dict(cadence=HOT, gzip=True, pulse=None, max_bytes=300 * KB)
MW_SHORT_SPEC = dict(cadence=COLD, gzip=True, pulse=None, max_bytes=100 * KB,
                     forbidden=mw_short_interest.FORBIDDEN, group=MW_GROUP)

# ── Onda 3: os setores (Apêndice G) ─────────────────────────────────────────────────────────────
# chaves que nunca saem nos 17 arquivos da tela (G.0; a base bruta, G.11, é a exceção declarada)
SECTOR_FORBIDDEN = frozenset({"updated_at", "value_usd", "valueUsd", "yoy_revenue", "yoyRevenue",
                              "inv_yoy", "invYoy", "hist_avg", "histAvg", "pig_iron", "pigIron",
                              "exprev_total", "exprevTotal"})


def _sector(name: str, builder: Callable, gz: bool, max_bytes: int,
            forbidden: frozenset = SECTOR_FORBIDDEN) -> Spec:
    return Spec(builder, EVENT, gz, None, max_bytes, after=partial(SE.after, name),
                urgent=partial(SE.urgent, name), forbidden=forbidden, group=SECTOR_GROUP)


# meta = o medido com o banco de 06/11/2026 (f636222) com folga para o crescimento
_COUNTRY_MAX = {"imp": {"total": 600, "flat": 800, "long": 800, "semi": 200},
                "exp": {"total": 800, "flat": 1100, "long": 1100, "semi": 300}}
_RAW_MAX = {"secex_country": 16 * MB, "secex_imports": 1 * MB, "secex_exports": 1 * MB,
            "secex_pulp_country": 2 * MB, "secex_pulp_port": 512 * KB, "gacc_woodchips": 512 * KB}
SECTOR_FILES: dict[str, Spec] = {
    "mm-trade.json":      _sector("mm-trade.json", mm_trade.build, True, 150 * KB),
    "mm-domestic.json":   _sector("mm-domestic.json", mm_domestic.build, True, 60 * KB),
    "mm-prediction.json": _sector("mm-prediction.json", mm_prediction.build, False, 30 * KB,
                                  SECTOR_FORBIDDEN | {"Wire Rod"}),
    **{SE.country_file(d, t): _sector(SE.country_file(d, t), partial(mm_countries.build,
                                                                      direction=d, tab=t),
                                      True, _COUNTRY_MAX[d][t] * KB)
       for d in SE.DIRECTIONS for t in SE.COUNTRY_TABS},
    **{SE.raw_file(SE.MM, t): _sector(SE.raw_file(SE.MM, t),
                                      partial(raw_table.build, sector=SE.MM, table=t),
                                      True, _RAW_MAX.get(t, 256 * KB), frozenset())
       for t in SE.RAW_TABLES[SE.MM]},
    "mm-index.json":      _sector("mm-index.json", partial(sector_index.build, sector=SE.MM),
                                  False, 4 * KB),
    "pp-pulp-exports.json": _sector("pp-pulp-exports.json", pp_pulp_exports.build, True, 500 * KB),
    "pp-woodchips.json":    _sector("pp-woodchips.json", pp_woodchips.build, True, 100 * KB),
    "pp-paper.json":        _sector("pp-paper.json", pp_paper.build, True, 60 * KB),
    "pp-corrugated.json":   _sector("pp-corrugated.json", pp_corrugated.build, False, 16 * KB),
    **{SE.raw_file(SE.PP, t): _sector(SE.raw_file(SE.PP, t),
                                      partial(raw_table.build, sector=SE.PP, table=t),
                                      True, _RAW_MAX.get(t, 256 * KB), frozenset())
       for t in SE.RAW_TABLES[SE.PP]},
    "pp-index.json":        _sector("pp-index.json", partial(sector_index.build, sector=SE.PP),
                                    False, 4 * KB),
}
FILES.update(SECTOR_FILES)

# a ordem de processamento dentro de cada cadência (o run.py ordena por cadência, depois por esta)
ORDER = ["live.json", "intraday.json", "news-latest.json", "news-digest.json", "config.json",
         "calendar.json", "quotes-daily.json", "reports.json", WEEK_FAMILY, "news-index.json",
         "quotes-monthly.json", "commodities-daily.json", "ratings.json", "mw-companies.json",
         "mw-short-interest.json", "mw-buybacks.json", "mw-insiders.json", "mw-filings.json",
         MW_FAMILY, HISTORY_FAMILY, "history-index.json", *SE.ALL_FILES]
FAMILIES = (WEEK_FAMILY, HISTORY_FAMILY, MW_FAMILY)


def _after_week(label: str) -> Callable:
    def after(src, obj, action):
        news_week.after(src, label, obj, action)
    return after


def _after_history(ticker: str) -> Callable:
    def after(src, obj, action):
        history_file.after(src, ticker, obj, action)
    return after


def spec_for(name: str) -> Spec:
    """O Spec de um arquivo (os fixos e os das famílias). KeyError se não existe."""
    if name in FILES:
        return FILES[name]
    label = W.label_from_file(name)
    if label is not None:
        return Spec(partial(news_week.build, label=label), after=_after_week(label), **WEEK_SPEC)
    ticker = H.ticker_from_file(name)
    if ticker is not None:
        return Spec(partial(history_file.build, ticker=ticker), after=_after_history(ticker),
                    **HISTORY_SPEC)
    company = MW.company_from_file(name)
    if company is not None:
        return Spec(partial(mw_short_file.build, company=company), **MW_SHORT_SPEC)
    raise KeyError(name)


def cadence_of(name: str) -> int:
    """A cadência para ORDENAR a execução (as famílias contam como COLD; os EVENT, depois de tudo)."""
    if name in FAMILIES:
        return COLD
    cadence = spec_for(name).cadence
    return EVENT_ORDER if cadence == EVENT else cadence


def position(name: str) -> int:
    if name in ORDER:
        return ORDER.index(name)
    if W.label_from_file(name):
        return ORDER.index(WEEK_FAMILY)
    if H.ticker_from_file(name):
        return ORDER.index(HISTORY_FAMILY)
    if MW.company_from_file(name):
        return ORDER.index(MW_FAMILY)
    return len(ORDER)


def valid_names() -> list[str]:
    return [n[:-5] if n.endswith(".json") else n for n in ORDER if not SE.sector_of(n)]


def resolve(only=None) -> list[str]:
    """`None`/"all" → todos (com os marcadores das famílias); "config,live" (com ou sem `.json`) →
    os nomes na ordem pedida; "news-week"/"history"/"mw-short" = a família; "news-week-2026-w45" =
    aquela semana; "history-vale3.sa" = o histórico daquele papel (só os do universo da Market);
    "mw-short-klbn11" = o aluguel daquela empresa (a empresa é conferida na montagem: tem de estar
    visível no Market Watch). Setores: os nomes fixos (`mm-trade`, `mm-countries-imp-flat`,
    `pp-raw-empapel`…) e os atalhos `sectors`, `mm`, `pp`, `mm-countries`, `mm-raw` e `pp-raw` (os
    arquivos de cada um, na ordem do registro). Nome desconhecido → ValueError com os válidos."""
    if only is None:
        return list(ORDER)
    parts = [p.strip() for p in (only.split(",") if isinstance(only, str) else only)]
    parts = [p for p in parts if p]
    if not parts or any(p.lower() == "all" for p in parts):
        return list(ORDER)
    out = []
    for p in parts:
        if p in SE.ALIASES:
            out.extend(n for n in SE.ALIASES[p] if n not in out)
            continue
        if p in FAMILIES or (p.endswith(".json") and p[:-5] in FAMILIES):
            name = p[:-5] if p.endswith(".json") else p
        else:
            name = p if p.endswith(".json") else p + ".json"
            if (name not in FILES and W.label_from_file(name) is None
                    and H.ticker_from_file(name) is None and MW.company_from_file(name) is None):
                raise ValueError(f"arquivo desconhecido: {p} (válidos: " + ", ".join(valid_names())
                                 + ", news-week-AAAA-wNN, history-<papel> como history-vale3.sa, "
                                   "mw-short-<empresa> como mw-short-vale3; setores: "
                                 + ", ".join(SE.ALIASES) + " ou um arquivo deles, como mm-trade, "
                                   "mm-countries-imp-flat, mm-raw-secex-country, pp-index)")
        if name not in out:
            out.append(name)
    return out
