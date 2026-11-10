"""history-index.json (COLD, texto, ~8 KB) — PLANO, Apêndice F.2.

`{ schemaVersion, generatedAt, updatedAt, items: [{ ticker, file, firstTs, lastTs, points,
lastSplitTs }] }`: os históricos JÁ PUBLICADOS (o hub nunca pede arquivo que não existe; papel fora
daqui = série leve, sem erro), na ordem do universo da Market. Serve ao hub ("há histórico? até
quando?") e à partida a frio do exportador (o registro nasce dele). `updatedAt` = o carimbo do dado
mais novo publicado (fora do hash). Zero leitura de banco: é derivado do registro (history.py); no
--out, dos arquivos da pasta.

Vai fora da cadência quando esta execução publicou um papel NOVO (a carga inicial aparece para o hub
no mesmo ciclo); a atualização diária de quem já está publicado espera o COLD. Com o registro
desconhecido (o índice publicado ilegível e o Mindassets parado) NÃO sai: um índice parcial
publicado faria a próxima partida a frio achar que falta tudo e refazer a carga.
"""
from __future__ import annotations


def build(src) -> dict:
    idx = src.history().index()
    return {
        "schemaVersion": 1,
        "generatedAt": src.generated_at,
        "updatedAt": idx["updatedAt"],
        "items": idx["items"],
    }


def urgent(src) -> bool:
    """O índice vai fora da cadência quando esta execução publicou um papel que ele não tinha."""
    store = src.history_if_loaded()
    return bool(store and store.registry_new)
