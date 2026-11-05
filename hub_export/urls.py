"""Filtro de URL de TODO link publicado (PLANO, Apêndice E; pesquisa 1b-dados §5): notícias, itens do
resumo da hora e relatórios.

O Mindassets é aberto a qualquer cliente. Os links das notícias vêm dos raspadores, mas os dos
relatórios são DIGITADOS pelo admin e podem apontar para SharePoint ou Storage com token de
compartilhamento — ou ser copiados de um e-mail recebido pelo time. Regra:
  • só `http(s)`, com host, sem usuário/senha no endereço;
  • fora: endereço interno (Supabase/Storage, localhost, rede privada) e link com credencial
    na consulta ou no fragmento (`token=`, assinaturas, SAS do Azure, `X-Amz-*`, `X-Goog-*`…);
  • rastreio de e-mail e de marketing (revisão do orquestrador, 05/11/2026), que pode identificar
    QUEM recebeu o e-mail:
      – REDIRECIONADOR de clique do provedor (`mailerurl.*`, `click.*`, `mail.closir.com/c/…`,
        Mailchimp `*.list-manage.com`, SendGrid `/ls/click`, Salesforce Marketing Cloud
        `*.sfmc.*`, HubSpot, Eloqua, Campaign Monitor…): o endereço inteiro é o rastreio → fora;
      – link NORMAL com parâmetro do destinatário (`upn`, `sfmc_*`, `mc_eid`, `_hsenc`, `_hsmi`,
        `mkt_tok`, `elqTrackId`, `elq`, `_ke`, `ss_email_id`…): o link fica, LIMPO — saem esses
        parâmetros e, junto, os de campanha do mesmo link (`utm_*`, `mc_cid`), para não sobrar
        rastro do e-mail. `utm_*` de link SEM parâmetro de destinatário fica como está (os RSS das
        notícias trazem `utm_source=rss`);
  • link recusado vira `null` (a notícia sem link sai do arquivo — ver news_common).
`url_problem` devolve o MOTIVO da recusa (estatística do log, nunca a URL) ou None; `safe_url`
devolve o link como vai ao arquivo (já limpo) ou None. Limpar é idempotente.
"""
from __future__ import annotations

import ipaddress
import re
from urllib.parse import parse_qsl, unquote_plus, urlsplit, urlunsplit

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

# redirecionadores de clique de e-mail/marketing: o endereço inteiro é o rastreio
_TRACK_HOST_PREFIXES = ("mailerurl.", "click.", "clicks.", "trk.", "track.", "tracking.")
_TRACK_HOSTS = frozenset({"mail.closir.com", "list-manage.com", "sendgrid.net", "mandrillapp.com",
                          "hubspotlinks.com", "exct.net", "rs6.net"})
_TRACK_SUFFIXES = (".list-manage.com", ".sendgrid.net", ".mandrillapp.com", ".hubspotlinks.com",
                   ".exacttarget.com", ".exct.net", ".rs6.net", ".createsend1.com", ".pardot.com",
                   ".eloqua.com", ".mktoedge.com")
_TRACK_LABELS = frozenset({"sfmc"})                        # cloud.sfmc.itau.com.br, *.sfmc.*
_TRACK_PATHS = ("/ls/click", "/wf/click", "/ss/c/", "/track/click", "/e3t/")
_CMAIL = re.compile(r"(^|\.)cmail\d+\.com$")               # Campaign Monitor
# parâmetros que carregam o DESTINATÁRIO (link normal: saem, e o link fica)
_RECIPIENT_PARAMS = frozenset({
    "upn",                                 # SendGrid
    "subscriberkey",                       # Salesforce Marketing Cloud (e todo `sfmc_*`)
    "mc_eid",                              # Mailchimp (id do assinante)
    "_hsenc", "_hsmi",                     # HubSpot
    "mkt_tok",                             # Marketo
    "elqtrackid", "elqtrack", "elq",       # Eloqua
    "_ke",                                 # Klaviyo (e-mail do destinatário)
    "ss_email_id", "vero_id", "oly_enc_id", "subscriber_id", "subscriberid", "recipient_id"})
_RECIPIENT_PREFIXES = ("sfmc_",)
# de campanha: saem JUNTO, só quando o link tem parâmetro de destinatário
_CAMPAIGN_PARAMS = frozenset({"mc_cid"})
_CAMPAIGN_PREFIXES = ("utm_",)
_BAD_CHARS = re.compile(r'[\x00-\x20\x7f<>"\\]')


def _keys(query: str) -> set[str] | None:
    try:
        return {k.strip().lower() for k, _ in parse_qsl(query, keep_blank_values=True)}
    except ValueError:
        return None


def _secret(keys: set[str]) -> bool:
    return any(k in _SECRET_PARAMS or k.startswith(_SECRET_PREFIXES)
               or any(p in k for p in _SECRET_PARTS) for k in keys)


def _recipient(key: str) -> bool:
    return key in _RECIPIENT_PARAMS or key.startswith(_RECIPIENT_PREFIXES)


def _internal_host(host: str) -> bool:
    if host in _INTERNAL_HOSTS or host.endswith(_INTERNAL_SUFFIXES):
        return True
    try:
        ip = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_unspecified


def _redirector(host: str, path: str) -> bool:
    labels = host.split(".")
    return (host in _TRACK_HOSTS or host.startswith(_TRACK_HOST_PREFIXES)
            or host.endswith(_TRACK_SUFFIXES) or bool(_TRACK_LABELS & set(labels))
            or bool(_CMAIL.search(host))
            or any(p in path for p in _TRACK_PATHS)
            or (labels[0] in ("mail", "email") and path.startswith("/c/")))   # Closir, Mailgun


def _clean_query(query: str) -> str:
    """A consulta sem os parâmetros do destinatário — e, se havia algum, sem os de campanha.
    Mexe só nos pedaços que saem: os outros ficam com a grafia (e a ordem) de antes."""
    parts = [p for p in query.split("&") if p]
    names = [unquote_plus(p.split("=", 1)[0]).strip().lower() for p in parts]
    if not any(_recipient(n) for n in names):
        return query
    keep = [p for p, n in zip(parts, names)
            if not (_recipient(n) or n in _CAMPAIGN_PARAMS or n.startswith(_CAMPAIGN_PREFIXES))]
    return "&".join(keep)


def _clean(u: str, parts) -> str:
    """O link sem os parâmetros do destinatário; sem nada a tirar, EXATAMENTE como veio."""
    query = _clean_query(parts.query)
    frag = parts.fragment
    if "?" in frag:
        head, tail = frag.split("?", 1)
        tail = _clean_query(tail)
        frag = f"{head}?{tail}" if tail else head
    elif "=" in frag:
        frag = _clean_query(frag)
    if query == parts.query and frag == parts.fragment:
        return u
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, frag))


def _check(value):
    """(motivo da recusa, link limpo): exatamente um dos dois é None."""
    if not isinstance(value, str) or not value.strip():
        return "vazio", None
    u = value.strip()
    if len(u) > MAX_LEN:
        return "longo", None
    if _BAD_CHARS.search(u):
        return "caractere inválido", None
    try:
        parts = urlsplit(u)
        host = (parts.hostname or "").lower()
        _ = parts.port                      # porta inválida levanta ValueError
    except ValueError:
        return "malformado", None
    if parts.scheme.lower() not in ("http", "https"):
        return "esquema", None
    if not host:
        return "sem host", None
    if parts.username is not None or parts.password is not None:
        return "credencial", None
    if _internal_host(host):
        return "interno", None
    path = parts.path.lower()
    if any(p in path for p in _INTERNAL_PATHS):
        return "storage", None
    frag = parts.fragment.split("?", 1)[1] if "?" in parts.fragment else parts.fragment
    keys = _keys(parts.query)
    fkeys = _keys(frag) if "=" in frag else set()
    if keys is None or fkeys is None or _secret(keys) or _secret(fkeys):
        return "credencial", None
    if _redirector(host, path):
        return "rastreio", None
    return None, _clean(u, parts)


def url_problem(value) -> str | None:
    """None se o link pode ir ao arquivo público (talvez depois de limpo); senão o motivo
    (curto, sem a URL)."""
    return _check(value)[0]


def safe_url(value) -> str | None:
    """O link como vai ao arquivo: sem espaços nas pontas e sem os parâmetros do destinatário
    (e os de campanha que vieram com eles); None se recusado."""
    return _check(value)[1]
