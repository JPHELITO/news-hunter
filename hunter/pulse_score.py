"""
Market Pulse v2 — pontuação diária. Python puro, sem numpy.

O modelo é um ridge linear, então pontuar é um produto escalar sobre 23 números:

    x  = preço(hoje) / preço(ontem) − 1          (dentro do mesmo corte)
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
from datetime import datetime, timezone

import requests

from .prices import _supa_upsert
from .pulse_snapshot import COMPANIES, GRUPO_DE, SEM_SINAL

log = logging.getLogger(__name__)


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
    Monta a variação de 24h de cada instrumento no corte pedido.
    Devolve (data do pregão, {símbolo: x}, instante da captura).
    """
    datas = _supa_get(f"pulse_snapshot?cut=eq.{cut}&select=session_date"
                      f"&order=session_date.desc&limit=200")
    distintas = []
    for row in datas:
        d = row["session_date"]
        if d not in distintas:
            distintas.append(d)
        if len(distintas) == 2:
            break
    if len(distintas) < 2:
        raise RuntimeError(f"corte {cut}: preciso de 2 pregões de snapshot, achei {len(distintas)}")
    hoje, ontem = distintas[0], distintas[1]

    linhas = _supa_get(f"pulse_snapshot?cut=eq.{cut}"
                       f"&session_date=in.({hoje},{ontem})"
                       f"&select=session_date,symbol,price,captured_at&limit=2000")
    p_hoje = {r["symbol"]: float(r["price"]) for r in linhas if r["session_date"] == hoje}
    p_ontem = {r["symbol"]: float(r["price"]) for r in linhas if r["session_date"] == ontem}
    captura = None
    for r in linhas:
        if r["session_date"] == hoje and r.get("captured_at"):
            ts = datetime.fromisoformat(r["captured_at"].replace("Z", "+00:00"))
            captura = ts if captura is None else max(captura, ts)

    x = {}
    for sym, ph in p_hoje.items():
        po = p_ontem.get(sym)
        if po:
            x[sym] = ph / po - 1.0
    log.info("pulse features %s: pregão %s vs %s, %d instrumentos", cut, hoje, ontem, len(x))
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
        if empresa in SEM_SINAL:
            rows.append({**base, "status": "sem_sinal", "gap_expected": None,
                         "score": None, "confidence": None,
                         "attribution": {"motivo": SEM_SINAL[empresa]}})
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
