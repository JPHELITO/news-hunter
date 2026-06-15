# -*- coding: utf-8 -*-
"""Semeia/atualiza os prompts do analista na tabela Supabase `llm_prompts` (RLS fechada).

Os prompts (system + few-shot) são IP e NÃO são versionados no repo público — vivem
no Supabase (produção) e numa pasta local de dev. Este script lê os arquivos-mestre e
faz upsert na tabela. Rodar após a migration SQL e a cada vez que iterar o prompt.

Uso:
  python scripts/seed_llm_prompts.py [PASTA_DOS_PROMPTS]
  (default: $LLM_PROMPTS_DIR; arquivos take_system.txt + take_fewshot.jsonl)
"""
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

NH = Path(__file__).resolve().parent.parent
load_dotenv(NH / ".env")

pdir = Path(sys.argv[1] if len(sys.argv) > 1 else os.environ.get("LLM_PROMPTS_DIR", ""))
if not pdir or not pdir.exists():
    sys.exit("Defina a pasta dos prompts (arg 1 ou $LLM_PROMPTS_DIR). Não encontrada: " + str(pdir))

sys_path, fs_path = pdir / "take_system.txt", pdir / "take_fewshot.jsonl"
if not sys_path.exists() or not fs_path.exists():
    sys.exit(f"Faltam take_system.txt / take_fewshot.jsonl em {pdir}")

url = os.environ["SUPABASE_URL"].rstrip("/")
key = os.environ["SUPABASE_SERVICE_KEY"]
H = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json",
     "Prefer": "resolution=merge-duplicates,return=minimal"}

rows = [
    {"name": "take_system", "content": sys_path.read_text(encoding="utf-8")},
    {"name": "take_fewshot", "content": fs_path.read_text(encoding="utf-8")},
]
r = requests.post(f"{url}/rest/v1/llm_prompts?on_conflict=name", json=rows, headers=H, timeout=20)
if r.ok:
    n_fs = len([l for l in rows[1]["content"].splitlines() if l.strip()])
    print(f"OK — prompts semeados (system {len(rows[0]['content'])} chars, few-shot {n_fs} exemplos).")
else:
    print(f"FALHA {r.status_code}: {r.text[:200]}")
    if r.status_code in (400, 404) and "llm_prompts" in r.text:
        print(">> rode antes o scripts/llm_shadow_migration.sql no Supabase SQL Editor.")
        sys.exit(1)
