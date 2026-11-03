"""hub_export — os arquivos públicos da Commodities Datahouse (hub de dashboards do Itaú).

Transforma o que os robôs já gravam (Supabase + a memória do ciclo do hunt.py) nos arquivos
públicos lidos pela seção `commoditiesDatahouse` do hub e os envia ao Dashboard Data Feeder
(initialize → PUT assinado → complete), que os publica no Mindassets:

    https://mindassets.cloud.itau.com.br/research/equity/market-data/
        metals-mining-pulp-paper_commodities-datahouse/<arquivo>

Onda 1a (contrato: PLANO.md, Apêndice B — research-dashboards-migracao):
    config.json        WARM (15 min)   flags do /admin, bolsas, grupos do heatmap, grupos de commodities
    live.json          HOT  (ciclo)    cotações, commodities (com o risquinho), macro — pulsação de 10 min
    intraday.json      HOT  (ciclo)    curva do dia por papel
    quotes-daily.json  COLD (60 min)   série diária por papel, só os últimos 400 dias
  live, intraday e quotes-daily vão com conteúdo GZIP e nome .json (o hub reconhece pelos bytes);
  o config.json vai em texto.

USO
  No loop: o fim de cada ciclo do hunt.py chama `run_cycle()` quando HUB_EXPORT_ENABLED=1 (variável
  do repositório, lida no `env` do hunt-loop.yml; o modo --playwright não exporta). Qualquer erro
  vira uma linha de aviso e o ciclo do robô segue.
  À mão (da raiz do repositório):
      python -m hub_export --env-file <.env> --all --out <pasta>   # grava, não envia (prévia local)
      python -m hub_export --env-file <.env> --all --dry-run       # diz o que enviaria
      python -m hub_export --only config,live [--force]            # envia
  No GitHub: workflow `hub-export.yml` (workflow_dispatch, entradas `files` e `force`).

VARIÁVEIS
  SUPABASE_URL, SUPABASE_SERVICE_KEY            leitura (service key, só GET/RPC)
  DASHBOARD_FEEDER_URL, DASHBOARD_FEEDER_PROJECT_ID, DASHBOARD_FEEDER_API_KEY   envio
  HUB_EXPORT_ENABLED     "1" liga o passo no hunt.py (padrão: desligado)
  HUB_EXPORT_STATE_DIR   pasta do estado fora do Actions (padrão: a temporária do sistema)
  HUB_EXPORT_PUBLIC_BASE endereço da cópia publicada (padrão: o Mindassets acima)

ESTADO
  $RUNNER_TEMP/hub_export_state.json no Actions (morre com o job, ~6 h); fora dele,
  $HUB_EXPORT_STATE_DIR ou a pasta temporária. Por arquivo: hash do último envio, quando,
  revisão, último build e último erro; mais os caches de dados (cotações, macro, commodities,
  spark, diário). Corrompido = começa do zero. Runner novo = compara com a cópia publicada antes
  de reenviar. Ver state.py.

TRÁFEGO DO SUPABASE: orçamento ≤ 5 MB/dia em regime — ver sources.py (memória do ciclo, sondas,
caches) e a linha "Supabase: …" que cada execução escreve no log.
"""
