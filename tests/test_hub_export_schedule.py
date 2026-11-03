"""
Cadência e pulsação do exportador do hub (hub_export/schedule.py + o uso no run.py).

O que estes testes TRAVAM, e por quê:
  1) "na vez" = agora − último build ≥ cadência (HOT todo ciclo, WARM 15 min, COLD 60 min), com
     60 s de folga para o relógio do loop (sem ela, 15 min viravam 20 sempre que o 3º ciclo
     chegasse 1 s adiantado). Arquivo fora da vez NEM É MONTADO — não lê o Supabase;
  2) a pulsação do live.json: sem mudança nenhuma, ele vai de novo depois de 15 min — o selo
     "aberta/fechada" do heatmap considera o preço velho com 30 min sem `updatedAt` novo, e num
     fim de semana o hash não muda nunca. O intraday.json NÃO pulsa;
  3) `--force` ignora cadência e hash.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import test_hub_export_fixtures as F  # noqa: E402
from hub_export import schedule as S  # noqa: E402
from hub_export.run import run_cycle  # noqa: E402

T0 = datetime(2026, 11, 3, 18, 0, 0, tzinfo=timezone.utc)


def at(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


def iso(d: datetime) -> str:
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_na_vez():
    assert S.is_due(S.HOT, iso(T0), T0)
    assert S.is_due(S.WARM, None, T0) and S.is_due(S.WARM, "lixo", T0)
    assert not S.is_due(S.WARM, iso(T0), at(10))
    assert not S.is_due(S.WARM, iso(T0), at(13.9))
    assert S.is_due(S.WARM, iso(T0), at(14))                    # 15 min − 60 s de folga
    assert S.is_due(S.WARM, iso(T0), at(15))
    assert not S.is_due(S.COLD, iso(T0), at(45)) and S.is_due(S.COLD, iso(T0), at(60))
    assert not S.is_due(S.DAILY, iso(T0), at(23 * 60)) and S.is_due(S.DAILY, iso(T0), at(24 * 60))
    assert (S.HOT, S.WARM, S.COLD, S.DAILY, S.PULSE_LIVE) == (0, 900, 3600, 86400, 900)


def test_pulsacao():
    assert not S.pulse_due(None, iso(T0), at(100))
    assert S.pulse_due(S.PULSE_LIVE, None, T0)
    assert not S.pulse_due(S.PULSE_LIVE, iso(T0), at(10))
    assert S.pulse_due(S.PULSE_LIVE, iso(T0), at(15))


def ciclo(http, path, minutos, **kw):
    kw.setdefault("memory", F.memory())
    return {r.name: r.action for r in run_cycle(env=F.env(), http=http, now=at(minutos),
                                                  state_file=path, **kw)}


def test_cadencia_no_ciclo(tmp_path):
    p = tmp_path / "st.json"
    http = F.FakeHTTP()
    assert set(ciclo(http, p, 0).values()) == {"enviado"}
    http = F.FakeHTTP()
    acoes = ciclo(http, p, 5)
    assert acoes == {"live.json": "igual", "intraday.json": "igual",
                     "config.json": "fora da vez", "quotes-daily.json": "fora da vez"}
    assert not http.supa_calls("rpc/get_dashboard_flags")      # fora da vez nem lê
    assert not [c for c in http.supa_calls("quotes") if "daily" in c.params["select"]]
    acoes = ciclo(F.FakeHTTP(), p, 15)
    assert acoes["config.json"] == "igual" and acoes["quotes-daily.json"] == "fora da vez"
    acoes = ciclo(F.FakeHTTP(), p, 60)
    assert acoes["quotes-daily.json"] == "igual"


def test_live_reenviado_depois_de_15_min_mesmo_sem_mudanca(tmp_path):
    p = tmp_path / "st.json"
    ciclo(F.FakeHTTP(), p, 0)
    for minuto in (5, 10):
        acoes = ciclo(F.FakeHTTP(), p, minuto)
        assert acoes["live.json"] == "igual" and acoes["intraday.json"] == "igual"
    http = F.FakeHTTP()
    res = {r.name: r for r in run_cycle(env=F.env(), http=http, now=at(15), memory=F.memory(),
                                         state_file=p)}
    assert res["live.json"].action == "enviado" and res["live.json"].reason == "pulsação"
    assert res["intraday.json"].action == "igual"              # o intraday não pulsa
    assert list(http.uploads) == ["live.json"]
    assert ciclo(F.FakeHTTP(), p, 20)["live.json"] == "igual"   # o relógio da pulsação zerou


def test_force_ignora_cadencia_e_hash(tmp_path):
    p = tmp_path / "st.json"
    ciclo(F.FakeHTTP(), p, 0)
    http = F.FakeHTTP()
    acoes = ciclo(http, p, 1, force=True)
    assert set(acoes.values()) == {"enviado"} and len(http.uploads) == 4
