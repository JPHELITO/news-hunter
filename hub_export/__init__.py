"""hub_export — os arquivos públicos da Commodities Datahouse (hub de dashboards do Itaú).

Transforma o que os robôs já gravam (Supabase + a memória do ciclo do hunt.py) nos arquivos
públicos lidos pela seção `commoditiesDatahouse` do hub e os envia ao Dashboard Data Feeder
(initialize → PUT assinado → complete), que os publica no Mindassets:

    https://mindassets.cloud.itau.com.br/research/equity/market-data/
        metals-mining-pulp-paper_commodities-datahouse/<arquivo>

Onda 1a (contrato: PLANO.md, Apêndice B — research-dashboards-migracao):
    config.json        WARM (15 min)   flags do /admin, bolsas, grupos do heatmap, grupos de commodities
                                       (+ coveredCompanies, Onda 1b)
    live.json          HOT  (ciclo)    cotações, commodities (com o risquinho), macro — pulsação de 10 min
    intraday.json      HOT  (ciclo)    curva do dia por papel
    quotes-daily.json  COLD (60 min)   série diária por papel, só os últimos 400 dias
Onda 1b (contrato: PLANO.md, Apêndice E):
    news-latest.json   HOT  (ciclo)    as incluídas das últimas 48 h ou desde o início do Pulse (~79 h)
    news-week-<ano ISO>-w<NN>.json     COLD: uma por semana ISO (anterior e corrente; antigas quando
                                       mudam; carga inicial desde 2026-w23 aos poucos)
    news-index.json    COLD (60 min)   as semanas publicadas e as contagens
    news-digest.json   HOT  (ciclo)    as últimas 48 janelas do resumo da hora (fail-closed)
    reports.json       COLD (60 min)   últimos relatórios (RPC get_recent_reports_v3)
    calendar.json      WARM (15 min)   eventos (recorrência como regra), categorias, janelas de dado
Onda 2 (contrato: PLANO.md, Apêndice F):
    quotes-monthly.json     COLD       a cauda mensal da série leve (meses inteiros antes do corte)
    commodities-daily.json  COLD       a série diária SÓ das 3 de fonte livre (minério 62% TE, cobre, ouro)
    ratings.json            COLD       histórico OP/MP/UP/NR + alvo (fail-closed na `market_ratings`)
    history-<id>.json       família    histórico diário completo por papel da Market (55; nome F.0);
                                       ~1×/dia útil por papel, no máximo 2 por execução (history.py)
    history-index.json      COLD       os históricos já publicados
  Market Watch (WP2.4B; Apêndice F.6–F.11; mw.py) — grupo com teto de 4 envios por execução:
    mw-companies.json       COLD       empresas, capital, último aluguel e os FATOS do cabeçalho da
                                       Performance (% do float, recompra ativa, insiders 12 m)
    mw-short-interest.json  COLD       aluguel, visão "Todas": 100 dias por papel, em colunas
    mw-short-<empresa>.json família    a série inteira do aluguel de cada empresa visível
    mw-buybacks.json        COLD       programas de recompra (relidos 2×/dia)
    mw-insiders.json        COLD       14 meses de insiders, TODAS as linhas (paginado), em colunas
    mw-filings.json         COLD       os 600 comunicados mais recentes (link do ENET filtrado)
    Os textos livres saem com e-mails e telefones mascarados (contacts.py, decisão U6).
  live, intraday, quotes-daily, news-latest, news-week-*, news-digest, calendar, quotes-monthly,
  commodities-daily, ratings, history-* e mw-* (salvo o mw-companies) vão com conteúdo GZIP e nome
  .json (o hub reconhece pelos bytes); config, reports, news-index, history-index e mw-companies vão
  em texto.

USO
  No loop: o fim de cada ciclo do hunt.py chama `run_cycle()` quando HUB_EXPORT_ENABLED=1 (variável
  do repositório, lida no `env` do hunt-loop.yml) E dentro do GitHub Actions; o modo --playwright
  não exporta. Qualquer erro vira uma linha de aviso e o ciclo do robô segue.
  À mão (da raiz do repositório):
      python -m hub_export --env-file <.env> --all --out <pasta>   # grava, não envia (prévia local)
      python -m hub_export --env-file <.env> --out <pasta> --only history,history-index \
          --history VALE3.SA,SPX                                    # só alguns históricos
      python -m hub_export --env-file <.env> --all --dry-run       # diz o que enviaria
      python -m hub_export --env-file <.env> --dry-run --check     # conferências do banco
      python -m hub_export --only config,live [--force]            # envia
  No GitHub: workflow `hub-export.yml` (workflow_dispatch, entradas `files`, `force` e `rebuild`).

VARIÁVEIS
  SUPABASE_URL, SUPABASE_SERVICE_KEY            leitura (service key, só GET/HEAD/RPC)
  DASHBOARD_FEEDER_URL, DASHBOARD_FEEDER_PROJECT_ID, DASHBOARD_FEEDER_API_KEY   envio
  HUB_EXPORT_ENABLED     "1" liga o passo no hunt.py (padrão: desligado)
  HUB_EXPORT_STATE_DIR   pasta do estado fora do Actions (padrão: a temporária do sistema)
  HUB_EXPORT_PUBLIC_BASE endereço da cópia publicada (padrão: o Mindassets acima)

ESTADO
  $RUNNER_TEMP/hub_export_state.json no Actions (morre com o job, ~6 h); fora dele,
  $HUB_EXPORT_STATE_DIR ou a pasta temporária. Por arquivo: hash do último envio, quando,
  revisão, último build e último erro; mais os caches de dados (cotações, macro, commodities,
  spark, diário, flags, resumo, calendário). As notícias têm o arquivo próprio
  `hub_export_state_news.json` ao lado (news.py). Corrompido = começa do zero. Runner novo =
  semeia das cópias publicadas e compara com elas antes de reenviar. Ver state.py. O Market Watch
  também tem o arquivo próprio `hub_export_state_mw.json` (mw.py).

TRÁFEGO DO SUPABASE: orçamento ≤ 5 MB/dia em regime para o exportador inteiro — ver sources.py e
news.py (memória do ciclo, sondas, caches, incremental com margem, varreduras) e a linha
"Supabase: …" que cada execução escreve no log (corpo + cabeçalhos, por fonte).
"""
