"""Runner do clipping (Fase 3): reivindica jobs pending → gera → sobe no Storage → marca done.

Rode de news-hunter/:  python -m clipping.run_jobs [--once]
Usa SUPABASE_URL + SUPABASE_SERVICE_KEY (a service key IGNORA RLS). Arquivos vão para o
bucket 'admin-uploads' em clippings/<job_id>/, e o job guarda URLs ASSINADAS (7 dias) para download.
"""
from __future__ import annotations

import argparse
import logging
import os
from datetime import date, datetime, timezone

import requests

try:
    from dotenv import load_dotenv
    load_dotenv()               # news-hunter/.env (local); no Actions vem de secrets
except Exception:
    pass

from .generate import build_from_payload

log = logging.getLogger(__name__)

BUCKET = "admin-uploads"
SIGN_TTL = 7 * 24 * 3600        # 7 dias
DOCX_CT = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _env() -> tuple[str, str]:
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    if not url or not key:
        raise RuntimeError("SUPABASE_URL / SUPABASE_SERVICE_KEY ausentes")
    return url, key


def _h(key: str, extra: dict | None = None) -> dict:
    h = {"apikey": key, "Authorization": f"Bearer {key}"}
    if extra:
        h.update(extra)
    return h


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def claim_job() -> dict | None:
    url, key = _env()
    r = requests.post(f"{url}/rest/v1/rpc/claim_next_clipping_job",
                      headers=_h(key, {"Content-Type": "application/json"}), json={}, timeout=30)
    r.raise_for_status()
    data = r.json()
    return data[0] if data else None


def read_job(job_id: str) -> dict | None:
    """Leitura direta (service key) — usada em teste local sem a RPC de claim."""
    url, key = _env()
    r = requests.get(f"{url}/rest/v1/clipping_jobs?id=eq.{job_id}&select=*", headers=_h(key), timeout=30)
    r.raise_for_status()
    data = r.json()
    return data[0] if data else None


def _upload(path: str, data: bytes, content_type: str) -> None:
    url, key = _env()
    r = requests.post(f"{url}/storage/v1/object/{BUCKET}/{path}",
                      headers=_h(key, {"Content-Type": content_type, "x-upsert": "true"}),
                      data=data, timeout=90)
    if not r.ok:
        raise RuntimeError(f"upload {path} -> {r.status_code} {r.text[:200]}")


def _sign(path: str) -> str:
    url, key = _env()
    r = requests.post(f"{url}/storage/v1/object/sign/{BUCKET}/{path}",
                      headers=_h(key, {"Content-Type": "application/json"}),
                      json={"expiresIn": SIGN_TTL}, timeout=30)
    if not r.ok:
        raise RuntimeError(f"sign {path} -> {r.status_code} {r.text[:200]}")
    signed = r.json()["signedURL"]            # ex.: /object/sign/<bucket>/<path>?token=...
    return f"{url}/storage/v1{signed}"


def patch_job(job_id: str, fields: dict) -> None:
    url, key = _env()
    r = requests.patch(f"{url}/rest/v1/clipping_jobs?id=eq.{job_id}",
                       headers=_h(key, {"Content-Type": "application/json", "Prefer": "return=minimal"}),
                       json=fields, timeout=30)
    if not r.ok:
        raise RuntimeError(f"patch {job_id} -> {r.status_code} {r.text[:200]}")


def process(job: dict) -> None:
    jid = job["id"]
    payload = job.get("payload") or []
    d = date.fromisoformat(job["ref_date"]) if job.get("ref_date") else date.today()
    try:
        res = build_from_payload(payload, d, fetch=True, config=job.get("config"))
        base = f"clippings/{jid}"
        _upload(f"{base}/{res['docx_name']}", res["docx"], DOCX_CT)
        _upload(f"{base}/{res['eml_name']}", res["eml"], "message/rfc822")
        _upload(f"{base}/{res['html_name']}", res["html"].encode("utf-8"), "text/html; charset=utf-8")
        # corpos que falharam (url, motivo) → gravados no job p/ o front oferecer "colar e regerar"
        errs = [{"url": u, "reason": r} for (u, r) in (res.get("errors") or [])]
        patch_job(jid, {
            "status": "done",
            "docx_path": _sign(f"{base}/{res['docx_name']}"),
            "eml_path": _sign(f"{base}/{res['eml_name']}"),
            "preview_path": _sign(f"{base}/{res['html_name']}"),   # prévia inline (HTML)
            "error": None, "errors": errs, "finished_at": _now(), "updated_at": _now(),
        })
        log.info("job %s DONE (%d itens, %d avisos)", jid, len(res["items"]), len(res["errors"]))
    except Exception as e:
        log.exception("job %s ERRO", jid)
        try:
            patch_job(jid, {"status": "error", "error": str(e)[:500],
                            "finished_at": _now(), "updated_at": _now()})
        except Exception:
            log.exception("falha ao marcar erro no job %s", jid)


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="processa no máximo 1 job e sai")
    a = ap.parse_args()
    n = 0
    while True:
        job = claim_job()
        if not job:
            break
        process(job)
        n += 1
        if a.once:
            break
    print(f"jobs processados: {n}")


if __name__ == "__main__":
    main()
