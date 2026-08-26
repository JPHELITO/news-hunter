"""
pulse_train.py — treina o Market Pulse v2 e grava os pesos em pulse_model.

Roda 1x por semana (pulse_train.yml) ou sob demanda. É o ÚNICO lugar do projeto que
precisa de pandas/numpy/scikit-learn — o caminho diário é Python puro de propósito,
para não engordar o hunt-loop que roda a cada 5 minutos.

O QUE ELE FAZ
-------------
Para cada (empresa, corte):
  1. alvo   = gap de abertura  =  abertura_ajustada / fechamento_ajustado_anterior − 1
  2. features = variação de 24h dos 23 instrumentos, lida de pulse_snapshot
  3. walk-forward (janela expansível, refit a cada 10 pregões, padronização SÓ no treino)
     → mede o `ic_oos`, que é o número de saúde do modelo
  4. calibra a logística de confiança nas previsões fora da amostra do passo 3
  5. re-treina no histórico inteiro e grava coefs/mu/sd/sigma_pred/conf_w

REFERÊNCIA DO HOLDOUT (205 pregões, corte 09) — se o ic_oos sair muito longe disto,
algo quebrou na captura, não no modelo:
    AURA33 0,57 · VALE3 0,47 · CSNA3 0,37 · CMIN3 0,28 · GGBR4 0,25
    USIM5  0,25 · KLBN11 0,21 · SUZB3 −0,04 · RANI3 0,01

Uso:
    python scripts/pulse_train.py --dry-run     # treina e imprime, não grava
    python scripts/pulse_train.py               # treina e grava em pulse_model
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests
from scipy import stats
from sklearn.linear_model import LogisticRegression, Ridge

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

from hunter.prices import HEADERS, _YAHOO_HOSTS, _supa_upsert          # noqa: E402
from hunter.pulse_score import _supa_get                                # noqa: E402
from hunter.pulse_snapshot import (COMPANIES, CUT_BASE, CUTS_SCORE,     # noqa: E402
                                   GEMEO, SNAPSHOT_SYMBOLS)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("pulse_train")

ALPHA = 10.0          # regularização validada ponta a ponta no holdout
MIN_TRAIN = 150       # pregões mínimos antes da primeira previsão fora da amostra
REFIT = 10            # re-treina a cada N pregões no walk-forward
MIN_LINHAS = 200      # abaixo disso não vale treinar
MIN_COBERTURA = 0.85  # fração dos pregões que um instrumento precisa cobrir para entrar

# Os 23 instrumentos com que o v2 foi validado em 2026-08-12 (IC 0,264 no holdout de 205
# pregões). Servem de LINHA DE BASE do experimento: qualquer instrumento novo só entra em
# produção se, medido contra esta lista no mesmo holdout, o ic_oos subir.
SYMBOLS_BASE_V2 = [
    "FMG.AX", "BHP.AX", "RIO.AX", "AAL.L", "^AXJO",
    "^HSI", "000001.SS", "^N225",
    "HG=F", "GC=F",
    "^STOXX50E", "^GDAXI", "^FTSE", "UPM.HE", "STERV.HE",
    "ES=F", "NQ=F", "CL=F",
    "USDBRL=X", "EURUSD=X", "AUDUSD=X", "DX-Y.NYB", "^VIX",
]


# ───────────────────────── alvo: o gap de abertura ─────────────────────────
def gaps_de_abertura(descartar_leilao_vazio: bool = True) -> pd.DataFrame:
    """
    Matriz data x empresa com o gap de abertura, ajustado por proventos.

    ⚠️ DIAS DE LEILÃO SEM NEGÓCIO SAEM DO ALVO. Quando a abertura sai EXATAMENTE igual ao
    fechamento anterior, não houve formação de preço no leilão — o "gap zero" é ausência de
    negócio, não uma resposta do mercado aos drivers da madrugada. Ensinar o modelo com
    esses dias é ensiná-lo a prever o silêncio. Medido em 3 meses (2026-08):
        RANI3 27,7% dos pregões · CMIN3 e KLBN11 16,9% · USIM5 7,7% · VALE3 4,6%
    A comparação é feita no preço CRU (open == close anterior); no ajustado um dia de
    provento daria diferente de zero e o filtro passaria batido.
    """
    out = {}
    for sym in COMPANIES:
        js = None
        for t in range(4):
            host = _YAHOO_HOSTS[t % len(_YAHOO_HOSTS)]
            try:
                r = requests.get(f"{host}/v8/finance/chart/{sym}"
                                 f"?range=3y&interval=1d&events=div,split",
                                 headers=HEADERS, timeout=30)
                if r.status_code in (429, 401, 403):
                    time.sleep(1.5 * (t + 1)); continue
                r.raise_for_status(); js = r.json(); break
            except Exception:
                time.sleep(1.0 * (t + 1))
        if not js:
            log.warning("  %s: sem histórico do Yahoo", sym); continue
        res = js["chart"]["result"][0]
        q = res["indicators"]["quote"][0]
        adj = (res["indicators"].get("adjclose") or [{}])[0].get("adjclose")
        off = res["meta"].get("gmtoffset", -10800)
        df = pd.DataFrame({
            "d": [datetime.fromtimestamp(t + off, timezone.utc).date().isoformat()
                  for t in res["timestamp"]],
            "open": q.get("open"), "close": q.get("close"),
            "adjclose": adj if adj else q.get("close"),
        }).dropna(subset=["open", "close", "adjclose"])
        # o fator de provento vale para TODOS os preços do dia
        fac = df.adjclose / df.close
        gap = (df.open * fac) / df.adjclose.shift(1) - 1
        gap[gap.abs() > 0.40] = np.nan            # grupamento/erro de dado
        if descartar_leilao_vazio:
            carimbado = (df.open / df.close.shift(1) - 1).abs() < 1e-9
            n = int(carimbado.sum())
            gap[carimbado] = np.nan
            if n:
                log.info("  %s: %d pregões sem negócio no leilão de abertura (fora do alvo)",
                         sym, n)
        out[sym] = pd.Series(gap.values, index=df.d.values)
        time.sleep(0.3)
    return pd.DataFrame(out)


# ───────────────────────── features: o snapshot ─────────────────────────
def _precos_do_corte(cut: str) -> pd.DataFrame:
    """Matriz data x instrumento com o PREÇO em um corte, lida de pulse_snapshot."""
    linhas, offset = [], 0
    while True:
        page = _supa_get(f"pulse_snapshot?cut=eq.{cut}&select=session_date,symbol,price"
                         f"&order=session_date.asc&limit=1000&offset={offset}")
        linhas.extend(page)
        if len(page) < 1000:
            break
        offset += 1000
    if not linhas:
        return pd.DataFrame()
    df = pd.DataFrame(linhas)
    px = df.pivot_table(index="session_date", columns="symbol", values="price").sort_index()
    return px.reindex(columns=[s for s in SNAPSHOT_SYMBOLS if s in px.columns])


def features_do_corte(cut: str, janela: str = "overnight") -> pd.DataFrame:
    """
    Matriz data x instrumento com a variação que alimenta o modelo.

    janela="overnight" (produção): preço(corte, D) / preço(18h, D-1) − 1. Mede SÓ o que
        aconteceu enquanto a B3 esteve fechada — a informação que ainda não está no preço
        de partida do gap.
    janela="24h" (só para comparação): preço(corte, D) / preço(corte, D-1) − 1, a fórmula
        antiga. Mantida para o experimento poder medir uma contra a outra no mesmo holdout.

    O casamento com a âncora é um merge as-of por DATA (a maior data base estritamente
    anterior), não um `shift(1)`: assim feriado e fim de semana se resolvem sozinhos, e um
    dia sem âncora vira linha ausente em vez de janela de 48h disfarçada de 24h.
    """
    px = _precos_do_corte(cut)
    if px.empty:
        return pd.DataFrame()
    if janela == "24h":
        return px / px.shift(1) - 1
    if janela != "overnight":
        raise ValueError(f"janela desconhecida: {janela!r}")

    base = _precos_do_corte(CUT_BASE)
    if base.empty:
        log.error("corte %s: sem âncora do fechamento (cut=%s) em pulse_snapshot — "
                  "rode scripts/pulse_backfill.py para reconstruí-la", cut, CUT_BASE)
        return pd.DataFrame()

    base = base.reindex(columns=px.columns)
    pos = base.index.searchsorted(px.index, side="left") - 1   # última âncora ANTES do corte
    tem = pos >= 0
    if not tem.any():
        return pd.DataFrame()
    alinhado = pd.DataFrame(base.iloc[pos[tem]].values,
                            index=px.index[tem], columns=px.columns)
    return px.loc[px.index[tem]] / alinhado - 1


# ───────────────────────── treino ─────────────────────────
def _fit(X: pd.DataFrame, y: pd.Series, alpha: float):
    mu, sd = X.mean(), X.std().replace(0, 1.0)
    Z = (X - mu) / sd
    mdl = Ridge(alpha=alpha).fit(Z.values, y.values)
    return mdl, mu, sd, float(np.std(mdl.predict(Z.values)))


def walk_forward(X: pd.DataFrame, y: pd.Series, alpha: float) -> pd.DataFrame:
    """Previsões fora da amostra, com padronização calculada só na janela de treino."""
    n = len(X)
    if n <= MIN_TRAIN + REFIT:
        return pd.DataFrame()
    linhas = []
    for start in range(MIN_TRAIN, n, REFIT):
        end = min(start + REFIT, n)
        mdl, mu, sd, sigma = _fit(X.iloc[:start], y.iloc[:start], alpha)
        Z = (X.iloc[start:end] - mu) / sd
        p = mdl.predict(Z.values)
        conc = X.iloc[start:end].apply(lambda r: abs(np.sign(r.values).mean()), axis=1).values
        linhas.append(pd.DataFrame({"pred": p, "y": y.iloc[start:end].values,
                                    "mag": np.clip(np.abs(p) / (sigma or 1e-9), 0, 4),
                                    "conc": conc}, index=X.index[start:end]))
    return pd.concat(linhas) if linhas else pd.DataFrame()


def _colunas_uteis(X: pd.DataFrame, y: pd.Series, empresa: str) -> list[str]:
    """
    Quais instrumentos entram no modelo DESTA empresa.

    O treino exige a linha completa (`notna().all()`), então uma única coluna com histórico
    curto encurta a amostra de todo mundo. Medido em 2026-08-26: o AUGO só existe desde
    16/07/2025 e sozinho cortaria os 405 pregões de TODAS as nove empresas para ~280.

    Regra: descarta coluna que não cubra ao menos MIN_COBERTURA dos pregões com alvo.
    Exceção: o GÊMEO da própria empresa fica, e a janela dela encolhe de propósito — para
    a AURA33, trocar 125 pregões pelo pré-mercado do AUGO é um negócio que vale a pena
    (e o ic_oos do walk-forward é quem confirma, não a intuição). O gêmeo das OUTRAS
    empresas segue sujeito à regra geral: para a Vale, o AUGO seria só ruído caro.
    """
    com_alvo = y.notna()
    if not com_alvo.any():
        return list(X.columns)
    cobertura = X[com_alvo].notna().mean()
    gemeo = GEMEO.get(empresa)
    largos = [c for c in X.columns if cobertura.get(c, 0) >= MIN_COBERTURA]
    manter = list(largos)

    # O gêmeo entra mesmo com cobertura curta — MAS só se ainda sobrar amostra para
    # treinar. Medido em 2026-08-26: o AUGO (50% de cobertura) deixava a AURA33 com 187
    # pregões alinhados, abaixo do mínimo de 200, e ela simplesmente não treinava. Trocar
    # um modelo excelente por nenhum modelo não é um trade-off, é um bug.
    if gemeo and gemeo in X.columns and gemeo not in manter:
        com = X[largos + [gemeo]].notna().all(axis=1) & com_alvo
        if int(com.sum()) >= MIN_LINHAS:
            manter.append(gemeo)
        else:
            log.info("  %s: gêmeo %s ficaria com %d pregões (mínimo %d) — fora por ora",
                     empresa, gemeo, int(com.sum()), MIN_LINHAS)

    fora = [c for c in X.columns if c not in manter]
    if fora:
        log.info("  %s: fora por histórico curto (%s)", empresa,
                 ", ".join(f"{c} {100*cobertura[c]:.0f}%" for c in fora))
    return manter


def treinar(X: pd.DataFrame, y: pd.Series, empresa: str, cut: str, alpha: float) -> dict | None:
    X = X[_colunas_uteis(X, y, empresa)]
    m = X.notna().all(axis=1) & y.notna()
    X, y = X[m], y[m]
    if len(X) < MIN_LINHAS:
        log.warning("  %s/%s: só %d pregões alinhados (mínimo %d) — pulado",
                    empresa, cut, len(X), MIN_LINHAS)
        return None

    oos = walk_forward(X, y, alpha)
    ic = float(stats.spearmanr(oos.pred, oos.y)[0]) if len(oos) > 30 else None
    acerto = float(((oos.pred > 0) == (oos.y > 0)).mean()) if len(oos) > 30 else None

    # Confiança = P(acertar a direção). Duas variáveis, escolhidas por teste empírico contra o
    # acerto realizado: magnitude do sinal (acerto sobe de 51,6% para 72,2% do 1º ao 4º quartil)
    # e concordância entre os drivers (53,8% -> 67,2%). Completude NÃO entra: como a pontuação
    # falha fechada, ela vale 1,0 em toda observação — seria uma cópia do intercepto.
    conf_w = None
    if len(oos) > 60:
        feats = np.column_stack([oos.mag.values, oos.conc.values])
        alvo = ((oos.pred > 0) == (oos.y > 0)).astype(int).values
        if 0 < alvo.mean() < 1:
            lr = LogisticRegression().fit(feats, alvo)
            conf_w = {"mag": float(lr.coef_[0][0]), "conc": float(lr.coef_[0][1]),
                      "intercept": float(lr.intercept_[0])}

    mdl, mu, sd, sigma = _fit(X, y, alpha)
    log.info("  %-11s %s  n=%4d  ic_oos=%s  acerto=%s",
             empresa, cut, len(X),
             f"{ic:+.3f}" if ic is not None else "  —  ",
             f"{100*acerto:.1f}%" if acerto is not None else "—")
    return {
        "company": empresa, "cut": cut,
        "coefs": {c: float(b) for c, b in zip(X.columns, mdl.coef_)},
        "mu": {c: float(v) for c, v in mu.items()},
        "sd": {c: float(v) for c, v in sd.items()},
        "sigma_pred": sigma, "conf_w": conf_w,
        "n_train": int(len(X)), "ic_oos": ic,
        "trained_at": datetime.now(timezone.utc).isoformat(),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--alpha", type=float, default=ALPHA)
    ap.add_argument("--cut", choices=list(CUTS_SCORE), help="treinar só um corte")
    ap.add_argument("--janela", choices=["overnight", "24h"], default="overnight",
                    help="janela da feature (padrão: overnight, a de produção)")
    ap.add_argument("--manter-leilao-vazio", action="store_true",
                    help="não descarta os pregões que abriram sem negócio no leilão")
    ap.add_argument("--symbols", choices=["todos", "base"], default="todos",
                    help="'base' restringe aos 23 instrumentos do v2 (linha de comparação)")
    args = ap.parse_args()

    log.info("baixando o alvo (gap de abertura das %d cobertas) ...", len(COMPANIES))
    G = gaps_de_abertura(descartar_leilao_vazio=not args.manter_leilao_vazio)
    log.info("  %d empresas, %d pregões", G.shape[1], G.shape[0])

    modelos = []
    for cut in ([args.cut] if args.cut else list(CUTS_SCORE)):
        X = features_do_corte(cut, janela=args.janela)
        if X.empty:
            log.error("corte %s: pulse_snapshot vazio — rode scripts/pulse_backfill.py antes", cut)
            continue
        if args.symbols == "base":
            X = X.reindex(columns=[s for s in SYMBOLS_BASE_V2 if s in X.columns])
        log.info("corte %s (janela %s, %s): %d pregões x %d instrumentos",
                 cut, args.janela, args.symbols, X.shape[0], X.shape[1])
        for empresa in COMPANIES:
            if empresa not in G.columns:
                continue
            y = G[empresa].reindex(X.index)
            r = treinar(X, y, empresa, cut, args.alpha)
            if r:
                modelos.append(r)

    if not modelos:
        log.error("nenhum modelo treinado.")
        return 1

    log.info("")
    log.info("=== RESUMO (ic_oos por empresa) ===")
    tab = pd.DataFrame([{"empresa": m["company"], "corte": m["cut"],
                         "n": m["n_train"], "ic_oos": m["ic_oos"]} for m in modelos])
    print(tab.pivot_table(index="empresa", columns="corte", values="ic_oos").round(3).to_string())

    if args.dry_run:
        log.info("dry-run: nada gravado.")
        return 0
    _supa_upsert("pulse_model", modelos)
    log.info("gravados %d modelos em pulse_model", len(modelos))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
