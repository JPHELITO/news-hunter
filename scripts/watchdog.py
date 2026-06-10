"""Watchdog de fontes Playwright (Platts, Fastmarkets).

Lê a tabela source_health no Supabase e SAI COM CÓDIGO 1 se alguma fonte estiver
"fora do ar" — ou seja: login_failed=True, ou a última sessão bem-sucedida (last_ok)
está mais velha que o limite, ou a fonte nunca reportou. O exit 1 faz o job do GitHub
Actions FALHAR, e o GitHub envia um email automático ao dono do repo.

Não escreve nada (somente leitura). Roda de hora em hora (ver watchdog.yml).
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone, timedelta

import requests

# Tolerância de "última vez OK" por fonte (minutos). hunt-playwright roda a cada 30 min,
# então last_ok normal é < ~35 min. 90 min permite alguns runs perdidos sem alarme falso.
THRESHOLDS_MIN = {"platts": 90, "fastmarkets": 90}

# ── Monitor de cobertura (anti-quebra-silenciosa) ────────────────────────────
# Lê news_articles e, para CADA fonte, aprende o ritmo (gap médio entre artigos
# nos últimos N dias) e alarma se a fonte ficou silenciosa muito além do normal.
# Auto-adaptativo: SMM (horário) e Ibá (semanal) têm limiares diferentes, sem
# lista manual. Pega quebra silenciosa (ex.: Estadão) antes de virar problema.
COVERAGE_WINDOW_DAYS = 21      # janela p/ aprender o ritmo de cada fonte
COVERAGE_MULT        = 3.5     # alarma se silêncio > 3.5× o gap típico da fonte
COVERAGE_MIN_H       = 36.0    # nunca alarma antes de 36h (evita falso alarme)
COVERAGE_MAX_H       = 14 * 24.0  # teto: 14 dias (até fonte rara alarma se sumir)
COVERAGE_MIN_BASELINE = 3      # precisa de >=3 artigos no período p/ ter baseline


def coverage_threshold_h(count: int, window_h: float) -> float:
    """Limiar de silêncio (horas) para uma fonte, dado seu volume na janela."""
    avg_gap = window_h / max(count, 1)
    return min(COVERAGE_MAX_H, max(COVERAGE_MIN_H, COVERAGE_MULT * avg_gap))


def _age_min(iso: str, now: datetime) -> float:
    t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return (now - t).total_seconds() / 60


def check_coverage(url: str, headers: dict, now: datetime) -> tuple[list[str], list[tuple]]:
    """Lê news_articles (janela) e detecta fontes silenciosas além do próprio ritmo.

    Retorna (problemas, relatório). Derivado dos dados (sem lista fixa de fontes):
    monitora toda fonte com >= COVERAGE_MIN_BASELINE artigos na janela.
    """
    # ISO sem offset (+00:00 quebraria a URL — o '+' vira espaço). found_at é UTC.
    since = (now - timedelta(days=COVERAGE_WINDOW_DAYS)).strftime("%Y-%m-%dT%H:%M:%S")
    rows: list[dict] = []
    off = 0
    while True:
        try:
            r = requests.get(
                f"{url}/rest/v1/news_articles?select=source_name,found_at"
                f"&found_at=gte.{since}&order=found_at.desc",
                headers={**headers, "Range": f"{off}-{off+999}"}, timeout=30,
            )
            batch = r.json()
        except Exception as e:
            print(f"WATCHDOG: erro lendo news_articles p/ cobertura: {e}", file=sys.stderr)
            return [], []
        if not isinstance(batch, list) or not batch:
            break
        rows.extend(batch)
        off += 1000
        if len(batch) < 1000 or off >= 15000:
            break

    agg: dict[str, list] = {}   # source -> [count, last_found]
    for a in rows:
        s = a.get("source_name")
        f = a.get("found_at")
        if not s or not f:
            continue
        try:
            ts = datetime.fromisoformat(f.replace("Z", "+00:00"))
        except Exception:
            continue
        if s not in agg:
            agg[s] = [0, ts]
        agg[s][0] += 1
        if ts > agg[s][1]:
            agg[s][1] = ts

    win_h = COVERAGE_WINDOW_DAYS * 24
    problems: list[str] = []
    report: list[tuple] = []
    for s, (count, last) in sorted(agg.items(), key=lambda x: -x[1][0]):
        if count < COVERAGE_MIN_BASELINE:
            continue
        avg_gap = win_h / count
        thr = coverage_threshold_h(count, win_h)
        silence = (now - last).total_seconds() / 3600
        ok = silence <= thr
        report.append((s, count, avg_gap, silence, thr, ok))
        if not ok:
            problems.append(
                f"{s}: {silence:.0f}h sem notícia (ritmo ~{avg_gap:.0f}h, "
                f"limite {thr:.0f}h) -> provavelmente QUEBRADA"
            )
    return problems, report


def main() -> int:
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    if not url or not key:
        print("WATCHDOG: SUPABASE_URL/SUPABASE_SERVICE_KEY ausentes", file=sys.stderr)
        return 1

    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    try:
        r = requests.get(
            f"{url}/rest/v1/source_health"
            "?select=source,last_ok,last_attempt,login_failed",
            headers=headers, timeout=20,
        )
        r.raise_for_status()
        rows = {row["source"]: row for row in r.json()}
    except Exception as e:
        print(f"WATCHDOG: erro lendo source_health: {e}", file=sys.stderr)
        return 1

    now = datetime.now(timezone.utc)
    problems: list[str] = []
    for src, limit in THRESHOLDS_MIN.items():
        row = rows.get(src)
        if not row:
            problems.append(f"{src}: SEM registro em source_health (nunca reportou)")
            continue
        if row.get("login_failed"):
            problems.append(f"{src}: login_failed=TRUE — autologin falhou no ultimo run")
        last_ok = row.get("last_ok")
        if not last_ok:
            problems.append(f"{src}: nunca teve sessao OK (last_ok vazio)")
            continue
        age = _age_min(last_ok, now)
        if age > limit:
            problems.append(
                f"{src}: ultima sessao OK ha {age:.0f} min (limite {limit} min) "
                f"-> fonte provavelmente FORA DO AR"
            )

    print(f"WATCHDOG @ {now.isoformat()} (UTC)")
    print("[Sessões Playwright]")
    for src in THRESHOLDS_MIN:
        row = rows.get(src, {})
        print(f"  {src:12} login_failed={row.get('login_failed')} "
              f"last_ok={row.get('last_ok')} last_attempt={row.get('last_attempt')}")

    # ── Monitor de cobertura por fonte (anti-quebra-silenciosa) ──────────────
    cov_problems, cov_report = check_coverage(url, headers, now)
    print("\n[Cobertura por fonte — últimos %d dias]" % COVERAGE_WINDOW_DAYS)
    for s, count, avg_gap, silence, thr, ok in cov_report:
        flag = "OK" if ok else "*** SILENCIOSA ***"
        print(f"  {s[:20]:20} n={count:4d} ritmo~{avg_gap:5.0f}h silêncio={silence:5.0f}h "
              f"limite={thr:4.0f}h  {flag}")
    problems += cov_problems

    if problems:
        print("\n*** ALERTA — FONTES FORA DO AR / SILENCIOSAS ***")
        for p in problems:
            print("  - " + p)
        return 1

    print("\nOK — sessões e cobertura normais.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
