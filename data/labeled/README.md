# Gabarito de takes (ground truth)

Solte aqui os **PDFs de news com takes** que você classificou à mão. Eles servem
de **gabarito** para medir e ajustar o classificador determinístico
(`hunter/news_take_classifier.py`) — **não** são lidos em runtime pelo pipeline.

## Fluxo (roda no terminal — não estoura tokens)

```bash
# 1) Inspecionar a estrutura de UM pdf (saída curta) — só na 1ª vez, p/ calibrar
python scripts/extract_labeled_takes.py --inspect "data/labeled/SEU_ARQUIVO.pdf"

# 2) Extrair todos os PDFs desta pasta -> golden_takes.csv
python scripts/extract_labeled_takes.py

# 3) Avaliar o classificador contra o gabarito (imprime só métricas + erros)
python scripts/eval_classifier.py
```

## Formato do gabarito (`golden_takes.csv`)

| coluna        | descrição                                   |
|---------------|---------------------------------------------|
| `source_file` | nome do PDF de origem                       |
| `headline`    | manchete                                    |
| `gold_take`   | take que você deu: `+`, `-` ou `=`          |
| `gold_include`| `true`/`false` — entrou no relatório?       |
| `raw_take`    | texto bruto do take antes de normalizar     |
| `notes`       | observação livre (opcional)                 |

Os PDFs (`*.pdf`) são ignorados pelo git (ver `.gitignore`). O `golden_takes.csv`
pode ser versionado como fixture de regressão, se você quiser.
