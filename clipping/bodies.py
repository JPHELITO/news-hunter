"""Busca do CORPO das matérias (HTML seguro) para o clipping.

- Autenticadas/SPA (Platts/Fastmarkets/Valor/Estadão): reader.py — fluxos DEDICADOS por fonte
  (portados do clipinator antigo), reaproveitando as sessões keep-alive do news-hunter
  (Supabase source_sessions p/ Platts/FM; state files em COOKIES_DIR p/ Valor/Estadão).
- Regulares (mining.com / portalcelulose.com.br / ...): requests+bs4 (_fetch_body_regular).

Retorna '' quando não conseguiu — o Word/e-mail mostram um aviso no lugar.
"""
from __future__ import annotations

import logging
import os
from urllib.parse import quote, urlparse

log = logging.getLogger(__name__)

# fontes com raspador dedicado (Playwright + sessão)
_AUTH = frozenset(["core.spglobal.com", "dashboard.fastmarkets.com",
                   "valor.globo.com", "www.estadao.com.br"])


# ── Armazém de corpos (clipping_bodies no Supabase — 100% à parte do news hunter) ──
# O aquecedor grava aqui; a geração e a prévia leem daqui. Só o clipping usa esta tabela.
def _supa() -> tuple[str, str]:
    return (os.environ.get("SUPABASE_URL", "").rstrip("/"),
            os.environ.get("SUPABASE_SERVICE_KEY", ""))


def get_stored_body(url: str) -> dict | None:
    """Corpo já guardado em clipping_bodies (ou None). {body, translated_title, translated_body}."""
    su, key = _supa()
    if not su or not key:
        return None
    import requests
    # 2 tentativas: uma leitura que FALHA (rede da nuvem lenta) parecia "não tem corpo guardado" e
    # o robô raspava de novo — foi assim que a tradução do Valor de 24/11 se perdeu (ver store_body).
    for tentativa in range(2):
        try:
            r = requests.get(
                f"{su}/rest/v1/clipping_bodies?url=eq.{quote(url, safe='')}"
                f"&select=body,translated_title,translated_body,char_len&limit=1",
                headers={"apikey": key, "Authorization": f"Bearer {key}"}, timeout=15)
            if r.ok:
                rows = r.json()
                if rows and (rows[0].get("char_len") or 0) > 80:
                    return rows[0]
                return None                              # respondeu: não tem (ou só o vazio)
        except Exception as e:
            log.warning("get_stored_body(%s): leitura falhou (%s)%s", url[-60:], type(e).__name__,
                        " — tento de novo" if tentativa == 0 else "")
    return None


def store_body(url: str, title: str, source_name: str, body: str,
               translated_title: str = "", translated_body: str = "", status: str = "ok") -> None:
    """Upsert do corpo em clipping_bodies (best-effort). Sem Supabase → no-op."""
    su, key = _supa()
    if not su or not key:
        return
    try:
        import requests
        row = {"url": url, "title": title or "", "source_name": source_name or "",
               "body": body or "", "status": status, "char_len": len(body or "")}
        # ⚠️ A TRADUÇÃO SÓ VAI QUANDO EXISTE. O upsert (merge) grava as colunas que vierem: mandar ""
        # apagava a tradução boa. 24/11/2026: uma leitura do cache falhou, o robô raspou o Valor de
        # novo, a tradução falhou (tradutores lotados) e o "" foi gravado por cima da de manhã.
        if (translated_title or "").strip():
            row["translated_title"] = translated_title
        if (translated_body or "").strip():
            row["translated_body"] = translated_body
        requests.post(
            f"{su}/rest/v1/clipping_bodies?on_conflict=url",
            json=[row],
            headers={"apikey": key, "Authorization": f"Bearer {key}",
                     "Content-Type": "application/json",
                     "Prefer": "resolution=merge-duplicates,return=minimal"}, timeout=20)
    except Exception as e:
        log.warning("store_body(%s): %s", url, e)


def norm_domain(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if host.endswith("estadao.com.br"):
        return "www.estadao.com.br"
    if host.endswith("globo.com") and "valor" in host:
        return "valor.globo.com"
    return host[4:] if host.startswith("www.") else host


def fetch_body(url: str) -> str:
    """HTML seguro do corpo. Escolhe o método pela origem da URL."""
    dom = norm_domain(url)
    if dom in _AUTH:
        try:
            from .reader import fetch_article        # roda em thread com timeout de 90s
            _title, body = fetch_article(url)
            return body or ""
        except Exception as e:
            log.warning("clipping: corpo autenticado falhou (%s): %s", dom, e)
            return ""
    try:
        from .build import _fetch_body_regular
        return _fetch_body_regular(url) or ""
    except Exception as e:
        log.warning("clipping: corpo regular falhou (%s): %s", url, e)
        return ""
