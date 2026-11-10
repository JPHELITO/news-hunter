"""quotes-monthly.json (COLD, conteúdo gzip com nome .json, ≤ 400 KB bruto) — PLANO, Apêndice F.3.

`{ schemaVersion, generatedAt, series: { <ticker>: { updatedAt, pts } } }`, os mesmos papéis do
quotes-daily.json: a CAUDA MENSAL da série leve, que o quotes-daily.json (só 400 dias) não leva — a
Watchlist em 5Y/Max, o risquinho, o "Max" e as datas antigas da Market precisam dela.

A série leve (`quotes.daily`) é derivada da longa no `append_quote_history` (site,
admin/supabase_quote_history.sql): o último pregão de cada MÊS UTC antes de `v_cut` + todos os pontos
≥ `v_cut`, com `v_cut = daily_updated_at − 372 dias` (exato: a mesma transação). O ponto do mês que
CONTÉM o `v_cut` anda todo dia (é o último pregão antes do corte, não o do fim do mês).
`pts` aqui = os pontos da série leve cujo mês UTC é ANTERIOR ao mês do `v_cut` — o último pregão de
cada mês INTEIRO antes do trecho diário. Sem o ponto que anda, o arquivo muda ~1×/mês (quando o
`v_cut` vira o mês) ou num split, não todo dia. E não deixa buraco: todo ponto da série leve
anterior ao 1º ponto do quotes-daily.json (corte de 400 dias, ~28 dias antes do `v_cut`) está aqui;
a sobreposição (o fim do mês anterior ao do `v_cut`, que costuma estar nos dois) é inofensiva.

No hub: série leve = os pontos daqui ANTERIORES ao 1º ponto do quotes-daily + o quotes-daily inteiro
(reproduz o `q.daily` do site). `updatedAt` = `daily_updated_at` (fora do hash).

Zero leitura nova em regime: sai do cache do diário da 1a (`Sources.daily()`), e o papel cujo cache
veio da cópia publicada do quotes-daily.json (cortada em 400 dias) usa a cauda da cópia PUBLICADA
deste arquivo (ver `Sources.monthly()`).
"""
from __future__ import annotations

from datetime import datetime, timezone

CUT_S = 372 * 86400          # o trecho diário da série leve (o range '1Y' do site)
_ISO = "%Y-%m-%dT%H:%M:%SZ"


def epoch_of(iso: str | None) -> int | None:
    if not iso:
        return None
    try:
        return int(datetime.strptime(iso, _ISO).replace(tzinfo=timezone.utc).timestamp())
    except ValueError:
        return None


def month_of(ts) -> int:
    """O mês UTC de um epoch como inteiro comparável (ano × 12 + mês − 1) — o `YYYY-MM` do SQL."""
    d = datetime.fromtimestamp(int(ts), timezone.utc)
    return d.year * 12 + d.month - 1


def cut_month(at: str | None) -> int | None:
    """O mês UTC do `v_cut` (= `daily_updated_at` − 372 d), ou None sem carimbo."""
    e = epoch_of(at)
    return None if e is None else month_of(e - CUT_S)


def monthly_part(pts, at: str | None) -> list[list]:
    """Os pontos da série leve de meses UTC anteriores ao mês do `v_cut` (sem carimbo: nenhum)."""
    m = cut_month(at)
    if m is None:
        return []
    return [list(p) for p in pts or [] if month_of(p[0]) < m]


def build(src) -> dict:
    tails = src.monthly()
    series = {}
    for ticker, _name, _exchange in src.quote_universe:
        e = tails.get(ticker)
        if e is None:
            continue
        series[ticker] = {"updatedAt": e.get("updatedAt"), "pts": e.get("pts") or []}
    return {"schemaVersion": 1, "generatedAt": src.generated_at, "series": series}
