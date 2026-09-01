"""
Market Pulse v2 — o universo de instrumentos e a captura da "foto" pré-abertura.

O QUE É A FOTO
--------------
Em dias úteis gravamos o preço dos instrumentos em três cortes:

    18:00 BRT (D-1)  → ÂNCORA: o mundo no instante em que a B3 fechou
    07:00 BRT (D)    → leitura preliminar
    09:00 BRT (D)    → leitura definitiva, uma hora antes da abertura

A feature que o modelo usa é a variação OVERNIGHT — do fechamento da B3 de ontem até o
corte de hoje:

    x = preço(corte, D) / preço(18h, D-1) − 1

POR QUE NÃO A VARIAÇÃO DE 24H (mudança de 2026-08-26)
-----------------------------------------------------
Até agosto/2026 a feature era `preço(corte, D) / preço(corte, D-1)`, uma janela de 24h.
Ela embute as 8 horas do pregão de ONTEM — informação que já está dentro do fechamento
de ontem da própria ação, ou seja, já precificada no ponto de partida do gap. Em regime
calmo isso passa batido; em dia de movimento forte dentro do horário da B3 a feature diz
o CONTRÁRIO do que o mercado fez de madrugada.

Medido na AURA33 (10 pregões de agosto/2026):
    corr(variação de 24h, gap)      = 0,18
    corr(overnight puro 18h→09h,gap)= 0,87   — e o sinal certo em 10 de 10 dias

⚠️ A âncora das 18h é obrigatória: sem o snapshot de D-1 não há feature para D. É falha
fechada de propósito (`sem_dado`) — um pulse com meia janela mente sobre o que mediu.

POR QUE ESTES INSTRUMENTOS
--------------------------
A ablação do estudo (ver Market-Pulse-Research/RESEARCH_LOG.md, E7/E9/E10) mostrou que o
poder preditivo vem de mercados que negociam ENQUANTO O BRASIL DORME: a sessão asiática
inteira, quatro horas de Europa e a noite dos futuros/câmbio. Bloco por bloco, tirar
minério, metais, China ou Europa piora; tirar câmbio e petróleo melhora um pouco — ficaram
porque a regularização do ridge já os encolhe e eles contam a história do dia para o leitor.

Em 2026-08-26 entrou o bloco que faltava: o **pré-mercado dos ADRs** (VALE, SID, GGB, AUGO)
e do EWZ. É o preço da PRÓPRIA empresa, negociado em Nova York desde as 05:00 BRT — nenhum
dos outros instrumentos chega perto disso. Mais três ações asiáticas do nosso setor
(Baoshan/planos, Nippon Steel, Nine Dragons/embalagem), a prata e a inclinação da curva do
VIX. Todos com histórico horário no Yahoo, então entram JÁ TREINADOS (o backfill reconstrói).

⚠️ Mexer nesta lista invalida os pesos treinados. Se acrescentar ou remover símbolo,
rode `scripts/pulse_train.py` antes da próxima pontuação, senão o produto escalar sai
sobre um vetor diferente do que o modelo aprendeu.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone

import requests

from .prices import HEADERS, _YAHOO_HOSTS, _supa_upsert, fetch_yahoo

log = logging.getLogger(__name__)

# Corte -> hora UTC. O Brasil não tem horário de verão desde 2019, então a conta é fixa.
#   "18" é a ÂNCORA (fechamento da B3); "07" e "09" são os cortes que pontuam.
CUTS = {"07": 10, "09": 12, "18": 21}
CUTS_SCORE = ("07", "09")
CUT_BASE = "18"
B3_OPEN_UTC = 13          # 10:00 BRT

# Grupos econômicos: usados na atribuição diária ("o que explica o pulse de hoje").
# ⚠️ Os RÓTULOS aparecem na dashboard, que é client-facing e fica EM INGLÊS
# (mesma regra do heatmap e do painel antigo: BULLISH/BEARISH/NEUTRAL).
GRUPOS = {
    "Iron ore & miners":  ["FMG.AX", "BHP.AX", "RIO.AX", "AAL.L", "^AXJO"],
    "China & Asia":       ["^HSI", "000001.SS", "^N225", "600019.SS", "5401.T", "2689.HK"],
    "Metals (LME/COMEX)": ["HG=F", "GC=F", "SI=F"],
    "Europe":             ["^STOXX50E", "^GDAXI", "^FTSE", "UPM.HE", "STERV.HE"],
    "US futures":         ["ES=F", "NQ=F"],
    "Oil":                ["CL=F"],
    "FX & risk":          ["USDBRL=X", "EURUSD=X", "AUDUSD=X", "DX-Y.NYB",
                           "^VIX", "^VIX9D", "^VIX3M"],
    # O preço da própria empresa, negociado em NY antes de a B3 abrir.
    "NY pre-market":      ["VALE", "SID", "GGB", "AUGO", "EWZ"],
}
SNAPSHOT_SYMBOLS = [s for v in GRUPOS.values() for s in v]
GRUPO_DE = {s: g for g, v in GRUPOS.items() for s in v}

# Símbolos cujo valor relevante acontece FORA do pregão regular (pré-mercado de NY, que
# abre 05:00 BRT). Para eles o `regularMarketPrice` do Yahoo devolveria o fechamento de
# ontem — é preciso ler as barras estendidas. Ver `_preco_estendido`.
PREMARKET_SYMBOLS = frozenset(GRUPOS["NY pre-market"])

# As cobertas cujo gap de abertura o modelo estima. Nove na B3 (abre 10:00 BRT) e três nas
# Américas (NYSE 10:30 BRT, Bolsa Mexicana em sincronia com ela).
#
# As três de fora da B3 entraram em 2026-08-26 e são, por construção, o caso mais fácil do
# universo: elas abrem UMA HORA E MEIA depois do corte das 09h, e o driver delas — o cobre
# na COMEX, o aço nos EUA — já negociou a noite inteira. É quase um nowcast, e a medição
# confirma (walk-forward, corte 09): SCCO ic_oos 0,787 / 81,0% de acerto · TX 0,628 / 74,7%
# · GMEXICOB 0,362 / 66,7%. O SCCO é o melhor modelo de todo o produto.
#
# ⚠️ O horário de verão americano move a abertura deles para 11:30 BRT entre novembro e
# março. Continua depois do corte, então o alinhamento não muda — mas não encoste o corte
# das 09h para mais tarde sem refazer esta conta.
COMPANIES = ["VALE3.SA", "CSNA3.SA", "CMIN3.SA", "GGBR4.SA", "USIM5.SA",
             "KLBN11.SA", "SUZB3.SA", "RANI3.SA", "AURA33.SA",
             "SCCO", "TX", "GMEXICOB.MX"]

# ─────────────────── que instrumentos cada empresa pode ver ───────────────────
# POR QUE ISTO EXISTE (2026-08-26, achado pelo usuário): com os 34 instrumentos soltos, o
# ridge escolhia "drivers" sem nenhuma economia por trás — a KLBN11 (papel e celulose)
# aparecia explicada pelo COBRE, a USIM5 pelo ADR da Vale, e o maior peso do modelo próprio
# da Klabin era o DAX. Matematicamente o número estava certo (aquele instrumento realmente
# contribuiu mais naquele dia); editorialmente era indefensável, e um analista perde a
# confiança no produto na primeira linha absurda que lê.
#
# A causa é colinearidade: com 34 séries que sobem e descem juntas, o ridge distribui peso
# por acidente estatístico. E o estudo original já tinha medido isto (RESEARCH_LOG, E8):
#     1 driver 0,195 · 3 → 0,206 · 5 → 0,222 · 12 → 0,214 · 20 → 0,208
#     "Cinco drivers é o ponto ótimo; mais atrapalha."
# Ou seja: restringir não é só cosmética de explicação, é o que a parcimônia já recomendava.
#
# Cada empresa vê o BLOCO da sua commodity + o macro que atinge todo mundo. O gêmeo entra
# sempre (é o mesmo ativo econômico noutro fuso). ⚠️ Isto é curadoria editorial — a lista é
# do analista, não do modelo; mexer aqui exige re-treinar.
MACRO = ["USDBRL=X", "EURUSD=X", "DX-Y.NYB", "^VIX", "ES=F", "NQ=F", "EWZ"]
_MINERIO = ["FMG.AX", "BHP.AX", "RIO.AX", "AAL.L", "^AXJO", "^HSI", "000001.SS"]
# A Anglo fica no bloco do aço porque produz minério E carvão metalúrgico — os dois insumos
# da siderurgia. Não é um "driver alheio" como o cobre era para a Klabin; é o complexo de
# matéria-prima da própria indústria. E tirá-la custa caro: medido em 2026-08-26, o ic_oos
# cai 0,072 na TX, 0,027 na GGBR4 e 0,016 na USIM5.
_ACO = ["600019.SS", "5401.T", "^HSI", "000001.SS", "AAL.L"]
_PP = ["UPM.HE", "STERV.HE", "2689.HK", "^HSI", "000001.SS"]

DRIVERS_POR_EMPRESA = {
    "VALE3.SA":    _MINERIO,
    "CMIN3.SA":    _MINERIO,
    "CSNA3.SA":    _MINERIO + ["600019.SS"],          # integrada: minério cativo + aço
    "GGBR4.SA":    _ACO + ["CL=F"],                   # scrap-EAF, forte exposição aos EUA
    "USIM5.SA":    _ACO,                              # planos: o aço chinês é o que manda
    "TX":          _ACO + ["CL=F"],                   # LatAm/EUA
    "AURA33.SA":   ["GC=F", "SI=F"],                  # ouro, e só
    "SCCO":        ["HG=F", "^HSI", "000001.SS"],
    "GMEXICOB.MX": ["HG=F", "^HSI", "000001.SS"],
    "SUZB3.SA":    _PP,
    "KLBN11.SA":   _PP,
    "RANI3.SA":    _PP,
}


def instrumentos_de(empresa: str) -> list[str]:
    """Os instrumentos que o modelo desta empresa pode usar: o bloco dela + macro + gêmeo."""
    base = DRIVERS_POR_EMPRESA.get(empresa, [])
    escolhidos = list(dict.fromkeys(list(base) + MACRO))     # sem repetir, ordem estável
    g = GEMEO.get(empresa)
    if g and g not in escolhidos:
        escolhidos.append(g)
    return [s for s in SNAPSHOT_SYMBOLS if s in set(escolhidos)]


# O papel GÊMEO de cada coberta em Nova York. É o mesmo ativo econômico negociando num
# fuso que abre antes: às 09:00 BRT o pré-mercado americano já roda há quatro horas.
# Serve para duas coisas:
#   1. no treino, o gêmeo da empresa nunca é descartado por cobertura curta (o AUGO só
#      existe desde jul/2025, e ainda assim é o melhor driver que a AURA33 tem);
#   2. na explicação do painel, dá para dizer "o ADR aponta +1,2%" com todas as letras.
GEMEO = {
    "VALE3.SA":  "VALE",
    "CSNA3.SA":  "SID",
    "GGBR4.SA":  "GGB",
    "AURA33.SA": "AUGO",
}

# ─────────────────────────── régua de publicação ───────────────────────────
# Piso de qualidade para PUBLICAR um número. Abaixo dele a empresa sai como "no signal".
#
# POR QUE ISTO EXISTE: o cliente julga o produto pelo PIOR cartão visível. Publicar um
# papel cujo modelo é essencialmente ruído com a mesma cara de outro que acerta 80% dos
# dias desconta a autoridade que o segundo construiu. Dizer "no reliable signal" é a
# informação honesta, e ela vale mais que um chute.
#
# A INTENÇÃO, que não muda: publicar só onde o modelo acerta a direção em torno de 60% das
# vezes ou mais. Um IC de ~0,29 corresponde a isso na nossa amostra.
#
# COMO CALIBRAR: o limiar tem de cair num VÃO da distribuição de ic_oos, não colado num
# valor — a 0,01 de distância a empresa entra e sai a cada re-treino semanal. Medido em
# 2026-08-26 com a configuração de produção (janela overnight + painel macro + drivers
# curados + 12 empresas), os dois cortes juntos, em ordem:
#     0,810 0,757 0,732 0,688 0,680 0,650 0,617 0,603 0,515 0,465 0,462 0,451 0,446
#     0,422 0,408 0,408 0,391 | [VÃO DE 0,110] 0,281 0,252 0,244 0,232 0,120 0,036
#
# A curadoria de drivers abriu um vão de 0,110 onde antes havia 0,06 — as duas populações
# ficaram nitidamente separadas, e não por acaso: sem instrumentos alheios para agarrar, o
# modelo de quem NÃO tem driver econômico não consegue mais fabricar correlação. 0,33 fica
# no meio, com folga de ~0,05 para cada lado.
#
# Fora ficam KLBN11 (0,281/0,244), SUZB3 (0,252/0,232) e RANI3 (0,120/0,036) — os três de
# papel e celulose. Não é coincidência: falta o preço DIÁRIO de celulose, que no mundo só
# existe no futuro SHFE de Xangai e ainda não tem histórico nosso para treinar (o coletor
# está acumulando; ver hunter/pulse_sina.py). É o buraco conhecido do grupo, não um defeito.
#
# ⚠️ Recalibre olhando a lista nova sempre que um re-treino mover a distribuição. O limiar é
# decisão de PRODUTO (quão confiante é preciso estar para mostrar um número), não um
# parâmetro do modelo — e o teste `test_o_limiar_cai_no_maior_vao` trava o critério.
#
# Histórico: 0,20 (janela de 24h) → 0,25 (janela overnight) → 0,27 (painel) → 0,29 (as três
# das Américas) → 0,33 (drivers curados). A qualidade subiu, e a fronteira subiu junto.
IC_MIN_PUBLICAR = 0.33

# Nome de "empresa" reservado, em pulse_model, para a linha do PAINEL: um único jogo de
# pesos treinado com as nove empilhadas (alvo padronizado pelo σ de cada uma). O que a
# produção publica é a MÉDIA entre o modelo da empresa e o painel — medido em 2026-08-26,
# ela bate o per-name em 9 de 9 empresas (+0,052 de IC), com o ganho concentrado nos nomes
# fracos, que param de sobreajustar o próprio ruído. Ver scripts/pulse_pooling.py.
PANEL_KEY = "_PANEL_"

# Empresas barradas À MÃO, independente do que o treino diga. Serve para o caso em que
# sabemos algo que o ic_oos não captura. Hoje está vazio: o gate por ic_oos já barra
# SUZB3 e RANI3 sozinho, com o motivo saindo do próprio número.
SEM_SINAL: dict[str, str] = {}

# Preço com mais de 30h sem negociar = mercado de origem em feriado. O Yahoo devolve o
# fechamento anterior como se fosse de hoje; sem esta trava, o feriado viraria "variação 0".
MAX_IDADE_H = 30.0


def cut_agora() -> str:
    """
    Qual corte este run representa, pela hora UTC. 10h→'07', 12h→'09', 21h→'18'.

    ⚠️ O DESEMPATE IMPORTA. Os crons disparam ~10 min ANTES do alvo, então a hora cheia
    do run pode cair exatamente no meio de dois cortes: às 11h UTC, '07' (10h) e '09'
    (12h) estão à mesma distância. Empatando para o corte mais CEDO, o run das 11:50
    gravaria a foto das 09h por cima da foto das 07h — e o modelo das 07h passaria a ser
    pontuado com dados de duas horas depois. Desempatamos para o corte mais TARDIO, que
    é a direção em que o run de fato está indo. (Hoje isso não acontece só porque o
    Actions costuma atrasar o cron; contar com o atraso não é um plano.)
    """
    h = datetime.now(timezone.utc).hour
    return min(CUTS, key=lambda c: (abs(CUTS[c] - h), -CUTS[c]))


# ── Janela em que cada corte pode ser fotografado ─────────────────────────────
# POR QUE ISTO EXISTE (2026-09-01). A trava anti-look-ahead do pulse_daily era de MÃO
# ÚNICA: recusava foto tarde demais, e nada impedia foto CEDO demais. Entre 27/08 e
# 01/09 o cron do GitHub passou a disparar em horas arbitrárias (00:16, 02:55, 17:14,
# 04:32 UTC) e o estrago foi silencioso, porque `cut_agora()` sempre devolve ALGUM corte:
# um run às 00:16 UTC mapeia para '07' — a foto sai às 21h BRT da véspera e é gravada com
# o rótulo "07:00 BRT".
#
# E não foi só lixo entrando: como `sessao_hoje()` é UTC−3, o run das 00:16 do dia 27
# carimbou sessão 26/08 e **sobrescreveu por upsert a foto boa** que tinha sido tirada às
# 10:19 daquele mesmo dia. Dado certo virou dado errado, sem uma linha de log.
#
# Daí a janela ter as DUAS bordas. Antes: o cron dispara 10 min antes do alvo, então 60
# min de tolerância cobre folgado qualquer adiantamento legítimo. Depois: para os cortes
# que pontuam, o limite continua sendo a abertura da B3 (a borda que já existia); para a
# âncora do fechamento, vale até o fim do dia — depois das 21h UTC o pregão acabou e o
# preço não anda mais, então chegar tarde nela é inofensivo.
TOLERANCIA_ANTES_MIN = 60


def janela(cut: str, agora: datetime | None = None) -> tuple[datetime, datetime]:
    """(início, fim) em UTC dentro dos quais a foto de `cut` é válida."""
    if cut not in CUTS:
        raise ValueError(f"corte inválido: {cut!r} (esperado {list(CUTS)})")
    agora = agora or datetime.now(timezone.utc)
    alvo = agora.replace(hour=CUTS[cut], minute=0, second=0, microsecond=0)
    inicio = alvo - timedelta(minutes=TOLERANCIA_ANTES_MIN)
    fim = (agora.replace(hour=B3_OPEN_UTC, minute=0, second=0, microsecond=0)
           if cut in CUTS_SCORE else
           agora.replace(hour=23, minute=59, second=59, microsecond=0))
    return inicio, fim


def fora_da_janela(cut: str, agora: datetime | None = None) -> str | None:
    """Motivo pelo qual fotografar `cut` AGORA seria inválido — ou None se está na hora."""
    agora = agora or datetime.now(timezone.utc)
    inicio, fim = janela(cut, agora)
    if agora < inicio:
        falta = int((inicio - agora).total_seconds() // 60)
        return (f"são {agora:%H:%M} UTC e a janela do corte {cut} só abre {inicio:%H:%M} "
                f"(faltam {falta} min): a foto seria de outro momento do dia, e gravá-la "
                f"com o rótulo {cut} sobrescreveria a foto boa se ela já existir")
    if agora >= fim:
        return (f"são {agora:%H:%M} UTC e a janela do corte {cut} fechou {fim:%H:%M}: "
                f"a foto seria pós-abertura da B3 (look-ahead)")
    return None


def sessao_hoje() -> str:
    """Data do pregão em Brasília (YYYY-MM-DD)."""
    return (datetime.now(timezone.utc) - timedelta(hours=3)).date().isoformat()


def _preco_estendido(symbol: str) -> tuple[float, int] | None:
    """
    Último preço de um símbolo INCLUINDO pré/pós-mercado, com o instante em que a barra
    fechou. Devolve (preço, epoch do fechamento da barra) ou None.

    Usa barras de 5 minutos e só aceita barra JÁ FECHADA (`ts + 300 <= agora`) — a mesma
    disciplina anti-look-ahead do backfill, que usa barras horárias (`ts + 3600 <= corte`).
    A granularidade difere de propósito: ao vivo queremos o pré-mercado fresco, e a
    assimetria é conservadora (a produção enxerga um pouco MAIS que o treino, nunca menos).
    """
    agora = int(time.time())
    for tentativa in range(3):
        host = _YAHOO_HOSTS[tentativa % len(_YAHOO_HOSTS)]
        try:
            r = requests.get(f"{host}/v8/finance/chart/{symbol}"
                             f"?range=1d&interval=5m&includePrePost=true",
                             headers=HEADERS, timeout=15)
            if r.status_code in (429, 401, 403):
                time.sleep(1.2 * (tentativa + 1))
                continue
            r.raise_for_status()
            res = (r.json().get("chart", {}).get("result") or [None])[0]
            if not res:
                return None
            ts = res.get("timestamp") or []
            closes = (res.get("indicators", {}).get("quote") or [{}])[0].get("close") or []
            melhor = None
            for t, c in zip(ts, closes):
                if c is None:
                    continue
                fecha_em = int(t) + 300
                if fecha_em <= agora:
                    melhor = (float(c), fecha_em)
            return melhor
        except Exception as e:
            if tentativa == 2:
                log.warning("  pré-mercado %s falhou: %s", symbol, e)
            time.sleep(0.8 * (tentativa + 1))
    return None


def capture(cut: str, dry_run: bool = False) -> dict[str, float]:
    """
    Tira a foto: busca o preço corrente dos instrumentos e grava em pulse_snapshot.
    Devolve {símbolo: preço} do que foi capturado com sucesso.

    Os símbolos comuns vêm de `fetch_yahoo` (paralelo, rotação de host, backoff no 429 que
    o Yahoo devolve a IP de datacenter). Os de pré-mercado precisam das barras estendidas,
    então são buscados à parte por `_preco_estendido`.
    """
    if cut not in CUTS:
        raise ValueError(f"corte inválido: {cut!r} (esperado {list(CUTS)})")
    session = sessao_hoje()
    agora = datetime.now(timezone.utc)

    comuns = [s for s in SNAPSHOT_SYMBOLS if s not in PREMARKET_SYMBOLS]
    dados = fetch_yahoo(comuns)

    precos, rows, velhos, faltando = {}, [], [], []

    def _guarda(sym: str, preco: float) -> None:
        precos[sym] = preco
        rows.append({
            "session_date": session,
            "symbol":       sym,
            "cut":          cut,
            "price":        preco,
            "captured_at":  agora.isoformat(),
        })

    for sym in comuns:
        d = dados.get(sym)
        if not d or d.get("price") is None:
            faltando.append(sym)
            continue
        qt = d.get("quote_time")
        if qt:
            idade_h = (agora - datetime.fromtimestamp(int(qt), timezone.utc)).total_seconds() / 3600
            if idade_h > MAX_IDADE_H:
                velhos.append(f"{sym}({idade_h:.0f}h)")
                continue
        _guarda(sym, float(d["price"]))

    # Pré-mercado: sequencial de propósito (são poucos e o endpoint de 5m é mais pesado).
    for sym in SNAPSHOT_SYMBOLS:
        if sym not in PREMARKET_SYMBOLS:
            continue
        r = _preco_estendido(sym)
        if r is None:
            faltando.append(sym)
            continue
        preco, fecha_em = r
        idade_h = (agora.timestamp() - fecha_em) / 3600
        if idade_h > MAX_IDADE_H:
            velhos.append(f"{sym}({idade_h:.0f}h)")
            continue
        _guarda(sym, preco)

    if faltando:
        log.warning("pulse snapshot %s: sem preço para %s", cut, faltando)
    if velhos:
        log.warning("pulse snapshot %s: descartados por idade (feriado?) %s", cut, velhos)
    log.info("pulse snapshot %s/%s: %d de %d instrumentos",
             session, cut, len(rows), len(SNAPSHOT_SYMBOLS))

    if dry_run:
        for r in rows:
            print(f"  {r['symbol']:<12} {r['price']:>12.4f}")
        return precos
    _supa_upsert("pulse_snapshot", rows)
    return precos


def _supa_env() -> tuple[str, str]:
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    if not url or not key:
        raise RuntimeError("SUPABASE_URL / SUPABASE_SERVICE_KEY ausentes no ambiente")
    return url, key
