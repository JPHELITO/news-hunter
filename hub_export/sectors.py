"""Os arquivos dos setores — Steel & Mining (`mm-*`) e Pulp & Paper (`pp-*`), Onda 3 (PLANO,
Apêndice G; pesquisa 3-dados §5): de onde vem cada banco, quando montar e a ordem de envio.

A ORIGEM são os dois bancos SQLite do repositório PÚBLICO do site — `Steel and Mining/
steel_sm_web.db.gz` (o banco web, nunca o completo) e `Pulp and Paper/pulp_paper.db` —, lidos pela
API do GitHub (github.py) e conferidos pelo SHA do git. Zero Supabase.

CADÊNCIA EVENT (schedule.EVENT): os 36 arquivos (17 de dado/índice + os 19 da base bruta, G.11)
nunca ficam "na vez" pelo relógio — só pelo `urgent`, que é este plano:
  • SONDA: o SHA do blob de cada banco, no máximo 1×/15 min (2 GETs; ~192/dia).
  • BLOB NOVO → todos os arquivos de dado do banco entram na FILA (`pending`); o banco é baixado UMA
    vez (conferido) para `$RUNNER_TEMP/hub_export_<setor>_<blob>.sqlite` (já descomprimido) e
    reaproveitado nos ciclos seguintes enquanto houver arquivo na fila; cada arquivo é montado
    (`sqlite3` em `mode=ro`, com a conferência de esquema) e decidido pelo hash do JSON: mudou →
    envia; igual → sai da fila sem envio (3 de 9 commits do aço trocam o blob sem trocar o dado).
  • GRUPO `sectors` com teto de 4 envios por execução (registry.GROUP_CAPS): o que passar fica na
    fila e vai no ciclo seguinte, sem baixar de novo. Depois do teto, nem é montado.
  • O ÍNDICE do setor (`mm-index.json`, `pp-index.json`) vai POR ÚLTIMO e só quando a fila do blob
    esvaziou (tudo enviado ou igual ao último envio): ele diz "publicado até o blob X" e leva o
    `source.blob` — muda (e vai) a cada blob novo, mesmo sem dado novo.
  • Arquivo da base bruta de tabela que não existe no banco sai da fila sem publicar (o painel do
    site lista só as tabelas que existem — o índice também, no campo `raw`).

PARTIDA A FRIO (runner novo, ~4,4 por dia): lê o índice PUBLICADO (≤ 1 KB do Mindassets) e a sonda:
  `source.blob` igual → nada a montar (2 GETs no GitHub, nenhum download);
  diferente, ou sem índice (404 com o Mindassets publicando) → fila inteira: baixa, monta e compara
  cada arquivo com a cópia PUBLICADA (run.py) — só vai o que difere;
  Mindassets PARADO (o índice não vem e o live.json publicado está velho ou fora do ar) → a montagem
  fica para a hora diária (07 UTC): no máximo 1 por dia, e não a cada runner novo.

SEM TOKEN NO LOOP: o run.py nem chama os setores (uma linha de log). GitHub fora do ar ou no limite
de taxa: nenhum arquivo do setor neste ciclo (uma linha de log) e o resto do ciclo segue; o último
arquivo publicado continua no ar e o plano tenta de novo no ciclo seguinte.

MODOS. "send": o estado acima (`sectors` no arquivo de estado). "dry": o mesmo plano sobre uma CÓPIA
do estado (não grava nada). "out": monta tudo o que foi pedido, sem plano. No --out/--dry-run,
`HUB_EXPORT_SECTORS_DB_DIR` aponta uma pasta com os dois bancos (os nomes do repositório:
`steel_sm_web.db.gz`, `pulp_paper.db`): sem rede; o blob vem do sha1 do arquivo e o índice sai sem
`commit`/`committedAt`.
"""
from __future__ import annotations

import copy
import gzip
import logging
import os
import sqlite3
import tempfile
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .encode import iso_utc
from .github import GitHubClient, git_blob_sha, token_from

log = logging.getLogger(__name__)

MM, PP = "mm", "pp"
SECTORS = (MM, PP)
GROUP = "sectors"
DB_DIR_ENV = "HUB_EXPORT_SECTORS_DB_DIR"
PROBE_EVERY_S = 900 - 60          # WARM (15 min) com a folga do relógio do loop (schedule.SLACK_S)
OUTAGE_HOUR_UTC = 7               # Mindassets parado + sem índice: montagem só nesta hora (1×/dia)
CACHE_PREFIX = "hub_export_"


@dataclass(frozen=True)
class Db:
    path: str          # caminho no repositório do site
    gz: bool           # o arquivo do repositório é gzip (o banco web do aço)
    label: str


DBS = {MM: Db("Steel and Mining/steel_sm_web.db.gz", True, "Steel & Mining"),
       PP: Db("Pulp and Paper/pulp_paper.db", False, "Pulp & Paper")}
INDEX = {MM: "mm-index.json", PP: "pp-index.json"}

# arquivos por direção × aba de produto (G.5): as categorias de cada aba, na ordem de `SCTY_CATS`
DIRECTIONS = ("imp", "exp")
COUNTRY_TABS = {"total": ("flat", "long", "semi"),
                "flat": ("flat", "hrc", "crc", "coated", "heavy_plate", "flat_others"),
                "long": ("long", "rebar", "bar", "wire_rod", "shapes", "long_others"),
                "semi": ("semi", "ingot_billet", "placa")}

# a base bruta "⤓ Data" (G.11, U7): as listas `RAW_TABLES` das duas páginas, na ordem delas
# (`SMD:1626-1638`, `PPD:1242-1251`). Tabela nova no banco do site → acrescentar aqui (e no hub).
RAW_TABLES = {MM: ("secex_imports", "secex_exports", "secex_country", "iabr_production",
                   "iabr_domestic_sales", "iabr_consumption", "iabr_exports", "iabr_imports",
                   "inda_distribution", "import_prediction", "pred_exports"),
              PP: ("secex_pulp_port", "secex_pulp_country", "gacc_woodchips", "iba_pulp", "iba_paper",
                   "empapel", "company_q", "calendar")}


def country_file(direction: str, tab: str) -> str:
    return f"mm-countries-{direction}-{tab}.json"


def raw_file(sector: str, table: str) -> str:
    """`mm-raw-secex-country.json`: minúsculas e hífen (a regra de nome do PLANO §2.5)."""
    return f"{sector}-raw-{table.replace('_', '-')}.json"


COUNTRY_FILES = [country_file(d, t) for d in DIRECTIONS for t in COUNTRY_TABS]
DATA_FILES = {MM: ["mm-trade.json", "mm-domestic.json", "mm-prediction.json", *COUNTRY_FILES],
              PP: ["pp-pulp-exports.json", "pp-woodchips.json", "pp-paper.json", "pp-corrugated.json"]}
RAW_FILES = {s: [raw_file(s, t) for t in RAW_TABLES[s]] for s in SECTORS}
RAW_TABLE_OF = {raw_file(s, t): (s, t) for s in SECTORS for t in RAW_TABLES[s]}
# a ordem de processamento (registry.ORDER): os dados da tela, a base bruta e, POR ÚLTIMO, o índice
FILES_OF = {s: DATA_FILES[s] + RAW_FILES[s] + [INDEX[s]] for s in SECTORS}
ALL_FILES = FILES_OF[MM] + FILES_OF[PP]
_SECTOR_OF = {n: s for s in SECTORS for n in FILES_OF[s]}
# atalhos do --only / `files` do workflow
ALIASES = {"sectors": ALL_FILES, MM: FILES_OF[MM], PP: FILES_OF[PP],
           "mm-countries": COUNTRY_FILES, "mm-raw": RAW_FILES[MM], "pp-raw": RAW_FILES[PP]}


def sector_of(name) -> str | None:
    return _SECTOR_OF.get(name)


def queue_of(sector: str) -> list[str]:
    """A fila de um blob novo: os arquivos de dado e da base bruta (o índice vai depois deles)."""
    return DATA_FILES[sector] + RAW_FILES[sector]


# ── os ganchos do registro (Spec.urgent / Spec.after) ─────────────────────────────────────────
def urgent(name: str, src) -> bool:
    return src.sectors().urgent(name)


def after(name: str, src, obj, action: str) -> None:
    src.sectors().after(name, obj, action)


def open_ro(path) -> sqlite3.Connection:
    """`sqlite3` em modo SÓ LEITURA (`mode=ro` pela URI do arquivo)."""
    return sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)


class SectorWaiting(RuntimeError):
    """O setor não monta neste ciclo (Mindassets parado sem o índice publicado)."""


class SectorStore:
    """Os setores de UMA execução: sonda, plano, banco (download/cache), índice e o log."""

    def __init__(self, src, env=None, http=None, cache_dir=None):
        env = env or {}
        self.src = src
        self.mode = src.mode
        self.now = src.now
        self.pub = src.published
        self.rebuild = src.rebuild
        self.gh = GitHubClient(token=token_from(env), http=http)
        db_dir = (env.get(DB_DIR_ENV) or "").strip() if isinstance(env.get(DB_DIR_ENV), str) else ""
        self.db_dir = Path(db_dir) if db_dir and self.mode in ("out", "dry") else None
        self.db_dir_ignored = bool(db_dir) and self.mode == "send"
        self.cache_dir = Path(cache_dir) if cache_dir else Path(tempfile.gettempdir())
        self.state = src.state.setdefault("sectors", {})
        self._sim: dict[str, dict] = {}            # dry: o plano sobre uma cópia do estado
        self._plans: dict[str, object] = {}        # setor → True | exceção (memo da execução)
        self._dbs: dict[str, object] = {}          # setor → conexão | exceção
        self._paths: dict[str, Path] = {}
        self._source: dict[str, dict] = {}         # out: {blob, size, commit, committedAt}
        self._local: dict[str, bytes] = {}
        self.waiting: dict[str, str] = {}
        self.downloads: dict[str, int] = {}
        self.probed: list[str] = []
        self.done: dict[str, Counter] = {s: Counter() for s in SECTORS}
        self.notes: list[str] = []
        if self.db_dir_ignored:
            self.notes.append(f"{DB_DIR_ENV} ignorado no envio (só vale no --out/--dry-run)")

    # ── estado ───────────────────────────────────────────────────────────────────────────────
    def registry(self, s: str) -> dict:
        """O registro do setor: o do estado (envio) ou uma cópia (dry-run); no --out, vazio."""
        if self.mode == "send":
            return self.state.setdefault(s, {})
        if s not in self._sim:
            self._sim[s] = copy.deepcopy(self.state.get(s) or {}) if self.mode == "dry" else {}
        return self._sim[s]

    def pending(self, s: str) -> list[str]:
        return list(self.registry(s).get("pending") or [])

    def _fresh(self, stamp, max_age: float) -> bool:
        iso = iso_utc(stamp)
        if not iso:
            return False
        then = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        return 0 <= (self.now - then).total_seconds() < max_age

    # ── a origem: GitHub (sonda + download) ou a pasta local do --out ─────────────────────────
    def _local_bytes(self, s: str) -> bytes:
        if s not in self._local:
            p = self.db_dir / Path(DBS[s].path).name
            if not p.is_file():
                raise FileNotFoundError(f"{DB_DIR_ENV}: falta {p.name} na pasta")
            self._local[s] = p.read_bytes()
        return self._local[s]

    def _probe(self, s: str) -> tuple[str, int]:
        if self.db_dir is not None:
            data = self._local_bytes(s)
            return git_blob_sha(data), len(data)
        self.probed.append(s)
        return self.gh.probe(DBS[s].path)

    def _commit(self, s: str) -> tuple[str | None, str | None]:
        if self.db_dir is not None:
            return None, None
        return self.gh.last_commit(DBS[s].path)

    # ── o plano (envio e dry-run) ────────────────────────────────────────────────────────────
    def plan(self, s: str) -> None:
        """Sonda + partida a frio + blob novo → a fila. Uma vez por execução; falha memorizada."""
        got = self._plans.get(s)
        if isinstance(got, BaseException):
            raise got
        if got:
            return
        try:
            self._plan(s)
            self._plans[s] = True
        except Exception as e:
            self._plans[s] = e
            raise

    def _plan(self, s: str) -> None:
        reg = self.registry(s)
        if self.rebuild:                            # --rebuild: nem o registro nem o índice publicado
            for k in ("seeded", "blob", "indexBlob", "pending", "probeAt", "probeBlob"):
                reg.pop(k, None)
        # 1) o blob de agora: a sonda vale 15 min (a fila anda nos ciclos de 5 min sem sondar)
        if self.db_dir is None and reg.get("probeBlob") and self._fresh(reg.get("probeAt"), PROBE_EVERY_S):
            blob, size = reg["probeBlob"], reg.get("probeSize")
        else:
            blob, size = self._probe(s)
            reg.update(probeBlob=blob, probeSize=size, probeAt=self.src.generated_at)
        # 2) partida a frio: o índice publicado diz até onde já foi publicado
        if not reg.get("seeded"):
            status, doc = self._published_index(s)
            if status == "ok":
                pub = doc["source"]
                if pub.get("blob") == blob:
                    reg.update(seeded=True, blob=blob, size=size, commit=pub.get("commit"),
                               committedAt=iso_utc(pub.get("committedAt")), indexBlob=blob, pending=[])
                    self.notes.append(f"{s}: partida a frio — o índice publicado já é do blob "
                                      f"{blob[:7]}: nada a montar")
                    return
                reg.update(seeded=True, blob=None, indexBlob=pub.get("blob"), pending=[])
            elif status == "none":
                reg.update(seeded=True, blob=None, indexBlob=None, pending=[])
            else:
                today = self.now.date().isoformat()
                if self.now.hour == OUTAGE_HOUR_UTC and reg.get("outageDay") != today:
                    reg.update(seeded=True, blob=None, indexBlob=None, pending=[], outageDay=today)
                    self.notes.append(f"{s}: Mindassets parado e sem o índice publicado — a montagem "
                                      f"do dia ({OUTAGE_HOUR_UTC:02d} UTC) vai agora")
                else:
                    self.waiting[s] = (f"Mindassets parado e sem o índice publicado — montagem só às "
                                       f"{OUTAGE_HOUR_UTC:02d} UTC (1×/dia)")
                    return
        # 3) blob novo → a fila inteira (o índice vai depois dela)
        if blob != reg.get("blob"):
            commit, committed = self._commit(s)
            reg.update(blob=blob, size=size, commit=commit, committedAt=committed,
                       pending=queue_of(s), queuedAt=self.src.generated_at)
            self.notes.append(f"{s}: blob novo {blob[:7]}"
                              + (f" (commit {commit[:7]} de {committed})" if commit else "")
                              + f" — {len(queue_of(s))} arquivo(s) na fila")

    def _published_index(self, s: str) -> tuple[str, dict | None]:
        """("ok", índice) | ("none", None) = nada publicado (404 com o Mindassets publicando, sem
        Mindassets configurado, --rebuild) | ("down", None) = não dá para saber (Mindassets parado)."""
        if self.pub is None or self.rebuild:
            return "none", None
        doc = self.pub.get(INDEX[s])
        src_ = doc.get("source") if isinstance(doc, dict) else None
        if (isinstance(doc, dict) and doc.get("schemaVersion") == 1 and isinstance(src_, dict)
                and isinstance(src_.get("blob"), str)):
            return "ok", doc
        if self.pub.status(INDEX[s]) in ("ausente", "erro", "ok") and self.pub.healthy(self.now):
            return "none", None
        return "down", None

    def urgent(self, name: str) -> bool:
        """Na vez fora do relógio? Dado/base bruta: está na fila do blob. Índice: a fila esvaziou e
        o índice publicado é de outro blob."""
        s = sector_of(name)
        if s is None:
            return False
        if self.mode == "out":
            return True
        try:
            self.plan(s)
        except Exception as e:
            self._fail(s, e)
            return False
        if s in self.waiting:
            return False
        reg = self.registry(s)
        if name == INDEX[s]:
            return (bool(reg.get("blob")) and reg.get("indexBlob") != reg.get("blob")
                    and not reg.get("pending"))
        return name in (reg.get("pending") or [])

    def _fail(self, s: str, e: Exception) -> None:
        msg = f"{s}: {type(e).__name__}: {e} — nenhum arquivo do setor neste ciclo"
        if msg not in self.notes:
            self.notes.append(msg)

    def after(self, name: str, obj, action: str) -> None:
        """Depois da decisão de um arquivo: enviado/igual (no dry-run, "enviaria"/igual) sai da fila;
        o índice enviado/igual marca o blob como publicado."""
        if self.mode == "out":
            return
        s = sector_of(name)
        if s is None:
            return
        self.done[s][action] += 1
        ok = ("enviado", "igual") if self.mode == "send" else ("enviaria", "igual")
        if action not in ok:
            return
        reg = self.registry(s)
        if name == INDEX[s]:
            blob = ((obj or {}).get("source") or {}).get("blob")
            if blob:
                reg["indexBlob"] = blob
        else:
            reg["pending"] = [n for n in reg.get("pending") or [] if n != name]

    # ── a origem do índice e o banco ─────────────────────────────────────────────────────────
    def source(self, s: str) -> dict:
        """{blob, size, commit, committedAt} do banco desta execução."""
        if self.mode == "out":
            if s not in self._source:
                blob, size = self._probe(s)
                commit, committed = self._commit(s)
                self._source[s] = {"blob": blob, "size": size, "commit": commit,
                                   "committedAt": committed}
            return self._source[s]
        self.plan(s)
        if s in self.waiting:
            raise SectorWaiting(f"{s}: {self.waiting[s]}")
        reg = self.registry(s)
        if not reg.get("blob"):
            raise SectorWaiting(f"{s}: sem blob conhecido")
        return {"blob": reg["blob"], "size": reg.get("size"), "commit": reg.get("commit"),
                "committedAt": reg.get("committedAt")}

    def db(self, s: str) -> sqlite3.Connection:
        """O banco do blob desta execução, só leitura (baixado e conferido uma vez; depois, o cache
        em disco). Falha memorizada: nenhum segundo download no mesmo ciclo."""
        got = self._dbs.get(s)
        if isinstance(got, BaseException):
            raise got
        if got is not None:
            return got
        try:
            con = self._open(s)
        except Exception as e:
            self._dbs[s] = e
            raise
        self._dbs[s] = con
        self._prune_raw(s, con)
        return con

    def _cache_path(self, s: str, blob: str) -> Path:
        return self.cache_dir / f"{CACHE_PREFIX}{s}_{blob}.sqlite"

    def _open(self, s: str) -> sqlite3.Connection:
        info = self.source(s)
        path = self._cache_path(s, info["blob"])
        self._paths[s] = path
        if path.is_file():
            try:
                return self._check(open_ro(path))
            except sqlite3.DatabaseError:
                self._unlink(path)                  # cache estragado: baixa de novo
        if self.db_dir is not None:
            data = self._local_bytes(s)
        else:
            data = self.gh.download(info["blob"], info.get("size"))
            self.downloads[s] = len(data)
        raw = gzip.decompress(data) if DBS[s].gz else data
        self._clear_cache(s)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(raw)
        os.replace(tmp, path)
        return self._check(open_ro(path))

    @staticmethod
    def _check(con: sqlite3.Connection) -> sqlite3.Connection:
        try:
            con.execute("SELECT count(*) FROM sqlite_master").fetchone()
        except sqlite3.DatabaseError:
            con.close()
            raise
        return con

    def _clear_cache(self, s: str, keep: Path | None = None) -> None:
        for p in self.cache_dir.glob(f"{CACHE_PREFIX}{s}_*.sqlite"):
            if keep is None or p != keep:
                self._unlink(p)

    @staticmethod
    def _unlink(p: Path) -> None:
        try:
            p.unlink()
        except OSError:
            pass

    def _prune_raw(self, s: str, con) -> None:
        """Base bruta de tabela que não existe no banco: sai da fila sem publicar."""
        if self.mode == "out":
            return
        have = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        reg = self.registry(s)
        gone = [raw_file(s, t) for t in RAW_TABLES[s] if t not in have]
        if gone and any(n in (reg.get("pending") or []) for n in gone):
            reg["pending"] = [n for n in reg.get("pending") or [] if n not in gone]
            self.notes.append(f"{s}: tabela(s) fora do banco, base bruta não sai: "
                              + ", ".join(RAW_TABLE_OF[n][1] for n in gone))

    def index_guard(self, s: str) -> None:
        """No envio, o índice só sai com a fila do blob vazia (é o marcador da partida a frio)."""
        if self.mode != "send":
            return
        self.plan(s)                                # a fila só é conhecida depois do plano
        left = self.pending(s)
        if left:
            raise RuntimeError(f"{INDEX[s]} espera os arquivos do blob: {len(left)} na fila "
                               f"({', '.join(left[:3])}{'…' if len(left) > 3 else ''})")

    # ── fim da execução ──────────────────────────────────────────────────────────────────────
    def finish(self) -> None:
        """Fecha os bancos e apaga o cache que não serve mais (fila vazia, ou --out/--dry-run)."""
        for s, con in list(self._dbs.items()):
            if isinstance(con, sqlite3.Connection):
                try:
                    con.close()
                except Exception:
                    pass
        for s, path in self._paths.items():
            reg = self.registry(s)
            if self.mode != "send" or (not reg.get("pending") and reg.get("indexBlob") == reg.get("blob")):
                self._unlink(path)

    def end_note(self) -> str | None:
        """Uma linha para o log — só quando houve sonda, download, fila ou aviso."""
        parts = []
        for s in SECTORS:
            reg = self.registry(s) if (s in self._plans or s in self._dbs) else {}
            bits = []
            if s in self.downloads:
                bits.append(f"banco baixado ({self.downloads[s] / 1024 / 1024:.1f} MB)".replace(".", ","))
            if self.done[s]:
                bits.append(", ".join(f"{n} {a}" for a, n in sorted(self.done[s].items())))
            left = reg.get("pending") or []
            if left:
                bits.append(f"{len(left)} na fila")
            if reg.get("blob") and reg.get("indexBlob") != reg.get("blob") and not left and s in self._plans:
                bits.append("índice pendente")
            if bits:
                blob = reg.get("blob") or (self._source.get(s) or {}).get("blob") or "?"
                parts.append(f"{s} blob {blob[:7]}: " + ", ".join(bits))
        for s, why in self.waiting.items():
            parts.append(f"{s}: {why}")
        parts += self.notes
        if self.probed:
            parts.append("sonda: " + ", ".join(sorted(set(self.probed))))
        if not parts:
            return None
        gh = f" · GitHub: {self.gh.calls} chamada(s), {self.gh.bytes / 1024:.0f} KB" if self.gh.calls else ""
        return "setores: " + " · ".join(parts) + gh
