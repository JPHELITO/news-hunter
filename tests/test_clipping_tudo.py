"""
BOTÃO ÚNICO da aba Clipping (24/11/2026): Word + destaques do blast + HTML do WhatsApp + e-mail
novo numa rodada só — e as duas mudanças no blast que vieram junto:

  * a MESMA chamada traz os destaques em português (WhatsApp) e em inglês (e-mail) — nenhuma
    chamada de IA a mais;
  * lotação do Google: em vez de desistir em ~30 s (o que deixou 2 "Gerar tudo" da tarde de
    24/11 sem destaques), a cadeia passa por TODAS as portas (Gemini nos 2 projetos, outros
    modelos do Google, Groq, Z.AI) e insiste em voltas por até 7 min.

Nada aqui fala com IA, Supabase ou internet: tudo simulado.
"""
from __future__ import annotations

import email
import re
import sys
from datetime import date
from email import policy
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clipping import blast, email_brand, generate, run_jobs, whatsapp     # noqa: E402
from clipping.build import ClippingItem                                   # noqa: E402

CHAVES = {"CLIPPING_TRANSLATE_KEY": "k1", "GEMINI_API_KEY": "k2", "ZAI_API_KEY": "k4"}


@pytest.fixture(autouse=True)
def _uma_volta_so(monkeypatch):  # (o conftest já protege a suíte toda; este fica por clareza)
    """Fora dos testes de insistência: uma volta pelas portas e nenhum sono de verdade. E nenhuma
    chave da Groq vinda da máquina (quem quer a Groq, põe no teste)."""
    monkeypatch.setattr(blast, "PACIENCIA_S", 0)
    monkeypatch.setattr(blast, "_dormir", lambda s: None)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setattr("clipping.build._TRANSLATE_RETRY_S", 0)


@pytest.fixture(autouse=True)
def _sem_tradutor_de_verdade():
    """Nenhum teste daqui chama o tradutor do clipping (IA) de verdade: por padrão ele 'falha' e
    o código cai no Google (que cada teste simula). Quem quer testar o tradutor, simula por cima."""
    with mock.patch("clipping.build._translate_via_llm", return_value=None):
        yield
ZERADO = {"gemini_clipping": 0, "gemini_takes": 0, "zai": 0}
D = date(2026, 11, 24)


def _resp(status, texto=None):
    r = mock.Mock()
    r.status_code, r.ok = status, 200 <= status < 300
    r.json.return_value = {"candidates": [{"content": {"parts": [{"text": texto or ""}]}}]}
    return r


def _destaques(pt, en):
    d = blast._Destaques(pt)
    d.en = en
    return d


# ── blast: as duas línguas na mesma chamada ─────────────────────────────────────────

class TestInglesNaMesmaChamada:
    def test_o_prompt_pede_as_duas_listas(self):
        visto = {}

        def falso(cfg, prompt):
            visto["p"] = prompt
            return ["x"]
        with mock.patch.dict("os.environ", CHAVES, clear=False), \
             mock.patch.object(blast, "_carga_hoje", return_value=ZERADO), \
             mock.patch.object(blast, "_chamar", side_effect=falso):
            blast.highlights([{"title": "a"}], n=3)
        assert '"highlights_en"' in visto["p"] and "em português" in visto["p"]

    def test_chamar_le_o_ingles_da_resposta(self):
        cfg = {"kind": "gemini", "model": "m", "key": "k", "rotulo": "g"}
        texto = '{"destaques": ["Minério sobe;"], "highlights_en": ["Iron ore rises."]}'
        with mock.patch.object(blast.requests, "post", return_value=_resp(200, texto)):
            got = blast._chamar(cfg, "p")
        assert got == ["Minério sobe"] and got.en == ["Iron ore rises"]
        assert cfg["_status"] == 200

    def test_sem_ingles_na_resposta_continua_valendo_o_portugues(self):
        cfg = {"kind": "gemini", "model": "m", "key": "k", "rotulo": "g"}
        with mock.patch.object(blast.requests, "post", return_value=_resp(200, '{"destaques": ["a"]}')):
            got = blast._chamar(cfg, "p")
        assert got == ["a"] and got.en == []

    def test_ingles_da_ia_vai_para_o_resultado(self):
        with mock.patch.dict("os.environ", CHAVES, clear=False), \
             mock.patch.object(blast, "_carga_hoje", return_value=ZERADO), \
             mock.patch.object(blast, "_chamar", return_value=_destaques(["a", "b"], ["A", "B"])):
            r = blast.highlights([{"title": "x"}], n=3)
        assert r["destaques"] == ["a", "b"] and r["destaques_en"] == ["A", "B"] and r["en_fonte"] == "ia"

    def test_ingles_torto_vira_traducao_do_google_sem_ia(self):
        tr = mock.Mock()
        tr.return_value.translate.side_effect = lambda s: "\n".join("EN " + x for x in s.split("\n"))
        with mock.patch.dict("os.environ", CHAVES, clear=False), \
             mock.patch.object(blast, "_carga_hoje", return_value=ZERADO), \
             mock.patch.object(blast, "_chamar", return_value=_destaques(["a", "b"], ["só um"])), \
             mock.patch("deep_translator.GoogleTranslator", tr):
            r = blast.highlights([{"title": "x"}], n=3)
        assert r["destaques_en"] == ["EN a", "EN b"] and r["en_fonte"] == "google"


# ── blast: lotação do Google — INSISTIR em vez de desistir (24/11/2026) ────────────────
#
# Às 15:37 e 16:14 BRT de 24/11 o "Gerar tudo" saiu SEM destaques: o Google respondia "503 high
# demand" no 3.1 e no 3.5-flash-lite, nos dois projetos, e a Z.AI "429". A cadeia tentou 5 vezes
# em ~30 s e desistiu — enquanto o robô dos takes, que insiste, recebia respostas no mesmo horário.

class _Relogio:
    """Relógio falso: `dormir` avança o tempo; cada chamada à IA custa `custo` segundos."""
    def __init__(self, custo=3.0):
        self.t, self.custo, self.sonos = 0.0, custo, []

    def agora(self):
        return self.t

    def dormir(self, s):
        self.sonos.append(s)
        self.t += s


def _insistir(falso, relogio, env=None, paciencia=420, noticias=None):
    def chamar(cfg, prompt):
        relogio.t += relogio.custo
        return falso(cfg, prompt)
    noticias = noticias or [{"title": "x", "body": "<p>" + "texto " * 2000 + "</p>"}]
    with mock.patch.dict("os.environ", {**CHAVES, "GROQ_API_KEY": "kg", **(env or {})}, clear=False), \
         mock.patch.object(blast, "_carga_hoje", return_value=ZERADO), \
         mock.patch.object(blast, "PACIENCIA_S", paciencia), \
         mock.patch.object(blast, "_agora", relogio.agora), \
         mock.patch.object(blast, "_dormir", relogio.dormir), \
         mock.patch.object(blast, "_chamar", side_effect=chamar):
        return blast.highlights(noticias, n=3)


class TestInsistirAteAlguemResponder:
    def test_todas_as_portas_em_ordem_de_qualidade(self):
        with mock.patch.dict("os.environ", {**CHAVES, "GROQ_API_KEY": "kg"}, clear=False), \
             mock.patch.object(blast, "_carga_hoje", return_value=ZERADO):
            portas = blast.escolher_provedores()
        assert [p["nome"] for p in portas] == [
            "gemini_clipping", "gemini_takes", "gemini_extra1", "gemini_extra2",
            "gemini_clipping_b", "gemini_takes_b", "groq", "zai"]
        modelos = {p["nome"]: p["model"] for p in portas}
        assert modelos["gemini_extra1"] == "gemini-3.5-flash"
        assert modelos["gemini_extra2"] == "gemini-3-flash-preview"
        assert modelos["gemini_takes_b"] == modelos["gemini_clipping_b"] != modelos["gemini_takes"]

    def test_normal_a_primeira_responde_e_ninguem_espera(self):
        rel, chamados = _Relogio(), []

        def falso(cfg, prompt):
            chamados.append(cfg["nome"]); cfg["_status"] = 200
            return _destaques(["ok"], ["ok"])
        r = _insistir(falso, rel)
        assert chamados == ["gemini_clipping"] and rel.sonos == [] and r["destaques"] == ["ok"]

    def test_a_tarde_de_24_11_agora_sai_com_destaques(self):
        """O que o registro mostrou: 3.1 e 3.5-flash-lite com 503 nos dois projetos, Z.AI com 429.
        Pessimista: os modelos extras e a Groq TAMBÉM falham na 1ª volta. Na 2ª, o 3.5-flash-lite
        do projeto dos takes responde — como respondeu ao robô dos takes no mesmo horário."""
        rel, chamados = _Relogio(), []

        def falso(cfg, prompt):
            chamados.append(cfg["nome"])
            volta2 = chamados.count(cfg["nome"]) == 2
            if cfg["nome"] == "gemini_takes_b" and volta2:
                cfg["_status"] = 200
                return _destaques(["Minério sobe"], ["Iron ore up"])
            cfg["_status"] = 429 if cfg["nome"] == "zai" else 503
            return None
        r = _insistir(falso, rel)
        assert r["destaques"] == ["Minério sobe"] and r["destaques_en"] == ["Iron ore up"]
        assert r["provedor"] == "gemini 2º modelo (projeto takes)" and r["erro"] is None
        assert rel.sonos == [10]                              # esperou 10 s e passou de novo
        assert len(chamados) == 8 + 6                         # volta 1 inteira + 6 da volta 2

    def test_insiste_ate_a_paciencia_e_conta_o_que_houve(self):
        rel, chamados = _Relogio(custo=4.0), []

        def falso(cfg, prompt):
            chamados.append(cfg["nome"])
            cfg["_status"] = 429 if cfg["nome"] == "zai" else 503
            return None
        r = _insistir(falso, rel)
        assert r["destaques"] == [] and r["provedor"] is None
        assert "nenhuma IA respondeu" in r["erro"] and "HTTP 503" in r["erro"] and "HTTP 429" in r["erro"]
        assert rel.sonos[:5] == [10, 20, 30, 45, 60] and set(rel.sonos[5:]) <= {60}
        assert len(rel.sonos) >= 5                            # muitas voltas, não 30 s
        assert rel.t <= 420 + 8 * 4.0                         # e não passa da paciência (+ 1 volta)

    def test_porta_que_nao_existe_sai_da_roda(self):
        """404 (modelo aposentado) e 401/403 (chave) não passam sozinhos: não insiste nessas."""
        rel, chamados = _Relogio(), []

        def falso(cfg, prompt):
            chamados.append(cfg["nome"])
            if cfg["nome"] == "gemini_extra2":
                cfg["_status"] = 404; return None
            if cfg["nome"] == "groq":
                cfg["_status"] = 401; return None
            if chamados.count("gemini_clipping") == 3 and cfg["nome"] == "gemini_clipping":
                cfg["_status"] = 200; return _destaques(["ok"], ["ok"])
            cfg["_status"] = 503; return None
        r = _insistir(falso, rel)
        assert r["destaques"] == ["ok"]
        assert chamados.count("gemini_extra2") == 1 and chamados.count("groq") == 1
        assert chamados.count("zai") == 2                     # 503/429 = tenta de novo

    def test_rede_que_cai_nao_derruba_e_tenta_na_volta_seguinte(self):
        rel, chamados = _Relogio(), []

        def falso(cfg, prompt):
            chamados.append(cfg["nome"])
            if len(chamados) <= 8:
                raise TimeoutError("read timeout")
            cfg["_status"] = 200
            return _destaques(["ok"], ["ok"])
        r = _insistir(falso, rel)
        assert r["destaques"] == ["ok"] and rel.sonos == [10] and len(chamados) == 9

    def test_resposta_vazia_de_dois_modelos_e_aceita_sem_insistir(self):
        """'Nada relevante hoje' (JSON válido, lista vazia) é resposta: pergunta a mais um modelo e,
        se ele concorda, aceita — sem ficar 7 min insistindo."""
        rel, chamados = _Relogio(), []

        def falso(cfg, prompt):
            chamados.append(cfg["nome"]); cfg["_status"] = 200
            return _destaques([], [])
        r = _insistir(falso, rel)
        assert len(chamados) == 2 and rel.sonos == [] and r["destaques"] == []
        assert "não achou destaque relevante" in r["erro"]

    def test_groq_recebe_o_texto_encurtado(self):
        """A Groq aceita 8 mil tokens por minuto: o mesmo pedido, com o texto das notícias menor."""
        rel, tamanhos = _Relogio(), {}

        def falso(cfg, prompt):
            tamanhos[cfg["nome"]] = len(prompt)
            if cfg["nome"] == "groq":
                cfg["_status"] = 200; return _destaques(["ok"], ["ok"])
            cfg["_status"] = 503; return None
        cheio = [{"title": f"notícia {i}", "body": "<p>" + "texto longo " * 400 + "</p>"} for i in range(8)]
        r = _insistir(falso, rel, noticias=cheio)
        assert r["provedor"] == "groq (gpt-oss)"
        assert tamanhos["groq"] < tamanhos["gemini_clipping"]
        assert tamanhos["groq"] <= 14000 + len(blast.INSTRUCAO) + 800

    def test_o_motivo_do_erro_vai_para_o_registro(self):
        r = mock.Mock(); r.json.return_value = {"error": {"code": 429, "message": "concurrency limit"}}
        assert blast._motivo(r) == "concurrency limit"
        r2 = mock.Mock(); r2.json.side_effect = ValueError(); r2.text = "Too Many Requests"
        assert blast._motivo(r2) == "Too Many Requests"


# ── o e-mail novo ────────────────────────────────────────────────────────────────────

def _item(i=0, sector="SM", take="+"):
    return ClippingItem(url=f"https://www.mining.com/n{i}", title=f"Copper rallies {i}",
                        source_name="Mining.com", body="<p>Copper rose 5% on Monday.</p><p>Second paragraph.</p>",
                        matched_keywords=[], domain="www.mining.com", take=take, sector=sector)


def _model(items, cfg=None):
    return whatsapp.news_model(items, D, cfg or {})


class TestEmailNovo:
    def _eml(self, en=("Iron ore rose",), cfg=None):
        items = [_item(0), _item(1, "PP", "-")]
        cfg = cfg or {"blast_highlights": {"2026-11-24": {"destaques": ["Minério subiu"]}}}
        r = email_brand.build_brand_email(items, D, _model(items, cfg), None, None, cfg, list(en))
        assert r["eml"] and not r["error"]
        return r

    def test_envelope_de_rascunho_valido_e_sem_o_word_anexado(self):
        """Pedido do usuário (24/11): o e-mail não leva mais o Word anexado."""
        raw = self._eml()["eml"]
        assert not re.search(rb"(?<!\r)\n", raw)                       # CRLF, nunca LF solto
        msg = email.message_from_bytes(raw, policy=policy.default)
        assert msg["X-Unsent"] == "1"
        assert [p.get_filename() for p in msg.walk() if p.get_filename()] == []
        assert not any("wordprocessingml" in p.get_content_type() for p in msg.walk())
        html = next(p.get_content() for p in msg.walk() if p.get_content_type() == "text/html")
        assert not re.search(r'<img\b[^>]*src="data:', html)

    def test_coluna_fluida_ocupa_a_janela(self):
        """Pedido do usuário (24/11): o e-mail preenche a janela inteira, sem coluna fixa no meio.
        E sem max-width em tabela (o Word converte em largura fixa e a folha corta ao imprimir)."""
        html = self._eml()["html"]
        col = re.search(r'<table[^>]*class="col"[^>]*>', html).group(0)
        assert 'width="100%"' in col and "width:100%" in col
        for tag in re.findall(r"<table\b[^>]*>", html):
            assert "max-width" not in tag, tag

    def test_indice_leva_a_materia_dentro_do_email(self):
        """Pedido do usuário (24/11): 'Sector headlines' é um ÍNDICE — o título leva à matéria
        completa no próprio e-mail. Cada link interno tem a sua marca, com nome que o Word aceita
        (só letra + número), e cada matéria volta ao índice."""
        html = self._eml()["html"]
        heads = html.split("Sector headlines", 1)[1].split("Full stories", 1)[0]
        alvos_indice = re.findall(r'href="#([^"]+)"', heads)
        assert alvos_indice and set(alvos_indice) == {"story0", "story1"}
        assert not re.search(r'href="https?://', heads)                  # índice não sai do e-mail
        marcas = set(re.findall(r'<a name="([^"]+)" id="\1"></a>', html))
        for alvo in set(re.findall(r'href="#([^"]+)"', html)):
            assert alvo in marcas, alvo                                  # nenhum link interno órfão
            assert re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", alvo), alvo     # formato de marcador do Word
        stories = html.split("Full stories", 1)[1]
        assert stories.count('href="#heads"') == 2                       # "Back to headlines" em cada
        assert stories.count("Open at") == 2                             # o site da fonte continua lá

    def test_destaques_em_ingles_e_nao_em_portugues(self):
        html = self._eml()["html"]
        assert "Iron ore rose" in html and "Minério subiu" not in html

    def test_sem_ingles_a_secao_de_destaques_nao_sai(self):
        html = self._eml(en=())["html"]
        assert ">Highlights<" not in html and "Sector headlines" in html

    def test_sem_a_frase_do_topo(self):
        assert "this morning" not in self._eml()["html"]

    def test_toda_imagem_tem_largura_em_atributo(self):
        html = self._eml()["html"]
        for tag in re.findall(r"<img\b[^>]*>", html):
            assert re.search(r'\bwidth="\d+"', tag), tag

    def test_falha_vira_eml_none_sem_excecao(self):
        r = email_brand.build_brand_email([_item()], D, None, None, None, {}, [])
        assert r["eml"] is None and r["error"]


# ── o job do botão único ─────────────────────────────────────────────────────────────

class TestJobTudo:
    def _rodar(self, bl=None, email_ok=True):
        items = [_item(0), _item(1, "PP", "-")]
        bl = bl or {"destaques": ["Minério subiu"], "destaques_en": ["Iron ore rose"], "en_fonte": "ia",
                    "provedor": "gemini (projeto clipping)", "erro": None}
        visto = {}

        def wa(its, d, cfg):
            visto["cfg_html"] = cfg
            return {"html": b"<html>wa</html>", "error": None, "model": _model(its, cfg), "fonts": None}

        def em(*a, **k):
            visto["en"] = a[6]
            return ({"eml": b"EML-NOVO", "html": "<html>novo</html>", "error": None} if email_ok
                    else {"eml": None, "html": None, "error": "boom"})
        payload = [{"url": it.url, "title": it.title, "pos": i, "take": it.take, "sector": it.sector,
                    "pin": 1 if i == 0 else None} for i, it in enumerate(items)]
        with mock.patch.object(generate, "_to_item", side_effect=lambda r, f, e: items[r["pos"]]), \
             mock.patch.object(generate, "build_docx", return_value=b"DOCX"), \
             mock.patch.object(blast, "highlights", side_effect=lambda noticias, n=3: (visto.__setitem__("noticias", noticias), bl)[1]), \
             mock.patch.object(whatsapp, "build_whatsapp", side_effect=wa), \
             mock.patch.object(email_brand, "build_brand_email", side_effect=em), \
             mock.patch.object(generate, "build_eml_bytes",
                               side_effect=lambda *a, **k: visto.__setitem__("antigo", k) or b"EML-ANTIGO"), \
             mock.patch.object(generate, "build_html", return_value="<html>antigo</html>"):
            res = generate.build_all_from_payload(payload, D, {"tudo": {"on": True}})
        return res, visto

    def test_tudo_numa_rodada_com_os_destaques_novos(self):
        res, visto = self._rodar()
        assert res["docx"] == b"DOCX" and res["eml"] == b"EML-NOVO" and res["whatsapp"]
        assert visto["cfg_html"]["blast_highlights"]["2026-11-24"]["destaques"] == ["Minério subiu"]
        assert visto["en"] == ["Iron ore rose"]
        assert [n.get("pin") for n in visto["noticias"]] == [1, None]       # a ★ chega à IA

    def test_email_novo_falhou_vai_o_antigo(self):
        res, visto = self._rodar(email_ok=False)
        assert res["eml"] == b"EML-ANTIGO" and res["email_novo"] is False and res["docx"] == b"DOCX"
        assert visto["antigo"]["attach"] is False          # a rede de segurança também sai sem anexo

    def test_sem_destaque_o_resto_sai_igual(self):
        res, visto = self._rodar(bl={"destaques": [], "destaques_en": [], "en_fonte": "",
                                     "provedor": None, "erro": "todos falharam"})
        assert res["docx"] == b"DOCX" and res["eml"] == b"EML-NOVO"
        assert "2026-11-24" not in visto["cfg_html"]["blast_highlights"]

    def test_destaque_velho_nunca_entra_no_html_nem_no_email(self):
        """Pergunta do usuário (24/11): o botão espera os destaques NOVOS? Sim — e se a IA falhar,
        o HTML e o e-mail saem SEM destaques, nunca com os de ontem nem com os de uma rodada
        anterior do mesmo dia."""
        items = [_item(0)]
        velho = {"blast_highlights": {"2026-11-23": {"destaques": ["DE ONTEM"], "destaques_en": ["YESTERDAY"]},
                                      "2026-11-24": {"destaques": ["DE HOJE CEDO"], "destaques_en": ["EARLIER"]}},
                 "tudo": {"on": True}}
        visto = {}

        def wa(its, d, cfg):
            visto["hl"] = whatsapp.news_model(its, d, cfg)["highlights"]
            return {"html": None, "error": None, "model": None, "fonts": None}
        falhou = {"destaques": [], "destaques_en": [], "en_fonte": "", "provedor": None, "erro": "lotado"}
        with mock.patch.object(generate, "_to_item", side_effect=lambda r, f, e: items[0]), \
             mock.patch.object(generate, "build_docx", return_value=b"DOCX"), \
             mock.patch.object(blast, "highlights", return_value=falhou), \
             mock.patch.object(whatsapp, "build_whatsapp", side_effect=wa), \
             mock.patch.object(email_brand, "build_brand_email",
                               side_effect=lambda *a, **k: visto.__setitem__("en", a[6]) or
                               {"eml": b"E", "html": "<html></html>", "error": None}):
            generate.build_all_from_payload([{"url": items[0].url, "pos": 0}], D, velho)
        assert visto["hl"] == [] and visto["en"] == []

    def test_atualizar_sem_ia_usa_o_texto_editado_pelo_analista(self):
        """'Atualizar HTML e e-mail' na aba = job com tudo.ia=false: NÃO chama a IA e usa os
        destaques carimbados (editados) da data — português no HTML, inglês no e-mail. Inglês
        vazio num item = só aquele item é traduzido (Google, sem IA)."""
        items = [_item(0)]
        cfg = {"tudo": {"on": True, "ia": False},
               "blast_highlights": {"2026-11-24": {"destaques": ["Editado 1", "Editado 2"],
                                                   "destaques_en": ["Edited 1", ""], "editado": True}}}
        visto = {}

        def wa(its, d, c):
            visto["hl"] = whatsapp.news_model(its, d, c)["highlights"]
            return {"html": None, "error": None, "model": None, "fonts": None}
        tr = mock.Mock()
        tr.return_value.translate.side_effect = lambda s: "\n".join("EN " + x for x in s.split("\n"))
        with mock.patch.object(generate, "_to_item", side_effect=lambda r, f, e: items[0]), \
             mock.patch.object(generate, "build_docx", return_value=b"DOCX"), \
             mock.patch.object(blast, "highlights", side_effect=AssertionError("não podia chamar a IA")), \
             mock.patch("deep_translator.GoogleTranslator", tr), \
             mock.patch.object(whatsapp, "build_whatsapp", side_effect=wa), \
             mock.patch.object(email_brand, "build_brand_email",
                               side_effect=lambda *a, **k: visto.__setitem__("en", a[6]) or
                               {"eml": b"E", "html": "<html></html>", "error": None}):
            res = generate.build_all_from_payload([{"url": items[0].url, "pos": 0}], D, cfg)
        assert visto["hl"] == ["Editado 1", "Editado 2"]
        assert visto["en"] == ["Edited 1", "EN Editado 2"]           # só o vazio foi traduzido
        assert res["blast"]["destaques"] == ["Editado 1", "Editado 2"]

    def test_process_desvia_para_o_job_unico_e_devolve_pelos_caminhos_de_sempre(self):
        res = {"docx": b"D", "eml": b"E", "docx_name": "NEWS - 11242026.docx", "eml_name": "NEWS - 11242026.eml",
               "html": "<html></html>", "html_name": "NEWS - 11242026.html", "whatsapp": b"<html>w</html>",
               "whatsapp_error": None, "whatsapp_name": "NEWS - 11242026.html",
               "blast": {"destaques": ["a"], "destaques_en": ["A"], "en_fonte": "ia", "provedor": "g", "erro": None},
               "email_novo": True, "email_error": None, "items": [], "errors": []}
        job = {"id": "j1", "ref_date": "2026-11-24", "payload": [], "config": {"tudo": {"on": True}}}
        patch = {}
        with mock.patch.object(generate, "build_all_from_payload", return_value=res), \
             mock.patch.object(run_jobs, "_upload"), \
             mock.patch.object(run_jobs, "_sign", side_effect=lambda p, download=None: "u:" + p), \
             mock.patch.object(run_jobs, "patch_job", side_effect=lambda j, f: patch.update(f)):
            run_jobs.process(job)
        assert patch["status"] == "done" and patch["docx_path"] and patch["eml_path"] and patch["preview_path"]
        assert patch["config"]["blast"]["destaques_en"] == ["A"]
        assert patch["config"]["whatsapp"]["url"].endswith("whatsapp.html")
        assert patch["config"]["tudo"]["email_novo"] is True


class TestGoogleEmLote:
    """24/11/2026, prova na nuvem: 3 pedidos seguidos ao Google grátis = "too many requests" e o
    e-mail saiu sem destaques. Agora é UM pedido para todos, com novas tentativas."""
    def test_um_pedido_so_para_todos_os_destaques(self):
        tr = mock.Mock()
        tr.return_value.translate.side_effect = lambda s: "\n".join("EN " + x for x in s.split("\n"))
        with mock.patch("deep_translator.GoogleTranslator", tr):
            en, fonte = blast._ingles(["a", "b", "c"], [])
        assert en == ["EN a", "EN b", "EN c"] and fonte == "google"
        assert tr.return_value.translate.call_count == 1

    def test_recusa_do_google_ganha_nova_tentativa(self):
        tr = mock.Mock()
        respostas = iter([RuntimeError("too many requests"), "A\nB"])

        def traduz(s):
            r = next(respostas)
            if isinstance(r, Exception):
                raise r
            return r
        tr.return_value.translate.side_effect = traduz
        with mock.patch("deep_translator.GoogleTranslator", tr), mock.patch("time.sleep"):
            en, fonte = blast._ingles(["a", "b"], [])
        assert en == ["A", "B"] and tr.return_value.translate.call_count == 2

    def test_tradutor_do_clipping_vem_antes_do_google(self):
        """Só os itens SEM inglês vão ao tradutor; o que o analista escreveu fica como está."""
        tr = mock.Mock()
        with mock.patch("clipping.build._translate_via_llm", return_value=("Daily highlights", ["B en"])) as llm, \
             mock.patch("deep_translator.GoogleTranslator", tr):
            en, fonte = blast._ingles(["a", "b"], ["A do analista", ""])
        assert en == ["A do analista", "B en"] and fonte == "traducao"
        assert llm.call_args[0][1] == ["b"] and not tr.called


class TestUploadComNovaTentativa:
    """24/11/2026: um 522 do Storage derrubou um job com o Word já pronto."""
    def _resp(self, code):
        r = mock.Mock(); r.status_code = code; r.ok = 200 <= code < 300; r.text = "x"
        return r

    def test_falha_momentanea_tenta_de_novo_e_passa(self):
        with mock.patch.object(run_jobs, "_env", return_value=("https://s", "k")), \
             mock.patch("time.sleep"), \
             mock.patch.object(run_jobs.requests, "post", side_effect=[self._resp(522), self._resp(200)]) as post:
            run_jobs._upload("a/b.html", b"x", "text/html")
        assert post.call_count == 2

    def test_pedido_errado_nao_repete(self):
        with mock.patch.object(run_jobs, "_env", return_value=("https://s", "k")), \
             mock.patch("time.sleep"), \
             mock.patch.object(run_jobs.requests, "post", return_value=self._resp(400)) as post:
            with pytest.raises(RuntimeError):
                run_jobs._upload("a/b.html", b"x", "text/html")
        assert post.call_count == 1

    def test_tres_falhas_seguidas_viram_erro(self):
        with mock.patch.object(run_jobs, "_env", return_value=("https://s", "k")), \
             mock.patch("time.sleep"), \
             mock.patch.object(run_jobs.requests, "post", return_value=self._resp(503)) as post:
            with pytest.raises(RuntimeError):
                run_jobs._upload("a/b.html", b"x", "text/html")
        assert post.call_count == 3

    def test_link_assinado_tambem_tenta_de_novo(self):
        ok = self._resp(200); ok.json.return_value = {"signedURL": "/object/sign/b/a.html?token=t"}
        with mock.patch.object(run_jobs, "_env", return_value=("https://s", "k")), \
             mock.patch("time.sleep"), \
             mock.patch.object(run_jobs.requests, "post",
                               side_effect=[run_jobs.requests.exceptions.ReadTimeout(), ok]) as post:
            u = run_jobs._sign("a.html")
        assert post.call_count == 2 and u.endswith("token=t")
