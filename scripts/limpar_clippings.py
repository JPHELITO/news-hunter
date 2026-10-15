"""Apaga do Storage os arquivos do clipping com mais de N dias (padrão 30).

POR QUE (2026-10-15, decisão do usuário): cada "Gerar" do clipping sobe o Word, o .eml e a
prévia em `admin-uploads/clippings/<job>/` (clipping/run_jobs.py) e nada nunca apagava —
215 MB em 80 dias, ~80 MB por mês, contra 1 GB do plano grátis (o limite chegaria por volta
de meados de 2027 e o upload do clipping passaria a falhar). Ninguém lê esses arquivos
depois: os links gravados no job são assinados por 7 dias e a aba Clipping só mostra o
último. Roda 1× por mês (limpar_clippings.yml).

Travas, de propósito:
  • só mexe em `clippings/` — o resto do bucket (pred_exports, iba…) nunca é tocado;
  • nunca apaga nada com 8 dias ou menos (o link mais novo ainda pode estar em uso);
  • arquivo sem data de criação legível FICA;
  • se a listagem falhar, não apaga nada; se um lote falhar, o job sai com erro (e-mail).
  • sem --aplicar só SIMULA (lista o que apagaria) — é o modo padrão na linha de comando.

Uso (de news-hunter/):
    python -m scripts.limpar_clippings               # simula
    python -m scripts.limpar_clippings --aplicar     # apaga os de +30 dias
    python -m scripts.limpar_clippings --dias 60 --aplicar
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

BUCKET = "admin-uploads"
PREFIXO = "clippings/"
DIAS_PADRAO = 30
DIAS_MINIMO = 8          # os links assinados valem 7 dias
LOTE = 100               # caminhos por chamada de DELETE


def _h(key: str) -> dict:
    return {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def listar(url: str, key: str, prefixo: str = PREFIXO) -> list[dict]:
    """Todos os arquivos sob `prefixo`, descendo nas pastas. Levanta em qualquer erro."""
    out: list[dict] = []
    off = 0
    while True:
        r = requests.post(f"{url}/storage/v1/object/list/{BUCKET}",
                          json={"prefix": prefixo, "limit": 1000, "offset": off,
                                "sortBy": {"column": "name", "order": "asc"}},
                          headers=_h(key), timeout=60)
        r.raise_for_status()
        itens = r.json()
        for it in itens:
            caminho = f"{prefixo}{it['name']}"
            if it.get("id") is None:                     # pasta
                out.extend(listar(url, key, caminho + "/"))
            else:
                out.append({"caminho": caminho, "criado": it.get("created_at"),
                            "bytes": int((it.get("metadata") or {}).get("size") or 0)})
        if len(itens) < 1000:
            return out
        off += 1000


def velhos(arquivos: list[dict], dias: int, agora: datetime) -> list[dict]:
    """Os que têm data de criação legível e mais antiga que `dias`. Sem data = fica."""
    corte = agora - timedelta(days=dias)
    out = []
    for a in arquivos:
        if not a["caminho"].startswith(PREFIXO):
            continue
        try:
            criado = datetime.fromisoformat(str(a["criado"]).replace("Z", "+00:00"))
        except Exception:
            continue
        if criado.tzinfo is None:
            criado = criado.replace(tzinfo=timezone.utc)
        if criado < corte:
            out.append(a)
    return out


def apagar(url: str, key: str, caminhos: list[str]) -> int:
    """Apaga em lotes. Devolve quantos o Storage confirmou. Levanta se um lote falhar."""
    feitos = 0
    for i in range(0, len(caminhos), LOTE):
        lote = caminhos[i:i + LOTE]
        assert all(c.startswith(PREFIXO) for c in lote), "fora de clippings/ — recusado"
        r = requests.delete(f"{url}/storage/v1/object/{BUCKET}", json={"prefixes": lote},
                            headers=_h(key), timeout=60)
        r.raise_for_status()
        feitos += len(r.json() or [])
    return feitos


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Apaga arquivos antigos do clipping no Storage.")
    ap.add_argument("--dias", type=int, default=DIAS_PADRAO)
    ap.add_argument("--aplicar", action="store_true", help="apaga de verdade (sem isto, simula)")
    a = ap.parse_args(argv)
    if a.dias < DIAS_MINIMO:
        print(f"recusado: --dias {a.dias} < {DIAS_MINIMO} (os links do clipping valem 7 dias)",
              file=sys.stderr)
        return 2
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    if not url or not key:
        print("SUPABASE_URL / SUPABASE_SERVICE_KEY ausentes", file=sys.stderr)
        return 1
    try:
        todos = listar(url, key)
    except Exception as e:
        print(f"listagem falhou — nada apagado: {e}", file=sys.stderr)
        return 1
    alvo = velhos(todos, a.dias, datetime.now(timezone.utc))
    mb = sum(x["bytes"] for x in alvo) / 1e6
    total_mb = sum(x["bytes"] for x in todos) / 1e6
    print(f"clippings/: {len(todos)} arquivos ({total_mb:.1f} MB); com mais de {a.dias} dias: "
          f"{len(alvo)} ({mb:.1f} MB)")
    if alvo:
        datas = sorted(str(x["criado"])[:10] for x in alvo)
        print(f"  do mais antigo {datas[0]} ao mais novo {datas[-1]}")
    if not a.aplicar:
        print("SIMULAÇÃO — nada apagado (use --aplicar)")
        return 0
    if not alvo:
        return 0
    try:
        n = apagar(url, key, [x["caminho"] for x in alvo])
    except Exception as e:
        print(f"ERRO apagando — parte pode ter ficado: {e}", file=sys.stderr)
        return 1
    print(f"apagados: {n} de {len(alvo)} ({mb:.1f} MB liberados)")
    return 0 if n == len(alvo) else 1


if __name__ == "__main__":
    sys.exit(main())
