"""
Saída DOURADA dos 4 arquivos do hub (hub_export/samples/*.json), montada do banco falso das
fixtures (test_hub_export_fixtures.py) no cenário do loop: memória do ciclo + estado anterior.

Por que existe: o hub (React) começa as telas por estas amostras — o orquestrador as copia para
`__tests__/fixtures` do lado do hub a cada mudança de contrato. Qualquer mudança no que sai daqui
É uma mudança de contrato e tem de aparecer no diff do commit, não passar escondida.

Mudou de propósito? Regere e confira o diff:
    UPDATE_GOLDEN=1 python -m pytest tests/test_hub_export_golden.py -q

As amostras vão SEM gzip (o quotes-daily.json também, para ser legível) e indentadas; os arquivos
de verdade saem compactos — o test_hub_export_run.py trava que o modo --out grava os mesmos bytes
que seriam enviados.
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
from hub_export.encode import forbidden_keys  # noqa: E402
from hub_export.registry import FILES  # noqa: E402

SAMPLES = Path(__file__).resolve().parents[1] / "hub_export" / "samples"
_SCALAR = r'(?:-?\d[\d.eE+-]*|null|true|false|"[^"\\\[\]{}]*")'
_SCALAR_ARRAY = re.compile(r"\[\s*(" + _SCALAR + r"(?:,\s*" + _SCALAR + r")*)\s*\]")


def pretty(obj) -> str:
    """JSON indentado, mas lista só de escalares numa linha (`[1793710800, 60.5]`, tickers):
    o diff da amostra fica legível na revisão."""
    text = json.dumps(obj, ensure_ascii=False, indent=2)
    return _SCALAR_ARRAY.sub(lambda m: "[" + re.sub(r",\s+", ", ", m.group(1)) + "]", text) + "\n"


def golden() -> dict[str, str]:
    src, _ = F.make_sources(state=F.previous_state())
    return {name: pretty(spec.builder(src)) for name, spec in FILES.items()}


def test_samples_dourados():
    atual = golden()
    if os.environ.get("UPDATE_GOLDEN") == "1":
        SAMPLES.mkdir(parents=True, exist_ok=True)
        for name, text in atual.items():
            (SAMPLES / name).write_text(text, encoding="utf-8", newline="\n")
    for name, text in atual.items():
        guardado = (SAMPLES / name).read_text(encoding="utf-8")
        assert guardado == text, f"{name} mudou — se foi de propósito: UPDATE_GOLDEN=1"


def test_samples_sao_exatamente_os_4_arquivos_e_validos():
    assert sorted(p.name for p in SAMPLES.glob("*.json")) == sorted(FILES)
    src, _ = F.make_sources(state=F.previous_state())
    for name, spec in FILES.items():
        obj = json.loads((SAMPLES / name).read_text(encoding="utf-8"))
        assert obj == spec.builder(src)                      # a formatação não mexe no dado
        assert obj["schemaVersion"] == 1 and obj["generatedAt"] == F.GENERATED
        assert forbidden_keys(obj) == []
