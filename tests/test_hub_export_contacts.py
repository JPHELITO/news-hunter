"""
A máscara de contato dos textos livres públicos (hub_export/contacts.py — decisão U6 do usuário).

O que estes testes TRAVAM, e por quê:
  1) os formatos REAIS medidos nos trechos dos comunicados (10/11/2026: 43 e-mails e 13 telefones em
     81 trechos, todos de RI) saem mascarados — `55 31 3499.8000` depois de "Tel.:"/"Fax.:",
     `(31) 3499-8000`, a continuação `/8500`, `+55 (21) …` —, e os usuais do Brasil também (celular,
     DDI sem `+`, 0800, ramal, internacional);
  2) o PEDAÇO no fim do texto: o trecho é cortado em 1.500 caracteres (medido: 2 dos 70 trechos
     cortados param no meio do contato de RI);
  3) nada que não é contato muda — datas (inclusive `20082026`, como 36 descrições de insiders trazem),
     intervalos de anos, CNPJ, NIRE, CEP, quantidades, valores, códigos, o "R." de rua, o `@perfil`;
  4) função pura e idempotente (os construtores a aplicam sobre o banco e sobre a cópia publicada).
Os números e endereços aqui são INVENTADOS no formato dos reais (nenhum contato de verdade).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hub_export.contacts import EMAIL_MASK, PHONE_MASK, mask_contacts  # noqa: E402

E, T = EMAIL_MASK, PHONE_MASK

CASOS = [
    # ── os formatos medidos nos trechos reais (números inventados) ─────────────────────────────
    ("Tel.: 55 31 3499.8000", f"Tel.: {T}"),
    ("Fax.: 55 31 3499.8001 E-mail: ri@siderurgica-exemplo.com.br", f"Fax.: {T} E-mail: {E}"),
    ("Tel.: (31) 3499-8000", f"Tel.: {T}"),
    ("Tel.: (31) 3499-8000/8500 www.empresa-exemplo.com.br/ri", f"Tel.: {T} www.empresa-exemplo.com.br/ri"),
    ("Relações com Investidores Tel.: +55 (21) 3485-0000 e-mail: ri@mineradora-exemplo.com",
     f"Relações com Investidores Tel.: {T} e-mail: {E}"),
    ("investor.relations@empresa-exemplo.com", E),
    ("contato: ri@g7-exemplo.com.br.", f"contato: {E}."),
    ("RI@EMPRESA-EXEMPLO.COM.BR", E),
    # ── e-mail escrito de outro jeito ───────────────────────────────────────────────────────────
    ("ri @ empresa-exemplo.com.br", E),
    ("ri[at]empresa-exemplo.com.br", E),
    ("ri (at) empresa-exemplo.com.br", E),
    ("<mailto:ri@empresa-exemplo.com>", f"<mailto:{E}>"),
    # ── celular, DDI, DDD, 0800, ramal, palavra-chave ──────────────────────────────────────────
    ("(11) 98765-4321", T),
    ("11 98765-4321", T),
    ("+55 11 98765-4321", T),
    ("+5511987654321", T),
    ("55-11-3503-9330", T),
    ("(011) 3503-9330", T),
    ("(0xx11) 3503-9330", T),
    ("31-3499-8000", T),
    ("SAC 0800 891 3456", f"SAC {T}"),
    ("0800-703-1300", T),
    ("08007031300", T),
    ("Telefone: 3503-9330", f"Telefone: {T}"),
    ("fone 3503 9330", f"fone {T}"),
    ("WhatsApp: (11) 99999-0000", f"WhatsApp: {T}"),
    ("Tel/Fax: 3503-9331", f"Tel/Fax: {T}"),
    ("(31) 3499-8000 ramal 223", T),
    ("(31) 3499-8000, ramal 223 e 224.", f"{T}."),
    ("(31) 3499-8000 ext. 12", T),
    # ── internacional ───────────────────────────────────────────────────────────────────────────
    ("Toronto: +1 (416) 649-1033", f"Toronto: {T}"),
    ("+44 20 7946 0000", T),
    ("+1 212 555 0100", T),
    # ── frase inteira ───────────────────────────────────────────────────────────────────────────
    ("Ligue (21) 3485-0000 ou escreva para ri@empresa-exemplo.com.",
     f"Ligue {T} ou escreva para {E}."),
    # ── o PEDAÇO no fim do texto (o trecho corta em 1.500 caracteres) ───────────────────────────
    ("Dúvidas: ri@aura-exemplo.co", f"Dúvidas: {E}"),
    ("Contato: investor.rel@", f"Contato: {E}"),
    ("E-mail: investor.relat", f"E-mail: {E}"),
    ("Tel.: (31) 3499-80", f"Tel.: {T}"),
    ("Tel.: (31", f"Tel.: {T}"),
    ("Investidores: +55 (31) 34", f"Investidores: {T}"),
    ("Investidores: (31) 34", f"Investidores: {T}"),
    ("Investidores: 55 31 349", f"Investidores: {T}"),
]

NAO_E_CONTATO = [
    "Operação liquidada em 20082026",                       # a data colada das descrições de insiders
    "Plano aprovado para o período 2025-2026 e 2026-2030; safra 2025/2026.",
    "Item 12 2025-2026 da tabela (21) 2026-2027; o anexo 31 1999.2000 também.",   # DDD + anos
    "Datas: 06/11/2026, 06.11.2026, 2026-11-06, 06-11-2026 e 6/11/26.",
    "CNPJ/MF nº 33.592.510/0001-54 e CNPJ 89.637.490/0001-45",
    "NIRE 33.300.019.766 · NIRE 43300002578 · NIRE 35-3.0000000.0 · NIRE 3530001234-1",
    "Sede: Rua Exemplo, 100, CEP 20030-900 (ou 90.010-150).",
    "Quantidade: 1.234.567.890 ações; 100.000.000 ON; 4.539.007.580 em circulação.",
    "Valor de R$ 123456789 e R$ 1.234.567,89; US$ 200.000.000; 8,65% a.a.; +12,5%; +1.234.567 ações",
    "Resolução CVM nº 44/2021, Lei nº 6.404/76, ISIN BRVALEACNOR0, (B3: CSNA3; NYSE: SID), KLBN11.",
    "Reunião às 10h30 de 15 de outubro; resultado do 3T26 e do 1S26.",
    "Tabela: (1) 123.456 12.345 12.345 · 15 123.456.789 1.234.567 · 1.234.567 123.456 123.456",
    "Endereço: Rua 25 de Março, 1000, São Paulo; R. 7 de Setembro, 12.",
    "Siga a Vale @valeoficial e #mineração",
    "",
]


@pytest.mark.parametrize("texto,esperado", CASOS)
def test_mascara_os_contatos(texto, esperado):
    assert mask_contacts(texto) == esperado


@pytest.mark.parametrize("texto", NAO_E_CONTATO)
def test_nao_mexe_no_que_nao_e_contato(texto):
    assert mask_contacts(texto) == texto


@pytest.mark.parametrize("texto", [t for t, _ in CASOS] + NAO_E_CONTATO)
def test_idempotente(texto):
    uma = mask_contacts(texto)
    assert mask_contacts(uma) == uma


def test_nao_texto_volta_como_veio():
    for v in (None, 0, 12.5, ["a@b.com"], {"email": "a@b.com"}):
        assert mask_contacts(v) is v or mask_contacts(v) == v


def test_rodape_de_ri_no_formato_real():
    """O fecho de um comunicado como os do banco (estrutura real, dados inventados): todos os
    contatos somem, o resto fica letra por letra."""
    texto = ("São Paulo, 06 de novembro de 2026. Fulano de Tal Diretor de Relações com Investidores "
             "Relações com Investidores: Tel.: 55 31 3499.8000 Fax.: 55 31 3499.8001 "
             "ri@siderurgica-exemplo.com.br www.siderurgica-exemplo.com.br/ri "
             "CNPJ 60.894.730/0001-05 NIRE 31300040127")
    out = mask_contacts(texto)
    assert out == ("São Paulo, 06 de novembro de 2026. Fulano de Tal Diretor de Relações com "
                   f"Investidores Relações com Investidores: Tel.: {T} Fax.: {T} {E} "
                   "www.siderurgica-exemplo.com.br/ri CNPJ 60.894.730/0001-05 NIRE 31300040127")
    assert "@" not in out and not re.search(r"\d{4}[.\-]\d{4}", out)
