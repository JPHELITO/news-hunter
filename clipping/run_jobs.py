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
from urllib.parse import quote

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


def _enc(path: str) -> str:
    """Caminho do arquivo → pedaço de URL. O nome tem ESPAÇO ("NEWS - MMDDYYYY.docx"),
    então tem que ir codificado (%20); explícito para não depender do requests."""
    return quote(path, safe="/")


def _upload(path: str, data: bytes, content_type: str) -> None:
    url, key = _env()
    r = requests.post(f"{url}/storage/v1/object/{BUCKET}/{_enc(path)}",
                      headers=_h(key, {"Content-Type": content_type, "x-upsert": "true"}),
                      data=data, timeout=90)
    if not r.ok:
        raise RuntimeError(f"upload {path} -> {r.status_code} {r.text[:200]}")


def _sign(path: str, download: str | None = None) -> str:
    url, key = _env()
    r = requests.post(f"{url}/storage/v1/object/sign/{BUCKET}/{_enc(path)}",
                      headers=_h(key, {"Content-Type": "application/json"}),
                      json={"expiresIn": SIGN_TTL}, timeout=30)
    if not r.ok:
        raise RuntimeError(f"sign {path} -> {r.status_code} {r.text[:200]}")
    signed = r.json()["signedURL"]            # ex.: /object/sign/<bucket>/<path>?token=...
    out = f"{url}/storage/v1{signed}"
    if download:
        # &download=<nome> → o Storage responde Content-Disposition: attachment com ESTE
        # nome, então o navegador salva "NEWS - MMDDYYYY.docx" mesmo que a URL mude.
        out += ("&" if "?" in out else "?") + f"download={quote(download)}"
    return out


def patch_job(job_id: str, fields: dict) -> None:
    url, key = _env()
    r = requests.patch(f"{url}/rest/v1/clipping_jobs?id=eq.{job_id}",
                       headers=_h(key, {"Content-Type": "application/json", "Prefer": "return=minimal"}),
                       json=fields, timeout=30)
    if not r.ok:
        raise RuntimeError(f"patch {job_id} -> {r.status_code} {r.text[:200]}")


def _bodies_por_url(urls: list) -> dict:
    """{url: corpo} do cache do clipping. Falha aqui NUNCA derruba o blast — sem corpo ele
    ainda sai, só com destaques mais pobres, então o erro vira aviso e a vida segue."""
    urls = [u for u in urls if u]
    if not urls:
        return {}
    url, key = _env()
    out = {}
    for i in range(0, len(urls), 40):           # a URL vai na query string; lote p/ não estourar
        lote = urls[i:i + 40]
        lista = ",".join('"' + u.replace('"', '') + '"' for u in lote)
        # ⚠️ o escape fica FORA da f-string: em Python 3.11 barra invertida dentro da
        # expressão de uma f-string é erro de sintaxe. `safe` preserva as aspas e a
        # vírgula, que são a gramática do `in.()` do PostgREST.
        cond = quote(lista, safe='",')
        try:
            r = requests.get(f"{url}/rest/v1/clipping_bodies?select=url,body,status"
                             f"&url=in.({cond})",
                             headers=_h(key), timeout=30)
            r.raise_for_status()
            for row in r.json():
                if row.get("status") == "ok" and row.get("body"):
                    out[row["url"]] = row["body"]
        except Exception as e:
            log.warning("blast: nao consegui ler os corpos guardados (%s) - sigo sem eles", e)
    return out


def process_blast(job: dict) -> None:
    """
    Job de BLAST: só os 3 destaques, e nada mais.

    O blast de WhatsApp é montado NO FRONT (preços do banco + manchetes que o analista já
    selecionou, com o take e o setor dele). A única parte que precisa de julgamento — quais
    notícias importam hoje — vem para cá, e é UMA chamada de IA. Sem Word, sem e-mail, sem
    raspar corpo: a rodada leva segundos em vez de 1 a 3 minutos.

    ⚠️ A resposta volta DENTRO do `config` (jsonb) do próprio job, em `config.blast`, e não
    numa coluna nova. Motivo: `admin_get_clipping_job` é `returns setof clipping_jobs` com
    `select *`, então o front recebe o `config` de graça — e a alternativa custaria mais uma
    migração de SQL na fila de coisas que o usuário precisa rodar à mão. A gravação FUNDE o
    que já estava no config (lê, mescla, escreve) para não apagar a configuração que veio do
    front no mesmo job.
    """
    from clipping.blast import highlights

    jid = job["id"]
    payload = job.get("payload") or []
    cfg = dict(job.get("config") or {})
    quantos = int(((cfg.get("blast") or {}).get("n")) or 3)

    # O TEXTO das notícias vem do cache que o clipping já encheu (`clipping_bodies`), e é
    # de graça: o aquecedor e a geração do Word já rasparam esses corpos. Sem isso a IA
    # escreveria destaque a partir de manchete, que é o que ela fazia até 01/09/2026 — sem
    # número, sem conseguir juntar duas notícias que contam a mesma história.
    # Corpo colado à mão pelo analista (payload) VENCE o cache: é a correção dele.
    corpos = _bodies_por_url([it.get("url") for it in payload if it.get("url")])
    # `pin` = a ★ que o analista marcou na tela (1, 2 ou 3). Vem no payload como qualquer
    # outro campo — jsonb aceita chave nova, então não houve migração de SQL. Sem ela a
    # escolha do analista morreria aqui, no caminho entre o navegador e o prompt.
    noticias = [{"title": it.get("title"), "source_name": it.get("source_name"),
                 "sector": it.get("sector"), "take": it.get("take"), "pin": it.get("pin"),
                 "body": it.get("body") or corpos.get(it.get("url"), "")} for it in payload]
    com_texto = sum(1 for n in noticias if n["body"])
    log.info("job %s BLAST: %d de %d notícias com texto, %d marcada(s) com ★",
             jid, com_texto, len(noticias), sum(1 for n in noticias if n.get("pin")))
    res = highlights(noticias, n=quantos)
    log.info("job %s BLAST: %d destaque(s) por %s%s", jid, len(res["destaques"]),
             res["provedor"] or "ninguem", f" (erro: {res['erro']})" if res["erro"] else "")

    atual = read_job(jid) or {}
    cfg = dict(atual.get("config") or cfg)
    cfg["blast"] = {**(cfg.get("blast") or {}), **res, "at": _now()}
    patch_job(jid, {"status": "done", "config": cfg, "error": None,
                    "finished_at": _now(), "updated_at": _now()})


def process(job: dict) -> None:
    jid = job["id"]
    payload = job.get("payload") or []
    # Job de blast não gera documento — desvia antes de qualquer raspagem ou tradução.
    if ((job.get("config") or {}).get("blast") or {}).get("only"):
        try:
            process_blast(job)
        except Exception as e:
            log.exception("job %s ERRO no blast", jid)
            try:
                patch_job(jid, {"status": "error", "error": str(e)[:500],
                                "finished_at": _now(), "updated_at": _now()})
            except Exception:
                log.exception("falha ao marcar erro no job %s", jid)
        return
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
            "docx_path": _sign(f"{base}/{res['docx_name']}", download=res["docx_name"]),
            "eml_path": _sign(f"{base}/{res['eml_name']}", download=res["eml_name"]),
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
