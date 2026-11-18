"""Sinal de paywall do Valor (clipping/valor_paywall.py) + o uso no keep-alive e no leitor.

Contexto (medido ao vivo em 18/11/2026, 15 medições): o Valor manda a matéria inteira e o script do
paywall a esconde depois (até 7 s). O sinal confiável é o CORTE (.paywall-hidden / corte antigo);
o cabeçalho "não logado" mentiu 1 vez em 9 com a sessão boa. A checagem antiga olhava "na hora" e
dizia "sessão viva" até SEM cookie.

Rodar: python -m pytest tests/test_valor_paywall.py -v
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from clipping import keepalive
from clipping.valor_paywall import CROPPED_JS, LOGGED_JS, _WAIT_JS, use_body, wait_cropped

ROOT = Path(__file__).parent.parent


class FakePage:
    """Página falsa. `cropped`: o paywall corta? `logged`: cabeçalho 'logado'?
    `links`: matérias da home. `per_link`: corte por matéria (sobrepõe `cropped`)."""

    def __init__(self, cropped=False, logged=True, links=None, per_link=None, wait_raises=False):
        self.cropped, self.logged = cropped, logged
        self.links = ["https://valor.globo.com/a/noticia/1.ghtml",
                      "https://valor.globo.com/b/noticia/2.ghtml"] if links is None else links
        self.per_link = per_link or {}
        self.wait_raises = wait_raises
        self.waited = []
        self.timeouts = []
        self.visited = []

    def _cut(self):
        if self.visited and self.visited[-1] in self.per_link:
            return self.per_link[self.visited[-1]]
        return self.cropped

    def wait_for_function(self, js, timeout=None):
        self.waited.append((js, timeout))
        if self.wait_raises or not (self._cut() or self.logged):
            raise TimeoutError("nada no prazo")

    def wait_for_timeout(self, ms):
        self.timeouts.append(ms)

    def goto(self, url, **kw):
        self.visited.append(url)

    def evaluate(self, js, *a):
        if "noticia" in js:
            return self.links
        if js == CROPPED_JS:
            return self._cut()
        if js == LOGGED_JS:
            return self.logged
        raise AssertionError("JS inesperado")


# ── wait_cropped ────────────────────────────────────────────────────────────────────────────
class TestWaitCropped:
    def test_sessao_boa_liberada(self):
        assert wait_cropped(FakePage(cropped=False, logged=True)) is False

    def test_paywall_cortou(self):
        assert wait_cropped(FakePage(cropped=True, logged=False)) is True

    def test_cabecalho_nao_logado_sem_corte_nao_e_corte(self):
        # medido: com a sessão boa o cabeçalho às vezes diz "não logado" e o texto fica inteiro
        pg = FakePage(cropped=False, logged=False)
        assert wait_cropped(pg) is False
        assert pg.waited, "tem de ESPERAR o paywall (não olhar a página na hora)"

    def test_espera_o_bastante_para_o_corte_mais_tardio(self):
        pg = FakePage()
        wait_cropped(pg)
        assert pg.waited[0][1] >= 8000          # corte mais tardio medido: 7 s
        assert pg.waited[0][0] == _WAIT_JS

    def test_parou_pelo_logado_confere_de_novo(self):
        pg = FakePage(cropped=False, logged=True)
        wait_cropped(pg)
        assert pg.timeouts and pg.timeouts[0] >= 500

    def test_pagina_quebrada_vira_none(self):
        class Bomba(FakePage):
            def evaluate(self, js, *a):
                raise RuntimeError("página fechou")
        assert wait_cropped(Bomba()) is None


class TestSinaisNoJS:
    def test_js_olha_os_cortes_medidos(self):
        for s in (".paywall-hidden", ".paywall.hide-all-content", ".wall.protected-content"):
            assert s in CROPPED_JS, s
        assert ".menu-login--logged" in LOGGED_JS

    def test_nao_usa_sinais_que_mentem(self):
        assert "mc-article-body.cropped" not in CROPPED_JS
        assert "not-logged" not in CROPPED_JS and "not-logged" not in _WAIT_JS


# ── use_body (leitor do clipping) ───────────────────────────────────────────────────────────
class TestUseBody:
    def test_liberada_usa(self):
        assert use_body(False, False) is True

    def test_corte_na_espera_nao_usa(self):
        assert use_body(False, True) is False

    def test_corte_antigo_do_dom_nao_usa(self):
        assert use_body(True, False) is False

    def test_nao_deu_para_ler_mantem_o_antigo(self):
        assert use_body(False, None) is True
        assert use_body(True, None) is False


# ── keep-alive ──────────────────────────────────────────────────────────────────────────────
class TestKeepaliveValor:
    def test_sessao_boa_viva(self):
        pg = FakePage(cropped=False)
        assert keepalive._valor_dead_reason(pg) is None
        assert len(pg.visited) == 2

    def test_duas_cortadas_mata(self):
        r = keepalive._valor_dead_reason(FakePage(cropped=True, logged=False))
        assert r and "CORTADAS" in r

    def test_so_uma_cortada_nao_mata(self):
        links = ["https://valor.globo.com/a/noticia/1.ghtml", "https://valor.globo.com/b/noticia/2.ghtml"]
        pg = FakePage(cropped=False, links=links, per_link={links[0]: True})
        assert keepalive._valor_dead_reason(pg) is None

    def test_cabecalho_mentiroso_nao_mata(self):
        # "não logado" no cabeçalho, mas o texto inteiro → viva
        assert keepalive._valor_dead_reason(FakePage(cropped=False, logged=False)) is None

    def test_sem_materia_nao_julga(self):
        assert keepalive._valor_dead_reason(FakePage(links=[])) is None

    def test_pagina_que_nao_carrega_nao_mata(self):
        class SemRede(FakePage):
            def goto(self, url, **kw):
                raise RuntimeError("timeout")
        assert keepalive._valor_dead_reason(SemRede(cropped=True)) is None


# ── fiação ──────────────────────────────────────────────────────────────────────────────────
class TestFiacao:
    def test_leitor_espera_o_paywall_antes_de_ler_o_dom(self):
        src = (ROOT / "clipping" / "reader.py").read_text(encoding="utf-8")
        assert src.index("_vcut = wait_cropped(page)") < src.index("dom_json: str = page.evaluate(")
        assert "use_body(" in src and ".paywall-hidden" in src

    def test_keepalive_usa_o_motivo_novo(self):
        src = (ROOT / "clipping" / "keepalive.py").read_text(encoding="utf-8")
        assert "dead_reason = _valor_dead_reason(page)" in src
