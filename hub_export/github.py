"""Cliente MÍNIMO da API do GitHub para os bancos dos setores (Onda 3 — PLANO, Apêndice G.0;
pesquisa 3-dados §5.1): o repositório do site (`JPHELITO/IBBA-Research-Dashboard`) é PÚBLICO e os
dois bancos SQLite moram nele, versionados (cada robô do site commita o arquivo inteiro).

TRÊS chamadas, todas GET e só leitura:
  sonda       `contents/<caminho>?ref=main` → `sha` do BLOB e `size` (~1 KB: acima de 1 MB a API
              não manda o conteúdo — medido nos dois bancos). É ela que diz "o banco mudou" (o SHA do
              blob muda a cada commit que toca o arquivo); 1 chamada por banco, no máximo 1×/15 min.
  download    `git/blobs/<sha>` com `Accept: application/vnd.github.raw+json` (o arquivo cru, até
              100 MB) — pelo SHA FIXO, imutável. Conferido antes de usar: `sha1("blob <tam>\\0" +
              bytes)` tem de ser o SHA pedido (é o SHA do git), e o tamanho, o da sonda. Errado →
              GitHubError e nada é usado. NUNCA o GitHub Pages: está atrasado (servia o P&P de 20/10
              em 11/11 — o deploy do Pages não roda nos commits de robô).
  commit      `commits?path=<caminho>&sha=main&per_page=1` → o último commit que tocou o arquivo e
              quando (`commit`/`committedAt` do índice do setor). Só quando o blob muda.

SONDA CONDICIONAL (Onda 4, o glossário — sitefiles.py): para arquivo PEQUENO (< 1 MB) a API de conteúdo
manda o arquivo inteiro junto (em base64: 26,8 KB pelo `admin/glossary.json` de 18,7 KB, medido em
13/11/2026). `probe_cached` manda o `ETag` da sonda anterior (`If-None-Match`): sem mudança, a API responde
304 sem corpo (medido) — e, com token, o 304 nem conta no limite de taxa. Só a 1ª sonda de cada runner
paga o corpo.

TOKEN: `GH_TOKEN` (no Actions = o `GITHUB_TOKEN` do próprio job: 1.000 chamadas/h por repositório).
Sem token, a API aceita 60/h por IP — basta para o --out/--dry-run no PC, não para o loop (o
run.py nem chama os setores no envio sem token). O token só vai no cabeçalho `Authorization`; as
mensagens de erro levam o tipo da chamada e o status HTTP — nunca URL, cabeçalho ou token (o log do
Actions é PÚBLICO).
"""
from __future__ import annotations

import hashlib
import re
from urllib.parse import quote

import requests

from .encode import iso_utc

API = "https://api.github.com"
OWNER = "JPHELITO"
REPO = "IBBA-Research-Dashboard"
REF = "main"
API_VERSION = "2022-11-28"
USER_AGENT = "hub-export"                 # nome genérico (a API exige um User-Agent)
API_TIMEOUT_S = 15.0
DOWNLOAD_TIMEOUT_S = 60.0
ACCEPT_JSON = "application/vnd.github+json"
ACCEPT_RAW = "application/vnd.github.raw+json"
_SHA = re.compile(r"[0-9a-f]{40}")


class GitHubError(RuntimeError):
    """Falha numa chamada ao GitHub — a mensagem nunca leva URL nem token."""

    def __init__(self, msg: str, status: int | None = None):
        super().__init__(msg)
        self.status = status


def git_blob_sha(data: bytes) -> str:
    """O SHA de um arquivo para o git: sha1("blob <tamanho>\\0" + bytes)."""
    return hashlib.sha1(b"blob %d\x00" % len(data) + data).hexdigest()


def token_from(env) -> str | None:
    """O token do GitHub do ambiente (`GH_TOKEN`), ou None."""
    v = (env or {}).get("GH_TOKEN") if env is not None else None
    v = v.strip() if isinstance(v, str) else ""
    return v or None


class GitHubClient:
    """As 3 chamadas acima, com contador (chamadas e bytes) para a linha do log."""

    def __init__(self, token: str | None = None, http=None, *, owner: str = OWNER, repo: str = REPO,
                 ref: str = REF, api: str = API):
        self.token = token or None
        self.http = http or requests.Session()
        self.base = f"{api.rstrip('/')}/repos/{owner}/{repo}"
        self.ref = ref
        self.calls = 0
        self.bytes = 0

    def _headers(self, accept: str) -> dict:
        h = {"Accept": accept, "X-GitHub-Api-Version": API_VERSION, "User-Agent": USER_AGENT}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def _get(self, what: str, url: str, params: dict | None, accept: str, timeout: float):
        try:
            r = self.http.request("GET", url, headers=self._headers(accept), params=params,
                                  timeout=timeout)
        except requests.RequestException as e:
            self.calls += 1
            raise GitHubError(f"GitHub ({what}): sem resposta ({type(e).__name__})") from None
        self.calls += 1
        self.bytes += len(r.content or b"")
        if r.status_code != 200:
            raise GitHubError(f"GitHub ({what}): {self._why(r)}", status=r.status_code)
        return r

    @staticmethod
    def _why(r) -> str:
        headers = r.headers or {}
        if r.status_code in (403, 429) and str(headers.get("X-RateLimit-Remaining", "")).strip() == "0":
            return f"HTTP {r.status_code} — limite de taxa da API esgotado"
        return f"HTTP {r.status_code}"

    @staticmethod
    def _json(r, what: str):
        try:
            return r.json()
        except ValueError:
            raise GitHubError(f"GitHub ({what}): resposta que não é JSON") from None

    def probe(self, path: str) -> tuple[str, int]:
        """(SHA do blob, tamanho em bytes) do arquivo no `ref` — a sonda barata."""
        r = self._get("sonda", f"{self.base}/contents/{quote(path, safe='/')}", {"ref": self.ref},
                      ACCEPT_JSON, API_TIMEOUT_S)
        return self._file_info(r)

    def probe_cached(self, path: str, etag: str | None = None) -> tuple[str, int, str | None] | None:
        """A sonda CONDICIONAL: `None` = nada mudou desde a sonda do `etag` (HTTP 304, sem corpo);
        senão (SHA do blob, tamanho, ETag novo). Sem `etag`, é a sonda comum (com o ETag de volta)."""
        url = f"{self.base}/contents/{quote(path, safe='/')}"
        headers = self._headers(ACCEPT_JSON)
        if etag:
            headers["If-None-Match"] = etag
        try:
            r = self.http.request("GET", url, headers=headers, params={"ref": self.ref},
                                  timeout=API_TIMEOUT_S)
        except requests.RequestException as e:
            self.calls += 1
            raise GitHubError(f"GitHub (sonda): sem resposta ({type(e).__name__})") from None
        self.calls += 1
        self.bytes += len(r.content or b"")
        if r.status_code == 304 and etag:
            return None
        if r.status_code != 200:
            raise GitHubError(f"GitHub (sonda): {self._why(r)}", status=r.status_code)
        sha, size = self._file_info(r)
        tag = (r.headers or {}).get("ETag")
        return sha, size, tag if isinstance(tag, str) and tag.strip() else None

    def _file_info(self, r) -> tuple[str, int]:
        d = self._json(r, "sonda")
        sha = d.get("sha") if isinstance(d, dict) else None
        size = d.get("size") if isinstance(d, dict) else None
        if (not isinstance(sha, str) or not _SHA.fullmatch(sha) or isinstance(size, bool)
                or not isinstance(size, int) or size <= 0 or d.get("type") != "file"):
            raise GitHubError("GitHub (sonda): resposta sem SHA/tamanho de arquivo")
        return sha, size

    def download(self, sha: str, size: int | None = None) -> bytes:
        """O blob cru pelo SHA, CONFERIDO (tamanho e sha1 do git). Errado → GitHubError."""
        if not isinstance(sha, str) or not _SHA.fullmatch(sha):
            raise GitHubError("GitHub (download): SHA inválido")
        r = self._get("download", f"{self.base}/git/blobs/{sha}", None, ACCEPT_RAW,
                      DOWNLOAD_TIMEOUT_S)
        data = r.content or b""
        if size is not None and len(data) != size:
            raise GitHubError(f"GitHub (download): {len(data)} bytes, a sonda disse {size} — "
                              "nada usado")
        got = git_blob_sha(data)
        if got != sha:
            raise GitHubError(f"GitHub (download): SHA do conteúdo ({got[:12]}) não confere com o "
                              f"blob pedido ({sha[:12]}) — nada usado")
        return data

    def last_commit(self, path: str) -> tuple[str | None, str | None]:
        """(SHA, data UTC `…Z`) do último commit do `ref` que tocou o arquivo."""
        r = self._get("commit", f"{self.base}/commits",
                      {"path": path, "sha": self.ref, "per_page": "1"}, ACCEPT_JSON, API_TIMEOUT_S)
        d = self._json(r, "commit")
        if not isinstance(d, list) or not d or not isinstance(d[0], dict):
            raise GitHubError("GitHub (commit): nenhum commit para o arquivo")
        c = d[0]
        sha = c.get("sha") if isinstance(c.get("sha"), str) and _SHA.fullmatch(c["sha"]) else None
        info = c.get("commit") if isinstance(c.get("commit"), dict) else {}
        who = info.get("committer") if isinstance(info.get("committer"), dict) else {}
        return sha, iso_utc(who.get("date"))
