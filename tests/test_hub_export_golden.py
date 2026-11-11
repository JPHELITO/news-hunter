"""
Saída DOURADA dos arquivos do hub (hub_export/samples/*.json), montada do banco falso das
fixtures (test_hub_export_fixtures.py) no cenário do loop: memória do ciclo + estado anterior.
Onda 1a: config, live, intraday, quotes-daily. Onda 1b: news-latest, três semanais
(news-week-2026-w43/w44/w45: a antiga da carga inicial, a anterior e a corrente), news-index (com
as três já "publicadas"), news-digest, reports e calendar. Onda 2: quotes-monthly,
commodities-daily, ratings, três históricos (history-vale3.sa: desde 2020, com pontos que a redução
mensal descarta; history-ibov: índice; history-scco: com split) e o history-index (com os três já
"publicados"). Onda 2 (WP2.4B, Market Watch): mw-companies (com os fatos de cada empresa),
mw-short-interest (visão "Todas", 100 dias), mw-buybacks, mw-insiders (MAIS DE 1.000 linhas: o
caso do corte do PostgREST — é a amostra grande, ~190 KB) e mw-filings, mais o aluguel de cada
empresa visível (mw-short-<empresa>: ON, PN, PNA/PNB, UNIT, BDR). Onda 3 (WP3.1B, setores): os 36
arquivos `mm-*`/`pp-*` montados dos dois bancos PEQUENOS de fixture (`samples/sectors/`, servidos
pelo GitHub falso), com os índices "depois de publicados" os arquivos de dado (o índice só sai com
a fila do blob vazia) — o SHA do blob no índice é o do arquivo guardado.

Por que existe: o hub (React) começa as telas por estas amostras — o orquestrador as copia para
`__tests__/fixtures` do lado do hub a cada mudança de contrato. Qualquer mudança no que sai daqui
É uma mudança de contrato e tem de aparecer no diff do commit, não passar escondida.

Mudou de propósito? Regere e confira o diff:
    UPDATE_GOLDEN=1 python -m pytest tests/test_hub_export_golden.py -q

As amostras vão SEM gzip (para serem legíveis) e indentadas; os arquivos de verdade saem
compactos — o test_hub_export_run.py trava que o modo --out grava os mesmos bytes que seriam
enviados.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import test_hub_export_fixtures as F  # noqa: E402
from hub_export import history as H  # noqa: E402
from hub_export import mw as MW  # noqa: E402
from hub_export import sectors as SE  # noqa: E402
from hub_export import weeks as W  # noqa: E402
from hub_export.encode import forbidden_keys  # noqa: E402
from hub_export.registry import FILES, spec_for  # noqa: E402

SAMPLES = Path(__file__).resolve().parents[1] / "hub_export" / "samples"
GOLDEN_WEEKS = (F.WEEK_OLD, F.WEEK_PREV, F.WEEK_CUR)
GOLDEN_HISTORY = ("VALE3.SA", "IBOV", "SCCO")
INDEXES = ("news-index.json", "history-index.json")
_SCALAR = r'(?:-?\d[\d.eE+-]*|null|true|false|"[^"\\\[\]{}]*")'
_SCALAR_ARRAY = re.compile(r"\[\s*(" + _SCALAR + r"(?:,\s*" + _SCALAR + r")*)\s*\]")


def pretty(obj) -> str:
    """JSON indentado, mas lista só de escalares numa linha (`[1793710800, 60.5]`, tickers):
    o diff da amostra fica legível na revisão."""
    text = json.dumps(obj, ensure_ascii=False, indent=2)
    return _SCALAR_ARRAY.sub(lambda m: "[" + re.sub(r",\s+", ", ", m.group(1)) + "]", text) + "\n"


def golden_objects() -> dict[str, dict]:
    http = F.FakeHTTP()
    http.database["quote_history"] = F.quote_history()
    src, _ = F.make_sources(http=http, state=F.previous_state())
    out = {name: spec.builder(src) for name, spec in FILES.items()
           if name not in INDEXES and not SE.sector_of(name)}
    out.update(F.sector_objects(src))                        # os 36 dos setores, índices por último
    store = src.news()
    for lab in GOLDEN_WEEKS:
        name = W.file_name(lab)
        obj = spec_for(name).builder(src)
        store.after_week(lab, obj["items"], obj["updatedAt"], "enviado")   # como se publicada
        out[name] = obj
    out["news-index.json"] = FILES["news-index.json"].builder(src)
    hist = src.history()
    for t in GOLDEN_HISTORY:
        name = H.file_name(t)
        obj = spec_for(name).builder(src)
        hist.after(t, obj, "enviado")                                      # como se publicado
        out[name] = obj
    out["history-index.json"] = FILES["history-index.json"].builder(src)
    for co in F.MW_VISIBLE:
        name = MW.file_name(co)
        out[name] = spec_for(name).builder(src)
    return out


def golden() -> dict[str, str]:
    return {name: pretty(obj) for name, obj in golden_objects().items()}


def test_samples_dourados():
    atual = golden()
    if os.environ.get("UPDATE_GOLDEN") == "1":
        SAMPLES.mkdir(parents=True, exist_ok=True)
        for name, text in atual.items():
            (SAMPLES / name).write_text(text, encoding="utf-8", newline="\n")
    for name, text in atual.items():
        guardado = (SAMPLES / name).read_text(encoding="utf-8")
        assert guardado == text, f"{name} mudou — se foi de propósito: UPDATE_GOLDEN=1"


def test_samples_sao_exatamente_os_arquivos_e_validos():
    objs = golden_objects()
    assert sorted(p.name for p in SAMPLES.glob("*.json")) == sorted(objs)
    assert set(FILES) <= set(objs)
    for name, built in objs.items():
        obj = json.loads((SAMPLES / name).read_text(encoding="utf-8"))
        assert obj == built                                  # a formatação não mexe no dado
        assert obj["schemaVersion"] == 1 and obj["generatedAt"] == F.GENERATED
        assert forbidden_keys(obj, extra=spec_for(name).forbidden) == []
