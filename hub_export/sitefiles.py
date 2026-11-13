"""Os arquivos do REPOSITÓRIO DO SITE que viram arquivo público — hoje só o glossário da página Data
(`admin/glossary.json` → `glossary.json`, Onda 4, PLANO Apêndice H.5; pesquisa 4-dados §5.1), no molde dos
bancos dos setores (sectors.py, Onda 3) e com o mesmo cliente do GitHub (github.py). Zero Supabase.

CADÊNCIA EVENT: o arquivo nunca fica "na vez" pelo relógio — só pelo `urgent`:
  • SONDA: o SHA do blob do arquivo no `main`, no máximo 1×/15 min (`PROBE_EVERY_S`), CONDICIONAL: o ETag
    da sonda anterior vai no `If-None-Match` e, sem mudança, a API responde 304 sem corpo (a sonda comum
    traria o arquivo inteiro: 26,8 KB). Só a 1ª sonda de cada runner paga o corpo (~4,4/dia).
  • na vez quando o blob da sonda é OUTRO que o do último envio (ou da cópia publicada, na partida a
    frio: `source.blob`). O download (`git/blobs/<sha>`, conferido pelo sha1 do git e pelo tamanho) e o
    commit (o último que tocou o arquivo: `source.commit`/`committedAt`) só acontecem aí; o resultado fica
    no estado até o blob mudar de novo (um envio que falhou não baixa outra vez).
  • enviado (ou igual à cópia publicada) → o blob vira o do último envio.
SEM TOKEN NO ENVIO: o run.py nem chama o glossário (como os setores; no Actions o `GH_TOKEN` do hunt-loop
basta). GitHub fora do ar ou no limite: o arquivo fica "fora da vez" (o publicado continua no ar), uma linha
no log, e o ciclo segue; tenta de novo no ciclo seguinte.
MODOS: "send" usa o estado; "dry" simula sobre uma CÓPIA dele; "out" monta sempre (sonda + download).
"""
from __future__ import annotations

import copy
import json
import logging
from datetime import datetime, timezone

from .encode import iso_utc
from .github import GitHubClient, GitHubError, token_from

log = logging.getLogger(__name__)

GLOSSARY = "glossary.json"
PROBE_EVERY_S = 900 - 60          # WARM (15 min) com a folga do relógio do loop (schedule.SLACK_S)
STATE_KEY = "glossary"


def urgent(src) -> bool:
    return src.sitefiles().urgent()


def after(src, obj, action: str) -> None:
    src.sitefiles().after(obj, action)


class SiteFiles:
    """O glossário de UMA execução: sonda, plano, download, cache e a linha do log."""

    def __init__(self, src, env=None, http=None):
        self.src = src
        self.mode = src.mode
        self.now = src.now
        self.pub = src.published
        self.rebuild = src.rebuild
        self.gh = GitHubClient(token=token_from(env or {}), http=http)
        self.state = src.state.setdefault(STATE_KEY, {})
        self._sim: dict | None = None
        self._probed = False
        self._cleared = False
        self._memo: dict | None = None
        self.notes: list[str] = []
        self.done: dict[str, int] = {}

    # ── estado ───────────────────────────────────────────────────────────────────────────────
    def registry(self) -> dict:
        """O registro: o do estado (envio), uma cópia (dry-run) ou vazio (--out)."""
        if self.mode == "send":
            return self.state
        if self._sim is None:
            self._sim = copy.deepcopy(self.state) if self.mode == "dry" else {}
        return self._sim

    def _fresh(self, stamp) -> bool:
        iso = iso_utc(stamp)
        if not iso:
            return False
        then = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        return 0 <= (self.now - then).total_seconds() < PROBE_EVERY_S

    # ── a sonda ──────────────────────────────────────────────────────────────────────────────
    def probe(self) -> tuple[str, int]:
        """(blob, tamanho) do glossário no `main` — a sonda vale 15 min; o ETag faz a próxima custar 304."""
        reg = self.registry()
        if self.rebuild and not self._cleared:      # --rebuild: nada guardado vale (sonda, envio, arquivo)
            self._cleared = True
            for k in ("probeBlob", "probeSize", "probeAt", "etag", "seeded", "sentBlob", "doc"):
                reg.pop(k, None)
        if reg.get("probeBlob") and self._fresh(reg.get("probeAt")) and (self.mode != "out" or self._probed):
            return reg["probeBlob"], reg.get("probeSize")
        from .exporters.glossary import PATH
        self._probed = True
        got = self.gh.probe_cached(PATH, reg.get("etag") if reg.get("probeBlob") else None)
        if got is not None:
            blob, size, etag = got
            reg.update(probeBlob=blob, probeSize=size, etag=etag)
        reg["probeAt"] = self.src.generated_at
        return reg["probeBlob"], reg.get("probeSize")

    def _published_blob(self) -> tuple[str, str | None]:
        """("ok", blob da cópia publicada) | ("none", None) = nada publicado ou ilegível."""
        if self.pub is None or self.rebuild:
            return "none", None
        doc = self.pub.get(GLOSSARY)
        src_ = doc.get("source") if isinstance(doc, dict) else None
        if isinstance(doc, dict) and doc.get("schemaVersion") == 1 and isinstance(src_, dict) \
                and isinstance(src_.get("blob"), str):
            return "ok", src_["blob"]
        return "none", None

    def urgent(self) -> bool:
        """Na vez fora do relógio? O blob de agora é outro que o do último envio (ou o da cópia publicada)."""
        if self.mode == "out":
            return True
        try:
            blob, _ = self.probe()
        except GitHubError as e:
            self._note(f"{e} — o arquivo não muda neste ciclo")
            return False
        reg = self.registry()
        if not reg.get("seeded"):
            status, pub_blob = self._published_blob()
            reg.update(seeded=True, sentBlob=pub_blob if status == "ok" else None)
            if status == "ok" and pub_blob == blob:
                self._note(f"partida a frio — a cópia publicada já é do blob {blob[:7]}: nada a montar")
        return blob != reg.get("sentBlob")

    def after(self, obj, action: str) -> None:
        if self.mode == "out":
            return
        self.done[action] = self.done.get(action, 0) + 1
        ok = ("enviado", "igual") if self.mode == "send" else ("enviaria", "igual")
        blob = ((obj or {}).get("source") or {}).get("blob")
        if action in ok and blob:
            self.registry()["sentBlob"] = blob

    # ── o arquivo ────────────────────────────────────────────────────────────────────────────
    def glossary(self) -> dict:
        """{source: {blob, commit, committedAt}, sections} do blob de agora (baixado só quando muda)."""
        if self._memo is not None:
            return self._memo
        from .exporters import glossary as G
        blob, size = self.probe()
        reg = self.registry()
        doc = reg.get("doc") if not self.rebuild else None
        if not (isinstance(doc, dict) and (doc.get("source") or {}).get("blob") == blob
                and isinstance(doc.get("sections"), list)):
            data = self.gh.download(blob, size)
            try:
                parsed = json.loads(data.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                raise ValueError(f"{G.PATH} do blob {blob[:7]} não é JSON — nada publicado") from None
            if not isinstance(parsed, dict) or not isinstance(parsed.get("sections"), list):
                raise ValueError(f"{G.PATH} do blob {blob[:7]} sem a lista de seções — nada publicado")
            commit, committed = self.gh.last_commit(G.PATH)
            doc = {"source": {"blob": blob, "commit": commit, "committedAt": committed},
                   "sections": G.sections(parsed)}
            reg["doc"] = doc
            self._note(f"blob {blob[:7]} baixado ({len(data) / 1024:.1f} KB)".replace(".", ","))
        self._memo = doc
        return doc

    # ── o log ────────────────────────────────────────────────────────────────────────────────
    def _note(self, msg: str) -> None:
        if msg not in self.notes:
            self.notes.append(msg)

    def end_note(self) -> str | None:
        """Uma linha — só quando houve sonda de verdade, download, envio ou aviso."""
        parts = list(self.notes)
        if self.done:
            parts.append(", ".join(f"{n} {a}" for a, n in sorted(self.done.items())))
        if not parts and not self._probed:
            return None
        gh = f" · GitHub: {self.gh.calls} chamada(s), {self.gh.bytes / 1024:.1f} KB".replace(".", ",")
        return "glossário: " + (" · ".join(parts) if parts else "sonda") + gh
