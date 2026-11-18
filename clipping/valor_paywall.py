"""Sinal de PAYWALL do Valor Econômico — fonte ÚNICA para o keep-alive (`clipping/keepalive.py`)
e para o leitor do clipping (`clipping/reader.py`).

Medido ao vivo em 18/11/2026 (Playwright, navegador novo por matéria; 15 medições: sessão real da
nuvem × sem cookie × só um GLBID falso, em Empresas / Finanças / Mundo):

- O Valor manda a matéria INTEIRA no HTML e um script do paywall a ESCONDE depois do carregamento
  (de 0,4 s até 7 s). Qualquer checagem feita "na hora" vê o texto todo — até sem login. Foi assim
  que o keep-alive passou a dizer "sessão viva" para QUALQUER cookie GLBID.
- O sinal confiável é o CORTE: `.paywall-hidden` (layout novo) ou `.paywall.hide-all-content` /
  `.wall.protected-content` visível (layout antigo, ainda em parte das matérias). Sessão boa: NUNCA
  apareceu (9 de 9, texto inteiro). Sem login / GLBID falso: SEMPRE apareceu (6 de 6, 0–1 parágrafo).
- ⚠️ O cabeçalho "não logado" (`.menu-login--not-logged`) NÃO serve de veredito: com a sessão boa
  ele apareceu 1 vez em 9, sem o texto ser escondido. "Logado" (`.menu-login--logged`) só serve de
  atalho para parar de esperar.
- ⚠️ `.mc-article-body.cropped` está em TODA matéria (logado ou não): NÃO serve de sinal.
"""
from __future__ import annotations

CROPPED_JS = """() => {
  const w = document.querySelector('.wall.protected-content');
  return !!document.querySelector('.paywall-hidden')
      || !!document.querySelector('.paywall.hide-all-content')
      || !!(w && w.offsetHeight > 0 && getComputedStyle(w).display !== 'none');
}"""

LOGGED_JS = "() => !!document.querySelector('.menu-login--logged')"

_WAIT_JS = "() => (" + CROPPED_JS + ")() || (" + LOGGED_JS + ")()"


def wait_cropped(page, timeout_ms: int = 9_000, settle_ms: int = 1_000):
    """O paywall escondeu o texto desta matéria? True = cortada · False = liberada ·
    None = não deu para ler a página. Nunca levanta exceção.

    Espera até `timeout_ms` pelo CORTE ou pelo cabeçalho "logado" (o corte mais tardio medido
    apareceu 7 s depois do carregamento). Se parou pelo "logado", confere de novo depois de
    `settle_ms` (garante que não era o paywall chegando junto). Sem nenhum dos dois no prazo, vale
    o estado da página — inclusive com o cabeçalho dizendo "não logado", que às vezes mente."""
    try:
        page.wait_for_function(_WAIT_JS, timeout=timeout_ms)
    except Exception:
        pass
    try:
        if not page.evaluate(CROPPED_JS) and page.evaluate(LOGGED_JS):
            page.wait_for_timeout(settle_ms)
        return bool(page.evaluate(CROPPED_JS))
    except Exception:
        return None


def use_body(dom_cropped: bool, cropped) -> bool:
    """Para o leitor do clipping: o corpo lido pode ir para o clipping? Não, se o DOM ou a espera
    acusaram corte (o texto que sobrou é só o lide — ou o HTML que o paywall ainda ia esconder)."""
    return not (bool(dom_cropped) or cropped is True)
