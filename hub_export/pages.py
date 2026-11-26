"""Onda 4 (PLANO, Apêndice H; decisão O58; pesquisa 4-dados §5): de onde vêm o `stock-guide.json`, o
`sg-sensitivity.json` e o `data-sources.json` — as RPCs do site com a chave de serviço (= visão de cliente),
cada uma atrás de uma SONDA barata. (O glossário vem do repositório do site: sitefiles.py; o calendário,
de sources.py.)

O CICLO DE CADA ARQUIVO (COLD, 60 min):
  1. FLAG (fail-closed no dado, O50, com a regra `hub_<chave>`): desligada → o arquivo vazio, NADA lido;
  2. SONDA "contagem + máximo" na tabela, numa chamada (`Prefer: count=exact` + `limit=1`, ~0,8 KB):
       stock-guide    `stock_guide_companies` e `stock_guide_global_peers` (`updated_at`; a dos pares só
                      com a flag `stock_guide_peers` ligada) — 2 chamadas;
       sg-sensitivity `stock_guide_sensitivities` filtrada por `definition->>kind = sens2d` (`updated_at`);
       data-sources   `data_source_status` (`checked_at`: o `updated_at` é a hora da INSERÇÃO e nunca muda).
     A sonda vale até o próximo ciclo COLD (`PROBE_MAX_AGE_S`): uma leitura que falhou tenta de novo no
     ciclo seguinte sem sondar outra vez;
  3. PARTIDA A FRIO (runner novo, ~4,4/dia; nada no estado): a cópia PUBLICADA (Mindassets) cujo marcador é
     igual à sonda vira a semente — zero leitura cara. Marcador = o bloco `source` (stock-guide,
     sg-sensitivity: `{stamp, companies, peers}` / `{stamp, rows, rule}`, DENTRO do hash: uma nova subida
     idêntica da planilha regrava os `updated_at`, sai 1 envio a mais e o marcador volta a valer) ou o
     `checkedAt` + o número de fontes (data-sources). No sg-sensitivity o marcador leva também `rule`, a
     versão da regra de leitura do exportador (`sg_sensitivity.RULE`): código novo com o banco igual (o corte do
     cobre, 26/11) → nem a cópia publicada velha vira semente nem o dado guardado no estado do runner (o loop
     troca o código entre os ciclos do mesmo job) vale — o primeiro ciclo do arquivo relê, sem `rebuild` à
     mão. Mindassets PARADO (`PublishedCopies.healthy`) e sem semente que sirva →
     a leitura espera (`PageWaiting`: o arquivo fica para o ciclo seguinte), salvo uma vez por dia às
     `OUTAGE_HOUR_UTC` — cada runner novo repetiria a leitura cheia (Stock Guide ~10 KB, sensibilidade
     ~57 KB);
  4. LEITURA CHEIA (as RPCs) quando a sonda muda, sem semente, ou na LEITURA DE SEGURANÇA — a gravação por
     PATCH com a chave de serviço não mexe em `updated_at` (as tabelas do Stock Guide não têm gatilho):
     Comp Table 1×/dia (depois das 06:00 UTC), sensibilidade 1×/semana (segunda, 06:00 UTC). Na partida a
     frio, a semente conta como lida — salvo nas 2 h depois da marca, quando a leitura de segurança do dia
     pode ter ficado com o runner que morreu;
  5. o HASH do arquivo decide o envio (run.py), como sempre.
`--rebuild` ignora a semente, o estado e a sonda guardada. No `--out`, a leitura nunca espera.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from .encode import iso_utc

log = logging.getLogger(__name__)

SG_FILE, SENS_FILE, DS_FILE = "stock-guide.json", "sg-sensitivity.json", "data-sources.json"
PROBE_MAX_AGE_S = 3600 - 60        # a sonda vale até o próximo ciclo COLD (schedule.SLACK_S de folga)
SG_SAFETY_UTC = (6, 0)             # Comp Table: leitura de segurança 1×/dia
SENS_SAFETY = (0, 6, 0)            # sensibilidade: 1×/semana (dia da semana: 0 = segunda, hora, minuto)
SAFETY_WINDOW_S = 2 * 3600         # na partida a frio dentro desta janela depois da marca, relê
OUTAGE_HOUR_UTC = 7                # Mindassets parado e sem semente: a leitura cheia só nesta hora (1×/dia)
_ISO = "%Y-%m-%dT%H:%M:%SZ"


class PageWaiting(RuntimeError):
    """O Mindassets está parado e a cópia publicada não serve de semente: a leitura do banco espera."""


def empty_page_state() -> dict:
    return {"probe": None, "probeNow": None, "probeAt": None, "probeVariant": None, "data": None,
            "fullAt": None, "seeded": False}


def _dt(iso: str) -> datetime:
    return datetime.strptime(iso, _ISO).replace(tzinfo=timezone.utc)


def _fresh(stamp, now: datetime, max_age: float) -> bool:
    iso = iso_utc(stamp)
    return bool(iso) and 0 <= (now - _dt(iso)).total_seconds() < max_age


def daily_mark(now: datetime, hour: int, minute: int = 0) -> str:
    """O último HH:MM UTC já passado."""
    n = now.astimezone(timezone.utc)
    mark = n.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if mark > n:
        mark -= timedelta(days=1)
    return iso_utc(mark)


def weekly_mark(now: datetime, weekday: int, hour: int, minute: int = 0) -> str:
    """O último <dia da semana> HH:MM UTC já passado (0 = segunda)."""
    n = now.astimezone(timezone.utc)
    mark = (n.replace(hour=hour, minute=minute, second=0, microsecond=0)
            - timedelta(days=(n.weekday() - weekday) % 7))
    if mark > n:
        mark -= timedelta(days=7)
    return iso_utc(mark)


def _in_window(now: datetime, mark: str | None) -> bool:
    return mark is not None and 0 <= (now - _dt(mark)).total_seconds() < SAFETY_WINDOW_S


def _reset(st: dict) -> None:
    """Flag desligada: nada guardado (religada, relê e confere de novo a cópia publicada)."""
    st.clear()
    st.update(empty_page_state())


def _probe(src, st: dict, fn, variant=None):
    """A sonda, guardada até o próximo ciclo COLD."""
    if (not src.rebuild and st.get("probeNow") is not None and st.get("probeVariant") == variant
            and _fresh(st.get("probeAt"), src.now, PROBE_MAX_AGE_S)):
        return st["probeNow"]
    value = fn()
    st.update(probeNow=value, probeAt=src.generated_at, probeVariant=variant)
    return value


def _resolve(src, st: dict, name: str, marker, marker_of, seed, safety_mark: str | None, read):
    """O miolo do arquivo: do estado, da cópia publicada (semente) ou do banco (ver o docstring)."""
    data = None if src.rebuild else st.get("data")
    if data is None and not src.rebuild and not st.get("seeded"):
        doc = src.published.get(name) if src.published is not None else None
        if isinstance(doc, dict) and doc.get("schemaVersion") == 1 and marker_of(doc) == marker:
            data = seed(doc)
            st.update(data=data, probe=marker,
                      fullAt=None if _in_window(src.now, safety_mark) else src.generated_at)
            src.page_note(f"{name}: partida a frio, a cópia publicada bate com a sonda — nada lido")
        elif (src.mode != "out" and src.published is not None
              and not src.published.healthy(src.now)):
            today = src.now.date().isoformat()
            if src.now.hour != OUTAGE_HOUR_UTC or st.get("outageDay") == today:
                raise PageWaiting(f"{name}: Mindassets parado e a cópia publicada não serve de semente — "
                                  f"a leitura do banco espera (às {OUTAGE_HOUR_UTC:02d} UTC, 1×/dia)")
            st["outageDay"] = today
        st["seeded"] = True
    stale = (data is None or marker != st.get("probe")
             or (safety_mark is not None and (not st.get("fullAt") or st["fullAt"] < safety_mark)))
    if stale:
        why = ("sem dado" if data is None else "a sonda mudou" if marker != st.get("probe")
               else "leitura de segurança")
        data = read()
        st.update(data=data, fullAt=src.generated_at)
        src.page_note(f"{name}: relido ({why})")
    st["probe"] = marker
    return data


def _count(n) -> int | None:
    return n if isinstance(n, int) and not isinstance(n, bool) else None


# ── stock-guide.json (H.1) ─────────────────────────────────────────────────────────────────────
def stock_guide(src) -> dict:
    """{updatedAt, source, years, footnote, companies, peers} (o miolo; a montagem é do exporter)."""
    from .exporters import stock_guide as SG
    st = src.state["stockGuide"]
    flags = src.client_flags()
    if flags.get("stock_guide") is not True:
        _reset(st)
        return {"updatedAt": None, "source": SG.empty_source(), **SG.empty()}
    peers_on = flags.get("stock_guide_peers") is not False

    def probe() -> dict:
        # a de `stock_guide_companies` é a mesma do ratings.json: no mesmo ciclo, uma chamada só
        rows, n = src.probe_max("stock_guide_companies", "updated_at", tag="stock-guide:sonda")
        stamps = [iso_utc(rows[0].get("updated_at")) if rows else None]
        n_peers = 0
        if peers_on:
            prow, n_peers = src.probe_max("stock_guide_global_peers", "updated_at", tag="stock-guide:sonda")
            stamps.append(iso_utc(prow[0].get("updated_at")) if prow else None)
        stamps = [s for s in stamps if s]
        return {"stamp": max(stamps) if stamps else None, "companies": _count(n),
                "peers": _count(n_peers) if peers_on else 0}

    def read() -> dict:
        comps = src.supa.rpc("get_stock_guide_comps", SG.COMPS_COLS, tag="stock-guide")
        cfg = src.supa.rpc("get_stock_guide_config", SG.CONFIG_COLS, tag="stock-guide")
        peers = (src.supa.rpc("get_stock_guide_global_peers", SG.PEER_COLS, tag="stock-guide")
                 if peers_on else [])
        return SG.data(comps, cfg, peers)

    marker = _probe(src, st, probe, variant=peers_on)
    data = _resolve(src, st, SG_FILE, marker, lambda doc: doc.get("source"), SG.from_published,
                    daily_mark(src.now, *SG_SAFETY_UTC), read)
    if not peers_on:
        data = {**data, "peers": []}
    return {"updatedAt": marker["stamp"], "source": marker, **data}


# ── sg-sensitivity.json (H.2) ──────────────────────────────────────────────────────────────────
def sensitivity(src) -> dict:
    """{updatedAt, source, companies}."""
    from .exporters import sg_sensitivity as SS
    st = src.state["sgSensitivity"]
    flags = src.client_flags()
    if flags.get("stock_guide") is not True or flags.get("stock_guide_sensitivity") is not True:
        _reset(st)
        return {"updatedAt": None, "source": {"stamp": None, "rows": 0, "rule": SS.RULE}, "companies": []}

    def probe() -> dict:
        rows, n = src.probe_max("stock_guide_sensitivities", "updated_at", tag="sg-sensitivity:sonda",
                                **SS.KIND_FILTER)
        return {"stamp": iso_utc(rows[0].get("updated_at")) if rows else None, "rows": _count(n)}

    def read() -> list:
        return SS.companies(src.supa.rpc("get_stock_guide_sensitivity_tables", SS.COLS,
                                         tag="sg-sensitivity", **SS.KIND_FILTER))

    # a regra entra DEPOIS da sonda guardada: uma sonda de antes da troca de código continua valendo
    # (nenhuma chamada a mais), e o marcador com a regra nova não bate com o estado nem com a cópia velha
    marker = {**_probe(src, st, probe), "rule": SS.RULE}
    companies = _resolve(src, st, SENS_FILE, marker, lambda doc: doc.get("source"), SS.from_published,
                         weekly_mark(src.now, *SENS_SAFETY), read)
    return {"updatedAt": marker["stamp"], "source": marker, "companies": companies}


# ── data-sources.json (H.4) ────────────────────────────────────────────────────────────────────
def data_sources(src) -> dict:
    """{checkedAt, sources}."""
    from .exporters import data_sources as DS
    st = src.state["dataSources"]
    if src.client_flags().get("data_page") is not True:
        _reset(st)
        return {"checkedAt": None, "sources": []}

    def probe() -> dict:
        rows, n = src.probe_max("data_source_status", "checked_at", tag="data-sources:sonda")
        return {"checkedAt": iso_utc(rows[0].get("checked_at")) if rows else None, "count": _count(n)}

    def read() -> list:
        return DS.sources(src.supa.rpc("get_data_source_status", DS.COLS, tag="data-sources"))

    def marker_of(doc: dict) -> dict:
        return {"checkedAt": iso_utc(doc.get("checkedAt")), "count": len(doc.get("sources") or [])}

    marker = _probe(src, st, probe)
    sources = _resolve(src, st, DS_FILE, marker, marker_of, DS.from_published, None, read)
    return {"checkedAt": DS.checked_at(sources), "sources": sources}
