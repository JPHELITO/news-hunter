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
  4) Os provedores respondem?      (--ping) chamada REAL a cada provedor da cadeia, com as
     MESMAS 3 tentativas da produção.  -> teria pego o 402 da Mistral no mesmo dia.

Padrão de alarme igual ao scripts/watchdog.py: **exit 1 faz o job do Actions FALHAR e o GitHub
manda e-mail ao dono do repo** — sem SMTP, e SEM e-mail quando está tudo bem (anti-spam).
O relatório completo sai sempre no log do job; rode com --ping local p/ ver os números.

E-MAIL SÓ COM ESTRAGO DE VERDADE (decisão do usuário, 2026-10-19)
------------------------------------------------------------------
O vigia ficou vermelho em 16 de 40 dias com os takes saindo normalmente. Causa medida: o ping
dava UMA tentativa por IA, a Mistral não responde (limite 0/min na conta) e o Gemini cai toda
tarde de dia útil (13-18h UTC, manhã dos EUA) — e o cron das 11:40 UTC dispara 3-9h atrasado,
bem nessa janela. Qualquer soluço virava e-mail. Agora:
  • alarme = estrago medido no DIA (GLM fazendo > TAKES_WEAK_MAX % dos takes; o titular fazendo
    < TAKES_GOOD_MIN %; "no take" curado alto; fila parada), ou ping em que NINGUÉM responde, ou
    o titular RECUSANDO a chamada (chave/assinatura/modelo — não se cura sozinho);
  • falha passageira (429, 5xx, rede) e reserva fora entram só no relatório.

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
# ✅ CALIBRADO 2026-09-01: a leitura de 28,3% em 07/ago era mesmo contaminação do backfill de 236
# artigos. Em janela LIMPA de 30 dias (828 artigos curados, Gemini fazendo 100%): 15,1% — colado
# nos 15,5% históricos. Com o normal em ~15%, o limiar de 35 só gritaria com a casa já em chamas
# (o GLM, o pior modelo medido, dava 35,0%) -> apertado p/ 25, que ainda deixa margem de 10 pontos.
NO_TAKE_MAX_PCT = float(os.environ.get("TAKES_NO_TAKE_MAX", "25"))
NO_TAKE_MIN_N   = int(os.environ.get("TAKES_NO_TAKE_MIN_N", "20"))   # amostra mínima p/ julgar

# Modelos por faixa de qualidade (erro efetivo de "no take" indevido, medido 2026-08-06).
TIER_GOOD = ("gemini", "gpt-oss")          # 0,8% e 2,4%
TIER_MID  = ("mistral",)                   # 4,7% — reserva aceitável, não é estrago
TIER_WEAK = ("glm", "qwen")                # ~25%
# Estrago do dia (2026-10-19). Normal: GLM 0-5% dos takes; dias ruins 22-29%; o limiar antigo
# (50%, e com a Mistral somada) nunca tocou — em 17/10 deu 49,4%. Daí 20.
WEAK_MAX_PCT = float(os.environ.get("TAKES_WEAK_MAX", "20"))
# O titular (faixa boa) fazendo menos da metade = fora a maior parte do dia, mesmo que a reserva
# seja a Mistral (o caso de 1º/ago, ao contrário: Mistral 100%, Gemini 0).
GOOD_MIN_PCT = float(os.environ.get("TAKES_GOOD_MIN", "50"))
SHARE_MIN_N = int(os.environ.get("TAKES_SHARE_MIN_N", "20"))     # amostra mínima p/ julgar o dia
PING_RETRIES = int(os.environ.get("TAKES_PING_RETRIES", "2"))    # = produção: 3 tentativas

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


def problemas_de_quem(fam: Counter) -> list[str]:
    """Checagem 1 — o estrago do DIA pela mistura de modelos (sem chamar IA nenhuma)."""
    total = sum(fam.values())
    if total < SHARE_MIN_N:
        return []                        # fim de semana com 5-10 takes: 2 do GLM não são 20%
    out = []
    pct_fraco = 100 * sum(n for m, n in fam.items() if m in TIER_WEAK) / total
    pct_bom = 100 * sum(n for m, n in fam.items() if m in TIER_GOOD) / total
    if pct_fraco > WEAK_MAX_PCT:
        out.append(f"{pct_fraco:.0f}% dos takes saíram de modelo FRACO ({', '.join(TIER_WEAK)}; "
                   f"limiar {WEAK_MAX_PCT:.0f}%) — o titular falhou e a reserva ruim classificou.")
    if pct_bom < GOOD_MIN_PCT:
        out.append(f"só {pct_bom:.0f}% dos takes saíram do titular ({', '.join(TIER_GOOD)}; mínimo "
                   f"{GOOD_MIN_PCT:.0f}%) — ele ficou fora a maior parte do dia.")
    return out


def classificar_falha(why: str) -> str:
    """Motivo devolvido por llm_take._try_provider -> 'ok' | 'permanente' | 'passageira'.

    Permanente = a chamada foi RECUSADA (401/402/403: chave ou assinatura; outro 4xx: modelo
    sumiu, pedido grande demais) — não se cura sozinha. O resto (429, 5xx, rede, resposta
    torta) é passageiro: o Gemini faz isso toda tarde e volta sozinho."""
    if why == "ok":
        return "ok"
    if why.startswith("auth") or why.startswith("http 4"):
        return "permanente"
    return "passageira"


def problemas_do_ping(chain: list[str], resultados: dict[str, str]) -> list[str]:
    """Checagem 4 — só vira alarme o que é estrago ou não se cura sozinho."""
    if not chain:
        return ["CHAIN vazia — nenhuma chave de IA configurada."]
    if not any(resultados.get(p) == "ok" for p in chain):
        return [f"NENHUM provedor de IA respondeu ({PING_RETRIES + 1} tentativas cada) — "
                f"os takes vão parar."]
    titular = chain[0]
    if classificar_falha(resultados.get(titular, "")) == "permanente":
        return [f"o titular da cadeia ({titular}) RECUSOU a chamada ({resultados[titular]}) — "
                f"chave, assinatura ou modelo; não volta sozinho e os takes estão saindo da reserva."]
    # 2026-10-21: o 2º Gemini é quem segura as tardes de lotação. Recusa permanente dele (o Google
    # aposentou o 2.5 para contas novas com um 404) deixaria o GLM de volta na linha de frente, calado.
    for p in chain[1:]:
        if p.startswith("gemini") and classificar_falha(resultados.get(p, "")) == "permanente":
            return [f"o 2º Gemini da cadeia ({p}) RECUSOU a chamada ({resultados[p]}) — modelo "
                    f"aposentado ou chave; as tardes de lotação voltariam a cair no GLM."]
    return []


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
            faixa = ("bom " if m in TIER_GOOD else "médio" if m in TIER_MID
                     else "FRACO" if m in TIER_WEAK else "?")
            print(f"   {m:12s} {n:5d}  {100*n/len(rows):5.1f}%  [{faixa}]")
        print(f"   (alarme: fraco > {WEAK_MAX_PCT:.0f}% ou titular < {GOOD_MIN_PCT:.0f}%, "
              f"com {SHARE_MIN_N}+ takes)")
        problemas.extend(problemas_de_quem(fam))

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
        print(f"\n4) Provedores (chamada real, {PING_RETRIES + 1} tentativas cada — como a produção)")
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from hunter import llm_take
        llm_take._load_prompts()
        ut = llm_take._user_text("Turkish rebar exports hold as buyers resist higher prices",
                                 source=None, body=None)
        resultados = {}
        for p in llm_take.CHAIN:
            try:
                res, why = llm_take._try_provider(p, ut, max_retries=PING_RETRIES)
            except Exception as e:
                res, why = None, f"exceção: {type(e).__name__}"
            resultados[p] = "ok" if res is not None else why
            classe = classificar_falha(resultados[p])
            rotulo = {"ok": "OK  ", "passageira": "FORA (passageira)",
                      "permanente": "FORA (RECUSADA)"}[classe]
            print(f"   {p:10s} {rotulo}  {why}" + (f"  -> take={res['take']}" if res else ""))
        if llm_take.CHAIN and len([p for p in llm_take.CHAIN if resultados[p] == "ok"]) < 2:
            print("   ⚠ sem rede de segurança agora — só relatório; o estrago, se houver, aparece em 1-3")
        problemas.extend(problemas_do_ping(llm_take.CHAIN, resultados))

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
