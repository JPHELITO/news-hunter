"""
BOTÃO ÚNICO da aba Clipping (24/11/2026): Word + destaques do blast + HTML do WhatsApp + e-mail
novo numa rodada só — e as duas mudanças no blast que vieram junto:

  * a MESMA chamada traz os destaques em português (WhatsApp) e em inglês (e-mail) — nenhuma
    chamada de IA a mais;
  * lotação do Google (5xx) ganha uma nova tentativa, e um modelo lotado não é tentado de novo
    em outro projeto (a lotação é do modelo): a vez passa ao 2º modelo do projeto do clipping
    antes da Z.AI, que é lenta. Em 24/11 o blast caiu na Z.AI (1m41s) por falta disso.

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


# ── blast: lotação do Google ─────────────────────────────────────────────────────────

class TestLotacaoDoGoogle:
    def test_2o_modelo_do_clipping_entra_antes_da_zai(self):
        with mock.patch.dict("os.environ", CHAVES, clear=False), \
             mock.patch.object(blast, "_carga_hoje", return_value=ZERADO):
            ordem = [p["nome"] for p in blast.escolher_provedores()]
        assert ordem.index("gemini_clipping_b") > ordem.index("gemini_takes")
        assert ordem.index("gemini_clipping_b") < ordem.index("zai")

    def test_503_ganha_uma_nova_tentativa_no_mesmo_provedor(self):
        chamados = []

        def falso(cfg, prompt):
            chamados.append(cfg["nome"])
            if len(chamados) == 1:
                cfg["_status"] = 503
                return None
            cfg["_status"] = 200
            return _destaques(["ok"], ["ok"])
        with mock.patch.dict("os.environ", CHAVES, clear=False), \
             mock.patch.object(blast, "_carga_hoje", return_value=ZERADO), \
             mock.patch.object(blast, "RETENTAR_5XX_S", 0), \
             mock.patch.object(blast, "_chamar", side_effect=falso):
            r = blast.highlights([{"title": "x"}], n=3)
        assert chamados == ["gemini_clipping", "gemini_clipping"] and r["destaques"] == ["ok"]

    def test_modelo_lotado_nao_e_tentado_em_outro_projeto(self):
        """24/11: 3.1 lotado no projeto do clipping E no dos takes. Pula o dos takes, vai ao 3.5."""
        chamados = []

        def falso(cfg, prompt):
            chamados.append(cfg["nome"])
            if cfg["nome"] == "gemini_clipping":
                cfg["_status"] = 503
                return None
            cfg["_status"] = 200
            return _destaques(["ok"], ["ok"])
        with mock.patch.dict("os.environ", CHAVES, clear=False), \
             mock.patch.object(blast, "_carga_hoje", return_value=ZERADO), \
             mock.patch.object(blast, "RETENTAR_5XX_S", 0), \
             mock.patch.object(blast, "_chamar", side_effect=falso):
            r = blast.highlights([{"title": "x"}], n=3)
        assert chamados == ["gemini_clipping", "gemini_clipping", "gemini_clipping_b"]
        assert r["provedor"].startswith("gemini 2º modelo")

    def test_erro_que_nao_e_lotacao_nao_repete(self):
        chamados = []

        def falso(cfg, prompt):
            chamados.append(cfg["nome"])
            cfg["_status"] = 400
            return None
        with mock.patch.dict("os.environ", CHAVES, clear=False), \
             mock.patch.object(blast, "_carga_hoje", return_value=ZERADO), \
             mock.patch.object(blast, "RETENTAR_5XX_S", 0), \
             mock.patch.object(blast, "_chamar", side_effect=falso):
            blast.highlights([{"title": "x"}], n=3)
        assert len(chamados) == len(set(chamados))          # cada provedor uma vez só


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
        r = email_brand.build_brand_email(items, D, _model(items, cfg), None, None, cfg, list(en),
                                          b"PK-docx", "NEWS - 11242026.docx")
        assert r["eml"] and not r["error"]
        return r

    def test_envelope_de_rascunho_valido(self):
        raw = self._eml()["eml"]
        assert not re.search(rb"(?<!\r)\n", raw)                       # CRLF, nunca LF solto
        msg = email.message_from_bytes(raw, policy=policy.default)
        assert msg["X-Unsent"] == "1"
        assert [p.get_filename() for p in msg.walk() if p.get_filename()] == ["NEWS - 11242026.docx"]
        html = next(p.get_content() for p in msg.walk() if p.get_content_type() == "text/html")
        assert not re.search(r'<img\b[^>]*src="data:', html)

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
        r = email_brand.build_brand_email([_item()], D, None, None, None, {}, [], b"x", "a.docx")
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
             mock.patch.object(generate, "build_eml_bytes", return_value=b"EML-ANTIGO"), \
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
        res, _ = self._rodar(email_ok=False)
        assert res["eml"] == b"EML-ANTIGO" and res["email_novo"] is False and res["docx"] == b"DOCX"

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
