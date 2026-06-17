# -*- coding: utf-8 -*-
"""Scraping BACKSTAGE do corpo do artigo p/ o classificador LLM de takes (FASE 3).

Princípios (LLM_TAKES_PLAN.md §1.3, §5.1):
  - O corpo é usado SÓ para classificar; NUNCA é persistido nem exposto na dashboard.
  - Paywall (Platts/Fastmarkets) -> sem corpo (título+snippet); leitor por sessão = FASE 6.
  - Guard-rails da FASE 2: pular fontes de "página compartilhada" (SMM serve um daily-report
    único p/ várias manchetes -> contamina) e detectar TEASER de paywall (corpo curto + marcador).

requests -> fallback curl_cffi (impersonate=chrome, vence Cloudflare/Reuters 401) -> trafilatura.
"""
from __future__ import annotations

import logging
import re

import requests

log = logging.getLogger(__name__)

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"}

# Exigem login -> sem corpo na v1 (são ~maioria do volume relevante; Decisão F / FASE 6).
PAYWALL_DOMAINS = ("spglobal.com", "platts.com", "fastmarkets.com")

# Fontes cujo "corpo" e pagina COMPARTILHADA. SMM RE-LIGADO (2026-06-17): o corpo per-artigo
# (/newscontent/<id>) vem distinto e util; so alguns data-reports servem o aviso "Data Source
# Statement" no lugar do corpo -> filtrado por _is_boilerplate abaixo, e nao por skip total da fonte.
NO_BODY_SOURCES: set[str] = set()

# Marcadores de TEASER de paywall (multi-idioma): se aparecem e o corpo é curto, descartar.
_TEASER_MARKERS = (
    "only premium", "+plus subscribers", "subscribe now", "subscribers can access",
    "assine", "já tem uma conta", "faça login", "conteúdo exclusivo", "para assinantes",
    "acesso exclusivo", "create an account", "sign in to read", "register to continue",
)
MIN_VALID = 200       # < isso = sem corpo útil
TEASER_MAXLEN = 900   # corpo curto COM marcador de teaser -> tratar como sem corpo
MAX_CHARS = 3500      # trunca p/ caber no contexto e baratear


def is_paywalled(url: str) -> bool:
    return any(p in (url or "") for p in PAYWALL_DOMAINS)


def _looks_like_teaser(body: str) -> bool:
    low = body.lower()
    return len(body) < TEASER_MAXLEN and any(m in low for m in _TEASER_MARKERS)


def _is_boilerplate(body: str) -> bool:
    # SMM as vezes extrai so o aviso de fonte ("Data Source Statement...") no lugar do corpo real.
    return body.lstrip()[:60].lower().startswith("data source statement")


def _extract(html: str) -> str:
    if not html or len(html) < 400:
        return ""
    try:
        import trafilatura
    except ImportError:
        log.warning("trafilatura ausente — `pip install trafilatura`")
        return ""
    b = trafilatura.extract(html, include_comments=False, include_tables=False) or ""
    return re.sub(r"\n{3,}", "\n\n", b).strip()


def fetch_body(url: str, source: str | None = None, timeout: int = 12):
    """Retorna (body|None, meta). meta = {ok, method, chars, reason}.

    Devolve None (sem quebrar) quando: sem url, paywall, fonte de página compartilhada,
    teaser de paywall, ou corpo curto/vazio. O chamador cai p/ título+snippet.
    """
    if not url:
        return None, {"ok": False, "method": None, "chars": 0, "reason": "sem url"}
    if source in NO_BODY_SOURCES:
        return None, {"ok": False, "method": None, "chars": 0, "reason": "fonte sem corpo (compartilhada)"}
    if is_paywalled(url):
        return None, {"ok": False, "method": None, "chars": 0, "reason": "paywall"}

    try:
        html = requests.get(url, headers=UA, timeout=timeout).text or ""
    except Exception:
        html = ""
    body = _extract(html)
    method = "requests" if len(body) >= MIN_VALID else None

    if not method:                       # fallback anti-bloqueio (Cloudflare/Reuters)
        try:
            from curl_cffi import requests as creq
            body = _extract(creq.get(url, impersonate="chrome", timeout=timeout + 6).text or "")
            if len(body) >= MIN_VALID:
                method = "curl_cffi"
        except Exception:
            pass

    if not method:
        return None, {"ok": False, "method": None, "chars": len(body), "reason": "vazio/curto"}
    if _looks_like_teaser(body):
        return None, {"ok": False, "method": method, "chars": len(body), "reason": "teaser de paywall"}
    if _is_boilerplate(body):
        return None, {"ok": False, "method": method, "chars": len(body), "reason": "boilerplate (data-statement)"}
    return body[:MAX_CHARS], {"ok": True, "method": method, "chars": len(body), "reason": "ok"}
