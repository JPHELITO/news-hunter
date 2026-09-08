"""Tabela de artigo → IMAGEM (print fiel), sem abrir o site.

Por que existe (2026-09-08): a Fastmarkets parou de publicar as tabelas de preço como PNG
e passou a mandá-las como `<table>` do CKEditor. Reconstruí-las como tabela de verdade
funcionou, mas saiu com cara de colagem de Excel no Word/e-mail. Aqui a tabela volta a ser
IMAGEM — só que renderizada por NÓS, com o CSS que a **própria Fastmarkets manda junto com
a matéria** (`<style>` embutido no `content` da API). Resultado: idêntico ao site, sem
precisar navegar até a página (nada de sessão, paywall ou espera de SPA).

É o mesmo destino de antes (um `<img>` no meio do corpo), então Word, e-mail e prévia já
sabem lidar: `build._fetch_image` lê data-URI e `eml._inline_images` converte p/ `cid:`.

Falhou (sem navegador, tabela exótica, timeout)? Devolve o HTML **intacto** — aí a tabela
segue pelo caminho de tabela de verdade, que continua funcionando. Nunca perde a tabela.
"""
from __future__ import annotations

import base64
import logging
import re

log = logging.getLogger(__name__)

# Largura da caixa: a tabela cresce com o conteúdo até este teto e só então quebra linha.
# 760px @2x = 1520px de PNG; o e-mail limita a 620px (eml._IMG_MAX_W) e o Word encaixa na
# coluna de 4,33in — nos dois casos sobra resolução, a imagem sai nítida.
_MAX_W    = 760
_SCALE    = 2
_TIMEOUT  = 20_000

# Base mínima: o CSS do CMS não declara fonte (o site herda a dele). Sem isto o Chromium
# renderiza em Times 16px e a tabela sai enorme e com cara de documento antigo.
_BASE_CSS = """<style>
  html,body{margin:0;padding:0;background:#fff}
  body{font:13px/1.35 Arial,"Liberation Sans",Helvetica,sans-serif;color:#000;
       -webkit-font-smoothing:antialiased;display:inline-block;padding:10px}
  figure.table,figure{margin:0 !important;max-width:%dpx !important}
  table{border-collapse:collapse;max-width:%dpx}
  td,th{font-size:13px}
  img{display:none}                 /* tabela é texto: nada de imagem remota travando o print */
</style>""" % (_MAX_W, _MAX_W)

# Só entra quando a matéria NÃO trouxe o CSS do CMS (caminho do DOM, artigo antigo).
# Imita o que a Fastmarkets manda: cabeçalho roxo, sem grade vertical, fio claro entre linhas.
_FALLBACK_CSS = """<style>
  table{border:2px solid #ccc;background:#fff}
  thead th{color:#6a1b9a;font-weight:bold;padding:6px 10px;border-bottom:2px solid #dcdcdc}
  tbody td{padding:4px 8px;border-top:1px solid #e2e2e2}
</style>"""


def _img_html(png: bytes, alt: str = "") -> str:
    b64 = base64.b64encode(png).decode("ascii")
    return f'<img src="data:image/png;base64,{b64}" alt="{alt}" class="reader-img">'


def tables_to_images(raw_html: str, *, source: str = "") -> str:
    """Troca cada `<table>` (com a `<figure>` que a envolve) por um `<img>` data-URI.

    Devolve o HTML original sem tocar se não houver tabela, se o navegador não subir ou se
    o print falhar — a tabela nunca some por causa desta etapa.
    """
    if not raw_html or "<table" not in raw_html.lower():
        return raw_html
    try:
        from bs4 import BeautifulSoup  # type: ignore
    except ImportError:
        return raw_html

    soup = BeautifulSoup(raw_html, "lxml")

    # Alvo = a <figure> que embrulha a tabela (é ela que o CSS do CMS estiliza) ou, se não
    # houver, a própria <table>. Tabela dentro de tabela vai junto com a de fora.
    alvos = []
    for tbl in soup.find_all("table"):
        if tbl.find_parent("table") is not None:
            continue
        fig = tbl.find_parent("figure")
        alvos.append(fig if fig is not None else tbl)
    if not alvos:
        return raw_html

    # O CSS que a Fastmarkets manda junto com a matéria (é o que dá o roxo do cabeçalho).
    css_cms = "".join(str(s) for s in soup.find_all("style"))
    css = _BASE_CSS + (css_cms or _FALLBACK_CSS)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log.info("table_shot: playwright ausente — tabela segue como tabela (%s)", source)
        return raw_html

    feitos = 0
    try:
        with sync_playwright() as pw:
            # --no-sandbox: o runner do Actions roda como root dentro do container do
            # Playwright; --disable-dev-shm-usage evita o /dev/shm minúsculo do Docker.
            browser = pw.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
            try:
                page = browser.new_page(viewport={"width": _MAX_W + 60, "height": 900},
                                        device_scale_factor=_SCALE)
                page.set_default_timeout(_TIMEOUT)
                for alvo in alvos:
                    try:
                        page.set_content(f"<body>{css}{alvo!s}</body>", wait_until="load")
                        el = page.query_selector("figure") or page.query_selector("table")
                        if el is None:
                            continue
                        png = el.screenshot()
                        if not png or len(png) < 500:
                            continue
                        legenda = alvo.find("figcaption")
                        alt = legenda.get_text(" ", strip=True) if legenda else ""
                        alvo.replace_with(BeautifulSoup(_img_html(png, alt), "lxml").img)
                        feitos += 1
                    except Exception as e:
                        log.info("table_shot: uma tabela não virou imagem (%s) — segue como tabela", e)
            finally:
                browser.close()
    except Exception as e:
        log.info("table_shot: navegador indisponível (%s) — tabelas seguem como tabela", e)
        return raw_html

    if not feitos:
        return raw_html
    log.debug("table_shot: %d tabela(s) viraram imagem (%s)", feitos, source)
    return str(soup)


# Regex usada pelo teste e por quem quiser saber se um corpo já tem print de tabela.
TABLE_IMG_RE = re.compile(r'<img[^>]+src="data:image/png;base64,', re.I)
