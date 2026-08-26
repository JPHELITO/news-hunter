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

    def test_alvos_de_fora_da_b3_abrem_depois_do_corte(self):
        """
        SCCO e TX abrem na NYSE (10:30 BRT) e a GMEXICOB na Bolsa Mexicana, que sincroniza
        com ela. Os dois cortes de pontuação são de manhã, ANTES disso — é o que torna a
        previsão delas quase um nowcast, e é o que não pode ser quebrado ao mexer nos
        horários. O corte mais tardio (09h BRT = 12h UTC) tem de ficar antes das 13:30 UTC.
        """
        for c in pulse_snapshot.CUTS_SCORE:
            assert pulse_snapshot.CUTS[c] < 13, f"corte {c} não é mais pré-abertura de NY"

    def test_um_alvo_nunca_e_seu_proprio_instrumento(self):
        """
        SCCO e TX são ALVOS. Se algum deles entrasse também no snapshot, o modelo veria o
        preço da própria ação e o IC viraria ficção.
        """
        for alvo in pulse_snapshot.COMPANIES:
            assert alvo not in pulse_snapshot.SNAPSHOT_SYMBOLS, \
                f"{alvo} é alvo E instrumento — look-ahead"


# ───────────────────────── gate de publicação (onda 0) ─────────────────────────
class TestGateDePublicacao:
    def test_modelo_forte_publica(self):
        assert pulse_score._sem_sinal_por_que("VALE3.SA", {"ic_oos": 0.68}) is None

    def test_modelo_fraco_nao_publica(self):
        """RANI3: 0,120 — small cap com 28% de leilões sem negócio."""
        motivo = pulse_score._sem_sinal_por_que("RANI3.SA", {"ic_oos": 0.120})
        assert motivo and "too weak" in motivo
        assert "+0.12" in motivo          # o número aparece: o cliente vê POR QUE

    def test_limite_e_inclusivo(self):
        assert pulse_score._sem_sinal_por_que("X", {"ic_oos": pulse_snapshot.IC_MIN_PUBLICAR}) is None

    def test_sem_modelo_ou_sem_validacao_nao_publica(self):
        assert pulse_score._sem_sinal_por_que("X", None)
        assert pulse_score._sem_sinal_por_que("X", {"ic_oos": None})

    def test_barra_manual_vence_o_numero(self):
        with mock.patch.dict(pulse_score.SEM_SINAL, {"VALE3.SA": "motivo curado"}, clear=False):
            assert pulse_score._sem_sinal_por_que("VALE3.SA", {"ic_oos": 0.9}) == "motivo curado"

    def test_o_limiar_cai_no_maior_vao(self):
        """
        O limiar tem de cair num VÃO da distribuição de ic_oos, não colado num valor: a
        0,01 de distância, a empresa entra e sai a cada re-treino semanal. Medido em
        2026-08-26 com a configuração de produção (janela overnight + painel), os dois
        cortes juntos.

        Se este teste falhar depois de um re-treino, a distribuição andou: recalibre o
        limiar olhando a lista nova, em vez de afrouxar o teste.
        """
        medidos = sorted([0.810, 0.757, 0.732, 0.688, 0.680, 0.650, 0.617, 0.603, 0.515,
                          0.465, 0.462, 0.451, 0.446, 0.422, 0.408, 0.408, 0.391,
                          0.281, 0.252, 0.244, 0.232, 0.120, 0.036])
        limiar = pulse_snapshot.IC_MIN_PUBLICAR

        # 1) folga do valor mais próximo: a 0,01 de distância a empresa pisca a cada re-treino
        folga = min(abs(v - limiar) for v in medidos)
        assert folga >= 0.02, f"limiar {limiar} está a {folga:.3f} do ic_oos mais próximo"

        # 2) o vão em que ele cai é um vão DE VERDADE, não um respiro entre dois vizinhos
        #    (comparar com os maiores vãos da lista inteira não serve: os maiores ficam no
        #    topo, onde os valores são esparsos e não há fronteira nenhuma para decidir)
        abaixo = max((v for v in medidos if v < limiar), default=0.0)
        acima = min((v for v in medidos if v > limiar), default=1.0)
        assert acima - abaixo >= 0.04, \
            f"limiar {limiar} caiu num vão estreito ({abaixo:.3f} → {acima:.3f})"


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

    def test_fim_de_semana_e_feriado_passam(self):
        """Segunda-feira ancora na sexta (3 dias) — é o caso normal, não pode falhar."""
        with mock.patch.object(pulse_score, "_supa_get",
                               side_effect=self._snapshot_falso(base_date="2026-08-21",
                                                                hoje="2026-08-24")):
            sessao, x, _ = pulse_score.features("09")
        assert sessao == "2026-08-24" and x

    def test_ancora_velha_demais_falha_fechada(self):
        """
        Se a captura das 18h falhar, a âncora disponível vira a de anteontem — e a feature
        mediria 48h+ se passando por overnight, sem avisar ninguém. Este é o defeito
        silencioso que a trava existe para impedir; a mensagem diz como recuperar.
        """
        with mock.patch.object(pulse_score, "_supa_get",
                               side_effect=self._snapshot_falso(base_date="2026-08-10",
                                                                hoje="2026-08-26")):
            with pytest.raises(RuntimeError, match="16 dias antes"):
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


# ───────────────────── painel, banda e convicção (onda 2) ─────────────────────
class TestPainelEApresentacao:
    """
    Um modelo mínimo, à mão, para exercitar a mecânica sem depender do banco.
    Uma feature só, coeficiente 1, média 0 e desvio 1 → a previsão É o valor da feature.
    """
    def _modelo(self, **conf):
        base = {"sigma_y": 0.02, "sigma_media": 0.01,
                "band_q10": -0.008, "band_q90": 0.012, "band_cov": 0.80,
                "hit_high": 0.75, "hit_mid": 0.62, "hit_low": 0.55}
        return {"company": "X.SA", "coefs": {"GC=F": 1.0}, "mu": {"GC=F": 0.0},
                "sd": {"GC=F": 1.0}, "sigma_pred": 0.01, "conf_w": {**base, **conf}}

    def _painel(self):
        return {"company": pulse_snapshot.PANEL_KEY, "coefs": {"GC=F": 0.5},
                "mu": {"GC=F": 0.0}, "sd": {"GC=F": 1.0}, "sigma_pred": 1.0, "conf_w": None}

    def test_publica_a_media_dos_dois_modelos(self):
        """per-name = 0,01 · painel = 0,5 × sigma_y(0,02) × 0,01 = 0,0001 → média."""
        out = pulse_score.pontuar_empresa(self._modelo(), {"GC=F": 0.01}, self._painel())
        esperado = 100 * (0.01 + 0.5 * 0.01 * 0.02) / 2
        assert out["gap_expected"] == pytest.approx(esperado, abs=1e-6)

    def test_sem_painel_publica_so_o_per_name(self):
        out = pulse_score.pontuar_empresa(self._modelo(), {"GC=F": 0.01}, None)
        assert out["gap_expected"] == pytest.approx(1.0)

    def test_painel_inaplicavel_falha_fechada(self):
        """
        O ic_oos que autoriza publicar foi medido sobre a MÉDIA. Cair no per-name sozinho
        seria trocar de modelo em silêncio e publicar um número que ninguém validou.
        """
        painel = self._painel()
        painel["coefs"] = {"GC=F": 0.5, "SUMIU=F": 0.3}
        assert pulse_score.pontuar_empresa(self._modelo(), {"GC=F": 0.01}, painel) is None

    def test_a_soma_das_contribuicoes_bate_com_o_gap(self):
        """
        A atribuição é EXATA, não aproximada — é por isso que o linear foi escolhido no
        lugar das árvores. Com o painel entrando na média, ela tem de continuar exata.
        """
        out = pulse_score.pontuar_empresa(self._modelo(), {"GC=F": 0.01}, self._painel())
        soma = sum(v for _, v, *_ in out["attribution"]["drivers"])
        assert soma == pytest.approx(out["gap_expected"], abs=1e-4)

    def test_banda_sai_em_torno_da_previsao(self):
        out = pulse_score.pontuar_empresa(self._modelo(), {"GC=F": 0.01}, None)
        lo, hi = out["attribution"]["band"]
        assert lo < out["gap_expected"] < hi
        assert out["attribution"]["band_cov"] == pytest.approx(0.80)

    @pytest.mark.parametrize("x,faixa,hit", [
        (0.02, "high", 0.75),    # |ŷ|/σ = 2,0
        (0.008, "mid", 0.62),    # 0,8
        (0.002, "low", 0.55),    # 0,2
    ])
    def test_conviccao_por_magnitude_carrega_o_acerto_historico(self, x, faixa, hit):
        """O selo não é adjetivo: vem com o acerto MEDIDO naquela faixa."""
        out = pulse_score.pontuar_empresa(self._modelo(), {"GC=F": x}, None)
        assert out["attribution"]["conviction"] == faixa
        assert out["attribution"]["conviction_hit"] == pytest.approx(hit)

    def test_faixa_sem_amostra_nao_vira_promessa(self):
        out = pulse_score.pontuar_empresa(self._modelo(hit_high=None), {"GC=F": 0.02}, None)
        assert out["attribution"]["conviction"] == "high"
        assert out["attribution"]["conviction_hit"] is None

    def test_driver_carrega_o_movimento_do_instrumento(self):
        """O leitor reconhece "o ouro caiu 1,3%"; a contribuição sozinha é jargão."""
        out = pulse_score.pontuar_empresa(self._modelo(), {"GC=F": -0.013}, None)
        sym, contrib, mov = out["attribution"]["drivers"][0]
        assert sym == "GC=F"
        assert mov == pytest.approx(-1.3, abs=0.01)

    def test_a_linha_do_painel_nao_e_confundida_com_empresa(self):
        assert pulse_snapshot.PANEL_KEY not in pulse_snapshot.COMPANIES
        assert pulse_snapshot.PANEL_KEY.startswith("_")


# ───────────────────── trava anti-look-ahead (onda 4) ─────────────────────
class TestTravaPosAbertura:
    """
    Um corte de manhã só pode ser capturado ANTES de a B3 abrir. O Actions atrasa rodadas,
    e `cut_agora()` devolveria '09' às 13h UTC — gravando com o rótulo das 09:00 uma foto
    tirada DEPOIS da abertura. Essa linha entraria no treino como se fosse pré-abertura e
    envenenaria o histórico em silêncio. Perder a rodada do dia é o mal menor.
    """
    def _rodar(self, argv, hora_utc):
        from hunter import pulse_daily
        with mock.patch.object(sys, "argv", ["pulse_daily"] + argv),              mock.patch("hunter.pulse_daily.datetime") as dt,              mock.patch("hunter.pulse_daily.pulse_snapshot.capture") as cap,              mock.patch("hunter.pulse_daily.pulse_score.score") as sc,              mock.patch("hunter.pulse_daily.pulse_sina.capture"):
            dt.now.return_value = mock.Mock(hour=hora_utc, minute=20)
            rc = pulse_daily.main()
        return rc, cap.called, sc.called

    def test_recusa_capturar_depois_da_abertura(self):
        rc, capturou, pontuou = self._rodar(["--cut", "09"], hora_utc=13)
        assert rc == 1 and not capturou and not pontuou

    def test_antes_da_abertura_captura_normalmente(self):
        rc, capturou, _ = self._rodar(["--cut", "09"], hora_utc=12)
        assert capturou

    def test_a_ancora_das_18h_nao_e_afetada(self):
        """A âncora é capturada às 21h UTC, muito depois da abertura — e tem de continuar."""
        _, capturou, _ = self._rodar(["--cut", pulse_snapshot.CUT_BASE], hora_utc=21)
        assert capturou

    def test_repontuar_sem_capturar_continua_permitido(self):
        rc, capturou, pontuou = self._rodar(["--cut", "09", "--skip-capture"], hora_utc=13)
        assert not capturou and pontuou


# ───────────────── curadoria de drivers (conserto de 2026-08-26) ─────────────────
class TestDriversCurados:
    """
    O usuário pegou o painel dizendo que a KLBN11 (papel e celulose) era explicada pelo
    COBRE, a USIM5 pelo ADR da Vale e a CSN pelo cobre. Matematicamente o número estava
    certo — aqueles instrumentos contribuíram mais naquele dia — mas um analista perde a
    confiança no produto na primeira linha absurda que lê. A causa era colinearidade entre
    34 séries soltas; a correção é cada empresa só enxergar o que tem ligação econômica.
    """
    def test_papel_e_celulose_nao_ve_cobre_nem_ouro(self):
        for emp in ("SUZB3.SA", "KLBN11.SA", "RANI3.SA"):
            vistos = pulse_snapshot.instrumentos_de(emp)
            assert "HG=F" not in vistos, f"{emp} enxerga cobre"
            assert "GC=F" not in vistos, f"{emp} enxerga ouro"

    def test_papel_e_celulose_ve_papel_e_celulose(self):
        for emp in ("SUZB3.SA", "KLBN11.SA", "RANI3.SA"):
            vistos = set(pulse_snapshot.instrumentos_de(emp))
            assert vistos & {"UPM.HE", "STERV.HE", "2689.HK"}, f"{emp} sem par de P&P"

    def test_ouro_nao_ve_minerio_nem_aco(self):
        vistos = set(pulse_snapshot.instrumentos_de("AURA33.SA"))
        assert "GC=F" in vistos
        assert not (vistos & {"FMG.AX", "600019.SS", "HG=F"})

    def test_cobre_ve_cobre(self):
        for emp in ("SCCO", "GMEXICOB.MX"):
            assert "HG=F" in pulse_snapshot.instrumentos_de(emp)

    def test_o_gemeo_entra_sempre(self):
        for emp, gemeo in pulse_snapshot.GEMEO.items():
            assert gemeo in pulse_snapshot.instrumentos_de(emp), f"{emp} sem o gêmeo {gemeo}"

    def test_todas_veem_o_macro(self):
        """Câmbio, VIX e futuros americanos atingem todo mundo — são o piso comum."""
        for emp in pulse_snapshot.COMPANIES:
            vistos = set(pulse_snapshot.instrumentos_de(emp))
            assert set(pulse_snapshot.MACRO) <= vistos, f"{emp} não vê todo o macro"

    def test_a_parcimonia_e_respeitada(self):
        """
        O estudo mediu a curva: 5 drivers 0,222 · 12 → 0,214 · 20 → 0,208 — "mais atrapalha".
        Nenhuma empresa deve voltar a enxergar as 34 séries.
        """
        for emp in pulse_snapshot.COMPANIES:
            n = len(pulse_snapshot.instrumentos_de(emp))
            assert n <= 16, f"{emp} vê {n} instrumentos — parcimônia perdida"

    def test_toda_empresa_tem_curadoria(self):
        """Empresa sem entrada no mapa cairia no comportamento antigo, sem ninguém notar."""
        for emp in pulse_snapshot.COMPANIES:
            assert emp in pulse_snapshot.DRIVERS_POR_EMPRESA, f"{emp} sem curadoria"
