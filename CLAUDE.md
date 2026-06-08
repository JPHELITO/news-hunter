# CLAUDE.md — News Hunter (backend/pipeline da dashboard IBBA)

> Backend que alimenta a **IBBA Research Dashboard**. Roda no GitHub Actions, escreve no Supabase.
> Frontend = repo separado `IBBA-Research-Dashboard` (tem seu próprio CLAUDE.md).
> **Clipinator é OUTRO produto** (`C:\Users\João Paulo Helito\clipinator\`) — não usar, não considerar, não importar nada de lá.

## O que faz
Coleta notícias (Steel & Mining + Pulp & Paper) e cotações/commodities/macro, classifica e faz push pro Supabase. O dashboard lê do Supabase via REST.

## Arquitetura (`hunter/`)
| Arquivo | Papel |
|---|---|
| `fetcher.py` | Orquestra coleta. `HEADERS` = UA de browser real; `_http_get` tenta requests→curl_cffi no 403. Logging diagnóstico por feed. Chama reuters + html scrapers. |
| `sources.py` | Lista de feeds RSS oficiais. `filter=False`=feed temático (aceita tudo); `filter=True`=feed geral (aplica keywords). |
| `html_scrapers.py` | Scrapers de sites sem RSS (IBRAM, Instituto Aço Brasil). `_title_from_url` deriva título do slug. |
| `reuters_scraper.py` | Reuters via Arc sitemap, `xml.etree.ElementTree` (stdlib, **NÃO** lxml). |
| `platts_scraper.py` | Playwright: headlines + **preços** Platts via DOM (AG-Grid). `_PRICE_SYMBOLS`={IODBZ00, STHRZ02, STCBM00, PLVHA00}. `get_platts_prices()` devolve cache. |
| `fastmarkets_scraper.py` | Playwright: headlines Fastmarkets P&P. |
| `filter.py` | `filter_articles` — keyword matching (normaliza acentos/unicode, word-boundary). |
| `config.py` | `ALL_KEYWORDS` (S&M + P&P + regulatório), `WINDOW_HOURS=6`, `SUPABASE_TABLE="news_articles"`. |
| `classify.py` | Classificação básica: sector/sentiment/tickers. |
| `news_take_classifier.py` | **Classificador determinístico** (regra/dicionário/score, auditável — NÃO black-box). `classify_article_take` gera: include_in_report, exclusion_reason, take(+/−/=/review), take_reason, sector, region, topics, covered_companies, confidence, matched_rules. Aliases ambíguos ("vale","tx","aura") exigem `_COMPANY_CONTEXT_RE`. Hard-exclui cripto/política/crime sem empresa coberta. ~96 testes em `tests/`. |
| `prices.py` | Cotações Yahoo + commodities + macro BCB. Ver tabela de cadência abaixo. |
| `sync.py` | Push Supabase. |

## hunt.py (runner)
`python hunt.py` (RSS+prices) ou `python hunt.py --playwright` (+ Platts/Fastmarkets + preços Platts).
Ordem: fetch → filter → classify básico → classify take → `push_articles` → `record_run` → `push_take_fields` → update preços.

## Supabase (env: `SUPABASE_URL`, `SUPABASE_SERVICE_KEY` — **nunca hardcodar credenciais**)
| Tabela | Conteúdo |
|---|---|
| `news_articles` | Notícias + campos de take. **INSERT com `resolution=ignore-duplicates`**: URL repetida é IGNORADA → `found_at` original imutável (notícia antiga não "renasce" como recente). |
| `hunter_runs` | Timestamp de cada run (dashboard mostra "sincronizado há X min"). Limpa >24h. |
| `quotes`, `commodities`, `macro` | Upsert via `_supa_upsert` (on_conflict). |

Campos de take vão por `push_take_fields` (PATCH separado). Migration SQL (já rodada) na docstring de `sync.py`.

## Commodities — cadência (importante)
| Commodity | Fonte | Workflow | Frequência |
|---|---|---|---|
| Copper (HG=F), Gold (GC=F) | Yahoo | `hunt-loop` | ~5 min (mercado ao vivo) |
| Iron Ore 61% (IODBZ00), HRC China (STHRZ02), Rebar Turkey (STCBM00), Met Coal (PLVHA00) | Platts | `hunt-playwright` | ~30 min (assessment diário; 30min é folgado) |

`update_platts_commodities` só roda com `--playwright` (guard `if platts_prices:`). `COMMODITIES_ORDER` define ordem no dashboard.

## Workflows (`.github/workflows/`)
- `hunt-loop.yml` — `python hunt.py` em loop `sleep 300s` por ~5h30, self-restarting; cron `*/30` backup. SEM playwright.
- `hunt-playwright.yml` — `python hunt.py --playwright`, cron `*/30`. Container `mcr.microsoft.com/playwright/python:v1.52.0-jammy`.
- `hunt.yml` — manual/emergência (workflow_dispatch). Legado.

## Regras invioláveis
1. **Nunca hardcodar login/senha/credenciais** — só via env/secrets.
2. **`resolution=ignore-duplicates`** no `push_articles` — não trocar por merge (quebra a imutabilidade do `found_at`).
3. Classificador é **determinístico/auditável** — nada de LLM/black-box. Mudou regra? Rode `pytest`.
4. Reuters usa **ElementTree stdlib**, não lxml. Playwright pinado em **1.52.0** (container e requirements).
5. Sessão Platts expira (~periódica): renovar via `scripts/capture_platts_session.py`.
6. **Não importar/usar nada do clipinator.**

## Scripts
`scripts/capture_platts_session.py` (renova sessão Platts), `backfill_take.py --all`, `backfill_classify.py`.
