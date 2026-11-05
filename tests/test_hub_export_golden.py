"""
Saída DOURADA dos arquivos do hub (hub_export/samples/*.json), montada do banco falso das
fixtures (test_hub_export_fixtures.py) no cenário do loop: memória do ciclo + estado anterior.
Onda 1a: config, live, intraday, quotes-daily. Onda 1b: news-latest, três semanais
(news-week-2026-w43/w44/w45: a antiga da carga inicial, a anterior e a corrente), news-index (com
as três já "publicadas"), news-digest, reports e calendar.

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
from hub_export import weeks as W  # noqa: E402
from hub_export.encode import forbidden_keys  # noqa: E402
from hub_export.registry import FILES, spec_for  # noqa: E402

SAMPLES = Path(__file__).resolve().parents[1] / "hub_export" / "samples"
GOLDEN_WEEKS = (F.WEEK_OLD, F.WEEK_PREV, F.WEEK_CUR)
_SCALAR = r'(?:-?\d[\d.eE+-]*|null|true|false|"[^"\\\[\]{}]*")'
_SCALAR_ARRAY = re.compile(r"\[\s*(" + _SCALAR + r"(?:,\s*" + _SCALAR + r")*)\s*\]")


def pretty(obj) -> str:
    """JSON indentado, mas lista só de escalares numa linha (`[1793710800, 60.5]`, tickers):
    o diff da amostra fica legível na revisão."""
    text = json.dumps(obj, ensure_ascii=False, indent=2)
    return _SCALAR_ARRAY.sub(lambda m: "[" + re.sub(r",\s+", ", ", m.group(1)) + "]", text) + "\n"


def golden_objects() -> dict[str, dict]:
    src, _ = F.make_sources(state=F.previous_state())
    out = {name: spec.builder(src) for name, spec in FILES.items() if name != "news-index.json"}
    store = src.news()
    for lab in GOLDEN_WEEKS:
        name = W.file_name(lab)
        obj = spec_for(name).builder(src)
        store.after_week(lab, obj["items"], obj["updatedAt"], "enviado")   # como se publicada
        out[name] = obj
    out["news-index.json"] = FILES["news-index.json"].builder(src)
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
        assert forbidden_keys(obj) == []
