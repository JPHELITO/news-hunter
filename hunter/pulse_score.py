"""
Market Pulse v2 — pontuação diária. Python puro, sem numpy.

O modelo é um ridge linear, então pontuar é um produto escalar sobre os instrumentos:

    x  = preço(corte, hoje) / preço(18h, ontem) − 1     (variação OVERNIGHT)
    z  = (x − mu) / sd                           (mu/sd vêm SÓ da janela de treino)
    ŷ  = Σ β·z                                   (gap esperado, em fração)

    score       = 100 · clip( ŷ / (2·σ_ŷ), −1, +1 )
    confidence  = logística( b0 + w1·|ŷ|/σ_ŷ + w2·concordância + w3·completude )
    atribuição  = β·z por instrumento, somado por grupo econômico

A soma das atribuições é EXATAMENTE ŷ — não é aproximação. Foi por isso que o linear venceu
o random forest no estudo: empataram em acurácia (0,272 contra 0,267) e só um explica.

Falha FECHADA por princípio: sem preço de hoje ou de ontem para algum instrumento, a empresa
sai com status 'sem_dado'. Um Market Pulse mutilado é pior que um Market Pulse ausente.
"""
from __future__ import annotations

import logging
import math
import os
from datetime import date, datetime, timezone

import requests

from .prices import _supa_upsert
from .pulse_snapshot import (COMPANIES, CUT_BASE, GRUPO_DE, IC_MIN_PUBLICAR,
                             SEM_SINAL)

log = logging.getLogger(__name__)

# Maior distância aceitável entre a âncora do fechamento e o pregão sendo pontuado.
# Cobre fim de semana (3) e o feriado mais longo da B3, o Carnaval (sexta→quarta = 4).
MAX_IDADE_ANCORA_DIAS = 5


def _supa_get(path: str) -> list[dict]:
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    if not url or not key:
        raise RuntimeError("SUPABASE_URL / SUPABASE_SERVICE_KEY ausentes no ambiente")
    r = requests.get(f"{url}/rest/v1/{path}",
                     headers={"apikey": key, "Authorization": f"Bearer {key}"}, timeout=30)
    r.raise_for_status()
    return r.json()


def _clip(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _logistica(z: float) -> float:
    if z < -30:
        return 0.0
    if z > 30:
        return 1.0
    return 1.0 / (1.0 + math.exp(-z))


def features(cut: str) -> tuple[str, dict[str, float], datetime | None]:
    """
    Monta a variação OVERNIGHT de cada instrumento: do fechamento da B3 de ontem
    (âncora das 18h) até o corte de hoje.

    Devolve (data do pregão, {símbolo: x}, instante da captura).

    ⚠️ A âncora é do PREGÃO ANTERIOR, não do mesmo dia — por isso a busca da data base
    exige `session_date < hoje`. Numa segunda-feira isso pega a sexta sozinho; num
    feriado, o último pregão que existiu. É por comparação de datas justamente para
    não precisar de calendário.
    """
    datas = _supa_get(f"pulse_snapshot?cut=eq.{cut}&select=session_date"
                      f"&order=session_date.desc&limit=50")
    hoje = next((r["session_date"] for r in datas), None)
    if not hoje:
        raise RuntimeError(f"corte {cut}: pulse_snapshot vazio")

    base = _supa_get(f"pulse_snapshot?cut=eq.{CUT_BASE}&session_date=lt.{hoje}"
                     f"&select=session_date&order=session_date.desc&limit=1")
    ontem = next((r["session_date"] for r in base), None)
    if not ontem:
        raise RuntimeError(
            f"corte {cut}: não achei âncora do fechamento (cut={CUT_BASE}) antes de {hoje}. "
            f"A captura das 18:00 BRT rodou ontem?")

    # ⚠️ Âncora velha demais = janela de vários dias se passando por overnight. Não dá para
    # distinguir "feriado longo" de "a captura das 18h falhou" só pela data, então limitamos
    # pelo maior feriado plausível da B3 (Carnaval: sexta a quarta = 4 dias corridos) e
    # falhamos fechado acima disso. Sem esta trava, uma noite sem captura sairia como um
    # pulse normal — com a feature medindo 48h e ninguém percebendo.
    idade = (date.fromisoformat(hoje) - date.fromisoformat(ontem)).days
    if idade > MAX_IDADE_ANCORA_DIAS:
        raise RuntimeError(
            f"corte {cut}: a âncora mais recente é de {ontem}, {idade} dias antes de {hoje} "
            f"(limite {MAX_IDADE_ANCORA_DIAS}). A captura das 18:00 BRT falhou? "
            f"Recuperar com: python -m hunter.pulse_daily --cut {CUT_BASE}")
    if idade > 1:
        log.info("pulse features %s: âncora de %d dias atrás (%s) — fim de semana ou feriado",
                 cut, idade, ontem)

    hoje_rows = _supa_get(f"pulse_snapshot?cut=eq.{cut}&session_date=eq.{hoje}"
                          f"&select=symbol,price,captured_at&limit=2000")
    base_rows = _supa_get(f"pulse_snapshot?cut=eq.{CUT_BASE}&session_date=eq.{ontem}"
                          f"&select=symbol,price&limit=2000")
    p_hoje = {r["symbol"]: float(r["price"]) for r in hoje_rows}
    p_base = {r["symbol"]: float(r["price"]) for r in base_rows}

    captura = None
    for r in hoje_rows:
        if r.get("captured_at"):
            ts = datetime.fromisoformat(r["captured_at"].replace("Z", "+00:00"))
            captura = ts if captura is None else max(captura, ts)

    x = {}
    for sym, ph in p_hoje.items():
        po = p_base.get(sym)
        if po:
            x[sym] = ph / po - 1.0
    log.info("pulse features %s: overnight de %s (18h) até %s (%sh), %d instrumentos",
             cut, ontem, hoje, cut, len(x))
    return hoje, x, captura


def pontuar_empresa(modelo: dict, x: dict[str, float]) -> dict | None:
    """Aplica um modelo (linha de pulse_model) ao vetor de features do dia."""
    coefs, mu, sd = modelo["coefs"], modelo["mu"], modelo["sd"]
    sigma = float(modelo["sigma_pred"]) or 1e-9

    faltando = [s for s in coefs if s not in x]
    if faltando:
        log.warning("  %s: sem dado para %s -> sem_dado", modelo["company"], faltando)
        return None

    contrib, y = {}, 0.0
    for sym, beta in coefs.items():
        z = (x[sym] - float(mu[sym])) / (float(sd[sym]) or 1e-12)
        c = float(beta) * z
        contrib[sym] = c
        y += c

    grupos: dict[str, float] = {}
    for sym, c in contrib.items():
        g = GRUPO_DE.get(sym, "Outros")
        grupos[g] = grupos.get(g, 0.0) + c

    score = 100.0 * _clip(y / (2.0 * sigma), -1.0, 1.0)

    # Confiança: mesma fórmula calibrada em scripts/pulse_train.py. Só magnitude e
    # concordância — completude é sempre 1,0 aqui (se faltasse instrumento, teríamos
    # devolvido None lá em cima), então não é informação.
    conf = None
    w = modelo.get("conf_w") or {}
    if w:
        sinais = [1 if x[s] > 0 else (-1 if x[s] < 0 else 0) for s in coefs]
        concordancia = abs(sum(sinais) / len(sinais)) if sinais else 0.0
        z = (float(w.get("intercept", 0.0))
             + float(w.get("mag", 0.0)) * _clip(abs(y) / sigma, 0, 4)
             + float(w.get("conc", 0.0)) * concordancia)
        conf = 100.0 * _logistica(z)

    # 6 casas: o front mostra 2, e sobra precisão de sobra para a soma das atribuições
    # continuar batendo com o gap esperado em qualquer arredondamento de exibição.
    drivers = sorted(contrib.items(), key=lambda kv: -abs(kv[1]))[:5]
    return {
        "gap_expected": round(100.0 * y, 6),                       # publicado em %
        "score": round(score, 1),
        "confidence": round(conf, 1) if conf is not None else None,
        "attribution": {
            "grupos": {g: round(100.0 * v, 6) for g, v in
                       sorted(grupos.items(), key=lambda kv: -abs(kv[1]))},
            "drivers": [[s, round(100.0 * v, 6)] for s, v in drivers],
        },
    }


def _sem_sinal_por_que(empresa: str, modelo: dict | None) -> str | None:
    """
    A empresa deve sair como "no signal"? Devolve o motivo (em inglês, vai para o tooltip
    do cliente) ou None se ela pode publicar número.

    Duas portas, nesta ordem:
      1. barra manual (`SEM_SINAL`) — para o que sabemos e o número não mostra;
      2. piso de qualidade: o `ic_oos` do walk-forward tem de alcançar IC_MIN_PUBLICAR.

    A segunda é a que importa no dia a dia, e é DERIVADA do treino: quando o modelo de uma
    empresa melhora (feature nova, regime que volta), ela reentra sozinha no re-treino
    seguinte; quando piora, sai sozinha. Nada de lista mantida à mão.
    """
    if empresa in SEM_SINAL:
        return SEM_SINAL[empresa]
    if modelo is None:
        return "no model trained yet for this name"
    ic = modelo.get("ic_oos")
    if ic is None:
        return "model not validated out-of-sample yet"
    if float(ic) < IC_MIN_PUBLICAR:
        return (f"model too weak to publish (out-of-sample IC {float(ic):+.2f}, "
                f"floor {IC_MIN_PUBLICAR:.2f})")
    return None


def score(cut: str, dry_run: bool = False) -> list[dict]:
    """Pontua todas as cobertas no corte e grava em pulse_daily."""
    sessao, x, captura = features(cut)
    modelos = {m["company"]: m for m in
               _supa_get(f"pulse_model?cut=eq.{cut}&select=*")}
    if not modelos:
        raise RuntimeError(f"corte {cut}: pulse_model vazio — rode scripts/pulse_train.py antes")

    agora = datetime.now(timezone.utc).isoformat()
    rows = []
    for empresa in COMPANIES:
        base = {"session_date": sessao, "cut": cut, "company": empresa,
                "snapshot_at": captura.isoformat() if captura else None,
                "updated_at": agora}
        motivo = _sem_sinal_por_que(empresa, modelos.get(empresa))
        if motivo:
            rows.append({**base, "status": "sem_sinal", "gap_expected": None,
                         "score": None, "confidence": None,
                         "attribution": {"motivo": motivo}})
            continue
        m = modelos.get(empresa)
        out = pontuar_empresa(m, x) if m else None
        if out is None:
            rows.append({**base, "status": "sem_dado", "gap_expected": None,
                         "score": None, "confidence": None, "attribution": None})
        else:
            rows.append({**base, "status": "ok", **out})

    ok = [r for r in rows if r["status"] == "ok"]
    log.info("pulse %s/%s: %d com leitura, %d sem sinal, %d sem dado",
             sessao, cut, len(ok),
             sum(1 for r in rows if r["status"] == "sem_sinal"),
             sum(1 for r in rows if r["status"] == "sem_dado"))

    if dry_run:
        print(f"\n  MARKET PULSE {sessao} (corte {cut} = {int(cut):02d}:00 BRT)")
        print(f"  {'Empresa':<12}{'Gap esp.':>10}{'Score':>8}{'Conf.':>8}   Principal driver")
        print("  " + "-" * 74)
        for r in sorted(rows, key=lambda r: -(abs(r["score"]) if r["score"] is not None else -1)):
            if r["status"] != "ok":
                print(f"  {r['company']:<12}{'—':>10}{'—':>8}{'—':>8}   {r['status']}")
                continue
            top = (r["attribution"]["grupos"] or {})
            k = next(iter(top), "—")
            print(f"  {r['company']:<12}{r['gap_expected']:>+9.2f}%{r['score']:>+8.0f}"
                  f"{r['confidence'] or 0:>7.0f}%   {k} ({top.get(k, 0):+.2f})")
        if ok:
            print(f"\n  MARKET PULSE (cobertura): {sum(r['score'] for r in ok)/len(ok):+.0f}")
        return rows

    _supa_upsert("pulse_daily", rows)
    return rows
