"""HTML do clipping para o WhatsApp (clipping/whatsapp.py) — a 4ª saída do mesmo job.

O que estes testes travam:
  • o CONTEÚDO sai dos mesmos itens e pelas mesmas regras do Word (parser de corpo, sem os
    "bullets" do Platts, tradução do Valor, setor/take saneados, publicações, analistas);
  • imagem reduzida e embutida (o arquivo abre offline), com teto de peso;
  • o motor de desenho cai na cópia guardada se o GitHub não responder;
  • NADA aqui derruba o Word: falha vira {"html": None, "error": ...} e o job segue.

Rodar: python -m pytest tests/test_clipping_whatsapp.py -v
"""
import base64
import io
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from clipping import whatsapp as W
from clipping.build import ClippingItem


def _item(**kw):
    base = dict(url="https://www.mining.com/x/", title="Iron ore slides", source_name="Mining.com",
                body="<p>Prices fell as traders weighed demand.</p>", matched_keywords=[],
                domain="www.mining.com", take="-", sector="SM")
    base.update(kw)
    return ClippingItem(**base)


def _png(w, h, color=(255, 85, 0)):
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", (w, h), color).save(b, "PNG")
    return "data:image/png;base64," + base64.b64encode(b.getvalue()).decode()


# ── conteúdo ──────────────────────────────────────────────────────────────────

def test_corpo_passa_pelo_parser_do_word():
    it = _item(body="<p>Primeiro parágrafo.</p><h3>Subtítulo</h3><blockquote>Uma citação aqui</blockquote>"
                    "<ul><li>item um</li><li>item dois</li></ul><p><strong>Negrito sozinho</strong></p>")
    m = W.news_model([it], date(2026, 11, 13), {})
    tipos = [b["t"] for b in m["items"][0]["blocks"]]
    assert tipos[:5] == ["p", "h3", "q", "li", "li"], tipos
    assert m["items"][0]["blocks"][0]["x"] == "Primeiro parágrafo."


def test_platts_perde_os_bullets_como_no_word():
    it = _item(url="https://core.spglobal.com/x", domain="core.spglobal.com", source_name="S&P Platts",
               body="<ul><li>destaque em negrito</li></ul><p>Asian HRC prices held firm.</p>")
    tipos = [b["t"] for b in W.news_model([it], date(2026, 11, 13), {})["items"][0]["blocks"]]
    assert "li" not in tipos and "p" in tipos


def test_bilingue_leva_a_traducao_sem_repetir_imagem():
    it = _item(url="https://valor.globo.com/x", domain="valor.globo.com", source_name="Valor Econômico",
               title="Vale adota medidas", body=f'<p>Texto original.</p><img src="{_png(600, 300)}">',
               translated_title="Vale adopts measures", translated_body=f'<p>Translated text.</p><img src="{_png(600, 300)}">')
    x = W.news_model([it], date(2026, 11, 13), {})["items"][0]
    assert x["tr_title"] == "Vale adopts measures"
    assert [b["t"] for b in x["tr_blocks"]] == ["p"]                 # sem a imagem 2×
    assert any(b["t"] == "img" for b in x["blocks"])


def test_setor_e_take_saneados():
    it = _item(sector="CEMENT", take="?")
    x = W.news_model([it], date(2026, 11, 13), {})["items"][0]
    assert x["sector"] == "SM" and x["take"] == "="


def test_destaques_sao_os_do_dia_do_clipping():
    cfg = {"blast_highlights": {"2026-11-12": {"destaques": ["ontem"]},
                                "2026-11-13": {"destaques": ["hoje 1", " ", "hoje 2"]}}}
    assert W.news_model([_item()], date(2026, 11, 13), cfg)["highlights"] == ["hoje 1", "hoje 2"]
    assert W.news_model([_item()], date(2026, 11, 14), cfg)["highlights"] == []


def test_publicacoes_earnings_e_analistas():
    cfg = {"recent_publications": [{"name": "Vale: Update", "sector": "SM", "link": "https://x/y",
                                    "date": "2026-11-09", "companies": ["VALE3"]},
                                   {"title": "Suzano: Pulp", "sector": "PP", "pdf_url": "https://x/z"},
                                   {"name": "  "}],
           "earnings_review": {"on": False, "items": [{"name": "não sai"}]},
           "analysts": [{"name": "Daniel Sasson, CFA", "email": "d@x.com"}, {"name": ""}]}
    m = W.news_model([_item()], date(2026, 11, 13), cfg)
    assert [p["name"] for p in m["publications"]] == ["Vale: Update", "Suzano: Pulp"]
    assert m["publications"][1]["link"] == "https://x/z" and m["publications"][1]["sector"] == "PP"
    assert m["earnings"] is None
    assert [a["name"] for a in m["analysts"]] == ["Daniel Sasson, CFA"]
    cfg["earnings_review"] = {"on": True, "label": "3Q26 Review", "items": [{"name": "Aura: 3Q26"}]}
    assert W.news_model([_item()], date(2026, 11, 13), cfg)["earnings"]["label"] == "3Q26 Review"


def test_mensagem_de_abertura_so_quando_ligada():
    cfg = {"intro": {"on": True, "html": "<b>Bom dia</b>"}}
    assert W.news_model([_item()], date(2026, 11, 13), cfg)["intro_html"] == "<b>Bom dia</b>"
    cfg["intro"]["on"] = False
    assert W.news_model([_item()], date(2026, 11, 13), cfg)["intro_html"] == ""


# ── imagens ───────────────────────────────────────────────────────────────────

def test_imagem_reduzida_e_embutida():
    it = _item(body=f'<p>Chart below.</p><img src="{_png(2000, 800)}"><img src="{_png(50, 20)}">')
    imgs = [b for b in W.news_model([it], date(2026, 11, 13), {})["items"][0]["blocks"] if b["t"] == "img"]
    assert len(imgs) == 1                                           # o ícone 50×20 fica de fora
    assert imgs[0]["w"] == 1000 and imgs[0]["h"] == 400
    assert imgs[0]["src"].startswith(("data:image/png;base64,", "data:image/jpeg;base64,"))


def test_orcamento_de_imagens(monkeypatch):
    monkeypatch.setattr(W, "IMG_BUDGET", 1)                         # nenhuma cabe
    it = _item(body=f'<p>x</p><img src="{_png(800, 400)}">')
    assert not [b for b in W.news_model([it], date(2026, 11, 13), {})["items"][0]["blocks"] if b["t"] == "img"]


# ── motor de desenho ──────────────────────────────────────────────────────────

def test_copia_guardada_existe_e_tem_o_renderizador():
    lib = (W.VENDOR_DIR / "reports-lib.js").read_text(encoding="utf-8")
    assert "renderNews" in lib and "newsDefaults" in lib
    assert (W.VENDOR_DIR / "unbounded.woff2").stat().st_size > 10000
    assert (W.VENDOR_DIR / "manrope.woff2").stat().st_size > 10000


def test_kit_cai_na_copia_guardada_sem_rede(monkeypatch):
    import requests

    def boom(*a, **k):
        raise requests.ConnectionError("sem rede")
    monkeypatch.setattr(requests, "get", boom)
    kit = W._kit()
    assert kit["src"] == "cópia guardada" and "renderNews" in kit["lib"] and kit["display"] and kit["body"]


def _kit_local(monkeypatch):
    real = W._kit

    def local():
        import requests

        def boom(*a, **k):
            raise requests.ConnectionError("teste offline")
        monkeypatch.setattr(requests, "get", boom)
        return real()
    monkeypatch.setattr(W, "_kit", local)


@pytest.mark.skipif(W._node() is None, reason="sem Node (nem o do Playwright)")
def test_html_ponta_a_ponta(monkeypatch):
    _kit_local(monkeypatch)
    items = [_item(), _item(url="https://www.fastmarkets.com/y", domain="www.fastmarkets.com", source_name="Fastmarkets",
                            title="Pulp prices <ease>", take="+", sector="PP",
                            body="<p>BHKP prices eased.</p><p>Second.</p><p>Third.</p><p>Fourth.</p>")]
    res = W.build_whatsapp(items, date(2026, 11, 13), {"recent_publications": [{"name": "Vale: Update", "link": "https://x"}]})
    assert res["error"] is None, res["error"]
    html = res["html"].decode("utf-8")
    assert "STEEL &amp; MINING" in html and "Daily News" in html and "Full stories" in html
    assert "Pulp prices &lt;ease&gt;" in html                       # título escapado, nunca HTML cru
    assert "Read the full story" in html                            # o resto do corpo atrás do <details>
    assert "NaN" not in html and "undefined" not in html
    assert "<script src" not in html and "<link" not in html        # abre offline


def test_falha_nao_derruba_e_diz_por_que(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("Node sumiu")
    monkeypatch.setattr(W, "render", boom)
    _kit_local(monkeypatch)
    res = W.build_whatsapp([_item()], date(2026, 11, 13), {})
    assert res["html"] is None and "Node sumiu" in res["error"]


# ── ligação com a geração e com o job ─────────────────────────────────────────

def test_build_from_payload_nao_gera_whatsapp_por_padrao(monkeypatch):
    from clipping import generate

    def nao_chame(*a, **k):
        raise AssertionError("build_whatsapp não devia rodar")
    monkeypatch.setattr(W, "build_whatsapp", nao_chame)
    payload = [{"url": "https://www.mining.com/x/", "title": "Iron ore slides", "source_name": "Mining.com",
                "take": "-", "sector": "SM", "pos": 0, "body": "<p>Prices fell.</p>"}]
    res = generate.build_from_payload(payload, date(2026, 8, 11), fetch=False)
    assert "whatsapp" not in res and res["docx_name"] == "NEWS - 08112026.docx"


def test_build_from_payload_com_whatsapp(monkeypatch):
    from clipping import generate
    monkeypatch.setattr(W, "build_whatsapp", lambda items, d, cfg: {"html": b"<html></html>", "error": None, "kit": "x"})
    payload = [{"url": "https://www.mining.com/x/", "title": "Iron ore slides", "source_name": "Mining.com",
                "take": "-", "sector": "SM", "pos": 0, "body": "<p>Prices fell.</p>"}]
    res = generate.build_from_payload(payload, date(2026, 8, 11), fetch=False, whatsapp=True)
    assert res["whatsapp"] == b"<html></html>" and res["whatsapp_name"] == "NEWS - 08112026.html"
    assert res["docx"] and res["eml"]                               # o Word e o e-mail saem iguais


def test_job_devolve_o_link_dentro_do_config(monkeypatch):
    from clipping import run_jobs
    up = []
    monkeypatch.setattr(run_jobs, "_upload", lambda path, data, ct: up.append((path, ct)))
    monkeypatch.setattr(run_jobs, "_sign", lambda path, download=None: f"https://signed/{path}?d={download}")
    job = {"id": "j1", "config": {"intro": {"on": True}, "blast": {"x": 1}}}
    cfg = run_jobs._with_whatsapp(job, "clippings/j1", {"whatsapp": b"<html>" * 300, "whatsapp_name": "NEWS - 11132026.html"})
    assert up == [("clippings/j1/whatsapp.html", "text/html; charset=utf-8")]
    assert cfg["whatsapp"]["url"].endswith("?d=NEWS - 11132026.html") and cfg["whatsapp"]["name"] == "NEWS - 11132026.html"
    assert cfg["intro"] == {"on": True} and cfg["blast"] == {"x": 1}          # o resto do config fica


def test_job_sem_html_registra_o_motivo(monkeypatch):
    from clipping import run_jobs
    monkeypatch.setattr(run_jobs, "_upload", lambda *a: (_ for _ in ()).throw(AssertionError("não sobe nada")))
    cfg = run_jobs._with_whatsapp({"config": {}}, "b", {"whatsapp": None, "whatsapp_error": "Node não encontrado"})
    assert cfg["whatsapp"]["error"] == "Node não encontrado" and "url" not in cfg["whatsapp"]


def test_job_upload_que_falha_nao_derruba(monkeypatch):
    from clipping import run_jobs

    def boom(*a):
        raise RuntimeError("403")
    monkeypatch.setattr(run_jobs, "_upload", boom)
    cfg = run_jobs._with_whatsapp({"config": {}}, "b", {"whatsapp": b"<html>", "whatsapp_name": "n.html"})
    assert cfg["whatsapp"]["error"].startswith("upload:")
