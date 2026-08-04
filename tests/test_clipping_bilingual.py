"""Config bilíngue do clipping: Portal Celulose (PT) deve gerar Original + Free
Translation, como o Valor (pedido do usuário 2026-08-03).

Rodar: python -m pytest tests/test_clipping_bilingual.py -v
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from clipping.build import _BILINGUAL_DOMAINS, _DOMAIN_LANG
from clipping.generate import _LANG


def test_portal_celulose_bilingue():
    assert "portalcelulose.com.br" in _BILINGUAL_DOMAINS
    assert _DOMAIN_LANG["portalcelulose.com.br"] == "Portuguese"
    assert _LANG["portalcelulose.com.br"] == "Portuguese"


def test_existentes_seguem_bilingues():
    # regressão: não removi os que já eram bilíngues
    for d in ("valor.globo.com", "www.estadao.com.br", "www.elfinanciero.com.mx"):
        assert d in _BILINGUAL_DOMAINS


def test_langs_consistentes_entre_build_e_generate():
    # invariante: TODO domínio bilíngue tem idioma nos DOIS mapas (build._DOMAIN_LANG
    # e generate._LANG) — evita que a tradução caia no default por esquecer um mapa.
    for d in _BILINGUAL_DOMAINS:
        assert d in _DOMAIN_LANG, f"{d} falta em build._DOMAIN_LANG"
        assert d in _LANG, f"{d} falta em generate._LANG"


def test_free_translation_link_interno_e_links_azuis():
    """Bilíngue: a headline linka p/ a Free Translation (bookmark próprio) e os links
    internos/de publicação saem em AZUL (pedido do usuário 2026-08-03)."""
    import io
    import zipfile
    from datetime import date

    from clipping.build import ClippingItem, build_docx

    item = ClippingItem(
        url="https://portalcelulose.com.br/x", title="Título em português",
        source_name="Portal Celulose", body="<p>corpo em português</p>",
        matched_keywords=[], domain="portalcelulose.com.br", take="=", sector="PP",
        translated_title="Title in English", translated_body="<p>body in english</p>",
    )
    config = {"recent_publications": [{"name": "Relatório X", "sector": "PP",
                                       "link": "https://exemplo.com/r.pdf"}]}
    docx = build_docx([item], date(2026, 8, 3), config)
    xml = zipfile.ZipFile(io.BytesIO(docx)).read("word/document.xml").decode("utf-8")

    assert 'w:name="art0tr"' in xml       # bookmark da Free Translation
    assert 'w:anchor="art0tr"' in xml     # headline linka p/ a tradução
    assert 'w:anchor="art0"' in xml       # headline linka p/ o original
    assert 'w:val="0000FF"' in xml        # links em azul
    assert 'w:val="000000"' not in xml    # nenhum link preto sobrando


def test_traducao_prefere_ia_e_monta_corpo(monkeypatch):
    """A tradução usa a IA quando disponível e monta o corpo a partir dos parágrafos dela."""
    import clipping.build as B
    monkeypatch.setattr(B, "_translate_via_llm",
                        lambda title, paras, lang: ("Title in English", ["Paragraph one.", "Paragraph two."]))
    tt, tb = B._translate_to_english("Título PT", "<p>Parágrafo um.</p><p>Parágrafo dois.</p>", "Portuguese")
    assert tt == "Title in English"
    assert "<p>Paragraph one.</p>" in tb
    assert "<p>Paragraph two.</p>" in tb
