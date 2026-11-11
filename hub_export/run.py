"""O ciclo do exportador: monta o que está na vez, compara com o último envio e manda ao Feeder só
o que mudou. Usado pelo hunt.py (fim de cada ciclo do hunt-loop, com HUB_EXPORT_ENABLED=1 dentro
do GitHub Actions) e pelo CLI (`python -m hub_export`).

Por arquivo, nesta ordem (HOT primeiro — se o orçamento acabar, quem espera é o COLD; dentro da
cadência, a ordem do registro: as semanas antes do índice):
  1. na vez? (cadência; `--force` ignora; o índice também vai quando uma semana foi publicada) —
     fora da vez nem monta, nem lê o banco;
  2. monta (campo a campo), codifica (JSON compacto; gzip se marcado) e calcula o hash do
     conteúdo sem `generatedAt`/`updatedAt`;
  3. decide: hash diferente do último ENVIO → envia; igual → só a pulsação do live.json (10 min)
     manda reenviar; sem envio no estado (runner novo) → compara com a cópia PUBLICADA
     (igual = registra e não envia);
  4. envia (initialize → PUT → complete) e grava hash/enviado em/revisão.
As famílias são expandidas na vez delas: `news-week` (news.py diz quais semanas vão nesta execução),
`history` (history.py: a fila do loop, no máximo 2 por execução, ou os papéis pedidos pelo nome
ou pelo `--history`) e `mw-short` (mw.py: as empresas visíveis na vez, ou as pedidas pelo nome).
Erro num arquivo vira uma linha de aviso e NÃO impede os outros (e o arquivo
tenta de novo no ciclo seguinte). Orçamento de 90 s por execução no loop: arquivo que não coube
fica para o próximo ciclo — a família `history` vem por último no COLD, então é ela que espera.
TETO POR GRUPO (registry.GROUP_CAPS; hoje o Market Watch, 4 por execução): o arquivo que mudou mas
passaria do teto fica "na fila" — não conta como erro e, sem o build registrado, vai no ciclo
seguinte. Vale no envio e é simulado no dry-run; o --out grava tudo.

Modos: "send" (o normal), "dry" (monta e diz o que enviaria) e "out" (grava os arquivos numa
pasta exatamente como seriam enviados — gzip inclusive). "dry" e "out" NUNCA mexem no registro
de envio (hash, enviado em, revisão, último build, semanas publicadas); só atualizam os caches de
dados (cotações, spark, diário, notícias, resumo, calendário), que são cópias do banco conferidas
por sonda — é o que deixa a 2ª execução barata.

Log: uma linha por arquivo (nome, bytes, enviado/igual/fora da vez/erro), uma das notícias (o
que foi lido) e uma de tráfego do Supabase (chamadas, corpo pela rede, cabeçalhos, por fonte,
uso da memória do ciclo, tempo).

SETORES (Onda 3, sectors.py): os 36 arquivos `mm-*`/`pp-*` têm cadência EVENT — só vão "na vez" pelo
`urgent` (blob novo do banco no GitHub, ou arquivo na fila), nunca pelo relógio; no dry-run, fora da
vez nem são montados (montar baixaria o banco). Sem `GH_TOKEN` no envio, nem entram no ciclo (uma
linha de log); passado o teto do grupo `sectors`, o que resta fica na fila SEM ser montado. O "fora
da vez" deles não tem linha própria no log (seriam 36 linhas a cada 5 min): a linha "setores: …" do
fim da execução resume sonda, download, fila e avisos.
"""
from __future__ import annotations

import logging
import os
import re
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from . import history as H
from . import mw as MW
from . import sectors as SE
from . import state as state_mod
from . import weeks as W
from .encode import content_hash, forbidden_keys, gzip_bytes, iso_utc, to_json_bytes
from .feeder import FeederClient, missing_env
from .github import token_from
from .news import news_state_path
from .registry import (GROUP_CAPS, HISTORY_FAMILY, MW_FAMILY, WEEK_FAMILY, cadence_of, position,
                       resolve, spec_for)
from .schedule import EVENT, is_due, pulse_due
from .sources import Sources, cycle_memory
from .supa import SupaReader

log = logging.getLogger(__name__)

BUDGET_S = 90.0
MODES = ("send", "dry", "out")
_URL_RE = re.compile(r"https?://\S+")


@dataclass
class Result:
    name: str
    action: str                   # enviado | igual | fora da vez | erro | adiado | gravado | enviaria
                                  # | na fila (passaria do teto do grupo: vai no ciclo seguinte)
    bytes: int | None = None      # como seria enviado (gzip quando o arquivo é gzip)
    raw_bytes: int | None = None  # JSON antes do gzip
    gz_bytes: int | None = None   # tamanho comprimido (referência, mesmo p/ os que vão em texto)
    hash: str | None = None
    revision: int | None = None
    due: bool | None = None       # na vez?
    changed: bool | None = None   # mudou em relação ao último envio (ou à cópia publicada)?
    reason: str | None = None     # mudou | pulsação | partida a frio | --force | mensagem de erro
    items: str | None = None      # resumo do conteúdo (contagens), p/ o CLI

    @property
    def failed(self) -> bool:
        return self.action in ("erro", "adiado")


def _kb(n: int | None) -> str:
    return "-" if n is None else f"{n / 1024:.1f} KB".replace(".", ",")


def _scrub(msg: str, secrets) -> str:
    """Mensagem de erro sem URL nem segredo (defesa extra: as nossas exceções já saem limpas)."""
    msg = _URL_RE.sub("<url>", msg)
    for s in secrets:
        if s and len(s) >= 6:
            msg = msg.replace(s, "<segredo>")
    return msg[:300]


def _items(name: str, obj: dict) -> str:
    if name == "config.json":
        hm = obj.get("heatmap") or {}
        return (f"{len(obj.get('flags') or {})} flags, {len(obj.get('exchanges') or {})} bolsas, "
                f"{len(hm.get('groups') or [])} grupos (cobertura {len(hm.get('coverage') or [])}, "
                f"ocultos {len(hm.get('hidden') or [])}), "
                f"{len(obj.get('commodityGroups') or [])} grupos de commodities, "
                f"{len(obj.get('coveredCompanies') or [])} empresas cobertas")
    if name == "live.json":
        q = (obj.get("quotes") or {}).get("items") or []
        c = (obj.get("commodities") or {}).get("items") or []
        m = (obj.get("macro") or {}).get("items") or []
        return (f"{len(q)} cotações ({sum(1 for i in q if i.get('price') is not None)} com preço), "
                f"{len(c)} commodities ({sum(1 for i in c if i.get('spark'))} com spark), "
                f"{len(m)} macro")
    if name == "news-latest.json" or W.label_from_file(name):
        it = obj.get("items") or []
        sem = sum(1 for i in it if i.get("snippet") is None)
        span = (f"desde {obj.get('windowStart')}" if name == "news-latest.json"
                else f"{obj.get('from')}…{obj.get('to')}")
        return f"{len(it)} notícias {span} ({sem} sem trecho)"
    if name == "news-index.json":
        return f"{len(obj.get('weeks') or [])} semanas, {obj.get('count')} notícias"
    if name == "news-digest.json":
        ws = obj.get("windows") or []
        by = {s: sum(1 for w in ws if w.get("status") == s) for s in ("ok", "list", "quiet")}
        return f"{len(ws)} janelas (ok {by['ok']}, lista {by['list']}, quietas {by['quiet']})"
    if name == "reports.json":
        it = obj.get("items") or []
        return f"{len(it)} relatórios ({sum(1 for i in it if i.get('link'))} com link)"
    if name == "calendar.json":
        ev = obj.get("events") or []
        return (f"{len(ev)} eventos ({sum(1 for e in ev if e.get('recurrence'))} recorrentes), "
                f"{len(obj.get('categories') or [])} categorias, "
                f"{len(obj.get('dataWindows') or [])} janelas de dado")
    if name == "ratings.json":
        cos = obj.get("companies") or []
        return f"{len(cos)} empresas, {sum(len(c.get('events') or []) for c in cos)} eventos"
    if name == "history-index.json":
        return f"{len(obj.get('items') or [])} históricos publicados"
    if name == "mw-companies.json":
        cos = obj.get("companies") or []
        com_capital = sum(1 for c in cos if c.get("capital"))
        return (f"{len(cos)} empresas ({com_capital} com capital), "
                f"{len(obj.get('shortLatest') or [])} papéis no aluguel até {obj.get('lendingThrough')}")
    if name == "mw-short-interest.json" or MW.company_from_file(name):
        series = obj.get("series") or {}
        rows = [r for s in series.values() for r in (s.get("rows") if isinstance(s, dict) else s) or []]
        days = sorted(r[0] for r in rows if r)
        span = f"{days[0]} a {days[-1]}" if days else "sem linhas"
        return f"{len(series)} papéis, {len(rows)} linhas ({span})"
    if name == "mw-buybacks.json":
        it = obj.get("items") or []
        return f"{len(it)} programas ({sum(1 for i in it if i.get('status'))} com situação)"
    if name == "mw-insiders.json":
        return f"{len(obj.get('rows') or [])} linhas desde {obj.get('from')}"
    if name == "mw-filings.json":
        it = obj.get("items") or []
        return (f"{len(it)} comunicados ({sum(1 for i in it if not i.get('url'))} sem link, "
                f"{sum(1 for i in it if i.get('docExcerpt'))} com trecho)")
    if SE.sector_of(name):
        return _sector_items(name, obj)
    if H.ticker_from_file(name):
        def day(ts):
            return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d") if ts else "?"
        # (sem seta: o console do Windows, cp1252, não imprime "→" e o CLI cairia na tabela final)
        return (f"{obj.get('points')} pontos, {day(obj.get('firstTs'))} a {day(obj.get('lastTs'))}"
                + (f", split {day(obj['lastSplitTs'])}" if obj.get("lastSplitTs") else ""))
    series = obj.get("series") or {}
    pts = sum(len(s.get("pts") or []) for s in series.values() if isinstance(s, dict))
    return f"{len(series)} séries, {pts} pontos"


def _sector_items(name: str, obj: dict) -> str:
    """O resumo de um arquivo dos setores para o CLI (sem seta: o console cp1252 do Windows)."""
    span = f"{obj.get('from')} a {obj.get('through')}"
    if name in SE.INDEX.values():
        src = obj.get("source") or {}
        dt = ", ".join(f"{k} {v}" for k, v in (obj.get("dataThrough") or {}).items())
        return (f"blob {(src.get('blob') or '?')[:7]} (commit {(src.get('commit') or '-')[:7]}); "
                f"dados até: {dt}; {len(obj.get('raw') or [])} tabelas brutas")
    if name in SE.RAW_TABLE_OF:
        data = obj.get("data") or []
        rows = len(data[0]) if data else 0
        return (f"{obj.get('table')}: {rows} linhas x {len(obj.get('columns') or [])} colunas "
                f"({len(obj.get('dict') or {})} em dicionário)")
    if name.startswith("mm-countries-"):
        cats = obj.get("categories") or {}
        return (f"{len(obj.get('countries') or [])} países, {len(cats)} categorias, "
                f"{sum(len(v) for v in cats.values())} séries ({span})")
    if name == "mm-domestic.json":
        i, n = obj.get("iabr") or {}, obj.get("inda") or {}
        return f"IABr {i.get('from')} a {i.get('through')}, INDA {n.get('from')} a {n.get('through')}"
    if name == "mm-prediction.json":
        im, ex = obj.get("imports") or {}, obj.get("exports") or {}
        return (f"{len(im.get('series') or {})} produtos; SECEX até {im.get('through')}, "
                f"aduana até {ex.get('through')}")
    if name == "pp-pulp-exports.json":
        ports, dest = obj.get("ports") or {}, obj.get("destinations") or {}
        return (f"{len(ports.get('names') or [])} portos, {len(dest.get('countries') or [])} destinos, "
                f"{len(dest.get('products') or {})} produtos ({span})")
    if name == "pp-woodchips.json":
        return (f"{len(obj.get('suppliers') or [])} fornecedores, "
                f"{len(obj.get('fibres') or {})} fibras ({span})")
    return span


def _decide(name, spec, fs, h, published, now, force):
    """(enviar?, motivo, mudou?, registro). `registro` = o que gravar no estado quando a partida
    a frio acha a cópia publicada IGUAL (ela passa a contar como o último envio)."""
    if force:
        return True, "--force", None, None
    last = fs.get("hash")
    if last is None:
        pub = published.content_hash(name) if published is not None else None
        if pub is None or pub != h:
            return True, "partida a frio", True, None
        sent = iso_utc((published.get(name) or {}).get("generatedAt"))
        rec = {"hash": h, "sentAt": sent, "revision": None}
        if pulse_due(spec.pulse, sent, now):
            return True, "pulsação", False, rec
        return False, "igual à cópia publicada", False, rec
    if last != h:
        return True, "mudou", True, None
    if pulse_due(spec.pulse, fs.get("sentAt"), now):
        return True, "pulsação", False, None
    return False, None, False, None


def _after(spec, src, obj, action) -> None:
    if spec.after is not None and obj is not None:
        try:
            spec.after(src, obj, action)
        except Exception as e:          # o registro das semanas nunca derruba o arquivo
            log.warning("pós-envio falhou (%s: %s)", type(e).__name__, e)


def _capped(spec, group_sent) -> str | None:
    """O motivo de ficar na fila quando o grupo do arquivo já bateu o teto nesta execução."""
    cap = GROUP_CAPS.get(spec.group) if spec.group else None
    if cap is None or group_sent is None or group_sent[spec.group] < cap:
        return None
    return f"teto de {cap} envios do grupo {spec.group} por execução — vai no próximo ciclo"


def _one(name, spec, *, st, src, mode, force, now, deadline, clock, feeder, published, out_dir,
         secrets, group_sent=None) -> Result:
    files = st["files"]
    fs = dict(files.get(name) or {})
    if clock() >= deadline:
        return Result(name, "adiado", reason="orçamento de tempo")
    due = bool(force or mode == "out" or is_due(spec.cadence, fs.get("builtAt"), now))
    if not due and spec.urgent is not None:
        try:
            due = bool(spec.urgent(src))
        except Exception:
            due = False
    if not due and (mode == "send" or spec.cadence == EVENT):
        return Result(name, "fora da vez", due=False)     # EVENT: nem no dry-run (baixaria o banco)
    if spec.cadence == EVENT and mode in ("send", "dry"):
        queued = _capped(spec, group_sent)                # passado o teto, nem monta: fica na fila
        if queued:
            return Result(name, "na fila", due=True, reason=queued)
    generated = src.generated_at
    try:
        obj = spec.builder(src)
        proibidas = forbidden_keys(obj, extra=spec.forbidden)
        if proibidas:           # rede embaixo da lista branca: arquivo público não sai assim
            raise ValueError("chave proibida no arquivo: " + ", ".join(proibidas[:5]))
        raw = to_json_bytes(obj)
        # setores no envio: o gzip só de quem vai (num blob novo sem dado novo, os 22 do aço — 8 MB
        # só a base bruta dos países — saem "igual" e não precisam ser comprimidos)
        lazy = spec.cadence == EVENT and mode == "send"
        payload = None if lazy else (gzip_bytes(raw) if spec.gzip else raw)
        res = Result(name, "", bytes=None if lazy else len(payload), raw_bytes=len(raw),
                     gz_bytes=None if lazy else (len(payload) if spec.gzip else len(gzip_bytes(raw))),
                     hash=content_hash(obj), due=due, items=_items(name, obj))
        if len(raw) > spec.max_bytes:
            log.warning("%s: %s acima da meta de %s — vai assim mesmo", name, _kb(len(raw)),
                        _kb(spec.max_bytes))
        if mode == "out":
            (Path(out_dir) / name).write_bytes(payload)
            res.action = "gravado"
            _after(spec, src, obj, res.action)
            return res
        send, reason, changed, rec = _decide(name, spec, fs, res.hash, published, now, force)
        res.changed, res.reason = changed, reason
        queued = _capped(spec, group_sent) if send and due else None
        if mode == "dry":
            if queued:
                res.action, res.reason = "na fila", queued
            else:
                res.action = ("enviaria" if send else "igual") if due else "fora da vez"
                if res.action == "enviaria" and spec.group and group_sent is not None:
                    group_sent[spec.group] += 1
            _after(spec, src, obj, res.action)
            return res
        if rec:
            fs.update(rec)
        if send:
            if queued:                  # passaria do teto: sem o build registrado, vai no próximo
                files[name] = fs
                res.action, res.reason = "na fila", queued
                return res
            if clock() >= deadline:
                files[name] = fs
                res.action, res.reason = "adiado", "orçamento de tempo"
                return res
            if payload is None:
                payload = gzip_bytes(raw) if spec.gzip else raw
                res.bytes = len(payload)
                res.gz_bytes = len(payload) if spec.gzip else None
            res.revision = feeder.upload(name, payload, spec.content_type, modified_at=generated,
                                         deadline=deadline)
            fs.update(hash=res.hash, sentAt=generated, revision=res.revision)
            res.action = "enviado"
            if spec.group and group_sent is not None:
                group_sent[spec.group] += 1
        else:
            res.action = "igual"
        fs.update(builtAt=generated, lastError=None, lastErrorAt=None)
        files[name] = fs
        _after(spec, src, obj, res.action)
        return res
    except Exception as e:
        msg = _scrub(f"{type(e).__name__}: {e}", secrets)
        if mode == "send":
            fs.update(lastError=msg, lastErrorAt=generated)
            files[name] = fs
        return Result(name, "erro", due=due, reason=msg)


def _log_result(res: Result) -> None:
    parts = [res.name]
    if res.bytes is not None:
        parts.append(_kb(res.bytes))
    elif res.raw_bytes is not None:
        parts.append(_kb(res.raw_bytes) + " cru")     # setor "igual" no envio: nem comprimido
    parts.append(res.action)
    extra = []
    if res.revision is not None:
        extra.append(f"rev {res.revision}")
    if res.reason and res.action != "erro":
        extra.append(res.reason)
    line = " ".join(parts) + (f" ({', '.join(extra)})" if extra else "")
    if res.action == "erro":
        log.warning("%s: %s", line, res.reason)
    elif res.action == "adiado":
        log.warning("%s", line)
    else:
        log.info("%s", line)


def traffic_line(supa, src, seconds: float) -> str:
    """A linha de tráfego do Supabase de uma execução, com os bytes por fonte (corpo pela rede +
    cabeçalhos, que o plano grátis também conta)."""
    mem = [f"{n} {k}" for k, n in (("cotações", src.used_memory["quotes"]),
                                     ("macro", src.used_memory["macro"])) if n is not None]
    fontes = ", ".join(f"{tag} {s['calls']}/{_kb(s['wire'] + s['headers'])}"
                       for tag, s in sorted(supa.by_source.items()))
    return (f"Supabase: {supa.calls} chamada(s), {_kb(supa.wire_bytes)} de corpo + "
            f"{_kb(supa.header_bytes)} de cabeçalhos pela rede ({_kb(supa.body_bytes)} "
            f"descomprimidos) · por fonte: {fontes or 'nenhuma'} · memória do ciclo: "
            f"{', '.join(mem) if mem else 'nenhuma'} · {seconds:.1f} s")


def run_cycle(*, only=None, force: bool = False, mode: str = "send", out_dir=None,
              now: datetime | None = None, env=None, http=None, supa=None, feeder=None,
              published=None, memory: dict | None = None, state_file=None,
              budget_s: float = BUDGET_S, clock=time.monotonic, rebuild: bool = False,
              weeks=None, first_week: str | None = None, history=None) -> list[Result]:
    """Uma execução do exportador. Devolve um `Result` por arquivo processado (os das famílias, um
    a um). `weeks` (só --out/--dry-run): quantas semanas mais recentes montar (int) ou "all";
    `history` (só --out/--dry-run): os papéis cujo histórico montar (lista) ou "all";
    `rebuild`: relê do banco em vez das sementes e dos caches."""
    if mode not in MODES:
        raise ValueError(f"modo inválido: {mode}")
    env = os.environ if env is None else env
    t0 = clock()
    deadline = t0 + budget_s
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    names = resolve(only)
    if mode == "send" and token_from(env) is None and any(SE.sector_of(n) for n in names):
        # sem token, a API do GitHub dá 60 chamadas/h por IP, divididas com o runner inteiro
        names = [n for n in names if not SE.sector_of(n)]
        log.info("setores (mm-*, pp-*): sem GH_TOKEN — fora deste ciclo (o resto segue)")

    if mode == "send" and feeder is None:
        falta = missing_env(env)
        if falta:
            log.warning("Feeder não configurado (faltam %s) — nada enviado", ", ".join(falta))
            return []
        feeder = FeederClient.from_env(env, http=http, clock=clock)   # o mesmo relógio do prazo
    if mode == "out":
        if not out_dir:
            raise ValueError("o modo out exige a pasta de saída")
        Path(out_dir).mkdir(parents=True, exist_ok=True)

    supa = supa or SupaReader.from_env(env, http=http)
    path = Path(state_file) if state_file else state_mod.state_path(env)
    st = state_mod.load(path)
    if published is None:
        published = state_mod.PublishedCopies(http=http, env=env)
    history = history if mode != "send" else None
    src = Sources(supa, st, now=now, memory=cycle_memory() if memory is None else memory,
                  published=published, mode=mode, rebuild=rebuild, out_dir=out_dir,
                  weeks_arg=weeks if mode != "send" else None, news_path=news_state_path(path),
                  first_week=first_week, history_arg=history, mw_path=MW.mw_state_path(path),
                  env=env, http=http, cache_dir=path.parent)
    supa_url = env.get("SUPABASE_URL") or ""
    secrets = [env.get("DASHBOARD_FEEDER_API_KEY"), env.get("SUPABASE_SERVICE_KEY"), supa_url,
               urlsplit(supa_url).hostname if supa_url else None, token_from(env)]
    kw = dict(st=st, src=src, mode=mode, force=force, now=now, deadline=deadline, clock=clock,
              feeder=feeder, published=published, out_dir=out_dir, secrets=secrets,
              group_sent=Counter())

    explicit = [lab for lab in (W.label_from_file(n) for n in names) if lab]
    explicit_h = [t for t in (H.ticker_from_file(n) for n in names) if t]
    explicit_m = [co for co in (MW.company_from_file(n) for n in names) if co]
    entries = [n for n in names if not W.label_from_file(n) and not H.ticker_from_file(n)
               and not MW.company_from_file(n)]
    if explicit and WEEK_FAMILY not in entries:
        entries.append(WEEK_FAMILY)
    if (explicit_h or history) and HISTORY_FAMILY not in entries:
        entries.append(HISTORY_FAMILY)
    if explicit_m and MW_FAMILY not in entries:
        entries.append(MW_FAMILY)
    results = []
    for entry in sorted(entries, key=lambda n: (cadence_of(n), position(n))):
        todo = [entry]
        if entry in (WEEK_FAMILY, HISTORY_FAMILY, MW_FAMILY):
            if clock() >= deadline:
                todo = []
                results.append(Result(entry, "adiado", reason="orçamento de tempo"))
                _log_result(results[-1])
            else:
                try:
                    if entry == WEEK_FAMILY:
                        todo = [W.file_name(lab) for lab in src.news().week_labels(
                            explicit=explicit, auto=WEEK_FAMILY in names)]
                    elif entry == MW_FAMILY:
                        todo = [n for n in (MW.file_name(co) for co in src.mw_family_plan(
                            explicit=explicit_m, auto=MW_FAMILY in names, force=force)) if n]
                    else:
                        todo = [H.file_name(t) for t in src.history().plan(
                            explicit=explicit_h, auto=HISTORY_FAMILY in names)]
                except Exception as e:
                    todo = []
                    results.append(Result(entry, "erro",
                                          reason=_scrub(f"{type(e).__name__}: {e}", secrets)))
                    _log_result(results[-1])
        for name in todo:
            res = _one(name, spec_for(name), **kw)
            results.append(res)
            if not (res.action == "fora da vez" and SE.sector_of(name)):
                _log_result(res)                       # os setores ociosos: só a linha "setores:"

    src.finish_sectors()
    state_mod.save(path, st)
    src.save_news()
    src.save_mw()
    for store in (src.news_if_loaded(), src.history_if_loaded(), src.mw_if_loaded(),
                  src.sectors_if_loaded()):
        note = store.end_note() if store is not None else None
        if note:
            log.info("%s", note)
    log.info("%s", traffic_line(supa, src, clock() - t0))
    return results
