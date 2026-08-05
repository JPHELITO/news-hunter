"""Corte do bloco "Related articles / Leia também" no corpo do clipping.

INCIDENTE 2026-08-05 — matéria da Platts saiu pela METADE no Word, sem erro e
sem padrão. Causa: o regex antigo casava a frase-gatilho em QUALQUER ponto de um
parágrafo e apagava TODO o resto do artigo. Prosa normal decapitava a matéria:

  • "…the market is also readjusting back to display fundamentals" → "Also read"
    (o regex não tinha fronteira de palavra no fim)
  • "…See also the discussion of 'Forward-Looking Statements' below."

Medido em 397 artigos reais de Platts/Fastmarkets: 2 (0,5%) perdiam ~65% do corpo.

Rodar:  python -m pytest tests/test_clipping_related_strip.py -v
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from clipping.html_utils import _strip_related_html, plain_text
from clipping.reader import _sanitize_ok

# Corpo com folga suficiente p/ o corte de rodapé não bater na trava de proporção.
CORPO = (
    "<p>Platts assessed IODEX at $93.20/dmt CFR North China on Aug. 4, down $0.50/dmt "
    "from $93.70/dmt Aug. 3, with more market participants expecting fundamentals to "
    "adjust back in view of recent derivatives volatility, said sources.</p>\n"
    "<p>In the afternoon session, BHP sold a 90,000 mt cargo of 60.7% Fe Newman High "
    "Grade Fines for loading between Sept. 1-10 at $92.60/dmt, market sources said.</p>"
)
SENTINELA = "<p>PARAGRAFO QUE NAO PODE SUMIR DO CLIPPING</p>"


class TestRotuloDeVerdadeEhCortado:
    """Bloco de links relacionados continua sendo removido."""

    def test_h3_related_articles(self):
        out = _strip_related_html(f"{CORPO}\n<h3>Related articles</h3>\n<p>Link A</p>\n<p>Link B</p>")
        assert "Related articles" not in out
        assert "Link A" not in out and "Link B" not in out
        assert "IODEX" in out and "Newman High" in out

    def test_p_com_dois_pontos(self):
        out = _strip_related_html(f"{CORPO}\n<p>Also read:</p>\n<p>Link A</p>")
        assert "Also read" not in out and "Link A" not in out
        assert "IODEX" in out

    def test_rotulo_em_portugues(self):
        out = _strip_related_html(f"{CORPO}\n<p>Leia também</p>\n<p>Link A</p>")
        assert "Leia também" not in out and "Link A" not in out
        assert "IODEX" in out

    def test_maiusculas_e_travessao(self):
        out = _strip_related_html(f"{CORPO}\n<h2>RELATED NEWS —</h2>\n<p>Link A</p>")
        assert "RELATED NEWS" not in out and "Link A" not in out


class TestProsaNuncaCorta:
    """As frases REAIS que causaram o incidente — e vizinhas do mesmo tipo."""

    CASOS = [
        # o gatilho exato do artigo "Asian seaborne iron ore prices soften…" (Platts)
        "<p>&#x201C;Previously, the market saw some narrowing in discounts as they were "
        "still adjusting to the lower swaps, but now, as derivatives are settling in, the "
        "market is also readjusting back to display fundamentals,&#x201D; said an "
        "international trader source.</p>",
        # o gatilho do "West Fraser announces second quarter 2026 results" (Fastmarkets)
        "<p>Risk and uncertainty disclosures are included in our 2025 Annual MD&amp;A, as "
        "updated in our Q2-26 MD&amp;A. See also the discussion of &quot;Forward-Looking "
        "Statements&quot; below.</p>",
        "<p>The mill still has one more story to tell about output this year.</p>",
        "<p>Analysts flagged related news coverage of the antidumping case in Brazil.</p>",
        "<p>Buyers were also ready to bid at these levels, traders said.</p>",
        "<p>The plant is also reading demand signals from the domestic market.</p>",
    ]

    def test_prosa_preserva_o_resto_do_artigo(self):
        for prosa in self.CASOS:
            html = f"{CORPO}\n{prosa}\n{SENTINELA}"
            out = _strip_related_html(html)
            assert "PARAGRAFO QUE NAO PODE SUMIR" in out, f"cortou em: {prosa[:80]}"
            assert len(out) == len(html.rstrip()), f"encolheu em: {prosa[:80]}"


class TestTravasDeSeguranca:
    def test_nao_corta_se_levaria_a_maior_parte(self):
        """Falso-positivo que apagaria o grosso do texto → mantém tudo."""
        html = ("<p>Curto.</p>\n<p>Related articles</p>\n"
                + "\n".join(f"<p>Parágrafo longo de conteúdo número {i} "
                            f"com bastante texto real dentro dele.</p>" for i in range(8)))
        out = _strip_related_html(html)
        assert "número 7" in out                      # nada foi perdido

    def test_bloco_aninhado_nao_conta(self):
        """<p> dentro de <blockquote> não é bloco de topo — não corta ali."""
        html = f"{CORPO}\n<blockquote><p>See also</p></blockquote>\n{SENTINELA}"
        assert "PARAGRAFO QUE NAO PODE SUMIR" in _strip_related_html(html)

    def test_vazio_e_sem_rotulo(self):
        assert _strip_related_html("") == ""
        assert _strip_related_html(CORPO) == CORPO.rstrip()


class TestGuardaDeSanitizacao:
    """_sanitize_ok: corpo de API é puro → perda grande = bug do sanitizador."""

    RAW = "<p>" + ("Texto real do artigo com bastante conteúdo. " * 30) + "</p>"

    def test_perda_grande_reprovada(self):
        metade = "<p>" + ("Texto real do artigo com bastante conteúdo. " * 8) + "</p>"
        assert _sanitize_ok(self.RAW, metade, "http://x", "Platts") is False

    def test_saida_integra_aprovada(self):
        assert _sanitize_ok(self.RAW, self.RAW, "http://x", "Platts") is True

    def test_corpo_curto_nao_dispara(self):
        assert _sanitize_ok("<p>bem curto</p>", "", "http://x", "Platts") is True

    def test_plain_text_resolve_entidades(self):
        assert plain_text("<p>S&amp;P&nbsp;Global</p>").startswith("S&P")
