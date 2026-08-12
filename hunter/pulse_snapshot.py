"""
Market Pulse v2 — o universo de instrumentos e a captura da "foto" pré-abertura.

O QUE É A FOTO
--------------
Duas vezes por dia útil (07:00 e 09:00 de Brasília) gravamos o preço de 23 instrumentos
globais. A feature que o modelo usa é a variação de 24h dentro do MESMO corte:

    x = preço(hoje, corte) / preço(ontem, mesmo corte) − 1

Comparar dois pontos do mesmo horário resolve dois problemas de uma vez: não precisa de
ajuste por provento (a razão cancela) e garante que as duas pontas da janela já existiam
antes da B3 abrir — nada de look-ahead.

POR QUE ESTES INSTRUMENTOS
--------------------------
A ablação do estudo (ver Market-Pulse-Research/RESEARCH_LOG.md, E7/E9/E10) mostrou que o
poder preditivo vem de mercados que negociam ENQUANTO O BRASIL DORME: a sessão asiática
inteira, quatro horas de Europa e a noite dos futuros/câmbio. Bloco por bloco, tirar
minério, metais, China ou Europa piora; tirar câmbio e petróleo melhora um pouco — ficaram
porque a regularização do ridge já os encolhe e eles contam a história do dia para o leitor.

⚠️ Mexer nesta lista invalida os pesos treinados. Se acrescentar ou remover símbolo,
rode `scripts/pulse_train.py` antes da próxima pontuação, senão o produto escalar sai
sobre um vetor diferente do que o modelo aprendeu.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

from .prices import _supa_upsert, fetch_yahoo

log = logging.getLogger(__name__)

# Corte -> hora UTC. O Brasil não tem horário de verão desde 2019, então a conta é fixa.
CUTS = {"07": 10, "09": 12}
B3_OPEN_UTC = 13          # 10:00 BRT

# Grupos econômicos: usados na atribuição diária ("o que explica o pulse de hoje").
# ⚠️ Os RÓTULOS aparecem na dashboard, que é client-facing e fica EM INGLÊS
# (mesma regra do heatmap e do painel antigo: BULLISH/BEARISH/NEUTRAL).
GRUPOS = {
    "Iron ore & miners":  ["FMG.AX", "BHP.AX", "RIO.AX", "AAL.L", "^AXJO"],
    "China & Asia":       ["^HSI", "000001.SS", "^N225"],
    "Metals (LME/COMEX)": ["HG=F", "GC=F"],
    "Europe":             ["^STOXX50E", "^GDAXI", "^FTSE", "UPM.HE", "STERV.HE"],
    "US futures":         ["ES=F", "NQ=F"],
    "Oil":                ["CL=F"],
    "FX & risk":          ["USDBRL=X", "EURUSD=X", "AUDUSD=X", "DX-Y.NYB", "^VIX"],
}
SNAPSHOT_SYMBOLS = [s for v in GRUPOS.values() for s in v]
GRUPO_DE = {s: g for g, v in GRUPOS.items() for s in v}

# As 9 cobertas negociadas na B3 (o alvo é o gap de abertura de cada uma).
COMPANIES = ["VALE3.SA", "CSNA3.SA", "CMIN3.SA", "GGBR4.SA", "USIM5.SA",
             "KLBN11.SA", "SUZB3.SA", "RANI3.SA", "AURA33.SA"]

# Empresas em que o modelo NÃO tem sinal validado: no holdout o erro dele é MAIOR que o de
# simplesmente prever zero (Irani +10,9%, Suzano +4,7%). Publicam "sem sinal", nunca número.
# Só sair desta lista com evidência nova de `scripts/pulse_check.py`.
# (texto em inglês: vai direto para o tooltip da dashboard)
SEM_SINAL = {
    "SUZB3.SA": "no global driver found (IC -0.04 over a 205-session holdout)",
    "RANI3.SA": "illiquid name, no global driver (IC 0.01 over a 205-session holdout)",
}

# Preço com mais de 30h sem negociar = mercado de origem em feriado. O Yahoo devolve o
# fechamento anterior como se fosse de hoje; sem esta trava, o feriado viraria "variação 0".
MAX_IDADE_H = 30.0


def cut_agora() -> str:
    """Qual corte este run representa, pela hora UTC. 10h→'07', 12h→'09' (o mais próximo)."""
    h = datetime.now(timezone.utc).hour
    return min(CUTS, key=lambda c: abs(CUTS[c] - h))


def sessao_hoje() -> str:
    """Data do pregão em Brasília (YYYY-MM-DD)."""
    return (datetime.now(timezone.utc) - timedelta(hours=3)).date().isoformat()


def capture(cut: str, dry_run: bool = False) -> dict[str, float]:
    """
    Tira a foto: busca o preço corrente dos 23 instrumentos e grava em pulse_snapshot.
    Devolve {símbolo: preço} do que foi capturado com sucesso.

    Reusa `fetch_yahoo` de propósito: ele já roda em paralelo, rotaciona query1/query2 e
    faz backoff no 429 que o Yahoo devolve para IP de datacenter (GitHub Actions).
    """
    if cut not in CUTS:
        raise ValueError(f"corte inválido: {cut!r} (esperado {list(CUTS)})")
    session = sessao_hoje()
    agora = datetime.now(timezone.utc)
    dados = fetch_yahoo(SNAPSHOT_SYMBOLS)

    precos, rows, velhos, faltando = {}, [], [], []
    for sym in SNAPSHOT_SYMBOLS:
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
        precos[sym] = float(d["price"])
        rows.append({
            "session_date": session,
            "symbol":       sym,
            "cut":          cut,
            "price":        float(d["price"]),
            "captured_at":  agora.isoformat(),
        })

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
