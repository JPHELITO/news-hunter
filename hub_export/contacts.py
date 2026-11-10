"""Máscara de contato nos TEXTOS LIVRES dos arquivos públicos (decisão U6 do usuário, 09/11/2026:
"publica tudo que está na minha dash" — com e-mails e telefones mascarados, regra do portal).

`mask_contacts(texto)` devolve o texto com cada e-mail trocado por `[e-mail]` e cada telefone por
`[telefone]`; todo o resto sai EXATAMENTE como veio (nomes e o texto da CVM ficam como publicados).
Função PURA e IDEMPOTENTE (mascarar o já mascarado não muda nada): os construtores do Market Watch a
aplicam em todo texto livre que publicam (`moveDesc`, `docExcerpt`, `docTitle`, `headline`, `reason`,
`purpose`), sobre o dado do banco e sobre a semente da cópia publicada.

TELEFONE — os formatos medidos nos trechos reais dos comunicados (10/11/2026: 13 telefones em 81
trechos, todos de RI) e os usuais do Brasil:
  • DDD entre parênteses, com ou sem DDI: `+55 (31) 3499-8000`, `(31) 3499-8000`, `(011) 3503-9330`,
    `55 (21) 3485 3900`, `(21) 98765-4321`;
  • DDI sem parênteses: `55 31 3499.8000` (o papel timbrado de uma das empresas), `+55 31 3499-8000`;
  • internacional com `+` (10 a 15 dígitos): `+1 (416) 649-1033`, `+44 20 7946 0000`;
  • só o DDD, sem parênteses: `31 3499-8000`, `11 98765-4321` — o DDD tem de existir (lista da
    Anatel), o número começar por 2–9 e ter hífen ou ponto no meio;
  • sem DDD, logo depois de "Tel.", "Telefone", "Fone", "Fax", "Celular", "WhatsApp", "Phone";
  • 0800/0300/0500/0900: `0800 891 3456`, `0800-703-1300`;
  • a continuação do mesmo contato (`/8500`, `/ 3499-8001`) e o ramal (`ramal 223`, `ext. 12`);
  • o PEDAÇO no fim do texto: o trecho do comunicado é cortado em 1.500 caracteres, às vezes no
    meio do contato (`… Tel.: (31) 3499-80`, `… +55 (31) 34`).
NÃO É TELEFONE (e não pode mudar): datas (`06/11/2026`, `06.11.2026`, `2026-11-06` e `06112026` —
36 descrições de insiders trazem a data assim), intervalos de anos (`2025-2026`), CNPJ, NIRE, CEP,
quantidades e valores (`1.234.567.890`, `R$ 123456789`), códigos de papel, ISIN, número de lei. Por
isso número solto sem separador nunca é telefone, e o "R." de rua não é marca de ramal (`R. 25 de
Março` existe).
E-MAIL: `usuario@dominio`, também com espaço dos DOIS lados do `@` (texto tirado de PDF) e com
`[at]`/`(at)`; o pedaço no fim do texto (`… ri@empresa.co`, `… E-mail: investor.rel`) também. Um
`@perfil` de rede social (sem nada colado antes) não é e-mail.
"""
from __future__ import annotations

import re

EMAIL_MASK = "[e-mail]"
PHONE_MASK = "[telefone]"

# DDDs em uso no Brasil (Anatel)
_DDD = frozenset({11, 12, 13, 14, 15, 16, 17, 18, 19, 21, 22, 24, 27, 28, 31, 32, 33, 34, 35, 37,
                  38, 41, 42, 43, 44, 45, 46, 47, 48, 49, 51, 53, 54, 55, 61, 62, 63, 64, 65, 66,
                  67, 68, 69, 71, 73, 74, 75, 77, 79, 81, 82, 83, 84, 85, 86, 87, 88, 89, 91, 92,
                  93, 94, 95, 96, 97, 98, 99})

_S = r"[ .\-]"                                       # separador dentro do número
_LOCAL = r"9?\d{4}" + _S + r"?\d{4}"                 # 8 ou 9 dígitos (4 ou 5 + 4)
# a continuação do MESMO contato: "/8500" (o fim de outra linha) ou "/ (31) 3499-8001"
_ALT = r"(?:\s?/\s?(?:\(\s?\d{2}\s?\)\s?)?(?:" + _LOCAL + r"|\d{4})(?!\d))*"
_EXT = (r"(?:\s?[,;\-]?\s?(?:ramal|ramais|ext\.?|extens[ãa]o)\s?:?\s?\d{1,5}"
        r"(?:\s?(?:/|,|e)\s?\d{1,5})*(?!\d))?")
_KW = r"\b(?:tel(?:efones?|ephone)?|fones?|fax|celular|cel|whats\s?app|phone)\b\.?\s{0,3}(?:[:\-]\s{0,3})?"
_YEARS = re.compile(r"(?:19|20)\d\d[ .\-/](?:19|20)\d\d")

_EMAIL = re.compile(r"[\w.%+\-]+(?:@|\s@\s|\s?\[(?:at|arroba)\]\s?|\s?\((?:at|arroba)\)\s?)"
                    r"[\w\-]+(?:\.[\w\-]*)*", re.I)
_PAREN = re.compile(r"(?<![\w])(?:(?:\+\s?)?55\s?[\-.]?\s?)?\(\s?(?:0(?:xx)?)?(?P<ddd>\d{2})\s?\)\s?"
                    r"(?P<local>" + _LOCAL + r")(?!\d)" + _ALT + _EXT, re.I)
_DDI = re.compile(r"(?<![\w.,/+])(?:\+\s?)?55" + _S + r"(?:0(?:xx)?)?(?P<ddd>\d{2})" + _S
                  + r"(?P<local>" + _LOCAL + r")(?!\d)" + _ALT + _EXT, re.I)
_INTL = re.compile(r"(?<![\w+])(?P<main>\+\s?\d{1,3}(?:[ .\-]?(?:\(\s?\d{1,4}\s?\)|\d{1,5})){1,5})"
                   r"(?!\d)" + _ALT + _EXT, re.I)
_FREE = re.compile(r"(?<![\w.,/])0[3589]00" + _S + r"?\d{3}" + _S + r"?\d{4}(?!\d)" + _EXT, re.I)
_KEYWORD = re.compile(r"(?P<kw>" + _KW + r")(?P<num>(?:\(\s?\d{2}\s?\)\s?|\d{2}[ \-])?"
                      + _LOCAL + r")(?!\d)" + _ALT + _EXT, re.I)
_DDD_ONLY = re.compile(r"(?<![\w.,/+])(?:0(?:xx)?)?(?P<ddd>\d{2})[ \-](?P<local>9?\d{4}[.\-]\d{4})"
                       r"(?!\d|[.,]\d)" + _ALT + _EXT, re.I)
# o pedaço no FIM do texto (o trecho corta em 1.500 caracteres)
_TAIL_KW = re.compile(r"(?P<kw>" + _KW + r")(?P<num>[+(\d][\d\s().\-/+]*)$", re.I)
_TAIL_PAREN = re.compile(r"(?<![\w])(?:\+\s?\d{1,3}[ .\-]?)?\(\s?0?\d{2}\s?\)[ .\-]?\d[\d .\-]*$")
_TAIL_DDI = re.compile(r"(?<![\w.,/+])(?:\+\s?)?55[ .\-]0?(?P<ddd>\d{2})[ .\-]\d[\d .\-]*$")
_TAIL_PLUS = re.compile(r"(?<![\w+])\+\s?\d[\d .\-()]*$")
_TAIL_EMAIL = re.compile(r"[\w.%+\-]+(?:@|\s@)\s?$")
_TAIL_EMAIL_KW = re.compile(r"(?P<kw>\be-?mail\s{0,2}:\s{0,3})(?P<num>[\w.%+\-]+)$", re.I)


def _digits(s: str) -> int:
    return sum(c.isdigit() for c in s)


def _local_ok(local: str) -> bool:
    """O número local começa por 2–9 (fixo 2–5, celular 9) e não é um intervalo de anos."""
    return local[:1] not in ("0", "1") and not _YEARS.fullmatch(local)


def _email(m: re.Match) -> str:
    s, tail = m.group(0), ""
    while s and s[-1] in ".-":                 # o ponto final da frase fica fora da máscara
        s, tail = s[:-1], s[-1] + tail
    return EMAIL_MASK + tail


def _paren(m: re.Match) -> str:
    if m.group("ddd")[0] == "0" or _YEARS.fullmatch(m.group("local")):
        return m.group(0)
    return PHONE_MASK


def _ddi(m: re.Match) -> str:
    if int(m.group("ddd")) not in _DDD or not _local_ok(m.group("local")):
        return m.group(0)
    return PHONE_MASK


def _intl(m: re.Match) -> str:
    return PHONE_MASK if 10 <= _digits(m.group("main")) <= 15 else m.group(0)


def _keyword(m: re.Match) -> str:
    return m.group("kw") + PHONE_MASK


def _tail_ddi(m: re.Match) -> str:
    return PHONE_MASK if int(m.group("ddd")) in _DDD else m.group(0)


def _tail_plus(m: re.Match) -> str:
    return PHONE_MASK if _digits(m.group(0)) >= 6 else m.group(0)


def _tail_kw(m: re.Match) -> str:
    return m.group("kw") + PHONE_MASK if _digits(m.group("num")) else m.group(0)


_STEPS = (
    (_EMAIL, _email),
    (_PAREN, _paren),
    (_DDI, _ddi),
    (_INTL, _intl),
    (_FREE, lambda m: PHONE_MASK),
    (_KEYWORD, _keyword),
    (_DDD_ONLY, _ddi),
    (_TAIL_EMAIL_KW, lambda m: m.group("kw") + EMAIL_MASK),
    (_TAIL_EMAIL, lambda m: EMAIL_MASK),
    (_TAIL_KW, _tail_kw),
    (_TAIL_PAREN, lambda m: PHONE_MASK),
    (_TAIL_DDI, _tail_ddi),
    (_TAIL_PLUS, _tail_plus),
)


def mask_contacts(text):
    """O texto com e-mails → `[e-mail]` e telefones → `[telefone]`; o resto igual. Não-texto volta
    como veio (None, número)."""
    if not isinstance(text, str) or not text:
        return text
    if "@" not in text and not any(c.isdigit() for c in text) and "[at]" not in text.lower() \
            and "(at)" not in text.lower() and "mail" not in text.lower():
        return text
    out = text
    for rx, fn in _STEPS:
        out = rx.sub(fn, out)
    return out
