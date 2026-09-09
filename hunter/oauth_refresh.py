"""Renovação da sessão de Platts e Fastmarkets SEM navegador e SEM login.

Por que isto existe
-------------------
O auto-login das duas fontes **não funciona da nuvem**: o Okta da S&P trata o IP de
datacenter do GitHub como device de risco e recusa a tela de login (medido em
2026-09-09: 5 tentativas, 5 recusas). A sessão só se mantinha viva porque o app, dentro
do navegador, renovava sozinho pelo *refresh token* — e isso, sim, passa do IP da nuvem.

Só que o app renova alguns segundos DEPOIS de subir, e o roll-forward gravava a sessão
ANTES disso. Como o Okta **rotaciona o refresh token a cada uso** (medido: cada renovação
devolve um token novo e invalida o anterior), o store ficava guardando um refresh token
JÁ CONSUMIDO. Enquanto alguém renovasse com frequência a corrente se sustentava; no dia em
que a rotação passou por cima do que estava guardado, ela arrebentou e não voltou mais —
porque a única saída seria o login interativo, que é justamente o que está bloqueado.

O que este módulo faz
---------------------
Fala com o servidor de identidade DIRETO (`grant_type=refresh_token`), em uma chamada
HTTP, e grava o token rotacionado NA HORA. Sem navegador, sem Playwright, sem login. É o
mesmo caminho que o app usa por dentro — a diferença é que aqui ele é explícito, roda a
cada ciclo e o resultado é persistido antes de qualquer outra coisa acontecer.

Com isto a sessão se mantém sozinha: enquanto o robô rodar mais rápido que o prazo de
ociosidade do refresh token, ninguém precisa logar nunca mais.

⚠️ REGRA DE OURO: a chamada de refresh CONSOME o token atual. Toda saída bem-sucedida tem
de ser gravada (local + store) ANTES de retornar. Se a gravação falhar depois de o
servidor ter rotacionado, a sessão morre. Por isso a gravação vem antes do log e do return.
"""
from __future__ import annotations

import json
import logging
import os
import time

import requests

log = logging.getLogger(__name__)

# Renova quando faltar menos que isto para vencer. O loop da nuvem roda a cada ~30 min,
# então 15 min garante pelo menos uma renovação por hora de vida do token.
RENOVAR_ANTES_S = 15 * 60
_TIMEOUT = 30


# ── leitura/escrita do blob de token dentro do storage_state ──────────────────
def _blob_platts(state: dict):
    """(entry_localStorage, dict_okta) do Platts, ou None."""
    for o in (state or {}).get("origins", []):
        for kv in o.get("localStorage", []):
            if kv.get("name") == "okta-token-storage":
                try:
                    return kv, json.loads(kv.get("value") or "{}")
                except Exception:
                    return None
    return None


def _blob_fm(state: dict):
    """(entry_localStorage, dict_oidc) do Fastmarkets, ou None."""
    for o in (state or {}).get("origins", []):
        for kv in o.get("localStorage", []):
            if (kv.get("name") or "").startswith("oidc.user:"):
                try:
                    return kv, json.loads(kv.get("value") or "{}")
                except Exception:
                    return None
    return None


def _exp_platts(d: dict) -> float | None:
    try:
        return float(d["accessToken"]["expiresAt"])
    except Exception:
        return None


def _exp_fm(d: dict) -> float | None:
    try:
        return float(d["expires_at"])
    except Exception:
        return None


def _pedido_platts(d: dict) -> dict:
    rt = d["refreshToken"]
    return {"url": rt["tokenUrl"],
            "client_id": d["idToken"]["clientId"],
            "scope": " ".join(rt.get("scopes") or []),
            "refresh_token": rt["refreshToken"]}


# O endpoint do Fastmarkets não vem no storage (o do Platts vem). Descoberto por OIDC
# discovery a partir do issuer e guardado em processo — nunca chutado por convenção.
_disc: dict[str, str] = {}


def _token_endpoint(issuer: str) -> str:
    issuer = issuer.rstrip("/")
    if issuer in _disc:
        return _disc[issuer]
    try:
        r = requests.get(f"{issuer}/.well-known/openid-configuration", timeout=_TIMEOUT)
        if r.ok and r.json().get("token_endpoint"):
            _disc[issuer] = r.json()["token_endpoint"]
            return _disc[issuer]
    except Exception as e:
        log.debug("oauth_refresh: discovery de %s falhou: %s", issuer, e)
    _disc[issuer] = f"{issuer}/connect/token"      # padrão IdentityServer, só como reserva
    return _disc[issuer]


def _pedido_fm(d: dict, chave: str) -> dict:
    issuer = (d.get("profile") or {}).get("iss") or chave.split("oidc.user:", 1)[-1].rsplit(":", 1)[0]
    return {"url": _token_endpoint(issuer),
            "client_id": chave.rsplit(":", 1)[-1],   # 'oidc.user:<issuer>:<client_id>'
            "scope": d.get("scope") or "",
            "refresh_token": d["refresh_token"]}


def _aplicar_platts(d: dict, j: dict, exp: float) -> None:
    d["accessToken"]["accessToken"] = j["access_token"]
    d["accessToken"]["expiresAt"] = int(exp)
    if j.get("id_token"):
        d["idToken"]["idToken"] = j["id_token"]
        d["idToken"]["expiresAt"] = int(exp)
    if j.get("refresh_token"):
        d["refreshToken"]["refreshToken"] = j["refresh_token"]
        d["refreshToken"]["expiresAt"] = int(exp)


def _aplicar_fm(d: dict, j: dict, exp: float) -> None:
    d["access_token"] = j["access_token"]
    d["expires_at"] = int(exp)
    if j.get("id_token"):
        d["id_token"] = j["id_token"]
    if j.get("refresh_token"):
        d["refresh_token"] = j["refresh_token"]


_PROV = {
    "platts":      {"blob": _blob_platts, "exp": _exp_platts,
                    "pedido": lambda d, k: _pedido_platts(d), "aplicar": _aplicar_platts},
    "fastmarkets": {"blob": _blob_fm, "exp": _exp_fm,
                    "pedido": _pedido_fm, "aplicar": _aplicar_fm},
}


def expira_em(provider: str, state: dict) -> float | None:
    """Segundos até o access token vencer (negativo = já venceu). None se não achou."""
    cfg = _PROV.get(provider)
    if not cfg:
        return None
    par = cfg["blob"](state)
    if not par:
        return None
    exp = cfg["exp"](par[1])
    return None if exp is None else exp - time.time()


def refresh(provider: str, *, force: bool = False) -> float | None:
    """Renova a sessão pelo refresh token e GRAVA (local + store). Devolve o novo prazo
    de validade em epoch, ou None se não deu (aí nada foi consumido nem alterado).

    force=False só renova quando falta menos de RENOVAR_ANTES_S para vencer.
    """
    cfg = _PROV.get(provider)
    if not cfg:
        return None
    from . import playwright_session as ps

    # 🔴 NUNCA renovar quando não se pode gravar. Renovar CONSOME o refresh token e o
    # servidor devolve outro; se o novo não puder ir para o store, o que fica lá é um
    # token queimado e a sessão MORRE — exatamente o defeito que este módulo conserta.
    # É o caso do hunt-once.yml (SESSION_STORE_READONLY=1), que é o workflow por trás do
    # botão "Buscar novas agora" da dashboard: sem esta guarda, um clique do analista
    # derrubaria a sessão da Platts. O one-shot usa a sessão que a corrente mantém viva.
    if os.environ.get("SESSION_STORE_READONLY") == "1":
        log.info("oauth_refresh: %s — SESSION_STORE_READONLY=1, não renovo "
                 "(rotacionar sem poder gravar mataria a sessão)", provider)
        return None

    try:
        ps.pull_session(provider)                  # sempre parte da versão mais nova do store
    except Exception as e:
        log.debug("oauth_refresh: pull_session(%s): %s", provider, e)

    sp = ps.state_path(provider)
    if not sp.exists():
        log.warning("oauth_refresh: %s sem arquivo de sessão", provider)
        return None
    try:
        state = json.loads(sp.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning("oauth_refresh: %s state ilegível: %s", provider, e)
        return None

    par = cfg["blob"](state)
    if not par:
        log.warning("oauth_refresh: %s sem blob de token na sessão", provider)
        return None
    entrada, d = par

    exp_atual = cfg["exp"](d)
    if not force and exp_atual and exp_atual - time.time() > RENOVAR_ANTES_S:
        return exp_atual                            # ainda tem folga, não gasta rotação à toa

    try:
        p = cfg["pedido"](d, entrada.get("name") or "")
    except Exception as e:
        log.warning("oauth_refresh: %s não montou o pedido: %s", provider, e)
        return None
    if not p.get("refresh_token"):
        log.warning("oauth_refresh: %s sem refresh token — só um login novo resolve", provider)
        return None

    try:
        r = requests.post(
            p["url"],
            data={"grant_type": "refresh_token", "refresh_token": p["refresh_token"],
                  "client_id": p["client_id"], "scope": p["scope"]},
            headers={"Content-Type": "application/x-www-form-urlencoded",
                     "Accept": "application/json"},
            timeout=_TIMEOUT)
    except Exception as e:
        log.warning("oauth_refresh: %s erro de rede: %s", provider, e)
        return None

    if r.status_code != 200:
        # 400 invalid_grant = refresh token consumido/expirado. Nada foi alterado; a
        # sessão precisa de um login novo (atalho local "Atualizar Platts").
        log.warning("oauth_refresh: %s HTTP %s — %s", provider, r.status_code, r.text[:180])
        return None
    try:
        j = r.json()
    except Exception:
        log.warning("oauth_refresh: %s resposta não-JSON", provider)
        return None
    if not j.get("access_token"):
        log.warning("oauth_refresh: %s resposta sem access_token", provider)
        return None

    # ⚠️ A partir daqui o token ANTIGO já foi consumido pelo servidor. Gravar PRIMEIRO.
    novo_exp = time.time() + float(j.get("expires_in") or 3600)
    cfg["aplicar"](d, j, novo_exp)
    entrada["value"] = json.dumps(d)
    texto = json.dumps(state)
    try:
        sp.write_text(texto, encoding="utf-8")
    except Exception as e:
        log.error("oauth_refresh: %s NÃO gravou local após rotacionar: %s", provider, e)
    try:
        ps._push_session_to_store(provider, texto)
    except Exception as e:
        log.error("oauth_refresh: %s NÃO gravou no store após rotacionar: %s", provider, e)

    log.info("oauth_refresh: %s renovado sem navegador (vale %d min)",
             provider, int((novo_exp - time.time()) / 60))
    return novo_exp


def keep_alive(providers=("platts", "fastmarkets")) -> dict[str, float | None]:
    """Mantém as sessões vivas. Best-effort: nunca levanta, nunca derruba o run."""
    out: dict[str, float | None] = {}
    for p in providers:
        try:
            out[p] = refresh(p)
        except Exception as e:
            log.warning("oauth_refresh: keep_alive(%s): %s", p, e)
            out[p] = None
    return out
