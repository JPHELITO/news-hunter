"""Envelope do .eml do clipping — o que o OUTLOOK precisa p/ não cuspir código na tela.

Regressão de 2026-08-04: o .eml saía com quebras de linha LF (bytes(msg)) em vez de
CRLF. Um .eml com LF é inválido: o Outlook não decodifica o quoted-printable e mostra o
código-fonte ("<t=able role=3D..."), com símbolos "=" e "=3D" espalhados. O usuário viu
isso 3× e descrevia como "e-mail todo desformatado".

⚠️ Estes testes olham os BYTES CRUS de propósito. Decodificar o HTML por fora (como eu
fazia) MASCARA o defeito — o Python é tolerante com LF, o Outlook não é.

Rodar: python -m pytest tests/test_clipping_eml.py -v
"""
import email
import sys
from datetime import date
from email import policy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from clipping.build import ClippingItem
from clipping.eml import build_eml_bytes


def _items():
    return [
        ClippingItem(
            url="https://www.mining.com/x/", title="Iron ore slides on demand doubts",
            source_name="Mining.com", body="<p>Prices fell as traders weighed demand.</p>",
            matched_keywords=["iron ore"], domain="www.mining.com", take="-", sector="SM",
        ),
        ClippingItem(
            url="https://valor.globo.com/y.ghtml", title="Suzano avança em conectividade",
            source_name="Valor Econômico", body="<p>A Suzano avalia novas soluções.</p>",
            matched_keywords=["suzano"], domain="valor.globo.com", take="=", sector="PP",
            translated_title="Suzano advances connectivity",
            translated_body="<p>Suzano is evaluating new solutions.</p>",
        ),
    ]


def _raw():
    return build_eml_bytes(_items(), date(2026, 8, 4), docx_bytes=b"PK\x03\x04fake",
                           docx_name="clipping_20260804.docx")


def test_quebras_de_linha_sao_crlf():
    """O bug. .eml exige CRLF; com LF o Outlook mostra o código-fonte cru."""
    raw = _raw()
    assert raw.count(b"\r\n") > 0, "nenhum CRLF — .eml inválido p/ Outlook"
    assert raw.count(b"\n") - raw.count(b"\r\n") == 0, "achou LF sozinho (quebra o Outlook)"


def test_x_unsent_abre_como_rascunho():
    """Sem X-Unsent o Outlook abre como mensagem RECEBIDA (só dá p/ Encaminhar → 'FW:')."""
    assert email.message_from_bytes(_raw()).get("X-Unsent") == "1"


def test_html_decodifica_limpo():
    """Simula o cliente: decodificar tem que devolver HTML, não '=3D' cru."""
    msg = email.message_from_bytes(_raw(), policy=policy.default)
    html = next(p.get_content() for p in msg.walk() if p.get_content_type() == "text/html")
    assert "=3D" not in html, "quoted-printable não decodificou"
    assert "Itaú BBA | Equity Research" in html          # banner
    assert "Sector Headlines" in html


def test_imagens_como_cid_nunca_data_uri():
    """O Outlook BLOQUEIA data:image (a logo virava quadrado quebrado) — tem que ser cid:."""
    msg = email.message_from_bytes(_raw(), policy=policy.default)
    html = next(p.get_content() for p in msg.walk() if p.get_content_type() == "text/html")
    assert "data:image" not in html, "imagem em base64 inline — quebra no Outlook"
    assert "cid:img1" in html, "logo deveria estar embutida como cid:"
    cids = [p.get("Content-ID") for p in msg.walk() if p.get("Content-ID")]
    assert "<img1>" in cids, "faltou a parte inline da imagem"


def test_linhas_dentro_do_limite_mime():
    """RFC 5322: linha > 998 bytes pode ser cortada/rejeitada no caminho."""
    assert max(len(l) for l in _raw().split(b"\r\n")) <= 998


def test_anexo_docx_presente():
    msg = email.message_from_bytes(_raw())
    nomes = [p.get_filename() for p in msg.walk() if p.get_filename()]
    assert "clipping_20260804.docx" in nomes


# ── Largura: uma imagem grande estourava o e-mail INTEIRO (2026-08-10) ────────────
#
# `max-width:100%` NÃO EXISTE no motor do Outlook/Word. Sem `width=` na tag, o print de
# tabela/mapa de 903px esticava a tabela de 640px p/ ~904px (medido: 9,42in de largura numa
# página de 5,91in úteis) e TODO o texto do e-mail saía CORTADO à direita, em toda página.

def _png(w: int, h: int) -> bytes:
    """PNG mínimo com o cabeçalho IHDR de w×h (é de lá que o tamanho é lido)."""
    import struct, zlib
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(b"\x00" * (3 * w + 1) * h, 1)) + chunk(b"IEND", b""))


def test_imagem_larga_ganha_width_limitado():
    from clipping.eml import _img_with_width, _IMG_MAX_W
    tag = _img_with_width('<img style="max-width:100%;height:auto" src="cid:img9">', _png(903, 948))
    assert f'width="{_IMG_MAX_W}"' in tag, "imagem larga tem que sair limitada à coluna"


def test_imagem_pequena_mantem_tamanho_real():
    from clipping.eml import _img_with_width
    tag = _img_with_width('<img src="cid:img9">', _png(570, 191))
    assert 'width="570"' in tag


def test_nao_duplica_width_existente():
    """A logo do banner já vem com width — não pode ganhar um segundo."""
    from clipping.eml import _img_with_width
    tag = _img_with_width('<img src="cid:img1" width="104" height="55">', _png(154, 81))
    assert tag.count("width=") == 1 and 'width="104"' in tag


def test_imagem_ilegivel_nao_quebra():
    from clipping.eml import _img_with_width
    tag = '<img src="cid:img9">'
    assert _img_with_width(tag, b"nao sou imagem") == tag


def test_pagina_no_formato_que_o_word_entende():
    """Imprimir/encaminhar pagina no motor do Word. `@page` sozinho ele IGNORA (margem
    seguia 1,18in → coluna de 6,67in passava da margem); com `@page WordSection1` +
    `div.WordSection1` a margem vira 0,6in (7,07in úteis) e cabe."""
    msg = email.message_from_bytes(_raw(), policy=policy.default)
    html = next(p.get_content() for p in msg.walk() if p.get_content_type() == "text/html")
    assert "@page WordSection1" in html
    assert 'div.WordSection1{page:WordSection1;}' in html
    assert '<div class="WordSection1">' in html


def test_toda_imagem_do_email_tem_width():
    """Contrato de saída: nenhuma imagem do .eml pode ir sem largura declarada."""
    import re
    msg = email.message_from_bytes(_raw(), policy=policy.default)
    html = next(p.get_content() for p in msg.walk() if p.get_content_type() == "text/html")
    tags = re.findall(r"<img[^>]*>", html)
    assert tags, "esperava ao menos a logo"
    assert all(re.search(r'\bwidth="\d+"', t) for t in tags), tags
