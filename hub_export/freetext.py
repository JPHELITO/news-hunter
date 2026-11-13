"""Texto livre e link num arquivo público da Onda 4 (PLANO, Apêndice H.0; decisão U12 do usuário, 12/11/2026:
o calendário publica local, descrição e links "pelo filtro e pela máscara").

`public_url(url)`: o link como vai ao arquivo, ou None. Passa pelo filtro de URL de sempre (`urls.safe_url`:
só http(s), sem credencial, sem endereço interno, sem rastreio de e-mail/marketing) e, por cima, recusa o
link que CARREGA um contato (um e-mail ou telefone dentro do endereço, `?email=fulano@…`): a máscara não
pode consertar um link sem quebrá-lo, então ele não sai.

`clean_text(texto)`: o texto como vai ao arquivo, ou None (vazio). Cada URL `http(s)` DENTRO do texto passa
pelo `public_url` — recusada vira `[link]` (a pontuação colada no fim da URL, como o ponto final da frase,
fica fora do teste e volta depois dela) — e o resto do texto passa pela máscara de contatos
(`contacts.mask_contacts`: e-mail → `[e-mail]`, telefone → `[telefone]`). A máscara roda com as URLs
trocadas por marcadores sem dígito nem `@`: o número de uma reunião no meio de um link não é telefone, e o
link aprovado volta inteiro. Função pura e idempotente.
"""
from __future__ import annotations

import re

from .contacts import mask_contacts
from .urls import safe_url

LINK_MASK = "[link]"
_URL_IN_TEXT = re.compile(r"https?://[^\s<>\"]+", re.I)     # a mesma ideia do `linkify` do site (AG:497)
_TRAIL = ".,;:!?)]}'"
_SLOT = "{}"                                    # marcador (uso privado do Unicode): sem dígito
_SLOT_RE = re.compile("([-])")


def public_url(value) -> str | None:
    """O link limpo pelo filtro de URL e sem contato dentro; None = não sai."""
    u = safe_url(value)
    if u is None:
        return None
    return u if mask_contacts(u) == u else None


def clean_text(value) -> str | None:
    """O texto sem as pontas em branco, com URL pelo filtro (recusada → `[link]`) e contatos mascarados."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if "" in text or "" in text:          # o texto não pode imitar o marcador
        text = text.replace("", "").replace("", "")
    links: list[str] = []

    def out(m: re.Match) -> str:
        raw = m.group(0)
        core = raw.rstrip(_TRAIL)
        tail = raw[len(core):]
        if len(links) >= 0xEFF:                          # (teto dos marcadores: texto absurdo)
            return LINK_MASK + tail
        links.append(public_url(core) or LINK_MASK)
        return _SLOT.format(chr(0xE100 + len(links) - 1)) + tail

    masked = mask_contacts(_URL_IN_TEXT.sub(out, text))
    return _SLOT_RE.sub(lambda m: links[ord(m.group(1)) - 0xE100], masked)
