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
from datetime import datetime, timezone

import requests

# Tolerância de "última vez OK" por fonte (minutos). hunt-playwright roda a cada 30 min,
# então last_ok normal é < ~35 min. 90 min permite alguns runs perdidos sem alarme falso.
THRESHOLDS_MIN = {"platts": 90, "fastmarkets": 90}


def _age_min(iso: str, now: datetime) -> float:
    t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return (now - t).total_seconds() / 60


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
    for src in THRESHOLDS_MIN:
        row = rows.get(src, {})
        print(f"  {src:12} login_failed={row.get('login_failed')} "
              f"last_ok={row.get('last_ok')} last_attempt={row.get('last_attempt')}")

    if problems:
        print("\n*** ALERTA — FONTES FORA DO AR ***")
        for p in problems:
            print("  - " + p)
        return 1

    print("\nOK — todas as fontes renovando normalmente.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
