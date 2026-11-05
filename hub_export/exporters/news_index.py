"""news-index.json (COLD, texto, ≤ 20 KB) — PLANO, Apêndice E.4.

`{ updatedAt, firstDate, lastDate, count, weeks: [{ week, from, to, count }] }`, a semana mais
nova primeiro. Só as semanas JÁ PUBLICADAS (o hub nunca pede arquivo que não existe): é derivado
do registro do exportador (semeado do índice publicado e atualizado a cada semana enviada ou
confirmada igual à publicada). No `--out`, das semanas gravadas na pasta. As contagens são de
linhas cruas, sem a dedup por título da tela. `firstDate`/`lastDate` = a primeira e a última data
de referência (`AAAA-MM-DD`, UTC) do arquivo inteiro. Zero leitura de banco.
"""
from __future__ import annotations

from .. import weeks as W
from ..encode import iso_date, iso_utc


def build(src) -> dict:
    store = src.news()
    reg = store.published_weeks()
    seed = store.index_seed()
    labels = sorted(reg, reverse=True)
    weeks = []
    for lab in labels:
        first, last = W.bounds(lab)
        weeks.append({"week": lab, "from": first, "to": last, "count": int(reg[lab]["count"])})
    firsts = [m["first"] for m in reg.values() if m.get("first")]
    lasts = [m["last"] for m in reg.values() if m.get("last")]
    stamps = [m["updatedAt"] for m in reg.values() if m.get("updatedAt")]
    if labels:
        if iso_date(seed.get("firstDate")):
            firsts.append(iso_date(seed["firstDate"]))
        if iso_date(seed.get("lastDate")):
            lasts.append(iso_date(seed["lastDate"]))
        if iso_utc(seed.get("updatedAt")):
            stamps.append(iso_utc(seed["updatedAt"]))
    return {
        "schemaVersion": 1,
        "generatedAt": src.generated_at,
        "updatedAt": max(stamps, default=None),
        "firstDate": min(firsts, default=None),
        "lastDate": max(lasts, default=None),
        "count": sum(w["count"] for w in weeks),
        "weeks": weeks,
    }


def urgent(src) -> bool:
    """O índice vai fora da cadência quando esta execução publicou (ou corrigiu) uma semana."""
    store = src.news_if_loaded()
    return bool(store and store.registry_changed)
