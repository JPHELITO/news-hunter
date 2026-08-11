"""Nome dos arquivos que saem do clipping: "NEWS - MMDDYYYY" (2026-08-11).

Convenção do usuário: o Word, o .eml, a prévia e o anexo dentro do e-mail usam o MESMO
nome, em mês-dia-ano. (Antes era clipping_AAAAMMDD.) O assunto do e-mail NÃO mudou — é o
que o cliente vê na caixa de entrada.

Rodar: python -m pytest tests/test_clipping_filename.py -v
"""
import email
import email.policy
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from clipping.build import ClippingItem, clipping_basename
from clipping.eml import build_eml_bytes
from clipping.generate import build_from_payload


def test_formato_do_nome():
    """O exemplo do usuário: 11/08/2026 → "NEWS - 08112026"."""
    assert clipping_basename(date(2026, 8, 11)) == "NEWS - 08112026"


def test_mes_e_dia_com_zero_a_esquerda():
    assert clipping_basename(date(2026, 1, 5)) == "NEWS - 01052026"
    assert clipping_basename(date(2026, 12, 31)) == "NEWS - 12312026"


def test_os_tres_arquivos_saem_com_o_mesmo_nome():
    """Word, e-mail e prévia — sem raspar nada (corpo colado no payload)."""
    payload = [{"url": "https://www.mining.com/x/", "title": "Iron ore slides",
                "source_name": "Mining.com", "take": "-", "sector": "SM", "pos": 0,
                "body": "<p>Prices fell as traders weighed demand.</p>"}]
    res = build_from_payload(payload, date(2026, 8, 11), fetch=False)
    assert res["docx_name"] == "NEWS - 08112026.docx"
    assert res["eml_name"] == "NEWS - 08112026.eml"
    assert res["html_name"] == "NEWS - 08112026.html"


def test_anexo_do_email_sem_nome_explicito():
    """Quem chama build_eml_bytes sem docx_name tem que cair na MESMA convenção."""
    it = ClippingItem(url="https://www.mining.com/x/", title="Iron ore slides",
                      source_name="Mining.com", body="<p>Prices fell.</p>",
                      matched_keywords=["iron ore"], domain="www.mining.com",
                      take="-", sector="SM")
    msg = email.message_from_bytes(
        build_eml_bytes([it], date(2026, 8, 11), docx_bytes=b"PK\x03\x04fake"))
    nomes = [p.get_filename() for p in msg.walk() if p.get_filename()]
    assert "NEWS - 08112026.docx" in nomes


def test_assunto_do_email_nao_mudou():
    """O que o CLIENTE vê continua o de sempre (decisão do usuário)."""
    it = ClippingItem(url="https://www.mining.com/x/", title="Iron ore slides",
                      source_name="Mining.com", body="<p>Prices fell.</p>",
                      matched_keywords=["iron ore"], domain="www.mining.com",
                      take="-", sector="SM")
    # policy.default decodifica o cabeçalho (o "Ú" viaja como =?utf-8?q?ITA=C3=9A?=)
    msg = email.message_from_bytes(build_eml_bytes([it], date(2026, 8, 11)),
                                   policy=email.policy.default)
    assert str(msg["Subject"]) == ("*** ITAÚ BBA Daily News: LatAm Steel & Mining, "
                                   "Pulp & Paper - 08/11/2026 ***")
