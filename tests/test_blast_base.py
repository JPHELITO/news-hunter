"""
A BASE DE DADOS DO BLAST DAS 06h BRT.

O blast matinal lê preços às 06:00 BRT = 09:00 UTC. A regra que rege este arquivo é uma
só: *todo instrumento do blast tem de estar NEGOCIANDO (ou ter fechado o próprio dia)
naquele instante* — senão o leitor recebe o movimento de ontem achando que é o de hoje,
e um número velho com cara de novo é pior que campo vazio.

Medido nas barras de 5 minutos do dia 01/09/2026 (1ª barra de cada instrumento, em UTC):

    HG=F · GC=F · BZ=F   00:00  -> 216 barras/dia, vivos a noite toda
    ALI=F                00:05  -> vivo, porém RALO: 33 barras contra 216 dos outros
    FEF.SGX (Sina)       24h    -> minério 62% em USD, vivo às 06h BRT
    hf_NID  (Sina)       24h    -> níquel da LME em USD/t; no Yahoo NAO EXISTE de graça
    RIO.L · AAL.L · GLEN.L · MT.AS   07:00  -> Europa abre antes do Brasil. VIVOS.
    BHP.AX · FMG.AX      00:00-06:12 -> pregão do DIA já encerrado às 09:00. É o overnight.
    ---------------------------------------------------------------------------------
    RIO · BHP · MT (NYSE)   13:30  -> às 09:00 UTC a bolsa está FECHADA. NAO SERVEM.
    ^GSPC                   13:30  -> idem; o bloco GLOBAL precisa do FUTURO (ES=F).

Se alguém trocar um ticker daqui por um listing americano "porque é o mesmo papel", o
blast volta a publicar o fechamento da véspera às 6 da manhã. Estes testes existem para
essa troca doer.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hunter import prices, pulse_sina   # noqa: E402


def _por_simbolo():
    return {q[5]: q for q in prices.QUOTES_LIST}


class TestPeersDoBlast:
    @pytest.mark.parametrize("rotulo,simbolo,sufixo", [
        ("RIO",  "RIO.L",  ".L"),    # Londres
        ("GLEN", "GLEN.L", ".L"),
        ("AAL",  "AAL.L",  ".L"),
        ("MT",   "MT.AS",  ".AS"),   # Amsterdã
    ])
    def test_europe_live_usa_a_linha_europeia(self, rotulo, simbolo, sufixo):
        """Às 06h BRT a Europa está aberta há 2h; NY não abriu."""
        assert simbolo in _por_simbolo(), f"{rotulo}: {simbolo} saiu do universo"
        assert simbolo.endswith(sufixo)

    @pytest.mark.parametrize("simbolo", ["BHP.AX", "FMG.AX"])
    def test_australia_overnight_usa_a_linha_de_sydney(self, simbolo):
        """A ASX fecha 06:12 UTC — às 09:00 o número do dia já é definitivo."""
        assert simbolo in _por_simbolo()

    def test_global_usa_o_futuro_e_nao_o_indice_a_vista(self):
        """O ^GSPC não existe às 06h BRT; o ES=F negocia quase 24h."""
        assert "ES=F" in _por_simbolo()

    def test_os_listings_americanos_continuam_existindo(self):
        """As linhas de NY seguem no universo — quem sai de cena é o uso delas no blast."""
        for adr in ("RIO", "BHP", "MT"):
            assert adr in _por_simbolo()


class TestCommoditiesDoBlast:
    def test_as_seis_commodities_do_blast_tem_fonte(self):
        yahoo = {c[0] for c in prices.COMMODITIES_LIST}
        sina = {code for code, _, _ in prices.SINA_COMMODITIES.values()}
        for code in ("COPPER", "GOLD", "BRENT", "ALUMINUM"):
            assert code in yahoo, f"{code} sem fonte Yahoo"
        for code in ("IRON_ORE_SGX", "NICKEL"):
            assert code in sina, f"{code} sem fonte Sina"

    def test_niquel_e_minerio_saem_em_dolar_por_tonelada(self):
        """O blast escreve 'USD X/ton' — a unidade não pode virar yuan sem alguém ver."""
        for _, _, unidade in prices.SINA_COMMODITIES.values():
            assert unidade == "USD/t"

    def test_sina_continua_no_universo_do_pulse(self):
        """Tirar o níquel/minério daqui quebraria o blast em silêncio."""
        for nome in prices.SINA_COMMODITIES:
            assert nome in pulse_sina.SINA_SYMBOLS


class TestVariacaoDoDia:
    """
    O campo 7 do payload `hf_` é o fechamento da sessão ANTERIOR — é ele que permite dar
    variação do dia sem esperar acumular série nossa. A identificação (2026-09-01) foi
    conferida por fora: o hf_FEF trazia 99,340 e o minério 62% do Trading Economics havia
    fechado o dia em 99,33 — mesmo número, fonte independente.
    """
    # payload real do hf_FEF em 2026-09-01 18:02 UTC
    FEF = ["99.261", "", "99.200", "99.250", "99.350", "98.650", "02:02:46",
           "99.340", "99.200", "393021", "53", "56", "2026-09-02", "新加坡铁矿石", "13694"]

    def test_extrai_o_fechamento_anterior(self):
        d = pulse_sina._parse("hf_FEF", self.FEF)
        assert d is not None and d["prev"] == pytest.approx(99.340)

    def test_a_variacao_sai_do_fechamento_anterior(self):
        d = pulse_sina._parse("hf_FEF", self.FEF)
        assert (d["price"] - d["prev"]) / d["prev"] * 100 == pytest.approx(-0.0795, abs=0.01)

    def test_sem_fechamento_anterior_nao_inventa_variacao(self):
        """Campo vazio no blast é aceitável; número errado, não."""
        curto = self.FEF[:7] + [""] + self.FEF[8:]
        d = pulse_sina._parse("hf_FEF", curto)
        assert d is not None and d["prev"] is None
