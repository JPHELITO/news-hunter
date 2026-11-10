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
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import partial
from typing import Callable

from . import history as H
from . import weeks as W
from .exporters import (calendar, commodities_daily, config, history_file, history_index, intraday,
                        live, news_digest, news_index, news_latest, news_week, quotes_daily,
                        quotes_monthly, ratings, reports)
from .schedule import COLD, HOT, PULSE_LIVE, WARM

KB = 1024
WEEK_FAMILY = W.PREFIX          # "news-week"
HISTORY_FAMILY = H.PREFIX       # "history"


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
}
WEEK_SPEC = dict(cadence=COLD, gzip=True, pulse=None, max_bytes=1024 * KB)
HISTORY_SPEC = dict(cadence=HOT, gzip=True, pulse=None, max_bytes=300 * KB)

# a ordem de processamento dentro de cada cadência (o run.py ordena por cadência, depois por esta)
ORDER = ["live.json", "intraday.json", "news-latest.json", "news-digest.json", "config.json",
         "calendar.json", "quotes-daily.json", "reports.json", WEEK_FAMILY, "news-index.json",
         "quotes-monthly.json", "commodities-daily.json", "ratings.json", HISTORY_FAMILY,
         "history-index.json"]
FAMILIES = (WEEK_FAMILY, HISTORY_FAMILY)


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
    raise KeyError(name)


def cadence_of(name: str) -> int:
    """A cadência para ORDENAR a execução (as famílias contam como COLD)."""
    return COLD if name in FAMILIES else spec_for(name).cadence


def position(name: str) -> int:
    if name in ORDER:
        return ORDER.index(name)
    if W.label_from_file(name):
        return ORDER.index(WEEK_FAMILY)
    if H.ticker_from_file(name):
        return ORDER.index(HISTORY_FAMILY)
    return len(ORDER)


def valid_names() -> list[str]:
    return [n[:-5] if n.endswith(".json") else n for n in ORDER]


def resolve(only=None) -> list[str]:
    """`None`/"all" → todos (com os marcadores das famílias); "config,live" (com ou sem `.json`) →
    os nomes na ordem pedida; "news-week"/"history" = a família; "news-week-2026-w45" = aquela
    semana; "history-vale3.sa" = o histórico daquele papel (só os do universo da Market).
    Nome desconhecido → ValueError com a lista dos válidos."""
    if only is None:
        return list(ORDER)
    parts = [p.strip() for p in (only.split(",") if isinstance(only, str) else only)]
    parts = [p for p in parts if p]
    if not parts or any(p.lower() == "all" for p in parts):
        return list(ORDER)
    out = []
    for p in parts:
        if p in FAMILIES or (p.endswith(".json") and p[:-5] in FAMILIES):
            name = p[:-5] if p.endswith(".json") else p
        else:
            name = p if p.endswith(".json") else p + ".json"
            if (name not in FILES and W.label_from_file(name) is None
                    and H.ticker_from_file(name) is None):
                raise ValueError(f"arquivo desconhecido: {p} (válidos: " + ", ".join(valid_names())
                                 + ", news-week-AAAA-wNN, history-<papel> como history-vale3.sa)")
        if name not in out:
            out.append(name)
    return out
