-- ════════════════════════════════════════════════════════════════════════════
-- FASE 3 (LLM takes — modo SHADOW) — rodar UMA vez no Supabase → SQL Editor → Run.
-- Seguro/idempotente (IF NOT EXISTS). Não toca em nada que já existe.
-- ════════════════════════════════════════════════════════════════════════════

-- 1) Colunas do shadow na news_articles. A IA grava SÓ aqui; o `take` publicado
--    (Market Pulse + badges) NÃO muda durante o shadow — zero risco pro cliente.
alter table news_articles
  add column if not exists take_llm          text,
  add column if not exists take_llm_model    text,
  add column if not exists take_llm_at        timestamptz,
  add column if not exists take_llm_attempts int default 0;

-- 2) Racional da IA (BACKSTAGE). RLS LIGADA sem policy => só a service key (pipeline)
--    lê/escreve; anon/authenticated (dashboard) NÃO acessam. Nunca exposto ao cliente.
create table if not exists llm_take_log (
  url        text primary key,
  reason     text,
  model      text,
  attempts   int,
  created_at timestamptz default now()
);
alter table llm_take_log enable row level security;

-- 3) Prompts do analista (IP — fora do repo público). Mesma proteção (RLS sem policy).
--    O pipeline lê daqui em runtime; eu populo via service key (seed_llm_prompts.py).
create table if not exists llm_prompts (
  name       text primary key,
  content    text,
  version    int default 1,
  updated_at timestamptz default now()
);
alter table llm_prompts enable row level security;
