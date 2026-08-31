"""Testes do risquinho das commodities (hunter/prices.py: as 5 janelas do carrossel).

O que estes testes TRAVAM, e por quê — todos vêm de defeitos que estavam NO AR em
31/08/2026 e que o analista viu na tela antes de qualquer log acusar:

  • que D e WoW NÃO desenham a mesma curva. As janelas eram contadas em PONTOS e as duas
    pediam 22 — resultado: a mesma figura, byte a byte, nas 29 commodities. Agora cada
    botão é um zoom (14d / 30d / 92d / YTD / 365d).
  • que YTD e YoY devolvem None quando a série não alcança a âncora, em vez de INVENTAR
    uma base. HRC China e Rebar Turkey só têm histórico desde 23/06/2026 e o código caía
    no primeiro ponto da série: o cartão publicava "YTD +1,83%" e "YoY +1,83%" quando os
    dois eram, na verdade, "desde 23 de junho".
  • que as âncoras são por DATA, nunca por contagem de pontos. Numa série SEMANAL
    (celulose, pellet premium) "5 pontos atrás" não é uma semana; num feriado a janela
    escorrega. Este é o erro que mais custou: ele não quebra nada, só mede outra coisa.
  • que sábado e domingo não entram na série — nem no append, nem herdados. Copper/Gold
    gravavam `assessed_at = hoje` inclusive no fim de semana (2 pontos falsos por semana:
    em um ano os 250 pregões do YoY virariam ~8 meses), e o backfill do Yahoo com
    `range=max` trazia barras MENSAIS carimbadas no dia 1º, que cai em fds 2 vezes em 7.
  • que uma janela mais funda do que a série mostra TUDO em vez de encolher até a âncora
    — senão o "MoM" de uma série curta vira do tamanho do "WoW" e os dois voltam a
    desenhar a mesma coisa (foi exatamente o que aconteceu no 1º corte deste conserto).
  • que `range=max` nunca mais é usado no histórico de commodity do Yahoo.

Rodar: python -m pytest tests/test_commodity_spark.py -v
"""
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import hunter.prices as prices


def _ep(d: dt.date) -> int:
    return int(dt.datetime(d.year, d.month, d.day, tzinfo=dt.timezone.utc).timestamp())


def serie_diaria(fim: dt.date, n: int, f=lambda i: 100.0 + i) -> list[list]:
    """n pregões (seg-sex) terminando em `fim`, valor dado por f(índice)."""
    dias, d = [], fim
    while len(dias) < n:
        if d.weekday() < 5:
            dias.append(d)
        d -= dt.timedelta(days=1)
    dias.reverse()
    return [[_ep(x), f(i)] for i, x in enumerate(dias)]


def serie_semanal(fim: dt.date, semanas: int) -> list[list]:
    """Série SEMANAL preenchida dia a dia — é como a celulose e o pellet premium chegam:
    o preço muda 1x/semana e os outros 4 pregões repetem o mesmo número."""
    pts, preco = [], 500.0
    d = fim - dt.timedelta(days=7 * semanas)
    while d <= fim:
        if d.weekday() < 5:
            if d.weekday() == 0:
                preco += 3.0
            pts.append([_ep(d), preco])
        d += dt.timedelta(days=1)
    return pts


SEG = dt.date(2026, 8, 31)          # segunda-feira


# ───────────────────── D e WoW deixaram de ser o mesmo desenho ─────────────────────
def test_d_e_wow_desenham_curvas_diferentes():
    sp = prices.commodity_periods(serie_diaria(SEG, 400))
    assert sp["d"]["s"] != sp["w"]["s"], "D e WoW voltaram a compartilhar a mesma figura"
    assert len(sp["d"]["s"]) < len(sp["w"]["s"]) < len(sp["m"]["s"])


def test_as_cinco_janelas_sao_cinco_zooms():
    sp = prices.commodity_periods(serie_diaria(SEG, 400))
    formas = [tuple(sp[k]["s"]) for k in ("d", "w", "m", "ytd", "y")]
    assert len(set(formas)) == 5, "duas janelas caíram na mesma curva"


def test_serie_semanal_tambem_separa_d_de_wow():
    # o caso que motivou o piso de valores distintos: em 1 mês a semanal tem 4 preços
    # repetidos em degraus, e sem o alargamento D e WoW convergiam para a mesma figura
    sp = prices.commodity_periods(serie_semanal(SEG, 60))
    assert sp["d"]["s"] != sp["w"]["s"]
    assert len(set(sp["d"]["s"])) >= 3, "o risquinho da semanal virou uma escadinha chata"


# ───────────────────── sem histórico ⇒ "—", nunca um número inventado ─────────────────
def test_ytd_e_yoy_sao_none_quando_a_serie_nao_alcanca():
    curta = serie_diaria(SEG, 48)                  # ~2 meses, como HRC China e Rebar Turkey
    sp = prices.commodity_periods(curta)
    assert sp["ytd"]["c"] is None and sp["y"]["c"] is None
    assert sp["d"]["c"] is not None and sp["w"]["c"] is not None


def test_periodo_sem_variacao_ainda_desenha():
    # o cartão nunca pode ficar sem gráfico: o analista quer a figura mesmo sem o número
    sp = prices.commodity_periods(serie_diaria(SEG, 48))
    for k in ("d", "w", "m", "ytd", "y"):
        assert len(sp[k]["s"]) >= 3, f"janela {k} ficou sem forma"


def test_ytd_usa_o_ultimo_fechamento_do_ano_anterior():
    serie = serie_diaria(SEG, 400)
    datas = [dt.datetime.fromtimestamp(p[0], dt.timezone.utc).date() for p in serie]
    do_ano_passado = [v for d, (_, v) in zip(datas, serie) if d.year < SEG.year]
    esperado = round((serie[-1][1] / do_ano_passado[-1] - 1) * 100, 2)
    assert prices.commodity_periods(serie)["ytd"]["c"] == esperado


# ───────────────────── âncoras por DATA, não por contagem de pontos ─────────────────
def test_wow_de_serie_semanal_mede_sete_dias_e_nao_cinco_pontos():
    serie = prices.serie_com_o_dia(serie_semanal(SEG, 60), None, None)
    datas = [dt.datetime.fromtimestamp(p[0], dt.timezone.utc).date() for p in serie]
    alvo = datas[-1] - dt.timedelta(days=7)
    base = [v for d, (_, v) in zip(datas, serie) if d <= alvo][-1]
    assert prices.commodity_periods(serie)["w"]["c"] == round((serie[-1][1] / base - 1) * 100, 2)


def test_d_e_o_pregao_anterior_mesmo_atravessando_o_fim_de_semana():
    serie = serie_diaria(SEG, 60)          # termina na segunda; o anterior é a sexta
    sp = prices.commodity_periods(serie)
    assert sp["d"]["c"] == round((serie[-1][1] / serie[-2][1] - 1) * 100, 2)


def test_feriado_no_meio_nao_desloca_a_ancora():
    serie = serie_diaria(SEG, 400)
    sem_feriado = [p for p in serie
                   if dt.datetime.fromtimestamp(p[0], dt.timezone.utc).date()
                   != SEG - dt.timedelta(days=14)]
    a, b = prices.commodity_periods(serie), prices.commodity_periods(sem_feriado)
    assert a["m"]["c"] == b["m"]["c"], "um pregão a menos mudou a âncora do MoM"


# ───────────────────── o trecho medido (`b`) aponta para a âncora ─────────────────
def test_b_marca_onde_comeca_o_trecho_medido():
    sp = prices.commodity_periods(serie_diaria(SEG, 400))
    for k in ("d", "w", "m"):
        assert 0 <= sp[k]["b"] < len(sp[k]["s"])
    # quanto mais curto o período, mais perto do fim começa o destaque
    assert sp["d"]["b"] / len(sp["d"]["s"]) > sp["m"]["b"] / len(sp["m"]["s"])


def test_janela_mais_funda_que_a_serie_mostra_tudo():
    # 48 pregões: a janela de 92 dias do MoM não cabe. Tem de desenhar a série inteira,
    # não encolher até a âncora de 30 dias (era o que fazia MoM virar cópia do WoW).
    sp = prices.commodity_periods(serie_diaria(SEG, 48))
    assert len(sp["m"]["s"]) > len(sp["w"]["s"])
    assert sp["m"]["s"] != sp["w"]["s"]


# ───────────────────── fim de semana nunca entra na série ─────────────────────
def test_domingo_nao_acrescenta_ponto():
    serie = serie_diaria(SEG - dt.timedelta(days=3), 10)      # termina na sexta
    assert prices.serie_com_o_dia(serie, 999.0, "2026-08-30") == serie


def test_ponto_de_fim_de_semana_herdado_e_varrido():
    serie = serie_diaria(SEG - dt.timedelta(days=3), 10)
    sujo = serie + [[_ep(dt.date(2026, 8, 29)), 777.0]]        # sábado herdado
    assert prices.serie_com_o_dia(sujo, None, None) == serie


def test_dia_util_puxa_o_fim_de_semana_para_a_sexta():
    assert prices._dia_util(dt.date(2026, 8, 29)) == dt.date(2026, 8, 28)   # sábado
    assert prices._dia_util(dt.date(2026, 8, 30)) == dt.date(2026, 8, 28)   # domingo
    assert prices._dia_util(dt.date(2026, 8, 31)) == dt.date(2026, 8, 31)   # segunda


def test_dedup_por_data_e_nao_por_epoch():
    serie = serie_diaria(SEG, 10)
    igual = prices.serie_com_o_dia(serie, 42.0, SEG.isoformat())
    assert len(igual) == len(serie) and igual[-1][1] == 42.0


# ───────────────────── carimbos e regressões de fonte ─────────────────────
def test_spark_carimba_asof_e_versao():
    sp = prices.commodity_periods(serie_diaria(SEG, 400))
    assert sp["asof"] == SEG.isoformat() and sp["v"] == prices.SPARK_VER


def test_historico_do_yahoo_nunca_usa_range_max():
    fonte = Path(prices.__file__).read_text(encoding="utf-8")
    trecho = fonte[fonte.index("def update_commodity_history"):
                   fonte.index("def serie_com_o_dia")]
    assert 'range_="max"' not in trecho, "range=max devolve barras MENSAIS (interval ignorado)"
    assert 'range_="10y"' in trecho
