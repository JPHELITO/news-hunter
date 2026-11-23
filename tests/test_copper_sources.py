"""Fontes das 4 de cobre (2026-11-23): TMX Money (comunicados), Reporte Minero (sitemap mensal filtrado pelo
endereço), Rumbo Minero por etiqueta, Energiminas/Portal Minero sem "antofagasta". Itens REAIS de 23/11/2026."""
from datetime import datetime, timezone

from hunter import tmx_news as T
from hunter import sitemap_scrapers as S
from hunter.fetcher import RawArticle
from hunter.filter import _matches, SOURCE_FILTER_RULES
from hunter.news_take_classifier import _ALWAYS_INCLUDE_SOURCES
from hunter.sources import SOURCES
from hunter.digest import TIER1
from hunter.article_body import _TMX_URL_RE

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc)


def _tmx(headline, dt="2026-11-12T08:00:00-05:00", newsid="8617132478744317", source="Business Wire via QuoteMedia"):
    return {"headline": headline, "datetime": dt, "newsid": newsid, "source": source,
            "summary": "Capstone Copper Corp.   (&#x201C;Capstone&#x201D; or the &#x201C;Company&#x201D;) (TSX:CS)"}


class TestTMX:
    def test_comunicado_da_empresa_entra(self):
        a = T.item_to_article("CS", _tmx("Capstone Copper Provides Update on Operations"), NOW)
        assert a is not None and a.source_name == "TMX" and a.needs_filter is False
        assert a.url == "https://money.tmx.com/en/quote/CS/news/8617132478744317"
        assert a.published_at == datetime(2026, 11, 12, 13, 0, tzinfo=timezone.utc)
        assert "“Capstone”" in a.snippet and "(Business Wire)" in a.snippet   # entidades HTML resolvidas

    def test_terceiro_que_cita_a_empresa_entra_propaganda_sai(self):
        assert T.item_to_article("HBM", _tmx("Pecoy Copper Announces C$49.8 Million Strategic Financing Led by Hudbay Minerals",
                                             dt="2026-11-12T06:00:00-05:00"), NOW)
        assert T.item_to_article("ERO", _tmx("Five Metals Producers Trade Higher as Gold, Silver and Copper Futures Pull Back"), NOW) is None

    def test_manchete_tem_de_citar_a_empresa_do_ticker(self):
        # a TMX marca HBM num comunicado de terras raras: não é da Hudbay
        assert T.item_to_article("HBM", _tmx("U.S. Import Reliance for Rare Earth Elements Hits Record"), NOW) is None
        assert T.item_to_article("ERO", _tmx("Ero to Host 2026 Capital Markets Day"), NOW)

    def test_janela_e_ticker(self):
        assert T.item_to_article("CS", _tmx("Capstone Copper Reports Third Quarter 2026 Results", dt="2026-10-29T16:16:00-04:00"), NOW) is None
        assert T.item_to_article("XYZ", _tmx("Capstone Copper Provides Update on Operations"), NOW) is None
        assert T.item_to_article("CS", {**_tmx("Capstone Copper Provides Update on Operations"), "newsid": ""}, NOW) is None

    def test_mesmo_comunicado_por_dois_newswires_vira_um(self, monkeypatch):
        dup = [_tmx("Lundin Mining Announces Declaration of Regular Dividend", newsid="1", dt="2026-11-12T17:30:00-05:00"),
               _tmx("Lundin Mining Announces Declaration of Regular Dividend", newsid="2", dt="2026-11-12T17:30:00-05:00",
                    source="PR Newswire via QuoteMedia")]
        monkeypatch.setattr(T, "_fetch_symbol", lambda s: dup if s == "LUN" else [])
        monkeypatch.setattr(T, "datetime", type("D", (), {"now": staticmethod(lambda tz=None: NOW),
                                                           "fromisoformat": datetime.fromisoformat}))
        out = T.collect_tmx_news()
        assert len(out) == 1 and out[0].url.endswith("/news/1")

    def test_falha_nao_derruba(self, monkeypatch):
        def boom(_s):
            raise RuntimeError("TMX fora")
        monkeypatch.setattr(T, "_fetch_symbol", boom)
        assert T.collect_tmx_news() == []

    def test_fonte_curada_em_todo_o_caminho(self):
        assert SOURCE_FILTER_RULES["TMX"]["pass_through"] is True
        assert "tmx" in _ALWAYS_INCLUDE_SOURCES and "tmx" in TIER1
        assert _TMX_URL_RE.match("https://money.tmx.com/en/quote/ERO/news/7824965507623788").group(1) == "7824965507623788"


SITEMAP = b"""<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<url><loc>https://www.reporteminero.cl/noticia/noticias/2026/11/capstone-copper-ajusta-produccion-2026-pinto-valley</loc><lastmod>2026-11-13T09:01:00-03:00</lastmod></url>
<url><loc>https://www.reporteminero.cl/noticia/responsabilidad-social/2026/11/capstone-copper-mantos-blancos-senal-vial-baquedano-seguridad</loc><lastmod>2026-11-11T16:34:00-03:00</lastmod></url>
<url><loc>https://www.reporteminero.cl/noticia/noticias/2026/11/visita-bill-gates-chuquicamata-reaviva-debate-capital-privado-codelco</loc><lastmod>2026-11-13T09:27:00-03:00</lastmod></url>
<url><loc>https://www.reporteminero.cl/noticia/noticias/2026/11/lundin-mining-resultados-q3-2026-cobre-caserones-chile</loc><lastmod>2026-11-05T09:07:00-03:00</lastmod></url>
</urlset>"""


class TestReporteMinero:
    SRC = S.MONTHLY_SLUG_SOURCES[0]

    def test_so_o_endereco_que_cita_e_na_janela(self):
        hits = S.parse_monthly_sitemap(SITEMAP, self.SRC, NOW)
        assert [u.rsplit("/", 1)[-1] for u, _ in hits] == ["capstone-copper-ajusta-produccion-2026-pinto-valley"]

    def test_mes_anterior_so_nos_primeiros_dias(self):
        assert S._months_to_read(datetime(2026, 11, 23, tzinfo=timezone.utc)) == [(2026, 11)]
        assert S._months_to_read(datetime(2026, 12, 2, tzinfo=timezone.utc)) == [(2026, 12), (2026, 11)]
        assert S._months_to_read(datetime(2027, 1, 1, tzinfo=timezone.utc)) == [(2027, 1), (2026, 12)]

    def test_titulo_do_og_sem_o_nome_do_site(self, monkeypatch):
        page = ('<meta property="og:title" content="Capstone Copper ajusta gu&iacute;a 2026 a 195.000 toneladas por '
                'retraso en Pinto Valley - Reporte Minero | El portal de miner&iacute;a en Chile">'
                '<meta property="og:description" content="Capstone Copper ajust&oacute; su proyecci&oacute;n.">').encode()
        calls = []
        def fake_get(url):
            calls.append(url)
            return (200, SITEMAP) if url.endswith(".xml") else (200, page)
        monkeypatch.setattr(S, "_http_get", fake_get)
        S._PAGE_TITLE_CACHE.clear()
        out = S._scrape_monthly(self.SRC, NOW)
        assert len(out) == 1 and out[0].source_name == "Reporte Minero" and out[0].needs_filter is False
        assert out[0].title == "Capstone Copper ajusta guía 2026 a 195.000 toneladas por retraso en Pinto Valley"
        assert out[0].snippet == "Capstone Copper ajustó su proyección."
        S._scrape_monthly(self.SRC, NOW)                      # 2ª volta: a página não é baixada de novo
        assert sum(1 for u in calls if not u.endswith(".xml")) == 1


class TestImprensa:
    def test_rumbo_minero_por_etiqueta_nunca_pela_busca(self):
        rumbo = [s for s in SOURCES if s["label"] == "Rumbo Minero"]
        assert len(rumbo) == 4 and all("/tag/" in s["url"] and "?s=" not in s["url"] and s["filter"] is False for s in rumbo)

    def _art(self, src, title):
        return RawArticle(url="https://x/1", domain="x", source_name=src, title=title, snippet="",
                          published_at=NOW, found_at=NOW, needs_filter=True)

    def test_antofagasta_regiao_nao_passa_nas_duas(self):
        t = "Cartera de inversión minera chilena alcanza los US$111.149 millones, Antofagasta lidera"
        assert _matches(self._art("Energiminas", t)) == []
        assert _matches(self._art("Portal Minero", t)) == []
        assert _matches(self._art("Valor Econômico", "Antofagasta eleva produção de cobre")) == ["antofagasta"]

    def test_nossos_nomes_passam(self):
        assert _matches(self._art("Energiminas", "Encuentro organizado por Hudbay analizó riesgos")) == ["hudbay"]
        assert _matches(self._art("Portal Minero", "Capstone Copper inicia expansión de Mantoverde"))
