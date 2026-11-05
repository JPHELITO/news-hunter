"""Filtro de URL de TODO link publicado (PLANO, Apêndice E; pesquisa 1b-dados §5): notícias, itens do
resumo da hora e relatórios.

O Mindassets é aberto a qualquer cliente. Os links das notícias vêm dos raspadores, mas os dos
relatórios são DIGITADOS pelo admin e podem apontar para SharePoint ou Storage com token de
compartilhamento — ou ser copiados de um e-mail recebido pelo time. Regra:
  • só `http(s)`, com host, sem usuário/senha no endereço;
  • fora: endereço interno (Supabase/Storage, localhost, rede privada) e link com credencial
    na consulta ou no fragmento (`token=`, assinaturas, SAS do Azure, `X-Amz-*`, `X-Goog-*`…);
  • fora também (revisão do orquestrador, 05/11/2026, sobre os links do calendário): link de
    RASTREIO DE CLIQUE de e-mail e de marketing, que pode identificar QUEM recebeu o e-mail —
    o redirecionador do provedor (`mailerurl.*`, `click.*`, `mail.closir.com/c/…`, Mailchimp
    `*.list-manage.com`, SendGrid `/ls/click?upn=`, Salesforce Marketing Cloud `*.sfmc.*`,
    HubSpot, Marketo, Eloqua…) e o parâmetro que carrega o destinatário (`upn`, `sfmc_id`,
    `mc_eid`, `_hsenc`, `mkt_tok`, `elqTrackId`, `_ke`…). `utm_*` NÃO é rastreio de pessoa
    (é campanha) e passa: os RSS das notícias trazem `utm_source=rss`;
  • link recusado vira `null` (a notícia sem link sai do arquivo — ver news_common).
`url_problem` devolve o MOTIVO (estatística do log, nunca a URL); `safe_url` devolve o link ou None.
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

# rastreio de clique de e-mail/marketing
_TRACK_HOST_PREFIXES = ("mailerurl.", "click.", "clicks.", "trk.", "track.", "tracking.")
_TRACK_HOSTS = frozenset({"mail.closir.com", "list-manage.com", "sendgrid.net", "mandrillapp.com",
                          "hubspotlinks.com", "exct.net", "rs6.net"})
_TRACK_SUFFIXES = (".list-manage.com", ".sendgrid.net", ".mandrillapp.com", ".hubspotlinks.com",
                   ".exacttarget.com", ".exct.net", ".rs6.net", ".createsend1.com", ".pardot.com",
                   ".eloqua.com", ".mktoedge.com")
_TRACK_LABELS = frozenset({"sfmc"})                        # cloud.sfmc.itau.com.br, *.sfmc.*
_TRACK_PATHS = ("/ls/click", "/wf/click", "/ss/c/", "/track/click", "/e3t/")
_TRACK_PARAMS = frozenset({
    "upn",                                 # SendGrid (o destinatário vai codificado)
    "sfmc_id", "sfmc_sub", "sfmc_subscriberid", "subscriberkey",     # Salesforce Marketing Cloud
    "mc_eid",                              # Mailchimp (id do assinante)
    "_hsenc", "_hsmi",                     # HubSpot
    "mkt_tok",                             # Marketo
    "elqtrackid", "elqtrack", "elq",       # Eloqua
    "_ke",                                 # Klaviyo (e-mail do destinatário)
    "ss_email_id", "vero_id", "oly_enc_id", "subscriber_id", "subscriberid", "recipient_id"})
_CMAIL = re.compile(r"(^|\.)cmail\d+\.com$")               # Campaign Monitor
_BAD_CHARS = re.compile(r'[\x00-\x20\x7f<>"\\]')


def _keys(query: str) -> set[str] | None:
    try:
        return {k.strip().lower() for k, _ in parse_qsl(query, keep_blank_values=True)}
    except ValueError:
        return None


def _secret(keys: set[str]) -> bool:
    return any(k in _SECRET_PARAMS or k.startswith(_SECRET_PREFIXES)
               or any(p in k for p in _SECRET_PARTS) for k in keys)


def _internal_host(host: str) -> bool:
    if host in _INTERNAL_HOSTS or host.endswith(_INTERNAL_SUFFIXES):
        return True
    try:
        ip = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_unspecified


def _tracking(host: str, path: str, keys: set[str]) -> bool:
    labels = host.split(".")
    return (host in _TRACK_HOSTS or host.startswith(_TRACK_HOST_PREFIXES)
            or host.endswith(_TRACK_SUFFIXES) or bool(_TRACK_LABELS & set(labels))
            or bool(_CMAIL.search(host))
            or any(p in path for p in _TRACK_PATHS)
            or (labels[0] in ("mail", "email") and path.startswith("/c/"))   # Closir, Mailgun
            or bool(keys & _TRACK_PARAMS))


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
    path = parts.path.lower()
    if any(p in path for p in _INTERNAL_PATHS):
        return "storage"
    frag = parts.fragment.split("?", 1)[1] if "?" in parts.fragment else parts.fragment
    keys = _keys(parts.query)
    fkeys = _keys(frag) if "=" in frag else set()
    if keys is None or fkeys is None or _secret(keys) or _secret(fkeys):
        return "credencial"
    if _tracking(host, path, keys | fkeys):
        return "rastreio"
    return None


def safe_url(value) -> str | None:
    """O link (sem espaços nas pontas) se pode ser publicado; senão None."""
    return value.strip() if url_problem(value) is None else None
