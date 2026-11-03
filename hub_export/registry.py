"""Os arquivos públicos da Commodities Datahouse e como cada um sai (PLANO §2.5).

`max_bytes` é a meta do arquivo BRUTO (JSON antes do gzip). Estourou → aviso no log e vai assim
mesmo: cortar dado em silêncio é pior que um arquivo maior.
Nomes: minúsculas, hífen, sem barra (regra do Feeder). O gzip vai com o nome `.json` (o hub
descobre pelos bytes 1f 8b — é o que Saúde e Óleo & Gás já fazem pelo mesmo Feeder).

Gzip em repouso (decisão de 03/11/2026, com o tamanho medido): live, intraday e quotes-daily.
O Mindassets não comprime na entrega e cada cliente com a página aberta relê o live e o
intraday a cada 2 min — 63 KB → 19 KB e 76 KB → 18 KB. O config.json (4 KB) vai em texto.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .exporters import config, intraday, live, quotes_daily
from .schedule import COLD, HOT, PULSE_LIVE, WARM

KB = 1024


@dataclass(frozen=True)
class Spec:
    builder: Callable
    cadence: int          # segundos (schedule.HOT/WARM/COLD/DAILY)
    gzip: bool            # conteúdo gzip (nome continua .json)
    pulse: int | None     # reenvia sem mudança depois disso (só o live.json)
    max_bytes: int        # meta do JSON bruto

    @property
    def content_type(self) -> str:
        return "application/gzip" if self.gzip else "application/json"


FILES: dict[str, Spec] = {
    "config.json":       Spec(config.build,       WARM, False, None,       20 * KB),
    "live.json":         Spec(live.build,         HOT,  True,  PULSE_LIVE, 80 * KB),
    "intraday.json":     Spec(intraday.build,     HOT,  True,  None,       150 * KB),
    "quotes-daily.json": Spec(quotes_daily.build, COLD, True,  None,       350 * KB),
}


def resolve(only=None) -> list[str]:
    """`None`/"all" → todos; "config,live" (com ou sem `.json`) → os nomes na ordem pedida.
    Nome desconhecido → ValueError com a lista dos válidos."""
    if only is None:
        return list(FILES)
    parts = [p.strip() for p in (only.split(",") if isinstance(only, str) else only)]
    parts = [p for p in parts if p]
    if not parts or any(p.lower() == "all" for p in parts):
        return list(FILES)
    out = []
    for p in parts:
        name = p if p.endswith(".json") else p + ".json"
        if name not in FILES:
            raise ValueError(f"arquivo desconhecido: {p} (válidos: "
                             + ", ".join(n[:-5] for n in FILES) + ")")
        if name not in out:
            out.append(name)
    return out
