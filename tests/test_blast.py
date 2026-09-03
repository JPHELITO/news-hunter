"""
Testes do blast de WhatsApp — a escolha de QUAL IA atende e a saída dos destaques.

A regra que estes testes travam nasceu de dois incidentes reais, e é a mesma lição vista
por dois ângulos opostos:

  ago/2026 — a cascata dos takes usava o primeiro provedor que RESPONDIA, então o melhor
             modelo (Gemini, 0,8% de erro) ficava intocado no fim da fila enquanto o pior
             (GLM, ~25%) fazia o trabalho. Ordem por disponibilidade, não por qualidade.
  set/2026 — a 1ª versão DESTE escolhedor fazia o inverso e dava no mesmo: mandava para o
             mais OCIOSO na marra, e punha a Mistral (4,7%) na frente do Gemini só porque
             ela estava zerada.

Em nenhum dos dois a QUALIDADE era a variável de decisão, e deveria ser. Daí a regra
final: **o mais ocioso ENTRE OS BONS**. Se alguém "simplificar" isto para `min(carga)`,
estes testes falham.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clipping import blast          # noqa: E402
from clipping import run_jobs       # noqa: E402


CHAVES = {
    "CLIPPING_TRANSLATE_KEY": "k1", "GEMINI_API_KEY": "k2",
    "MISTRAL_API_KEY": "k3", "ZAI_API_KEY": "k4",
}


def _ordem(carga, env=None):
    with mock.patch.dict("os.environ", {**CHAVES, **(env or {})}, clear=False), \
         mock.patch.object(blast, "_carga_hoje", return_value=carga):
        return [p["nome"] for p in blast.escolher_provedores()]


ZERADO = {"gemini_clipping": 0, "gemini_takes": 0, "mistral": 0, "zai": 0}


class TestEscolhaDoProvedor:
    def test_com_todos_ociosos_ganha_o_de_melhor_qualidade(self):
        """Mistral e Z.AI zeradas não passam na frente do Gemini."""
        ordem = _ordem(ZERADO)
        assert ordem[0].startswith("gemini")
        assert ordem.index("mistral") > ordem.index("gemini_takes")

    def test_entre_iguais_em_qualidade_ganha_o_mais_ocioso(self):
        """É aqui que o balanceamento de carga realmente acontece."""
        carga = {**ZERADO, "gemini_takes": 400, "gemini_clipping": 7}
        assert _ordem(carga)[0] == "gemini_clipping"
        carga2 = {**ZERADO, "gemini_takes": 7, "gemini_clipping": 400}
        assert _ordem(carga2)[0] == "gemini_takes"

    def test_pool_sobrecarregado_sai_da_disputa(self):
        """Os dois Gemini quase no teto -> a vez é da Mistral, e não de um Gemini apertado."""
        carga = {**ZERADO, "gemini_clipping": 490, "gemini_takes": 495}
        ordem = _ordem(carga)
        assert ordem[0] == "mistral"
        assert "gemini_clipping" not in ordem and "gemini_takes" not in ordem

    def test_lento_fica_por_ultimo_mesmo_ocioso(self):
        """A Z.AI leva ~1 min por chamada; folga de cota não paga o analista esperando."""
        assert _ordem(ZERADO)[-1] == "zai"

    def test_todos_apertados_usa_o_menos_pior_em_vez_de_desistir(self):
        """Blast sem destaque é aceitável; blast que nem tenta, não."""
        carga = {"gemini_clipping": 500, "gemini_takes": 500, "mistral": 300, "zai": 900}
        ordem = _ordem(carga)
        assert len(ordem) == 1 and ordem[0] == "zai"      # 10% livre é a maior folga restante

    def test_provedor_sem_chave_nao_entra(self):
        assert "mistral" not in _ordem(ZERADO, env={"MISTRAL_API_KEY": ""})

    def test_a_folga_e_relativa_ao_teto_de_cada_pool(self):
        """200 usados pesam diferente num teto de 500 e num de 1000."""
        carga = {**ZERADO, "mistral": 150, "zai": 150}     # 50% vs 85% de folga
        with mock.patch.dict("os.environ", CHAVES, clear=False), \
             mock.patch.object(blast, "_carga_hoje", return_value=carga):
            por_nome = {p["nome"]: p["folga"] for p in blast.escolher_provedores()}
        assert por_nome["mistral"] == pytest.approx(0.5)
        assert por_nome["zai"] == pytest.approx(0.85)


class TestDestaques:
    def test_sem_noticia_nao_chama_ia_nenhuma(self):
        with mock.patch.object(blast, "_chamar") as c:
            r = blast.highlights([], n=3)
        assert not c.called and r["destaques"] == [] and r["erro"]

    def test_desce_a_fila_quando_o_primeiro_falha(self):
        chamados = []

        def falso(cfg, prompt):
            chamados.append(cfg["nome"])
            return ["destaque bom"] if len(chamados) > 1 else None

        with mock.patch.dict("os.environ", CHAVES, clear=False), \
             mock.patch.object(blast, "_carga_hoje", return_value=ZERADO), \
             mock.patch.object(blast, "_chamar", side_effect=falso):
            r = blast.highlights([{"title": "x"}], n=3)
        assert len(chamados) == 2 and r["destaques"] == ["destaque bom"]

    def test_provedor_que_explode_nao_derruba_o_blast(self):
        with mock.patch.dict("os.environ", CHAVES, clear=False), \
             mock.patch.object(blast, "_carga_hoje", return_value=ZERADO), \
             mock.patch.object(blast, "_chamar", side_effect=RuntimeError("boom")):
            r = blast.highlights([{"title": "x"}], n=3)
        assert r["destaques"] == [] and r["provedor"] is None and r["erro"]

    def test_respeita_o_teto_de_destaques(self):
        with mock.patch.dict("os.environ", CHAVES, clear=False), \
             mock.patch.object(blast, "_carga_hoje", return_value=ZERADO), \
             mock.patch.object(blast, "_chamar", return_value=["a", "b", "c", "d", "e"]):
            assert len(blast.highlights([{"title": "x"}], n=3)["destaques"]) == 3

    def test_a_noticia_chega_a_ia_com_setor_take_e_fonte(self):
        """Sem o take e o setor do analista, a IA escolheria no escuro."""
        linha = blast._linha({"title": "Vale sobe", "source_name": "Valor",
                              "sector": "SM", "take": "+"})
        assert "Vale sobe" in linha and "Valor" in linha
        assert "SM" in linha and "positivo" in linha

    def test_no_take_aparece_como_sem_take_e_nao_some(self):
        assert "sem take" in blast._linha({"title": "x", "take": "no take"})


class TestDestaquesMarcadosAMao:
    """
    2026-09-03 — o analista marca com ★ (na tela do clipping) as notícias que ele QUER nos
    destaques, no máximo 3 e sem mínimo. O que ele marcou a IA é obrigada a escrever; ela
    escolhe só as vagas que sobram. Marcar zero é o comportamento antigo, intacto.

    O risco que estes testes travam é o silencioso: a ★ some no caminho (front → payload →
    job → prompt) e o blast sai igualzinho ao de antes, sem erro nenhum, com o analista
    achando que escolheu.
    """
    def _prompt(self, noticias, n=3):
        capturado = {}

        def falso(cfg, prompt):
            capturado["p"] = prompt
            return ["x"]

        with mock.patch.dict("os.environ", CHAVES, clear=False),              mock.patch.object(blast, "_carga_hoje", return_value=ZERADO),              mock.patch.object(blast, "_chamar", side_effect=falso):
            blast.highlights(noticias, n=n)
        return capturado["p"]

    def test_sem_marcacao_o_prompt_e_o_de_sempre(self):
        p = self._prompt([{"title": "a"}, {"title": "b"}])
        assert "★" not in p
        assert "no máximo 3 destaques; pode haver menos" in p

    def test_marcada_vira_obrigatoria_e_a_ia_escolhe_o_resto(self):
        p = self._prompt([{"title": "a", "pin": 1}, {"title": "b"}, {"title": "c"}])
        assert "★1 " in p and "OBRIGATÓRIAS" in p
        assert "o outro destaque" not in p          # sobram 2, não 1
        assert "os outros 2 destaques você escolhe" in p

    def test_tres_marcadas_nao_deixam_vaga_livre(self):
        p = self._prompt([{"title": "a", "pin": 1}, {"title": "b", "pin": 2},
                          {"title": "c", "pin": 3}, {"title": "d"}])
        assert "exatamente 3 destaques, um para cada ★" in p
        assert "não escolha nenhuma outra notícia" in p

    def test_a_ordem_do_analista_e_a_ordem_do_blast(self):
        """Ele numera clicando; o número tem de chegar ao prompt como ele clicou."""
        p = self._prompt([{"title": "primeira", "pin": 2}, {"title": "segunda", "pin": 1}])
        assert "★2 [NR | take sem take | ?] primeira" in p
        assert "★1 [NR | take sem take | ?] segunda" in p

    def test_marcacao_torta_do_payload_e_renumerada_1_2_3(self):
        """O payload vem do navegador. ★7 e ★9 viram ★1 e ★2 — não um pedido de 9 destaques."""
        ns = [{"title": "a", "pin": 9}, {"title": "b", "pin": 7}]
        p = self._prompt(ns)
        assert "★1 [NR | take sem take | ?] b" in p and "★2 [NR | take sem take | ?] a" in p
        assert "★7" not in p and "★9" not in p

    def test_mais_marcadas_que_o_teto_nao_pedem_o_impossivel(self):
        """5 ★ num teto de 3: as 2 últimas perdem a ★ em vez de a IA escolher qual ignorar."""
        ns = [{"title": "n%d" % i, "pin": i + 1} for i in range(5)]
        p = self._prompt(ns)
        assert "★3" in p and "★4" not in p and "★5" not in p
        assert "exatamente 3 destaques" in p
        for i in range(5):
            assert ("n%d" % i) in p, "notícia sumiu do prompt ao perder a ★"

    def test_highlights_nao_mexe_no_dicionario_de_quem_chamou(self):
        ns = [{"title": "a", "pin": 9}]
        self._prompt(ns)
        assert ns[0]["pin"] == 9

    def test_pin_tolera_texto_e_booleano(self):
        assert blast._pin({"pin": "2"}) == 2 and blast._pin({"pin": True}) == 1
        assert blast._pin({"pin": None}) == 0 and blast._pin({"pin": "x"}) == 0


class TestCorpoDasNoticias:
    """
    A partir de 01/09/2026 a IA LÊ as notícias, não só as manchetes. Medido no mesmo
    conjunto de 10, o salto e o motivo dele:

      antes:  "produção de cobre no Chile registrou queda de 9,4% em julho"
      depois: "recuou 9,4% YoY em julho, atingindo 403.424 toneladas, impactada por
               tempestades severas no norte do país e paradas para manutenção"

    Numero e causa só existem no corpo. O texto vem do cache que o clipping ja encheu
    (`clipping_bodies`), entao nao custa raspagem nenhuma — so tokens, e poucos: 3.411
    num clipping tipico de 7 noticias, contra 7.586 de UMA manchete classificada.
    """
    def test_o_corpo_entra_no_que_a_ia_le(self):
        linha = blast._linha({"title": "Cobre", "body": "<p>Producao caiu 9,4%</p>"})
        assert "Producao caiu 9,4%" in linha and "<p>" not in linha

    def test_corpo_muito_longo_e_cortado_em_palavra_inteira(self):
        n = {"title": "x", "body": "palavra " * 3000}
        linha = blast._linha(n, corpo_max=100)
        assert len(linha) < 200 and linha.endswith("…")
        assert "palavr…" not in linha, "cortou no meio de uma palavra"

    def test_clipping_gordo_encolhe_o_corpo_de_TODAS_em_vez_de_perder_noticia(self):
        """
        Noticia que some do prompt nao pode ser escolhida. Quem decide o que e relevante
        e a IA, nao a ordem em que o analista arrastou os itens — entao o teto aperta
        todo mundo por igual.
        """
        muitas = [{"title": "n%d" % i, "body": "x" * 4000} for i in range(40)]
        bloco = blast._montar_noticias(muitas)
        assert len(bloco) <= blast.PROMPT_MAX_CHARS * 1.02
        for i in range(40):
            assert ("n%d" % i) in bloco, "a noticia %d sumiu do prompt" % i

    def test_sem_corpo_ainda_manda_a_manchete(self):
        """Corpo que o cache nao tem nao pode derrubar a notícia do prompt."""
        bloco = blast._montar_noticias([{"title": "so manchete", "source_name": "Platts"}])
        assert "so manchete" in bloco and "Platts" in bloco

    def test_html_do_corpo_nao_vaza_para_o_prompt(self):
        """Tag no prompt e token gasto a toa, e confunde o modelo."""
        bloco = blast._montar_noticias([{"title": "x", "body": "<p>a</p><table><tr><td>b</td></tr></table>"}])
        assert "<" not in bloco and "a" in bloco and "b" in bloco


class TestOPinChegaAoMotor:
    """
    O caminho todo da ★: navegador → payload do job → prompt. É aqui que ela sumia de
    graça — `process_blast` monta um dicionário NOVO por notícia, e campo que não for
    copiado à mão morre em silêncio, sem erro e sem log.
    """
    def test_process_blast_repassa_a_estrela_do_payload(self):
        visto = {}

        def falso(noticias, n=3):
            visto["noticias"] = noticias
            return {"destaques": ["d"], "provedor": "teste", "erro": None}

        job = {"id": "j1", "config": {"blast": {"only": True, "n": 3}},
               "payload": [{"url": "u1", "title": "a", "pin": 2},
                           {"url": "u2", "title": "b"}]}
        with mock.patch.object(blast, "highlights", side_effect=falso),              mock.patch.object(run_jobs, "_bodies_por_url", return_value={}),              mock.patch.object(run_jobs, "read_job", return_value=job),              mock.patch.object(run_jobs, "patch_job") as pj:
            run_jobs.process_blast(job)
        assert [n.get("pin") for n in visto["noticias"]] == [2, None]
        assert pj.call_args[0][1]["config"]["blast"]["destaques"] == ["d"]
