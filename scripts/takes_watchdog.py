"""Vigia da QUALIDADE dos takes (nasceu do incidente de 2026-08-06).

Por que existe: naquele dia a cadeia de IAs degradou em silêncio — a Mistral morreu com 402
às 07:33, o Groq já dava 413 em 100% das chamadas, e a fila despencou no GLM (o pior modelo
medido, ~25% de erro). O sintoma só apareceu 5 dias depois, e quem viu foi o ANALISTA, no olho,
numa manchete errada. Não havia nenhum número vigiando isso.

Quatro perguntas, todas somente-leitura sobre o banco (custo ZERO de IA, exceto o --ping):

  1) QUEM está classificando?      distribuição de take_llm_model nas últimas 24h.
     -> teria gritado em 1º/ago: Mistral 100%, Gemini 0.
  2) A qualidade caiu?             % de "no take" em Platts+Fastmarkets (fontes curadas,
     on-coverage, onde "no take" é raro). Saudável ~8-16%; o GLM levou a 35%.
  3) A fila travou?                artigos sem take há mais de 2h.
  4) Os provedores respondem?      (--ping) 1 chamada REAL por provedor da cadeia.
     -> teria pego o 402 da Mistral no mesmo dia.

Padrão de alarme igual ao scripts/watchdog.py: **exit 1 faz o job do Actions FALHAR e o GitHub
manda e-mail ao dono do repo** — sem SMTP, e SEM e-mail quando está tudo bem (anti-spam).
O relatório completo sai sempre no log do job; rode com --ping local p/ ver os números.

Uso:  python -m scripts.takes_watchdog [--ping] [--hours 24]
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import requests

try:                                    # .env p/ rodar local (na nuvem vem dos secrets)
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

# ── Limiares (todos ajustáveis por env, p/ calibrar sem mexer em código) ────────
# "no take" nas fontes CURADAS (Platts+Fastmarkets). Medido 2026-08-06 em 45 dias:
# gemini 15,5% · gpt-oss 15,8% · mistral 23,5% · glm 35,0%.
# ⚠️ CALIBRAR: a 1ª leitura (2026-08-07) deu 28,3% com o Gemini sozinho, bem acima dos 15,5%
# históricos — mas a janela estava CONTAMINADA pelo backfill de 236 artigos do dia anterior,
# cuja amostra era enviesada p/ "no take". Reconferir numa janela limpa e ajustar
# TAKES_NO_TAKE_MAX; se o normal do Gemini for mesmo ~28%, este limiar de 35 é frouxo demais.
NO_TAKE_MAX_PCT = float(os.environ.get("TAKES_NO_TAKE_MAX", "35"))
NO_TAKE_MIN_N   = int(os.environ.get("TAKES_NO_TAKE_MIN_N", "20"))   # amostra mínima p/ julgar

# Modelos por faixa de qualidade (erro efetivo de "no take" indevido, medido 2026-08-06).
TIER_GOOD = ("gemini", "gpt-oss")          # 0,8% e 2,4%
TIER_WEAK = ("mistral", "glm", "qwen")     # 4,7% e ~25%
WEAK_MAX_PCT = float(os.environ.get("TAKES_WEAK_MAX", "50"))

STUCK_MAX   = int(os.environ.get("TAKES_STUCK_MAX", "5"))    # artigos sem take há > STUCK_HOURS
STUCK_HOURS = int(os.environ.get("TAKES_STUCK_HOURS", "2"))

CURATED = ("S&P Platts", "Fastmarkets")


def _supa():
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    if not (url and key):
        print("SUPABASE_URL / SUPABASE_SERVICE_KEY ausentes — vigia desabilitado.")
        sys.exit(0)                      # sem credencial não é falha do sistema vigiado
    return url, {"apikey": key, "Authorization": f"Bearer {key}"}


def _fam(model: str) -> str:
    m = str(model or "")
    for p in ("gemini", "mistral", "glm", "gpt-oss", "qwen"):
        if m.startswith(p):
            return p
    return m or "None"


def _get(url, H, path, timeout=60):
    r = requests.get(f"{url}/rest/v1/{path}", headers=H, timeout=timeout)
    r.raise_for_status()
    return r.json()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ping", action="store_true",
                    help="testa cada provedor da cadeia com 1 chamada REAL (~8k tokens cada)")
    ap.add_argument("--hours", type=int, default=24)
    args = ap.parse_args()

    url, H = _supa()
    now = datetime.now(timezone.utc)
    since = quote((now - timedelta(hours=args.hours)).isoformat())
    problemas: list[str] = []

    print(f"═══ Vigia dos takes — {now:%Y-%m-%d %H:%M} UTC (janela {args.hours}h) ═══\n")

    # ── 1) QUEM classificou ──────────────────────────────────────────────────────
    rows = []
    for off in range(0, 8000, 1000):
        b = _get(url, H, f"news_articles?select=take_llm,take_llm_model,source_name"
                         f"&take_llm_at=gte.{since}&limit=1000&offset={off}")
        if not b:
            break
        rows.extend(b)

    print(f"1) QUEM classificou — {len(rows)} takes em {args.hours}h")
    if not rows:
        problemas.append(f"NENHUM take em {args.hours}h — o classificador não rodou.")
        print("   ⚠ nenhum take no período")
    else:
        fam = Counter(_fam(r["take_llm_model"]) for r in rows)
        for m, n in fam.most_common():
            faixa = "bom " if m in TIER_GOOD else ("FRACO" if m in TIER_WEAK else "?")
            print(f"   {m:12s} {n:5d}  {100*n/len(rows):5.1f}%  [{faixa}]")
        weak = sum(n for m, n in fam.items() if m in TIER_WEAK)
        pct_weak = 100 * weak / len(rows)
        if pct_weak > WEAK_MAX_PCT:
            problemas.append(
                f"{pct_weak:.0f}% dos takes saíram de modelos FRACOS ({', '.join(TIER_WEAK)}) "
                f"— a cadeia degradou; confira se o provedor titular está fora.")

    # ── 2) Taxa de "no take" nas fontes curadas ──────────────────────────────────
    cur = [r for r in rows if r["source_name"] in CURATED]
    nt = sum(1 for r in cur if r["take_llm"] == "no take")
    print(f"\n2) 'no take' em {'/'.join(CURATED)} — {len(cur)} artigos")
    if len(cur) < NO_TAKE_MIN_N:
        print(f"   amostra pequena (<{NO_TAKE_MIN_N}) — sem julgamento")
    else:
        pct = 100 * nt / len(cur)
        print(f"   {nt}/{len(cur)} = {pct:.1f}%  (limiar {NO_TAKE_MAX_PCT}%)")
        if pct > NO_TAKE_MAX_PCT:
            problemas.append(
                f"'no take' em {pct:.0f}% das fontes curadas (normal 8-16%) — "
                f"sinal de modelo fraco classificando ou prompt/parse quebrado.")

    # ── 3) Fila travada ──────────────────────────────────────────────────────────
    old = quote((now - timedelta(hours=STUCK_HOURS)).isoformat())
    win = quote((now - timedelta(hours=48)).isoformat())
    r = requests.get(
        f"{url}/rest/v1/news_articles?select=url&take_llm=is.null&include_in_report=eq.true"
        f"&found_at=lt.{old}&found_at=gte.{win}",
        headers={**H, "Prefer": "count=exact", "Range": "0-0"}, timeout=30)
    cr = r.headers.get("content-range", "")
    stuck = int(cr.split("/")[-1]) if "/" in cr and cr.split("/")[-1].isdigit() else 0
    print(f"\n3) Fila — {stuck} artigo(s) sem take há mais de {STUCK_HOURS}h (limiar {STUCK_MAX})")
    if stuck > STUCK_MAX:
        problemas.append(f"{stuck} notícias sem take há >{STUCK_HOURS}h — provedores fora ou fila afogada.")

    # ── 4) Provedores respondem? ─────────────────────────────────────────────────
    if args.ping:
        print("\n4) Provedores (1 chamada real cada)")
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from hunter import llm_take
        llm_take._load_prompts()
        ut = llm_take._user_text("Turkish rebar exports hold as buyers resist higher prices",
                                 source=None, body=None)
        vivos = []
        for p in llm_take.CHAIN:
            try:
                res, why = llm_take._try_provider(p, ut, max_retries=0)
            except Exception as e:
                res, why = None, f"exceção: {type(e).__name__}"
            ok = res is not None
            print(f"   {p:10s} {'OK  ' if ok else 'FORA'}  {why}"
                  + (f"  -> take={res['take']}" if ok else ""))
            if ok:
                vivos.append(p)
        if not llm_take.CHAIN:
            problemas.append("CHAIN vazia — nenhuma chave de IA configurada.")
        elif not vivos:
            problemas.append("NENHUM provedor de IA respondeu — os takes vão parar.")
        elif llm_take.CHAIN[0] not in vivos:
            problemas.append(
                f"o titular da cadeia ({llm_take.CHAIN[0]}) está FORA — "
                f"os takes estão saindo de um provedor de qualidade inferior.")
        elif len(vivos) < 2:
            problemas.append(
                f"só {vivos[0]} responde — sem rede de segurança; se ele cair, os takes param.")

    # ── Veredicto ────────────────────────────────────────────────────────────────
    print("\n" + "═" * 60)
    if problemas:
        print(f"❌ {len(problemas)} PROBLEMA(S):")
        for p in problemas:
            print(f"   • {p}")
        return 1                    # exit 1 → job falha → GitHub manda e-mail
    print("✅ takes saudáveis.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
