"""O ciclo do exportador: monta o que está na vez, compara com o último envio e manda ao Feeder só
o que mudou. Usado pelo hunt.py (fim de cada ciclo do hunt-loop, com HUB_EXPORT_ENABLED=1) e pelo
CLI (`python -m hub_export`).

Por arquivo, nesta ordem (HOT primeiro — se o orçamento acabar, quem espera é o COLD):
  1. na vez? (cadência; `--force` ignora) — fora da vez nem monta, nem lê o banco;
  2. monta (campo a campo), codifica (JSON compacto; gzip se marcado) e calcula o hash do
     conteúdo sem `generatedAt`/`updatedAt`;
  3. decide: hash diferente do último ENVIO → envia; igual → só a pulsação do live.json (15 min)
     manda reenviar; sem envio no estado (runner novo) → compara com a cópia PUBLICADA
     (igual = registra e não envia);
  4. envia (initialize → PUT → complete) e grava hash/enviado em/revisão.
Erro num arquivo vira uma linha de aviso e NÃO impede os outros (e o arquivo tenta de novo no
ciclo seguinte). Orçamento de 90 s por execução: arquivo que não coube fica para o próximo ciclo.

Modos: "send" (o normal), "dry" (monta e diz o que enviaria) e "out" (grava os arquivos numa
pasta exatamente como seriam enviados — gzip inclusive). "dry" e "out" NUNCA mexem no registro
de envio (hash, enviado em, revisão, último build); só atualizam os caches de dados (cotações,
spark, diário), que são cópias do banco conferidas por sonda — é o que deixa a 2ª execução barata.

Log: uma linha por arquivo (nome, bytes, enviado/igual/fora da vez/erro) e uma de tráfego do
Supabase (chamadas, bytes pela rede e descomprimidos, uso da memória do ciclo, tempo).
"""
from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from . import state as state_mod
from .encode import content_hash, forbidden_keys, gzip_bytes, iso_utc, to_json_bytes
from .feeder import FeederClient, missing_env
from .registry import FILES, resolve
from .schedule import is_due, pulse_due
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
                f"{len(obj.get('commodityGroups') or [])} grupos de commodities")
    if name == "live.json":
        q = (obj.get("quotes") or {}).get("items") or []
        c = (obj.get("commodities") or {}).get("items") or []
        m = (obj.get("macro") or {}).get("items") or []
        return (f"{len(q)} cotações ({sum(1 for i in q if i.get('price') is not None)} com preço), "
                f"{len(c)} commodities ({sum(1 for i in c if i.get('spark'))} com spark), "
                f"{len(m)} macro")
    series = obj.get("series") or {}
    pts = sum(len(s.get("pts") or []) for s in series.values() if isinstance(s, dict))
    return f"{len(series)} séries, {pts} pontos"


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


def _one(name, *, st, src, mode, force, now, deadline, clock, feeder, published, out_dir,
         secrets) -> Result:
    spec = FILES[name]
    files = st["files"]
    fs = dict(files.get(name) or {})
    if clock() >= deadline:
        return Result(name, "adiado", reason="orçamento de tempo")
    due = bool(force or mode == "out" or is_due(spec.cadence, fs.get("builtAt"), now))
    if mode == "send" and not due:
        return Result(name, "fora da vez", due=False)
    generated = src.generated_at
    try:
        obj = spec.builder(src)
        proibidas = forbidden_keys(obj)
        if proibidas:           # rede embaixo da lista branca: arquivo público não sai assim
            raise ValueError("chave proibida no arquivo: " + ", ".join(proibidas[:5]))
        raw = to_json_bytes(obj)
        payload = gzip_bytes(raw) if spec.gzip else raw
        res = Result(name, "", bytes=len(payload), raw_bytes=len(raw),
                     gz_bytes=len(payload) if spec.gzip else len(gzip_bytes(raw)),
                     hash=content_hash(obj), due=due, items=_items(name, obj))
        if len(raw) > spec.max_bytes:
            log.warning("%s: %s acima da meta de %s — vai assim mesmo", name, _kb(len(raw)),
                        _kb(spec.max_bytes))
        if mode == "out":
            (Path(out_dir) / name).write_bytes(payload)
            res.action = "gravado"
            return res
        send, reason, changed, rec = _decide(name, spec, fs, res.hash, published, now, force)
        res.changed, res.reason = changed, reason
        if mode == "dry":
            res.action = ("enviaria" if send else "igual") if due else "fora da vez"
            return res
        if rec:
            fs.update(rec)
        if send:
            if clock() >= deadline:
                files[name] = fs
                res.action, res.reason = "adiado", "orçamento de tempo"
                return res
            res.revision = feeder.upload(name, payload, spec.content_type, modified_at=generated,
                                         deadline=deadline)
            fs.update(hash=res.hash, sentAt=generated, revision=res.revision)
            res.action = "enviado"
        else:
            res.action = "igual"
        fs.update(builtAt=generated, lastError=None, lastErrorAt=None)
        files[name] = fs
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


def run_cycle(*, only=None, force: bool = False, mode: str = "send", out_dir=None,
              now: datetime | None = None, env=None, http=None, supa=None, feeder=None,
              published=None, memory: dict | None = None, state_file=None,
              budget_s: float = BUDGET_S, clock=time.monotonic) -> list[Result]:
    """Uma execução do exportador. Devolve um `Result` por arquivo pedido."""
    if mode not in MODES:
        raise ValueError(f"modo inválido: {mode}")
    env = os.environ if env is None else env
    t0 = clock()
    deadline = t0 + budget_s
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    names = resolve(only)

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
    src = Sources(supa, st, now=now, memory=cycle_memory() if memory is None else memory,
                  published=published)
    supa_url = env.get("SUPABASE_URL") or ""
    secrets = [env.get("DASHBOARD_FEEDER_API_KEY"), env.get("SUPABASE_SERVICE_KEY"), supa_url,
               urlsplit(supa_url).hostname if supa_url else None]

    results = []
    for name in sorted(names, key=lambda n: FILES[n].cadence):
        res = _one(name, st=st, src=src, mode=mode, force=force, now=now, deadline=deadline,
                   clock=clock, feeder=feeder, published=published, out_dir=out_dir,
                   secrets=secrets)
        results.append(res)
        _log_result(res)

    state_mod.save(path, st)
    mem = [f"{n} {k}" for k, n in (("cotações", src.used_memory["quotes"]),
                                     ("macro", src.used_memory["macro"])) if n is not None]
    log.info("Supabase: %d chamada(s), %s pela rede (%s descomprimidos) · memória do ciclo: %s · "
             "%.1f s", supa.calls, _kb(supa.wire_bytes), _kb(supa.body_bytes),
             ", ".join(mem) if mem else "nenhuma", clock() - t0)
    return results
