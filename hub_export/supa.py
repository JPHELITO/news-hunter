"""Leitor REST do Supabase (service key) com contador de bytes — só leitura.

Regras de tráfego (PLANO §2.4/10; o plano grátis conta CABEÇALHOS e mede o tráfego COMPRIMIDO,
~1 KB de cabeçalho por resposta medido em 03/11/2026):
  • `select=` explícito SEMPRE — pedir `*` (ou não pedir nada) levanta erro aqui mesmo;
  • `Accept-Encoding: gzip`;
  • cada chamada soma no contador: bytes que vieram pela rede (o `Content-Length` quando o
    servidor manda; senão o tamanho do corpo como chegou — comprimido, lido direto do socket),
    os bytes dos CABEÇALHOS da resposta (também contam no plano grátis) e o tamanho
    descomprimido para referência. Tudo também POR FONTE (`tag`: "news:sweep", "calendar"…;
    sem tag, o nome da tabela/RPC). O run.py loga isso numa linha por ciclo;
  • contagem barata: `count()` = HEAD com `Prefer: count=exact` (só cabeçalhos, sem corpo);
    `select_count()` = a leitura com o total do `Content-Range` junto (sonda "contagem + max").

Nada de URL, chave ou corpo de resposta em mensagem de erro: o log do Actions é PÚBLICO e o
endereço do projeto do Supabase é segredo do repositório. O erro guarda o status HTTP e o código
do PostgREST (`22007`, `42703`, `PGRST202`…); o texto do erro fica num atributo à parte
(`detail`), lido só pelas conferências do `--check` (que imprimem dele só o nome do tipo).
"""
from __future__ import annotations

import gzip
import json
import os
import re

import requests

DEFAULT_TIMEOUT_S = 20.0
PAGE_ROWS = 1000                 # teto de linhas por resposta do PostgREST do Supabase
_RANGE_TOTAL = re.compile(r"/(\d+)\s*$")
_PG_CODE = re.compile(r"[A-Z0-9]{3,10}")


class SupaError(RuntimeError):
    """Falha de leitura do Supabase — a mensagem nunca leva URL, chave ou corpo."""

    def __init__(self, msg: str, status: int | None = None, code: str | None = None,
                 detail: str | None = None):
        super().__init__(msg)
        self.status = status
        self.code = code
        self.detail = detail


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
            body = gzip.decompress(data) if data else b""
        elif enc in ("", "identity"):
            body = data
        else:
            raise SupaError(f"codificação inesperada na resposta ({enc})")
        return body, len(data)
    body = resp.content or b""
    return body, len(body)


def _header_bytes(resp) -> int:
    """Tamanho aproximado dos cabeçalhos da resposta como vieram pela rede (HTTP/1.1: linha de
    status + `Nome: valor\\r\\n` por cabeçalho + linha em branco). É a parte do custo que o
    corpo não mostra (~0,75 KB por resposta no Supabase)."""
    try:
        items = list((resp.headers or {}).items())
    except Exception:
        return 0
    return 17 + sum(len(str(k)) + len(str(v)) + 4 for k, v in items) + 2


def _total(resp) -> int | None:
    """O total do `Content-Range` (`0-0/1234` ou `*/1234`); None se o servidor não contou."""
    h = resp.headers or {}
    cr = h.get("Content-Range") or h.get("content-range")
    m = _RANGE_TOTAL.search(str(cr or ""))
    return int(m.group(1)) if m else None


class SupaReader:
    """GET em tabela e POST em RPC do PostgREST, contando chamadas e bytes (total e por fonte)."""

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
        self.header_bytes = 0    # cabeçalhos das respostas (o plano grátis também conta)
        self.by_source: dict[str, dict] = {}   # tag → {"calls", "wire", "headers"}

    @classmethod
    def from_env(cls, env=None, http=None) -> "SupaReader":
        env = os.environ if env is None else env
        return cls(env.get("SUPABASE_URL", ""), env.get("SUPABASE_SERVICE_KEY", ""), http=http)

    # ── API ──────────────────────────────────────────────────────────────────────────────────
    def select(self, table: str, select: str, *, tag: str | None = None, **params) -> list[dict]:
        """GET /rest/v1/<tabela>?select=…&<filtros>. Filtros no formato do PostgREST
        (`order="updated_at.desc.nullslast"`, `limit=1`, `ticker="in.(…)"`; `or`/`and` entram
        como `**{"or": "(…)"}`)."""
        rows, _ = self._call("GET", f"/rest/v1/{table}", table, select, params, None, tag=tag)
        return rows

    def select_count(self, table: str, select: str, *, tag: str | None = None,
                     **params) -> tuple[list[dict], int | None]:
        """A mesma leitura do `select`, mais o total de linhas que casam com os filtros (do
        `Content-Range`, `Prefer: count=exact`): "contagem + max" numa chamada só."""
        return self._call("GET", f"/rest/v1/{table}", table, select, params, None, tag=tag,
                          count=True)

    def count(self, table: str, select: str, *, tag: str | None = None, **params) -> int | None:
        """Só o total de linhas que casam com os filtros: HEAD + `Prefer: count=exact` (a resposta
        não tem corpo, custa só os cabeçalhos). None se o servidor não contou."""
        _, total = self._call("HEAD", f"/rest/v1/{table}", table, select, params, None, tag=tag,
                              count=True)
        return total

    def select_all(self, table: str, select: str, *, order: str, tag: str | None = None,
                   page: int = PAGE_ROWS, max_pages: int = 200, **params) -> list[dict]:
        """Paginado por `limit`/`offset` (nunca pelo cabeçalho Range: a Cloudflare do Supabase o
        trata como bytes). Exige `order` estável (desempate único no fim). Para na 1ª página curta."""
        out: list[dict] = []
        for n in range(max_pages):
            rows = self.select(table, select, tag=tag, order=order, limit=page, offset=n * page,
                               **params)
            out.extend(rows)
            if len(rows) < page:
                return out
        raise SupaError(f"{table}: mais de {max_pages * page} linhas — leitura interrompida")

    def rpc(self, fn: str, select: str, args: dict | None = None, *,
            tag: str | None = None) -> list[dict]:
        """POST /rest/v1/rpc/<função>?select=… (funções que devolvem `setof <tabela>` ou
        `table(...)`)."""
        rows, _ = self._call("POST", f"/rest/v1/rpc/{fn}", f"rpc/{fn}", select, {}, args or {},
                             tag=tag)
        return rows

    # ── interno ──────────────────────────────────────────────────────────────────────────────
    def _account(self, tag: str, wire: int, headers: int) -> None:
        s = self.by_source.setdefault(tag, {"calls": 0, "wire": 0, "headers": 0})
        s["calls"] += 1
        s["wire"] += wire
        s["headers"] += headers

    def _call(self, method, path, what, select, params, body, *, tag=None, count=False):
        sel = (select or "").strip()
        if not sel or sel == "*" or any(c.strip() == "*" for c in sel.split(",")):
            raise ValueError(f"{what}: select explícito obrigatório (nunca '*')")
        query = {"select": sel, **{k: str(v) for k, v in params.items()}}
        headers = dict(self._headers)
        if count:
            headers["Prefer"] = "count=exact"
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
            self._account(tag or what, 0, 0)
            raise
        except Exception as e:
            self._account(tag or what, 0, 0)
            raise SupaError(f"{what}: {type(e).__name__}") from None
        cl = (resp.headers or {}).get("Content-Length")
        if cl is not None and method != "HEAD":       # no HEAD o Content-Length é o do GET
            try:
                wire = int(cl)
            except ValueError:
                pass
        hb = _header_bytes(resp)
        self.wire_bytes += wire
        self.header_bytes += hb
        self.body_bytes += len(content)
        self._account(tag or what, wire, hb)
        if not 200 <= resp.status_code < 300:
            code = detail = None
            try:
                err = json.loads(content.decode("utf-8")) if content else {}
            except (ValueError, UnicodeDecodeError):
                err = {}
            if isinstance(err, dict):
                if err.get("code") is not None and _PG_CODE.fullmatch(str(err["code"])):
                    code = str(err["code"])
                detail = str(err.get("message") or "")[:200] or None
            extra = f" ({code})" if code else ""
            raise SupaError(f"{what}: HTTP {resp.status_code}{extra}", status=resp.status_code,
                            code=code, detail=detail)
        total = _total(resp) if count else None
        if method == "HEAD":
            return [], total
        try:
            out = json.loads(content.decode("utf-8")) if content else []
        except ValueError:
            raise SupaError(f"{what}: resposta não é JSON") from None
        if not isinstance(out, list):
            raise SupaError(f"{what}: resposta não é uma lista")
        return [r for r in out if isinstance(r, dict)], total
