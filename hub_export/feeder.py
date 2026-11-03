"""Cliente do Dashboard Data Feeder do Itaú — o único caminho até o Mindassets.

Protocolo (research-dashboards: docs/dashboard-data-feeder.md §2 e o "Python Client" do
collaborator-guide), três passos obrigatórios:
  1. initialize  POST {URL}/api/projects/{id}/files/uploads  (name, sourceFileName, contentType,
                 sizeBytes, sourceModifiedAt; `Authorization: Bearer <chave>`) → `update.id` +
                 `upload.url` (PUT assinado, 15 min) + `upload.requiredHeaders`;
  2. PUT         os bytes direto no armazenamento, com EXATAMENTE os cabeçalhos pedidos;
  3. complete    POST …/uploads/{updateId}/complete → `update.revision`.
Falha no PUT: avisa o Feeder (`…/failed`, `storage_rejected` com o status ou `network_error`) —
sem deixar uma falha DESSE aviso esconder a original — e levanta.

Tempo: 20 s nas chamadas da API, 60 s no PUT; no máximo 1 nova tentativa, só em erro de conexão
ou 5xx (4xx não repete — é pedido errado, repetir não conserta). Com `deadline`, a nova
tentativa só acontece se ainda couber no orçamento da execução.

SEGREDOS: nunca vão para log nem para mensagem de exceção a chave, a URL assinada (a assinatura
mora na query) nem a URL da API com caminho — só HOST e STATUS. As exceções de `requests`
carregam a URL inteira no texto; por isso são trocadas por `FeederError` sem encadear (`from None`).
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from urllib.parse import quote, urlsplit

import requests

log = logging.getLogger(__name__)

API_TIMEOUT_S = 20.0
PUT_TIMEOUT_S = 60.0
ENV_NAMES = ("DASHBOARD_FEEDER_URL", "DASHBOARD_FEEDER_PROJECT_ID", "DASHBOARD_FEEDER_API_KEY")


class FeederError(RuntimeError):
    """Falha no envio ao Feeder. A mensagem só tem etapa, status e host."""


class _StatusError(FeederError):
    def __init__(self, msg: str, status: int):
        super().__init__(msg)
        self.status = status


class _NetworkError(FeederError):
    pass


def missing_env(env=None) -> list[str]:
    """Quais das 3 variáveis do Feeder faltam (vazias contam como ausentes)."""
    env = os.environ if env is None else env
    return [n for n in ENV_NAMES if not (env.get(n) or "").strip()]


class FeederClient:
    def __init__(self, base_url: str, project_id: str, api_key: str, http=None,
                 api_timeout: float = API_TIMEOUT_S, put_timeout: float = PUT_TIMEOUT_S,
                 retry_wait: float = 1.0, clock=time.monotonic, sleep=time.sleep):
        self.base_url = (base_url or "").strip().rstrip("/")
        self.project_id = (project_id or "").strip()
        self._key = (api_key or "").strip()
        if not (self.base_url and self.project_id and self._key):
            raise FeederError("Feeder não configurado (URL, projeto ou chave ausentes)")
        self.http = http or requests.Session()
        self.api_timeout = api_timeout
        self.put_timeout = put_timeout
        self.retry_wait = retry_wait
        self.clock = clock
        self.sleep = sleep

    @classmethod
    def from_env(cls, env=None, http=None, **kw) -> "FeederClient":
        env = os.environ if env is None else env
        falta = missing_env(env)
        if falta:
            raise FeederError("faltam as variáveis do Feeder: " + ", ".join(falta))
        return cls(env["DASHBOARD_FEEDER_URL"], env["DASHBOARD_FEEDER_PROJECT_ID"],
                   env["DASHBOARD_FEEDER_API_KEY"], http=http, **kw)

    # ── API pública ─────────────────────────────────────────────────────────────────────────
    def upload(self, name: str, data: bytes, content_type: str, *,
               modified_at: str | None = None, deadline: float | None = None) -> int | None:
        """Os 3 passos. Devolve a revisão criada pelo Feeder (None se ele não informar)."""
        base = f"{self.base_url}/api/projects/{quote(self.project_id, safe='')}/files/uploads"
        payload = {"name": name, "sourceFileName": name, "contentType": content_type,
                   "sizeBytes": len(data),
                   "sourceModifiedAt": modified_at or datetime.now(timezone.utc)
                   .strftime("%Y-%m-%dT%H:%M:%SZ")}
        init = self._json(self._send("POST", base, what="initialize", headers=self._api_headers(),
                                     data=json.dumps(payload).encode("utf-8"),
                                     timeout=self.api_timeout, deadline=deadline), "initialize")
        update = init.get("update") if isinstance(init.get("update"), dict) else {}
        upload = init.get("upload") if isinstance(init.get("upload"), dict) else {}
        update_id, put_url = update.get("id"), upload.get("url")
        if update_id in (None, "") or not isinstance(put_url, str) or not put_url:
            raise FeederError("initialize: resposta sem update.id/upload.url")
        req_headers = upload.get("requiredHeaders")
        req_headers = {str(k): str(v) for k, v in req_headers.items()} \
            if isinstance(req_headers, dict) else {}
        upd = f"{base}/{quote(str(update_id), safe='')}"

        try:
            self._send("PUT", put_url, what="PUT", headers=req_headers, data=data,
                       timeout=self.put_timeout, deadline=deadline)
        except _StatusError as e:
            self._report_failed(upd, {"reason": "storage_rejected", "storageStatus": e.status},
                                deadline)
            raise
        except FeederError:
            self._report_failed(upd, {"reason": "network_error"}, deadline)
            raise

        done = self._json(self._send("POST", f"{upd}/complete", what="complete",
                                     headers=self._api_headers(), data=b"{}",
                                     timeout=self.api_timeout, deadline=deadline), "complete")
        rev = (done.get("update") or {}).get("revision") if isinstance(done.get("update"), dict) \
            else None
        if isinstance(rev, bool):
            return None
        if isinstance(rev, int):
            return rev
        if isinstance(rev, str) and rev.isdigit():
            return int(rev)
        return None

    # ── internos ────────────────────────────────────────────────────────────────────────────
    def _api_headers(self) -> dict:
        return {"Authorization": f"Bearer {self._key}", "Content-Type": "application/json",
                "Accept": "application/json"}

    def _can_retry(self, deadline: float | None) -> bool:
        return deadline is None or deadline - self.clock() > self.retry_wait + 1.0

    def _send(self, method: str, url: str, *, what: str, headers: dict, data: bytes,
              timeout: float, deadline: float | None):
        host = urlsplit(url).hostname or "?"
        for attempt in (1, 2):
            t = timeout
            if deadline is not None:
                left = deadline - self.clock()
                if left <= 0:
                    raise _NetworkError(f"{what}: orçamento de tempo esgotado (host {host})")
                t = min(timeout, max(1.0, left))
            try:
                resp = self.http.request(method, url, headers=headers, data=data, timeout=t)
            except (requests.ConnectionError, requests.Timeout) as e:
                if attempt == 1 and self._can_retry(deadline):
                    log.warning("Feeder %s: %s (host %s) — nova tentativa", what,
                                type(e).__name__, host)
                    self.sleep(self.retry_wait)
                    continue
                raise _NetworkError(f"{what}: {type(e).__name__} (host {host})") from None
            except Exception as e:
                raise _NetworkError(f"{what}: {type(e).__name__} (host {host})") from None
            status = resp.status_code
            if 200 <= status < 300:
                return resp
            if status >= 500 and attempt == 1 and self._can_retry(deadline):
                log.warning("Feeder %s: HTTP %s (host %s) — nova tentativa", what, status, host)
                self.sleep(self.retry_wait)
                continue
            raise _StatusError(f"{what}: HTTP {status} (host {host})", status)
        raise _NetworkError(f"{what}: sem resposta (host {host})")   # inalcançável

    def _json(self, resp, what: str) -> dict:
        try:
            out = json.loads((resp.content or b"").decode("utf-8"))
        except ValueError:
            raise FeederError(f"{what}: resposta não é JSON") from None
        if not isinstance(out, dict):
            raise FeederError(f"{what}: resposta não é um objeto")
        return out

    def _report_failed(self, upd_url: str, body: dict, deadline: float | None) -> None:
        """Avisa o Feeder que o PUT falhou. Qualquer problema AQUI só vira log: a falha que
        importa (e que sobe) é a do PUT."""
        try:
            self._send("POST", f"{upd_url}/failed", what="failed", headers=self._api_headers(),
                       data=json.dumps(body).encode("utf-8"), timeout=self.api_timeout,
                       deadline=deadline)
        except Exception as e:
            log.warning("Feeder failed: o aviso de falha também falhou (%s)", e)
