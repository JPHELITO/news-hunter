"""
Testes do filtro de ingestão (hunter/filter.py).

Foco: fronteira de palavra (não casar keyword dentro de outra palavra) e
regra de fontes gerais (exigem keyword no título).

Rodar: python -m pytest tests/test_filter.py -v
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from hunter.fetcher import RawArticle
from hunter.filter import filter_articles, _strong_pattern, _ambiguous_pattern

_NOW = datetime.now(timezone.utc)


def _mk(title, snippet="", source="Mining.com"):
    return RawArticle(
        url=f"http://x/{hash((title, snippet, source))}",
        domain="x.com", source_name=source, title=title, snippet=snippet,
        published_at=_NOW, found_at=_NOW, needs_filter=True,
    )


def _passes(title, snippet="", source="Mining.com"):
    return len(filter_articles([_mk(title, snippet, source)])) == 1


class TestWordBoundary:
    def test_aura_not_in_restaurante(self):
        """'aura' não deve casar dentro de 'restaurante'."""
        assert not _passes("Assistir aos jogos da Copa em bares e restaurantes",
                           "futebol", "Folha de S.Paulo")

    def test_app_not_in_application(self):
        assert not _passes("New banking application launches today", "fintech app", "Exame")

    def test_strong_pattern_excludes_substring(self):
        pat = _strong_pattern()
        assert not pat.search("agerdaus")          # 'gerdau' dentro de outra palavra não casa
        assert pat.search("gerdau anuncia usina")  # 'gerdau' isolado casa

    def test_ambiguous_requires_capitalized(self):
        """Ambíguas (vale, app, aura) só casam capitalizadas — evita 'vale a pena'/'app'."""
        pat = _ambiguous_pattern()
        assert not pat.search("se vale a pena investir")  # 'vale' minúsculo NÃO casa
        assert pat.search("Vale bate recorde")            # 'Vale' (Title) casa
        assert pat.search("acao da VALE sobe")            # 'VALE' (caps) casa
        assert not pat.search("baixe o app no celular")   # 'app' minúsculo NÃO casa
        assert pat.search("APP eleva precos")             # 'APP' (caps) casa


class TestValeIdiom:
    """'Vale' + infinitivo = expressão ('Vale lembrar/a pena'), não a empresa."""

    def test_vale_a_pena_blocked(self):
        assert not _passes("Dicas de investimento", "Vale a pena diversificar a carteira", "Exame")

    def test_vale_lembrar_blocked(self):
        assert not _passes("Renda fixa em foco", "Vale lembrar que o CDI subiu este mes", "G1 Economia")

    def test_vale_company_3rd_person_passes(self):
        """3ª pessoa = empresa: 'Vale registra/anuncia' continua passando."""
        assert _passes("Vale registra lucro recorde no trimestre", "resultado", "Money Times")
        assert _passes("Mercado de olho", "Vale anuncia novo programa de dividendos", "Exame")


class TestLegitimateStillPass:
    def test_vale_mining(self):
        assert _passes("Vale eleva producao de minerio de ferro", "recorde", "Mining.com")

    def test_gerdau_steel(self):
        assert _passes("Gerdau anuncia expansao de capacidade de aco", "nova usina", "Money Times")

    def test_hrc_demand(self):
        assert _passes("China HRC prices rise on steel demand", "asia", "SMM")

    def test_suzano_pulp(self):
        assert _passes("Suzano eleva preco da celulose", "BHKP", "Valor Econômico")


class TestBroadSourcesTitleOrSnippet:
    """Two-tier: grandes jornais agora aceitam keyword no título OU resumo. O ruído de
    ambíguas é cortado pelo casing case-sensitive (não mais exigindo keyword no título)."""

    def test_broad_source_strong_keyword_in_snippet_passes(self):
        """'Gerdau' (forte) só no resumo → agora PASSA (antes era bloqueado)."""
        assert _passes("Inauguracao reune autoridades locais", "Gerdau eleva producao de aco", "UOL Economia")

    def test_broad_source_ambiguous_lowercase_in_snippet_blocked(self):
        """'vale' minúsculo no resumo ('vale a pena') → NÃO conta → bloqueado."""
        assert not _passes("Dicas de investimento para 2026", "veja se vale a pena aplicar", "G1 Economia")

    def test_broad_source_Vale_capitalized_passes(self):
        assert _passes("Vale bate recorde de minerio", "trimestre", "Metrópoles")

    def test_specialized_source_snippet_match_ok(self):
        """Fonte especializada (Mining.com) pode casar só no snippet."""
        assert _passes("Quarterly results beat expectations", "iron ore output up", "Mining.com")


class TestThematicSourcesAcceptAll:
    """Fontes setoriais (needs_filter=False) aceitam tudo, sem exigir keyword."""

    def _mk_thematic(self, title, source="Portal Celulose"):
        return RawArticle(
            url=f"http://x/{hash((title, source))}", domain="x.com",
            source_name=source, title=title, snippet="",
            published_at=_NOW, found_at=_NOW, needs_filter=False,
        )

    def test_thematic_accepts_without_keyword(self):
        # 'Nova maquina inaugurada' não tem keyword nossa → mas é fonte setorial
        assert len(filter_articles([self._mk_thematic("Nova maquina inaugurada no interior paulista")])) == 1

    def test_thematic_still_blocks_blocklist(self):
        assert len(filter_articles([self._mk_thematic("Futebol: final movimenta a cidade neste fim de semana")])) == 0

    def test_keyword_source_still_requires_keyword(self):
        # fonte needs_filter=True (default do _mk) sem keyword → bloqueada
        assert not _passes("Nova maquina inaugurada no interior paulista", "", "Folha de S.Paulo")


class TestBlocklist:
    def test_crypto_title_blocked(self):
        assert not _passes("Bitcoin sobe e Vale acompanha rali", "minerio", "InfoMoney")

    def test_soccer_title_blocked(self):
        assert not _passes("Futebol: final da Copa movimenta mercado de aço", "steel", "G1")

    # ── Word-boundary (não substring): não pode derrubar palavra que só CONTÉM o termo ──
    def test_define_nao_bloqueia_vale(self):
        # 'define' contém 'defi' (cripto) — não pode bloquear manchete real da Vale
        assert _passes("Vale define plano de investimento em mina", "trimestre", "InfoMoney")

    def test_wheaton_nao_bloqueia(self):
        # 'Wheaton' (mineradora) contém 'wheat' — não pode bloquear
        assert _passes("CSN e Wheaton avançam em acordo de minério", "negócio", "Exame")

    def test_blocklist_real_ainda_bloqueia(self):
        assert not _passes("Vale e a safra de soja batem recorde", "agro", "G1")
        assert not _passes("Tokenização de ativos avança no mercado", "token", "InfoMoney")


class TestFalseFriendsBlocked:
    """Regressão 2026-07-28: keywords ambíguas que deixavam entrar futebol/fofoca/novela."""

    # 'cartão' (paperboard) casava cartão de futebol e cartão de crédito
    def test_cartao_futebol_blocked(self):
        assert not _passes("CBF recebe alerta de manipulação de apostas em cartão de Acosta",
                           "Fluminense", "CNN Brasil")

    def test_cartao_credito_blocked(self):
        assert not _passes("Férias em família nos EUA exigem mais do que um roteiro",
                           "leve mais de um cartão de crédito", "CNN Brasil")

    def test_cartao_embalagem_ainda_passa(self):
        """A forma inequívoca do setor ('cartão para embalagem') continua entrando."""
        assert _passes("Klabin eleva preço do cartão para embalagem", "papelcartão", "Valor Econômico")

    # 'Mariana' (desastre) casava o nome próprio (pessoa)
    def test_mariana_nome_proprio_blocked(self):
        assert not _passes("Filha de Ana Maria Braga anuncia fim de casamento",
                           "Mariana comenta a relação", "CNN Brasil")

    def test_barragem_de_mariana_ainda_passa(self):
        assert _passes("Vale é cobrada por reparação da barragem de Mariana", "rejeitos", "G1")

    # 'Vale Tudo' (novela) / 'Vale tudo para…' (expressão) casava a empresa Vale
    def test_vale_tudo_novela_blocked(self):
        assert not _passes("Manuela Dias explica por que decidiu manter Odete viva em “Vale Tudo”",
                           "novela", "CNN Brasil")

    def test_vale_tudo_expressao_blocked(self):
        assert not _passes("‘Vale tudo para salvar a democracia’, diz político em evento",
                           "política", "InfoMoney")

    def test_vale_silicio_blocked(self):
        """'Vale do Silício' (Silicon Valley) não é a mineradora."""
        assert not _passes("Vale do Silício racha no debate sobre regulação de IA", "tecnologia", "InfoMoney")

    def test_vale_do_rio_doce_ainda_passa(self):
        """'Vale do Rio Doce' (nome histórico da empresa) NÃO pode ser bloqueado."""
        assert _passes("Companhia Vale do Rio Doce eleva produção de minério", "trimestre", "Estadão")

    def test_vale_empresa_apos_dois_pontos_passa(self):
        """'Vale: tudo sobre a mineradora' — o ':' quebra o \\s+ do idioma → segue passando."""
        assert _passes("Vale: tudo sobre a mineradora nesta temporada de resultados",
                       "minério de ferro", "Money Times")


class TestPlattsPassThrough:
    """Platts é fonte curada: todas as notícias passam, exceto 'rationale' no título."""

    def test_platts_passes_without_keyword(self):
        """Notícia Platts sem nenhuma keyword nossa ainda passa (pass_through)."""
        assert _passes("Market overview for the week ahead", "", "S&P Platts")

    def test_platts_passes_short_title(self):
        """Título curto que o page-index normalmente cortaria — Platts passa."""
        assert _passes("China HRC up", "", "S&P Platts")

    def test_platts_rationale_in_title_blocked(self):
        assert not _passes("Iron ore rationale: IODEX edges lower", "assessment", "S&P Platts")

    def test_platts_pricing_rational_typo_blocked(self):
        # grafia errada da fonte ("Rational" sem o "e") — ver _EXCLUDE_CONTENT_TYPES
        assert not _passes("Pricing Rational: NBSK CIF China", "market", "S&P Platts")

    def test_platts_rationale_case_insensitive(self):
        assert not _passes("HRC China Rationale", "", "S&P Platts")

    def test_platts_normal_news_still_passes(self):
        assert _passes("Iron ore prices rise on China demand", "", "S&P Platts")

    def test_non_platts_still_keyword_filtered(self):
        """A regra pass_through NÃO vaza para outras fontes."""
        assert not _passes("Market overview for the week ahead", "", "Mining.com")


# ─────────────────────────────────────────────────────────────────────────────
# Blocklist de domínios de ruído (notícia geral) — corte na ingestão
# ─────────────────────────────────────────────────────────────────────────────
from hunter.fetcher import _is_blocked_domain


class TestBlockedDomains:
    def test_el_financiero_blocked(self):
        assert _is_blocked_domain("elfinanciero.com.mx")
        assert _is_blocked_domain("www.elfinanciero.com.mx")

    def test_legit_domains_not_blocked(self):
        assert not _is_blocked_domain("mining.com")
        assert not _is_blocked_domain("valor.globo.com")
        assert not _is_blocked_domain("")

    def test_no_spoofing_bypass(self):
        """Domínio que só CONTÉM o bloqueado não pode ser confundido."""
        assert not _is_blocked_domain("elfinanciero.com.mx.evil.com")


# ─────────────────────────────────────────────────────────────────────────────
# Linha fina do Valor — regressão de 2026-09-30
# ─────────────────────────────────────────────────────────────────────────────
import feedparser

from hunter.fetcher import _entry_snippet

# Item copiado do feed real (pox.globo.com/rss/valor/empresas, 30/09/2026). Matéria de
# ASSINANTE: a <description> é só o aviso de paywall; a linha fina vem em <atom:subtitle>.
_VALOR_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom" xmlns:media="http://search.yahoo.com/mrss/">
<channel><title>valor &gt; Empresas</title>
<item> <title>Frete e preço baixo levam a corte de produção em mineradoras</title>
 <atom:subtitle>CSN Mineração reduziu produção do minério, aumentando lista de empresas que já vinham reagindo ao cenário; papéis tiveram a segunda maior queda do Ibovespa</atom:subtitle>
 <link>https://valor.globo.com/empresas/noticia/2026/09/30/frete-e-preco-baixo-levam-a-corte-de-producao-em-mineradoras.ghtml</link>
 <description> <![CDATA[ <img src="https://s2-valor.glbimg.com/x/arte30emp-201-minerio-b1.jpg" /><br /> ]]>    Matéria exclusiva para assinantes. Para ter acesso completo, acesse o link da matéria e faça o seu cadastro.  </description>
 <pubDate>Wed, 30 Sep 2026 08:00:00 -0000</pubDate>
</item>
<item> <title>Tenda aprova recompra de até 5 milhões de ações</title>
 <atom:subtitle>Programa terá a duração de até 12 meses</atom:subtitle>
 <link>https://valor.globo.com/empresas/noticia/2026/09/30/tenda.ghtml</link>
 <description>A Tenda aprovou um programa de recompra de até 5 milhões de ações ordinárias.</description>
 <pubDate>Wed, 30 Sep 2026 11:24:38 -0000</pubDate>
</item>
</channel></rss>"""


class TestValorLinhaFina:
    """O Valor manda a linha fina em <atom:subtitle> e, na matéria de assinante, só o aviso
    de paywall na <description>. Lendo só a description, o filtro via apenas o título."""

    def _entries(self):
        return feedparser.parse(_VALOR_RSS).entries

    def test_snippet_da_assinante_traz_a_linha_fina(self):
        snip = _entry_snippet(self._entries()[0])
        assert snip.startswith("CSN Mineração reduziu produção do minério")

    def test_aviso_de_paywall_sai_do_snippet(self):
        snip = _entry_snippet(self._entries()[0])
        assert "assinantes" not in snip.lower()
        assert "cadastro" not in snip.lower()

    def test_materia_aberta_mantem_a_descricao(self):
        snip = _entry_snippet(self._entries()[1])
        assert snip == ("Programa terá a duração de até 12 meses "
                        "A Tenda aprovou um programa de recompra de até 5 milhões de ações ordinárias.")

    def test_sem_subtitulo_nada_muda(self):
        """As outras fontes não mandam subtítulo: o snippet segue sendo a description."""
        e = feedparser.parse(_VALOR_RSS.replace("atom:subtitle", "atom:ignorar")).entries[1]
        assert _entry_snippet(e) == "A Tenda aprovou um programa de recompra de até 5 milhões de ações ordinárias."

    def test_linha_fina_nao_duplica_quando_ja_esta_na_descricao(self):
        rss = _VALOR_RSS.replace("A Tenda aprovou um programa",
                                 "Programa terá a duração de até 12 meses. A Tenda aprovou um programa")
        snip = _entry_snippet(feedparser.parse(rss).entries[1])
        assert snip.count("Programa terá a duração") == 1

    def test_linha_fina_sobrevive_ao_corte_de_400(self):
        rss = _VALOR_RSS.replace("A Tenda aprovou", "A Tenda aprovou " + "texto longo " * 60)
        snip = _entry_snippet(feedparser.parse(rss).entries[1])
        assert len(snip) == 400 and snip.startswith("Programa terá a duração")

    def test_a_materia_da_csn_mineracao_passa_no_filtro(self):
        e = self._entries()[0]
        art = _mk(e.title, _entry_snippet(e), "Valor Econômico")
        got = filter_articles([art])
        assert len(got) == 1
        assert {"csn", "mineração"} <= set(got[0]["matched_keywords"])

    def test_mineradoras_no_titulo_basta(self):
        """Mesmo sem linha fina (8 de 100 assinantes do feed não têm), a manchete entra."""
        assert _passes("Frete e preço baixo levam a corte de produção em mineradoras", "", "Valor Econômico")
        assert _passes("Mineradora canadense compra projeto de minério no Pará", "", "Folha de S.Paulo")


# ─────────────────────────────────────────────────────────────────────────────
# Registro de fontes — trava as decisões de filtro que foram MEDIDAS
# ─────────────────────────────────────────────────────────────────────────────
from hunter.sources import SOURCES


class TestSourceRegistry:
    def test_brasil_mineral_registrada_como_tematica(self):
        """Brasil Mineral é setorial (mineração) → filter=False.

        Medido no feed ao vivo (2026-08-27): a keyword derrubava 7 de 26, e logo as
        boas (Lundin/cobre, AngloGold/ouro, tungstênio) — "cobre"/"ouro" isolados não
        estão em ALL_KEYWORDS. Mesmo diagnóstico dos feeds de commodity do Mining.com.
        """
        bm = [s for s in SOURCES if s["label"] == "Brasil Mineral"]
        assert bm, "Brasil Mineral sumiu de sources.py"
        assert all(s.get("filter") is False for s in bm)
