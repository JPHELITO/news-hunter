"""
Régua do termômetro de notícias — o agregado diário que a home não tem como guardar.

    python -m hunter.news_pulse                          # recalcula as últimas 5 sessões e grava
    python -m hunter.news_pulse --sessions 20            # mais sessões para trás
    python -m hunter.news_pulse --backfill --from 2026-06-02   # o histórico inteiro
    python -m hunter.news_pulse --dry-run                # imprime a tabela, não grava

O QUE É: uma linha por (pregão, setor, corte) com as contagens de take das manchetes da
janela — a MESMA conta do card da home (index.html: _pulseTally / _pulseGaugeVal) — mais a
impressão digital do instrumento que produziu aquele dia: quantas manchetes por fonte e
quantas por modelo de IA.

POR QUE EXISTE (2026-09-09): o ponteiro da home mudou de nível com o próprio robô, não com
o mercado — mediana mensal 39 → 45 → 51 → 54 conforme entraram as fontes internacionais
(25/06) e a cadeia de IAs foi reordenada (06/08). Qualquer calibração por história
("percentil dos últimos 60 pregões") precisa saber QUANDO o instrumento mudou, e é isso que
`sources` e `models` guardam, dia a dia. A série em si pode ser refeita de news_articles a
qualquer momento (o histórico é retido); a régua materializa para a home ler 90 linhas em
vez de 10 mil manchetes, e para cada dia ter registro de qual fórmula valia (`formula`).

JANELAS (espelham a home):
  cut 'close' = fechamento anterior (17:00 BRT do último dia útil ANTES de D) → 17:00 de D.
                Uma sessão é tudo que foi publicado entre dois fechamentos. Não sobrepõe.
  cut '07' / '09' = o mesmo início → 07:00 / 09:00 de D. É o que o card mostra de manhã, e
                é a leitura que um dia se vai querer calibrar.
  Segunda-feira começa na sexta às 17:00 — o fim de semana inteiro cai na sessão de segunda.

SÓ GRAVA CORTE ASSENTADO: janela fechada há pelo menos SETTLE_H horas, para a IA ter
classificado o que chegou no fim (pendente é contado, mas não pontua). E SEMPRE recalcula as
últimas N sessões por cima (upsert): take que chegou tarde, notícia achada tarde, correção de
classificação — tudo converge sem ninguém rodar nada à mão.

⚠️ CONSTANTES ESPELHADAS do index.html (mudar lá = mudar aqui, e subir FORMULA):
  CLOSE_HOUR=17 · MIN_SCORED=20 · 'sm' = steel+mining · 'pp' = pp · tempo efetivo =
  published_at, com found_at de reserva · include_in_report=true.

Falha fechada e sem drama: se a tabela ainda não existe (o SQL de admin/ não foi rodado), sai
0 com um aviso — este passo roda dentro do pulse_daily.yml e não pode derrubar a foto do
Market Pulse. Erro de verdade (HTTP 5xx, rede) sai 1, que é como o GitHub avisa.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import logging
import os
import sys
from pathlib import Path

import requests

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

log = logging.getLogger("news_pulse")

BRT = dt.timezone(dt.timedelta(hours=-3))       # Brasil sem horário de verão desde 2019
CLOSE_HOUR = 17                                 # fechamento da B3 — âncora da janela (= PULSE_CLOSE_HOUR)
MIN_SCORED = 20                                 # abaixo disto o ponteiro não fala (= PULSE_MIN_SCORED)
FORMULA = 1                                     # versão da conta: neutro ancora, no-take fora, portão 20
CUTS = {"07": 7, "09": 9, "close": CLOSE_HOUR}  # hora BRT em que cada corte fecha
SETTLE_H = 2                                    # folga para a IA classificar o fim da janela
SECTORS = ("all", "sm", "pp")
TABLE = "news_pulse_daily"
SESSOES_PADRAO = 5
SELECT = "take_llm,take_llm_model,take_covered_companies,sector,source_name,published_at,found_at"


# ── calendário ──────────────────────────────────────────────────────────────────────────
def eh_dia_util(d: dt.date) -> bool:
    return d.weekday() < 5


def dia_util_anterior(d: dt.date) -> dt.date:
    """O último dia útil ESTRITAMENTE antes de d."""
    d -= dt.timedelta(days=1)
    while not eh_dia_util(d):
        d -= dt.timedelta(days=1)
    return d


def sessoes_ate(fim: dt.date, n: int) -> list[dt.date]:
    """As n últimas sessões (dias úteis) até `fim`, inclusive se `fim` for dia útil."""
    while not eh_dia_util(fim):
        fim -= dt.timedelta(days=1)
    out = [fim]
    while len(out) < n:
        out.append(dia_util_anterior(out[-1]))
    return sorted(out)


def sessoes_entre(ini: dt.date, fim: dt.date) -> list[dt.date]:
    out, d = [], ini
    while d <= fim:
        if eh_dia_util(d):
            out.append(d)
        d += dt.timedelta(days=1)
    return out


def janela(sessao: dt.date, cut: str) -> tuple[dt.datetime, dt.datetime]:
    """(início, fim) da janela do corte, em UTC. Início = fechamento do dia útil anterior."""
    ant = dia_util_anterior(sessao)
    ini = dt.datetime(ant.year, ant.month, ant.day, CLOSE_HOUR, tzinfo=BRT)
    fim = dt.datetime(sessao.year, sessao.month, sessao.day, CUTS[cut], tzinfo=BRT)
    return ini.astimezone(dt.timezone.utc), fim.astimezone(dt.timezone.utc)


def assentado(fim: dt.datetime, agora: dt.datetime) -> bool:
    return agora >= fim + dt.timedelta(hours=SETTLE_H)


# ── a conta (espelho do index.html) ─────────────────────────────────────────────────────
def tempo_efetivo(a: dict) -> dt.datetime | None:
    t = a.get("published_at") or a.get("found_at")
    if not t:
        return None
    try:
        return dt.datetime.fromisoformat(str(t).replace("Z", "+00:00")).astimezone(dt.timezone.utc)
    except ValueError:
        return None


def setores_de(a: dict) -> set[str]:
    """Em quais linhas de setor a manchete conta. 'all' sempre; 'sm' e 'pp' pelo setor da keyword."""
    s = {"all"}
    sec = a.get("sector")
    if sec in ("steel", "mining"):
        s.add("sm")
    elif sec == "pp":
        s.add("pp")
    return s


def _bump(e: dict, tk) -> None:
    if tk is None:
        e["pending"] += 1
    elif tk == "+":
        e["pos"] += 1
    elif tk == "-":
        e["neg"] += 1
    elif tk == "=":
        e["neu"] += 1
    else:
        e["notake"] += 1


def contar(artigos: list[dict]) -> dict:
    """Contagens agregadas + por empresa coberta + impressão digital (fontes, modelos)."""
    c = {"n_items": 0, "pos": 0, "neg": 0, "neu": 0, "notake": 0, "pending": 0}
    covered: dict[str, dict] = {}
    sources: collections.Counter = collections.Counter()
    models: collections.Counter = collections.Counter()
    for a in artigos:
        c["n_items"] += 1
        tk = a.get("take_llm")
        _bump(c, tk)
        sources[a.get("source_name") or "?"] += 1
        if tk is not None:
            models[a.get("take_llm_model") or "?"] += 1
        for tok in str(a.get("take_covered_companies") or "").split(";"):
            tok = tok.strip().upper()
            if not tok:
                continue
            e = covered.setdefault(tok, {"n": 0, "pos": 0, "neg": 0, "neu": 0, "notake": 0, "pending": 0})
            e["n"] += 1
            _bump(e, tk)
    c["scored"] = c["pos"] + c["neg"] + c["neu"]
    c["covered"] = covered
    c["sources"] = dict(sources)
    c["models"] = dict(models)
    return c


def ponteiro(c: dict) -> int | None:
    """50 + (pos − neg)/scored × 50, com o neutro ancorando. None = amostra pequena demais."""
    if c["scored"] < MIN_SCORED:
        return None
    return round(50 + (c["pos"] - c["neg"]) / c["scored"] * 50)


def linhas(artigos: list[dict], sessoes: list[dt.date], agora: dt.datetime,
           cuts: tuple[str, ...] = ("07", "09", "close")) -> list[dict]:
    """Uma linha por (sessão, setor, corte) — só cortes já assentados. Chaves UNIFORMES:
    o PostgREST recusa o lote inteiro se uma linha tiver coluna a menos."""
    com_tempo = [(tempo_efetivo(a), a) for a in artigos]
    com_tempo = [(t, a) for t, a in com_tempo if t is not None]
    out = []
    for sessao in sessoes:
        for cut in cuts:
            ini, fim = janela(sessao, cut)
            if not assentado(fim, agora):
                continue
            na_janela = [a for t, a in com_tempo if ini <= t < fim]
            for sector in SECTORS:
                sub = [a for a in na_janela if sector in setores_de(a)]
                c = contar(sub)
                out.append({
                    "session_date": sessao.isoformat(),
                    "sector": sector,
                    "cut": cut,
                    "window_start": ini.isoformat(),
                    "window_end": fim.isoformat(),
                    "n_items": c["n_items"],
                    "pos": c["pos"], "neg": c["neg"], "neu": c["neu"],
                    "notake": c["notake"], "pending": c["pending"],
                    "scored": c["scored"],
                    "gauge": ponteiro(c),
                    "covered": c["covered"],
                    "sources": c["sources"],
                    "models": c["models"],
                    "formula": FORMULA,
                    "computed_at": agora.isoformat(),
                })
    return out


# ── Supabase ────────────────────────────────────────────────────────────────────────────
def _env() -> tuple[str, str]:
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    if not url or not key:
        raise RuntimeError("SUPABASE_URL / SUPABASE_SERVICE_KEY ausentes")
    return url, key


def buscar_artigos(ini: dt.datetime, fim: dt.datetime | None = None) -> list[dict]:
    """Manchetes relevantes com tempo efetivo ≥ ini (o fim é filtrado em Python, por janela).
    Paginado por limit/offset — o teto do PostgREST é 1000 por resposta."""
    url, key = _env()
    corte = ini.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    base = (f"{url}/rest/v1/news_articles?select={SELECT}&include_in_report=eq.true"
            f"&or=(published_at.gte.{corte},and(published_at.is.null,found_at.gte.{corte}))"
            f"&order=found_at.asc")
    rows: list[dict] = []
    off = 0
    while True:
        r = requests.get(f"{base}&limit=1000&offset={off}",
                         headers={"apikey": key, "Authorization": f"Bearer {key}"}, timeout=60)
        if not r.ok:
            raise RuntimeError(f"news_articles HTTP {r.status_code}: {r.text[:200]}")
        lote = r.json()
        rows.extend(lote)
        off += len(lote)
        if len(lote) < 1000:
            break
    if fim is not None:
        rows = [a for a in rows if (tempo_efetivo(a) or fim) < fim]
    return rows


def _tabela_nao_existe(r: requests.Response) -> bool:
    txt = r.text or ""
    return r.status_code == 404 or "PGRST205" in txt or "42P01" in txt


def gravar(rows: list[dict], lote: int = 200) -> int:
    """Upsert por (session_date, sector, cut). Tabela ausente → aviso e 0 (o SQL ainda não
    foi rodado); qualquer outro erro HTTP → RuntimeError (o job tem de ficar vermelho)."""
    if not rows:
        return 0
    url, key = _env()
    gravadas = 0
    for i in range(0, len(rows), lote):
        r = requests.post(
            f"{url}/rest/v1/{TABLE}?on_conflict=session_date,sector,cut",
            json=rows[i:i + lote],
            headers={"apikey": key, "Authorization": f"Bearer {key}",
                     "Content-Type": "application/json",
                     "Prefer": "resolution=merge-duplicates,return=minimal"},
            timeout=60,
        )
        if r.ok:
            gravadas += len(rows[i:i + lote])
            continue
        if _tabela_nao_existe(r):
            log.warning("tabela %s não existe ainda — rode admin/supabase_news_pulse.sql no Supabase "
                        "(nada gravado, sem erro)", TABLE)
            return 0
        raise RuntimeError(f"{TABLE} HTTP {r.status_code}: {r.text[:300]}")
    log.info("%s: %d linhas gravadas", TABLE, gravadas)
    return gravadas


# ── entrypoint ──────────────────────────────────────────────────────────────────────────
def _imprimir(rows: list[dict]) -> None:
    print("sessão      cut    n   pontuadas   +    −    =   no-take  pend  ponteiro  fontes(top3)")
    for r in rows:
        if r["sector"] != "all":
            continue
        top = ", ".join(f"{k}:{v}" for k, v in sorted(r["sources"].items(), key=lambda kv: -kv[1])[:3])
        g = "—" if r["gauge"] is None else str(r["gauge"])
        print(f"{r['session_date']}  {r['cut']:5s} {r['n_items']:4d}   {r['scored']:6d}   "
              f"{r['pos']:3d}  {r['neg']:3d}  {r['neu']:3d}   {r['notake']:5d}  {r['pending']:4d}   "
              f"{g:>5s}   {top}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sessions", type=int, default=SESSOES_PADRAO,
                    help=f"quantas sessões recalcular (padrão {SESSOES_PADRAO})")
    ap.add_argument("--backfill", action="store_true", help="todas as sessões desde --from")
    ap.add_argument("--from", dest="desde", default="2026-06-02",
                    help="início do backfill (o feed começa em 2026-06-02)")
    ap.add_argument("--dry-run", action="store_true", help="imprime a tabela e não grava")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    agora = dt.datetime.now(dt.timezone.utc)
    hoje = agora.astimezone(BRT).date()
    if args.backfill:
        sessoes = sessoes_entre(dt.date.fromisoformat(args.desde), hoje)
    else:
        sessoes = sessoes_ate(hoje, args.sessions)
    if not sessoes:
        log.warning("nenhuma sessão no intervalo")
        return 0
    ini = janela(sessoes[0], "close")[0]
    try:
        artigos = buscar_artigos(ini)
    except Exception as e:  # rede, credencial
        log.error("não deu para ler news_articles: %s", e)
        return 1
    rows = linhas(artigos, sessoes, agora)
    log.info("%d manchetes desde %s → %d linhas (%d sessões, %d assentadas)",
             len(artigos), ini.date(), len(rows), len(sessoes),
             len({r["session_date"] for r in rows}))
    if args.dry_run:
        _imprimir(rows)
        return 0
    try:
        gravar(rows)
    except Exception as e:
        log.error("gravação falhou: %s", e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
