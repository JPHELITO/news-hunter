"""Filtro de URL de TODO link publicado (PLANO, Apêndice E; pesquisa 1b-dados §5).

O Mindassets é aberto a qualquer cliente. Os links das notícias vêm dos raspadores, mas os dos
relatórios e do calendário são DIGITADOS pelo admin e podem apontar para SharePoint ou Storage
com token de compartilhamento. Regra:
  • só `http(s)`, com host, sem usuário/senha no endereço;
  • fora: endereço interno (Supabase/Storage, localhost, rede privada) e link com credencial
    na consulta ou no fragmento (`token=`, assinaturas, SAS do Azure, `X-Amz-*`, `X-Goog-*`…);
  • link recusado vira `null` (a notícia sem link sai do arquivo — ver news_common).
`url_problem` devolve o MOTIVO (estatística do log, nunca a URL); `safe_url` devolve o link ou
None; `scrub_text` tira de um texto livre (descrição de evento) os links que não passariam.
"""
from __future__ import annotations

import ipaddress
import re
from urllib.parse import parse_qsl, urlsplit

MAX_LEN = 2048

# nomes de parâmetro (minúsculas) que são credencial por si só
_SECRET_PARAMS = frozenset({
    "token", "access_token", "id_token", "refresh_token", "auth", "auth_token", "authtoken",
    "authorization", "apikey", "api_key", "api-key", "key", "sig", "signature", "jwt", "sas",
    "password", "passwd", "pwd", "secret", "client_secret", "code_verifier", "key-pair-id",
    "sessionid", "session_id"})
# pedaços de nome que denunciam credencial (accessToken, X-Amz-Credential, x-goog-signature…)
_SECRET_PARTS = ("token", "signature", "secret", "password", "apikey", "api_key", "credential")
_SECRET_PREFIXES = ("x-amz-", "x-goog-", "x-ms-")
_INTERNAL_HOSTS = frozenset({"localhost", "supabase.co", "supabase.in", "supabase.net"})
_INTERNAL_SUFFIXES = (".supabase.co", ".supabase.in", ".supabase.net", ".localhost", ".local",
                      ".internal", ".lan")
_INTERNAL_PATHS = ("/storage/v1/", "/rest/v1/", "/auth/v1/", "/functions/v1/", "/realtime/v1/")
_BAD_CHARS = re.compile(r'[\x00-\x20\x7f<>"\\]')
_TEXT_URL = re.compile(r"https?://[^\s<>\"'()\[\]{}]+", re.I)


def _secret_keys(query: str) -> bool:
    try:
        keys = {k.strip().lower() for k, _ in parse_qsl(query, keep_blank_values=True)}
    except ValueError:
        return True
    for k in keys:
        if (k in _SECRET_PARAMS or k.startswith(_SECRET_PREFIXES)
                or any(p in k for p in _SECRET_PARTS)):
            return True
    return False


def _internal_host(host: str) -> bool:
    if host in _INTERNAL_HOSTS or host.endswith(_INTERNAL_SUFFIXES):
        return True
    try:
        ip = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_unspecified


def url_problem(value) -> str | None:
    """None se o link pode ir ao arquivo público; senão o motivo (curto, sem a URL)."""
    if not isinstance(value, str) or not value.strip():
        return "vazio"
    u = value.strip()
    if len(u) > MAX_LEN:
        return "longo"
    if _BAD_CHARS.search(u):
        return "caractere inválido"
    try:
        parts = urlsplit(u)
        host = (parts.hostname or "").lower()
        _ = parts.port                      # porta inválida levanta ValueError
    except ValueError:
        return "malformado"
    if parts.scheme.lower() not in ("http", "https"):
        return "esquema"
    if not host:
        return "sem host"
    if parts.username is not None or parts.password is not None:
        return "credencial"
    if _internal_host(host):
        return "interno"
    if any(p in parts.path.lower() for p in _INTERNAL_PATHS):
        return "storage"
    frag = parts.fragment.split("?", 1)[1] if "?" in parts.fragment else parts.fragment
    if _secret_keys(parts.query) or ("=" in frag and _secret_keys(frag)):
        return "credencial"
    return None


def safe_url(value) -> str | None:
    """O link (sem espaços nas pontas) se pode ser publicado; senão None."""
    return value.strip() if url_problem(value) is None else None


def scrub_text(text):
    """Texto livre sem os links http(s) que o filtro recusaria (o resto do texto fica igual)."""
    if not isinstance(text, str):
        return text
    return _TEXT_URL.sub(lambda m: m.group(0) if url_problem(m.group(0)) is None else "", text)
