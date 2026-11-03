"""Leitor REST do Supabase (service key) com contador de bytes — só leitura.

Regras de tráfego (PLANO §2.4/10; o plano grátis conta CABEÇALHOS e mede o tráfego COMPRIMIDO,
~1 KB de cabeçalho por resposta medido em 03/11/2026):
  • `select=` explícito SEMPRE — pedir `*` (ou não pedir nada) levanta erro aqui mesmo;
  • `Accept-Encoding: gzip`;
  • cada chamada soma no contador: bytes que vieram pela rede (o `Content-Length` quando o
    servidor manda; senão o tamanho do corpo como chegou — comprimido, lido direto do socket),
    mais o tamanho descomprimido para referência. O run.py loga isso numa linha por ciclo.

Nada de URL, chave ou corpo de resposta em mensagem de erro: o log do Actions é PÚBLICO e o
endereço do projeto do Supabase é segredo do repositório.
"""
from __future__ import annotations

import gzip
import json
import os

import requests

DEFAULT_TIMEOUT_S = 20.0


class SupaError(RuntimeError):
    """Falha de leitura do Supabase — a mensagem nunca leva URL, chave ou corpo."""


def _body_and_wire(resp) -> tuple[bytes, int]:
    """(corpo descomprimido, bytes que vieram pela rede).

    Com `requests` em modo stream, lê o corpo CRU do socket (comprimido) e descomprime aqui —
    é a única forma de saber o tamanho comprimido quando o servidor manda em blocos (chunked)
    e sem Content-Length. Qualquer outro objeto de resposta (o HTTP falso dos testes) usa
    `content` e o Content-Length/len do corpo."""
    raw = getattr(resp, "raw", None)
    if isinstance(resp, requests.Response) and raw is not None and resp._content is False:
        data = raw.read(decode_content=False)
        try:
            raw.release_conn()       # corpo lido inteiro: a conexão volta para o pool
        except Exception:
            pass
        enc = (resp.headers.get("Content-Encoding") or "").strip().lower()
        if enc == "gzip":
            body = gzip.decompress(data)
        elif enc in ("", "identity"):
            body = data
        else:
            raise SupaError(f"codificação inesperada na resposta ({enc})")
        return body, len(data)
    body = resp.content or b""
    return body, len(body)


class SupaReader:
    """GET em tabela e POST em RPC do PostgREST, contando chamadas e bytes."""

    def __init__(self, url: str, key: str, http=None, timeout: float = DEFAULT_TIMEOUT_S):
        if not url or not key:
            raise SupaError("SUPABASE_URL/SUPABASE_SERVICE_KEY ausentes")
        self._url = url.rstrip("/")
        self._headers = {"apikey": key, "Authorization": f"Bearer {key}",
                         "Accept": "application/json", "Accept-Encoding": "gzip"}
        self.http = http or requests.Session()
        self.timeout = timeout
        self.calls = 0
        self.wire_bytes = 0      # o que veio pela rede (comprimido quando o servidor comprime)
        self.body_bytes = 0      # o mesmo, descomprimido

    @classmethod
    def from_env(cls, env=None, http=None) -> "SupaReader":
        env = os.environ if env is None else env
        return cls(env.get("SUPABASE_URL", ""), env.get("SUPABASE_SERVICE_KEY", ""), http=http)

    def select(self, table: str, select: str, **params) -> list[dict]:
        """GET /rest/v1/<tabela>?select=…&<filtros>. Filtros no formato do PostgREST
        (`order="updated_at.desc.nullslast"`, `limit=1`, `ticker="in.(…)"`)."""
        return self._call("GET", f"/rest/v1/{table}", table, select, params, None)

    def rpc(self, fn: str, select: str, args: dict | None = None) -> list[dict]:
        """POST /rest/v1/rpc/<função>?select=… (funções que devolvem `setof <tabela>`)."""
        return self._call("POST", f"/rest/v1/rpc/{fn}", fn, select, {}, args or {})

    def _call(self, method, path, what, select, params, body) -> list[dict]:
        sel = (select or "").strip()
        if not sel or sel == "*" or any(c.strip() == "*" for c in sel.split(",")):
            raise ValueError(f"{what}: select explícito obrigatório (nunca '*')")
        query = {"select": sel, **{k: str(v) for k, v in params.items()}}
        headers = dict(self._headers)
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode("utf-8")
        self.calls += 1
        try:
            resp = self.http.request(method, self._url + path, headers=headers, params=query,
                                     data=data, timeout=self.timeout, stream=True)
            content, wire = _body_and_wire(resp)
        except SupaError:
            raise
        except Exception as e:
            raise SupaError(f"{what}: {type(e).__name__}") from None
        cl = (resp.headers or {}).get("Content-Length")
        try:
            self.wire_bytes += int(cl) if cl is not None else wire
        except ValueError:
            self.wire_bytes += wire
        self.body_bytes += len(content)
        if not 200 <= resp.status_code < 300:
            raise SupaError(f"{what}: HTTP {resp.status_code}")
        try:
            out = json.loads(content.decode("utf-8")) if content else []
        except ValueError:
            raise SupaError(f"{what}: resposta não é JSON") from None
        if not isinstance(out, list):
            raise SupaError(f"{what}: resposta não é uma lista")
        return [r for r in out if isinstance(r, dict)]
