"""
pulse_watchdog.py — as fotos do Market Pulse saíram, e saíram NA HORA?

POR QUE EXISTE (2026-09-01). O Market Pulse degradou por cinco pregões sem ninguém
perceber. O cron do GitHub começou a disparar em horas arbitrárias e aconteceram duas
coisas, nenhuma das quais gerava alarme:

  1) cortes que simplesmente não saíram — e silêncio, no Actions, é indistinguível de
     sucesso;
  2) pior: fotos tiradas de madrugada e gravadas com o rótulo do corte das 07h. Como a
     gravação é upsert por (sessão, corte, símbolo), uma delas SOBRESCREVEU uma foto boa.

Quem viu foi o analista, no olho, cinco dias depois — exatamente o enredo do incidente
das IAs de agosto, que gerou o scripts/takes_watchdog.py. Este aqui é o mesmo remédio
aplicado ao pulse.

Três perguntas, todas somente-leitura:

  1) A sessão de hoje tem as fotos dos cortes cuja janela JÁ FECHOU?
  2) Alguma foto recente foi tirada FORA da janela do próprio corte?
     (o teste retroativo é o mesmo `fora_da_janela`, avaliado no instante da captura)
  3) A âncora do fechamento (corte 18) da sessão anterior existe?
     Sem ela não há janela overnight para medir e a manhã seguinte sai 'sem_dado'.

Alarme = exit 1 -> o job do Actions FALHA -> o GitHub manda e-mail ao dono do repo.
Sem SMTP, e SEM e-mail quando está tudo bem (mesmo padrão do watchdog.py).

⚠️ LIMITE HONESTO: este vigia também roda por cron, e cron foi justamente o que quebrou.
Um disparo atrasado ainda relata a verdade (ele lê ESTADO, não o instante), só relata
mais tarde. A rede de segurança de verdade contra o cron é o scripts/pulse_tick.py, que
pega carona na corrente do hunt-loop; o vigia é a segunda camada, não a primeira.

Uso:  python -m scripts.pulse_watchdog [--dias 7]
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

from hunter import pulse_snapshot as ps                              # noqa: E402

# Fração mínima de instrumentos p/ a foto valer — mesma régua do pulse_daily.
MIN_FRACAO = float(os.environ.get("PULSE_MIN_FRACAO", "0.75"))

# ⚠️ SÓ AS SESSÕES RECENTES DISPARAM ALARME. As mais antigas continuam sendo AUDITADAS e
# impressas no relatório, mas não fazem o job falhar. Sem isto, todo buraco histórico —
# e ficaram cinco pregões de buracos permanentes entre 26/08 e 31/08, que ninguém vai
# preencher — viraria um e-mail POR DIA, para sempre. Vigia que grita sempre é vigia que
# ninguém lê, e aí ele não vale mais que o silêncio que veio substituir.
# Duas sessões: a de hoje e a anterior (cuja âncora é pré-requisito da manhã de hoje).
ALARME_SESSOES = int(os.environ.get("PULSE_ALARME_SESSOES", "2"))


def _supa():
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    if not (url and key):
        print("SUPABASE_URL / SUPABASE_SERVICE_KEY ausentes — vigia desabilitado.")
        sys.exit(0)              # sem credencial não é falha do sistema vigiado
    return url, {"apikey": key, "Authorization": f"Bearer {key}"}


def _get(url, H, path):
    r = requests.get(f"{url}/rest/v1/{path}", headers=H, timeout=60)
    r.raise_for_status()
    return r.json()


def _sessoes_uteis(ate: str, n: int) -> list[str]:
    """As n últimas datas de pregão (dias de semana) até `ate`, inclusive."""
    d, out = datetime.fromisoformat(ate).date(), []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d -= timedelta(days=1)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dias", type=int, default=7, help="pregões a auditar (padrão 7)")
    args = ap.parse_args()

    url, H = _supa()
    agora = datetime.now(timezone.utc)
    hoje = ps.sessao_hoje()
    sessoes = _sessoes_uteis(hoje, args.dias)
    recentes = set(sessoes[:ALARME_SESSOES])       # só estas fazem o job falhar
    problemas: list[str] = []
    historicos: list[str] = []

    def anotar(sessao: str, texto: str) -> None:
        (problemas if sessao in recentes else historicos).append(texto)

    print(f"═══ Vigia do Market Pulse — {agora:%Y-%m-%d %H:%M} UTC "
          f"(últimos {args.dias} pregões) ═══\n")

    desde = min(sessoes)
    linhas = _get(url, H, f"pulse_snapshot?select=session_date,cut,symbol,captured_at"
                          f"&session_date=gte.{quote(desde)}&limit=20000")
    fotos: dict[tuple[str, str], list] = defaultdict(list)
    for r in linhas:
        fotos[(r["session_date"], r["cut"])].append(r)

    # ⚠️ Contar só os símbolos DO MODELO. A tabela pulse_snapshot também recebe o coletor
    # do Sina (minério de Cingapura e futuros da China), que é dado extra acumulando
    # histórico e não entra no vetor — sem filtrar, uma foto aparecia como "40/34" e a
    # régua de completude não queria dizer nada.
    do_modelo = set(ps.SNAPSHOT_SYMBOLS)
    total_simbolos = len(do_modelo)
    minimo = int(total_simbolos * MIN_FRACAO)

    # ── 1 e 2: por sessão e corte, existe? está no horário? está completa? ──────
    print("1) Fotos por sessão e corte")
    for sessao in sorted(sessoes, reverse=True):
        for cut in sorted(ps.CUTS, key=lambda c: ps.CUTS[c]):
            grupo = fotos.get((sessao, cut), [])
            _, fim = ps.janela(cut, datetime.fromisoformat(sessao + "T12:00:00+00:00"))
            janela_fechou = (sessao < hoje) or (agora.hour >= fim.hour)

            if not grupo:
                if janela_fechou:
                    print(f"   {sessao} corte {cut}   ✗ SEM FOTO")
                    anotar(sessao, f"{sessao}: corte {cut} sem foto (janela já fechou)")
                else:
                    print(f"   {sessao} corte {cut}   · janela ainda aberta")
                continue

            captura = min(r["captured_at"] for r in grupo)
            quando = datetime.fromisoformat(captura.replace("Z", "+00:00"))
            n = len({r["symbol"] for r in grupo} & do_modelo)
            motivo = ps.fora_da_janela(cut, quando)
            marca = "✗" if motivo else ("!" if n < minimo else "✓")
            print(f"   {sessao} corte {cut}   {marca} {n:>2}/{total_simbolos} "
                  f"instrumentos, capturada {quando:%H:%M} UTC")
            if motivo:
                anotar(sessao, f"{sessao} corte {cut}: foto FORA DA JANELA — {motivo}")
            elif n < minimo:
                anotar(sessao, f"{sessao} corte {cut}: só {n} de "
                       f"{total_simbolos} instrumentos do modelo (mínimo {minimo})")

    # ── 3: a âncora de ontem, pré-requisito da manhã de hoje ───────────────────
    anterior = sessoes[1] if len(sessoes) > 1 else None
    print(f"\n2) Âncora do fechamento (corte {ps.CUT_BASE}) da sessão anterior")
    if anterior and not fotos.get((anterior, ps.CUT_BASE)):
        print(f"   ✗ sessão {anterior} sem âncora — a manhã seguinte sai 'sem_dado'")
        anotar(anterior, f"{anterior}: sem a âncora do corte {ps.CUT_BASE}; sem ela não há "
                          f"janela overnight e a rodada da manhã seguinte sai 'sem_dado'")
    elif anterior:
        print(f"   ✓ sessão {anterior} tem a âncora")

    print("\n" + "═" * 60)
    if historicos:
        print(f"i  {len(historicos)} buraco(s) em sessoes antigas - auditados, sem alarme "
              f"(so as {ALARME_SESSOES} ultimas fazem o job falhar):")
        for h in historicos:
            print(f"   . {h}")
        print()
    if problemas:
        print(f"🔴 {len(problemas)} problema(s):\n")
        for p in problemas:
            print(f"   • {p}")
        print("\nO que costuma ser: cron do GitHub disparando fora de hora (ver "
              "scripts/pulse_tick.py), Yahoo bloqueando o runner, ou feriado em cadeia.")
        return 1
    print("✅ Market Pulse saudável: fotos presentes, completas e no horário.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
