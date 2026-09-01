"""
Market Pulse — coletor dos preços que o Yahoo não tem: minério de Cingapura e os
futuros da China (celulose, vergalhão, bobina a quente, minério de Dalian, carvão).

POR QUE ISTO EXISTE
-------------------
O snapshot v2 lê minério e aço de SEGUNDA MÃO — pelas mineradoras australianas e pelos
índices asiáticos. E não lê celulose de jeito nenhum, que é exatamente o buraco de
SUZB3/KLBN11/RANI3 (o benchmark de celulose é semanal: PIX/FOEX sai nas terças).

Estes são os preços de primeira mão, e são de graça:

    FEF.SGX   minério 62% Fe CFR China, em USD — o ÚNICO minério vivo às 07h e às 09h BRT
              (a sessão T do SGX fecha 09:00 BRT em ponto)
    SP.SHFE   celulose (NBSK) em Xangai — o único preço de celulose que tica todo dia no
              mundo; correlação de 95% com o NBSK CIF China
    RB.SHFE   vergalhão · HC.SHFE bobina a quente · I.DCE minério de Dalian · JM.DCE carvão

A sessão diurna da China fecha às 04:00 BRT, então nos dois cortes da manhã o número já é
do MESMO dia, com 3 a 5 horas de idade.

⚠️ ESTES SÍMBOLOS AINDA NÃO ENTRAM NO MODELO. A Sina só serve o preço AO VIVO; o histórico
dela é diário, e o fechamento diário de um contrato chinês inclui a sessão NOTURNA, que vai
até 12:00 BRT — depois dos nossos cortes. Usar esse fechamento para reconstruir o passado
seria look-ahead, o mesmo pecado que já custou 12% de IC inflado na barra horária do Yahoo.
Então: coletamos ao vivo a partir de agora, com o carimbo de hora do próprio payload, e o
treino incorpora quando houver massa. Como `features_do_corte` reindexa pelas colunas de
SNAPSHOT_SYMBOLS, estes símbolos ficam guardados sem contaminar nada.

⚠️ API não-oficial (é o backend do site da Sina, no ar há mais de uma década). A resposta é
posicional e vem em GBK. Por isso cada preço é conferido contra a faixa do dia antes de ser
aceito: se o layout mudar, o coletor devolve nada em vez de gravar lixo com cara de preço.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

import requests

from .prices import _supa_upsert

log = logging.getLogger(__name__)

SINA_URL = "https://hq.sinajs.cn/list="
# O Referer é obrigatório: sem ele a Sina devolve 403.
SINA_HEADERS = {
    "Referer": "https://finance.sina.com.cn",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
}

# nosso nome -> código da Sina.
#   hf_*  = futuro estrangeiro (layout A)
#   nf_*  = futuro doméstico chinês, contrato contínuo (layout B)
# ⚠️ nf_I0 é com "I" MAIÚSCULO — nf_i0 devolve string vazia, sem erro.
SINA_SYMBOLS = {
    "FEF.SGX":  "hf_FEF",
    # 伦镍 = níquel da LME, em USD/t (conferido no campo 13 do payload, 2026-09-01).
    # É o ÚNICO níquel gratuito que achamos: no Yahoo, NICKEL / NID=F / SHNI=F / LN=F /
    # ^LME dão 404, sem exceção. Entra pelo mesmo canal que já trazia o minério de
    # Cingapura, negocia 24h e por isso está vivo às 06h BRT, que é a hora do blast.
    "NID.LME":  "hf_NID",
    "SP.SHFE":  "nf_SP0",
    "RB.SHFE":  "nf_RB0",
    "HC.SHFE":  "nf_HC0",
    "I.DCE":    "nf_I0",
    "JM.DCE":   "nf_JM0",
}

_DATA_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _num(v: str) -> float | None:
    try:
        f = float(v)
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None


def _parse(codigo: str, campos: list[str]) -> dict | None:
    """
    Extrai (preço, máxima, mínima, carimbo) de uma linha da Sina.

    Os dois layouts, medidos em 2026-08-26:
      hf_ (15 campos):  0=último  3=máx  5=mín  6=hora HH:MM:SS  12=data  13=nome
      nf_ (44 campos):  0=nome  1=hora HHMMSS  2=abertura  3=máx  4=mín  8=último
                        (a data aparece mais adiante; achamos por formato, não por posição)

    Aceitar preço fora da faixa [mín, máx] do próprio dia seria aceitar que o layout mudou.
    """
    prev = None
    if codigo.startswith("hf_"):
        if len(campos) < 13:
            return None
        preco, alta, baixa = _num(campos[0]), _num(campos[3]), _num(campos[5])
        hora = campos[6] if len(campos) > 6 else ""
        data = next((c for c in campos if _DATA_RE.match(c)), "")
        # campo 7 = FECHAMENTO DA SESSÃO ANTERIOR. Identificado em 2026-09-01 e conferido
        # por fora: o hf_FEF trazia 99,340 e o nosso minério 62% do Trading Economics tinha
        # fechado o dia em 99,33 — mesmo número, fonte independente. É o que permite dar a
        # VARIAÇÃO DO DIA sem esperar acumular série nossa.
        prev = _num(campos[7]) if len(campos) > 7 else None
    else:
        if len(campos) < 12:
            return None
        preco, alta, baixa = _num(campos[8]), _num(campos[3]), _num(campos[4])
        bruta = campos[1] if len(campos) > 1 else ""
        hora = (f"{bruta[:2]}:{bruta[2:4]}:{bruta[4:6]}"
                if len(bruta) == 6 and bruta.isdigit() else bruta)
        data = next((c for c in campos if _DATA_RE.match(c)), "")

    if preco is None:
        return None
    if alta and baixa and not (baixa * 0.98 <= preco <= alta * 1.02):
        log.warning("  sina %s: preço %.3f fora da faixa do dia [%.3f, %.3f] — "
                    "layout mudou? descartado", codigo, preco, baixa, alta)
        return None
    return {"price": preco, "stamp": f"{data} {hora}".strip(), "prev": prev}


def fetch_sina() -> dict[str, dict]:
    """{nosso_nome: {'price': float, 'stamp': 'AAAA-MM-DD HH:MM:SS'}} — uma requisição só."""
    codigos = ",".join(SINA_SYMBOLS.values())
    try:
        r = requests.get(SINA_URL + codigos, headers=SINA_HEADERS, timeout=20)
        r.raise_for_status()
        texto = r.content.decode("gbk", errors="replace")
    except Exception as e:
        log.warning("sina: requisição falhou (%s)", e)
        return {}

    por_codigo: dict[str, dict] = {}
    for linha in texto.splitlines():
        if "=" not in linha or '"' not in linha:
            continue
        codigo = linha.split("=", 1)[0].replace("var hq_str_", "").strip()
        corpo = linha.split('"')[1]
        if not corpo:
            continue                      # símbolo inexistente devolve aspas vazias
        p = _parse(codigo, corpo.split(","))
        if p:
            por_codigo[codigo] = p

    out = {nome: por_codigo[cod] for nome, cod in SINA_SYMBOLS.items() if cod in por_codigo}
    faltando = [n for n in SINA_SYMBOLS if n not in out]
    if faltando:
        log.warning("sina: sem preço para %s", faltando)
    return out


def capture(cut: str, session_date: str, dry_run: bool = False) -> dict[str, float]:
    """Grava os preços da Sina em pulse_snapshot, no mesmo corte da foto do Yahoo."""
    dados = fetch_sina()
    if not dados:
        return {}
    agora = datetime.now(timezone.utc).isoformat()
    rows = [{
        "session_date": session_date,
        "symbol":       nome,
        "cut":          cut,
        "price":        d["price"],
        "captured_at":  agora,
    } for nome, d in dados.items()]

    log.info("pulse sina %s/%s: %d de %d instrumentos (%s)",
             session_date, cut, len(rows), len(SINA_SYMBOLS),
             ", ".join(f"{n}={d['price']:g}@{d['stamp']}" for n, d in dados.items()))

    if dry_run:
        return {n: d["price"] for n, d in dados.items()}
    _supa_upsert("pulse_snapshot", rows)
    return {n: d["price"] for n, d in dados.items()}
