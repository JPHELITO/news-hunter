"""
Testes do Market Pulse — a régua de publicação (onda 0) e a janela overnight (onda 1).

Cada teste aqui trava uma decisão que custou medição para ser tomada. Se um deles
começar a falhar, leia o comentário antes de "consertar" o teste.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hunter import pulse_score, pulse_sina, pulse_snapshot   # noqa: E402


# ───────────────────────── cortes e desempate ─────────────────────────
class TestCortes:
    def test_ancora_nao_esta_entre_os_cortes_que_pontuam(self):
        """A foto das 18h é ponto de partida, não previsão."""
        assert pulse_snapshot.CUT_BASE not in pulse_snapshot.CUTS_SCORE
        assert pulse_snapshot.CUT_BASE in pulse_snapshot.CUTS

    @pytest.mark.parametrize("hora_utc,esperado", [
        (10, "07"), (12, "09"), (21, "18"),
        # 11h UTC fica à mesma distância de 07 (10h) e 09 (12h). O cron dispara ANTES do
        # alvo, então o run das 11:50 está indo para o corte das 09 — nunca para o das 07.
        # Empatar para trás faria a foto das 09h sobrescrever a das 07h.
        (11, "09"),
        (20, "18"),
    ])
    def test_desempate_vai_para_o_corte_mais_tardio(self, hora_utc, esperado):
        with mock.patch("hunter.pulse_snapshot.datetime") as dt:
            dt.now.return_value.hour = hora_utc
            assert pulse_snapshot.cut_agora() == esperado

    def test_premarket_sao_um_subconjunto_do_snapshot(self):
        assert pulse_snapshot.PREMARKET_SYMBOLS
        assert pulse_snapshot.PREMARKET_SYMBOLS <= set(pulse_snapshot.SNAPSHOT_SYMBOLS)

    def test_todo_simbolo_tem_grupo(self):
        """A atribuição do painel soma por grupo — símbolo órfão sumiria da explicação."""
        for s in pulse_snapshot.SNAPSHOT_SYMBOLS:
            assert s in pulse_snapshot.GRUPO_DE, f"{s} não está em nenhum grupo"

    def test_nao_ha_simbolo_repetido(self):
        """Símbolo repetido entraria duas vezes no produto escalar."""
        syms = pulse_snapshot.SNAPSHOT_SYMBOLS
        assert len(syms) == len(set(syms))


# ───────────────────────── gate de publicação (onda 0) ─────────────────────────
class TestGateDePublicacao:
    def test_modelo_forte_publica(self):
        assert pulse_score._sem_sinal_por_que("VALE3.SA", {"ic_oos": 0.69}) is None

    def test_modelo_fraco_nao_publica(self):
        """RANI3: 0,209 com a janela overnight — small cap com 28% de leilões sem negócio."""
        motivo = pulse_score._sem_sinal_por_que("RANI3.SA", {"ic_oos": 0.209})
        assert motivo and "too weak" in motivo
        assert "+0.21" in motivo          # o número aparece: o cliente vê POR QUE

    def test_limite_e_inclusivo(self):
        assert pulse_score._sem_sinal_por_que("X", {"ic_oos": pulse_snapshot.IC_MIN_PUBLICAR}) is None

    def test_sem_modelo_ou_sem_validacao_nao_publica(self):
        assert pulse_score._sem_sinal_por_que("X", None)
        assert pulse_score._sem_sinal_por_que("X", {"ic_oos": None})

    def test_barra_manual_vence_o_numero(self):
        with mock.patch.dict(pulse_score.SEM_SINAL, {"VALE3.SA": "motivo curado"}, clear=False):
            assert pulse_score._sem_sinal_por_que("VALE3.SA", {"ic_oos": 0.9}) == "motivo curado"

    def test_o_limiar_separa_as_duas_populacoes_medidas(self):
        """
        Medido em 2026-08-26, já com a janela overnight (corte 09). O limiar tem de ficar
        ENTRE as duas populações com folga, senão empresa entra e sai a cada re-treino.
        Se este teste falhar depois de um re-treino, a distribuição mudou de lugar:
        recalibre o limiar em vez de afrouxar o teste.
        """
        publica = [0.773, 0.691, 0.507, 0.456, 0.448, 0.423, 0.288]   # menor: KLBN11
        barra = [0.209, 0.092]                                        # maior: RANI3
        assert max(barra) < pulse_snapshot.IC_MIN_PUBLICAR < min(publica)
        assert pulse_snapshot.IC_MIN_PUBLICAR - max(barra) > 0.03
        assert min(publica) - pulse_snapshot.IC_MIN_PUBLICAR > 0.03


# ───────────────────────── janela overnight (onda 1) ─────────────────────────
class TestJanelaOvernight:
    def _snapshot_falso(self, base_date="2026-08-25", hoje="2026-08-26"):
        """Responde às consultas de `features` como o PostgREST responderia."""
        def fake(path: str):
            if "select=session_date&order=session_date.desc&limit=50" in path:
                return [{"session_date": hoje}]
            if f"cut=eq.{pulse_snapshot.CUT_BASE}" in path and "lt." in path:
                return [{"session_date": base_date}]
            if f"cut=eq.{pulse_snapshot.CUT_BASE}" in path:
                return [{"symbol": "GC=F", "price": 100.0},
                        {"symbol": "^HSI", "price": 200.0}]
            return [{"symbol": "GC=F", "price": 102.0, "captured_at": "2026-08-26T12:00:00+00:00"},
                    {"symbol": "^HSI", "price": 199.0, "captured_at": "2026-08-26T12:00:00+00:00"}]
        return fake

    def test_mede_do_fechamento_de_ontem_ate_o_corte(self):
        with mock.patch.object(pulse_score, "_supa_get", side_effect=self._snapshot_falso()):
            sessao, x, _ = pulse_score.features("09")
        assert sessao == "2026-08-26"
        assert x["GC=F"] == pytest.approx(0.02)      # 102/100 − 1
        assert x["^HSI"] == pytest.approx(-0.005)    # 199/200 − 1

    def test_a_ancora_e_de_um_pregao_ANTERIOR(self):
        """
        A âncora nunca pode ser do mesmo dia: às 18h de hoje a B3 já abriu, e o gap que
        queremos prever é justamente o dessa abertura. O filtro `lt.<hoje>` é o que garante.
        """
        vistos = []

        def fake(path: str):
            vistos.append(path)
            if "select=session_date&order=session_date.desc&limit=50" in path:
                return [{"session_date": "2026-08-26"}]
            if f"cut=eq.{pulse_snapshot.CUT_BASE}" in path and "lt." in path:
                return [{"session_date": "2026-08-25"}]
            return [{"symbol": "GC=F", "price": 1.0}]

        with mock.patch.object(pulse_score, "_supa_get", side_effect=fake):
            pulse_score.features("09")
        assert any(f"cut=eq.{pulse_snapshot.CUT_BASE}" in p and "session_date=lt.2026-08-26" in p
                   for p in vistos)

    def test_sem_ancora_falha_fechada(self):
        """Sem a foto das 18h de ontem não há janela — melhor não publicar que publicar torto."""
        def fake(path: str):
            if "select=session_date&order=session_date.desc&limit=50" in path:
                return [{"session_date": "2026-08-26"}]
            return []

        with mock.patch.object(pulse_score, "_supa_get", side_effect=fake):
            with pytest.raises(RuntimeError, match="âncora"):
                pulse_score.features("09")


# ───────────────────────── coletor Sina (onda 1) ─────────────────────────
class TestSina:
    # Respostas REAIS medidas em 2026-08-26.
    FEF = ("97.142,,97.150,97.350,97.350,97.050,05:14:55,97.140,97.250,"
           "402330,106,32,2026-08-26,新加坡铁矿石,15657")
    SP = ("纸浆连续,150000,4836.000,4852.000,4780.000,4786.000,4786.000,4790.000,4786.000,"
          "4790.000,4830.000,123456,234567,沪,,,,2026-08-26")

    def test_le_o_minerio_de_cingapura(self):
        p = pulse_sina._parse("hf_FEF", self.FEF.split(","))
        assert p["price"] == pytest.approx(97.142)
        assert p["stamp"] == "2026-08-26 05:14:55"

    def test_le_a_celulose_de_xangai(self):
        p = pulse_sina._parse("nf_SP0", self.SP.split(","))
        assert p["price"] == pytest.approx(4786.0)
        assert p["stamp"] == "2026-08-26 15:00:00"     # HHMMSS → HH:MM:SS

    def test_preco_fora_da_faixa_do_dia_e_descartado(self):
        """
        A resposta é POSICIONAL e a API não é oficial. Se o layout mudar, o campo que
        lermos como preço vai cair fora da faixa [mínima, máxima] do próprio dia — e aí
        preferimos devolver nada a gravar lixo com cara de preço.
        """
        campos = self.SP.split(",")
        campos[8] = "99999.0"
        assert pulse_sina._parse("nf_SP0", campos) is None

    def test_simbolo_inexistente_nao_vira_preco(self):
        assert pulse_sina._parse("nf_XX0", "".split(",")) is None

    def test_dalian_usa_I_maiusculo(self):
        """nf_i0 minúsculo devolve string vazia, sem erro — some em silêncio."""
        assert pulse_sina.SINA_SYMBOLS["I.DCE"] == "nf_I0"

    def test_os_simbolos_da_sina_ficam_fora_do_vetor_do_modelo(self):
        """
        Enquanto não houver histórico validado (o fechamento diário chinês inclui a sessão
        noturna, que roda DEPOIS dos nossos cortes), eles só acumulam. Se um dia entrarem,
        que seja por decisão explícita — não por vazarem para dentro do treino.
        """
        for nome in pulse_sina.SINA_SYMBOLS:
            assert nome not in pulse_snapshot.SNAPSHOT_SYMBOLS
