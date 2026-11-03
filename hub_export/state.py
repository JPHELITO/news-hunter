"""Estado do exportador em disco + a checagem da cópia PUBLICADA (partida a frio).

ONDE: `$RUNNER_TEMP/hub_export_state.json` no GitHub Actions (o runner esvazia a pasta a cada
job, ~6 h — molde do `digest_done.json` do resumo da hora e da memória de takes do sync.py);
fora dele, `$HUB_EXPORT_STATE_DIR` ou a pasta temporária do sistema. Sem tabela nova no Supabase.

O QUE GUARDA:
  files       por arquivo: hash do último ENVIO, quando foi enviado, revisão do Feeder, quando
              foi montado pela última vez (é isso que a cadência mede) e o último erro;
  quotes      último estado conhecido das cotações (+ curva do dia) — a memória do ciclo é
              PARCIAL (o Yahoo às vezes não devolve um papel) e vai por cima disto;
  macro       idem para os indicadores macro;
  commodities as linhas leves da última leitura (reserva se o banco falhar num ciclo);
  spark       cache do `commodities.spark` por código (só rebaixa quem mudou);
  daily       cache do `quotes.daily` por papel (só rebaixa quem mudou).

Arquivo ilegível/corrompido = começar do zero (nunca derruba o ciclo).

PARTIDA A FRIO de um arquivo (sem hash no estado): antes de enviar, baixa a cópia publicada no
Mindassets, descomprime se for gzip e calcula o mesmo hash. Igual → registra e não envia.
404 ou qualquer erro → envia. Se o endereço não responde (rede), as demais checagens da mesma
execução nem tentam: cada uma esperaria o timeout de novo e estouraria o orçamento de tempo.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

import requests

from .encode import content_hash, maybe_gunzip

log = logging.getLogger(__name__)

STATE_FILE = "hub_export_state.json"
STATE_VERSION = 1
DEFAULT_PUBLIC_BASE = ("https://mindassets.cloud.itau.com.br/research/equity/market-data/"
                       "metals-mining-pulp-paper_commodities-datahouse")
PUBLIC_TIMEOUT_S = 10.0


def state_path(env=None) -> Path:
    env = os.environ if env is None else env
    base = env.get("RUNNER_TEMP") or env.get("HUB_EXPORT_STATE_DIR") or tempfile.gettempdir()
    return Path(base) / STATE_FILE


def empty_state() -> dict:
    return {"version": STATE_VERSION, "files": {},
            "quotes": {"rows": {}, "probe": None, "checked": []},
            "macro": {"rows": {}, "probe": None, "checked": []},
            "commodities": {"rows": []},
            "spark": {},
            "daily": {"rows": {}, "probe": None}}


def load(path: Path) -> dict:
    """Lê o estado. Ausente → vazio; ilegível, de outra versão ou com forma errada → vazio + aviso."""
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return empty_state()
    except OSError as e:
        log.warning("estado ilegível (%s) — começando do zero", type(e).__name__)
        return empty_state()
    try:
        data = json.loads(raw)
    except ValueError:
        log.warning("estado corrompido — começando do zero")
        return empty_state()
    if not isinstance(data, dict) or data.get("version") != STATE_VERSION:
        log.warning("estado de outra versão/forma — começando do zero")
        return empty_state()
    base = empty_state()
    for k, v in base.items():
        if not isinstance(data.get(k), type(v)):
            data[k] = v
    return data


def save(path: Path, state: dict) -> bool:
    """Grava com troca atômica (arquivo pela metade nunca é lido). Falha = aviso, não exceção."""
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(state, ensure_ascii=False, separators=(",", ":")),
                       encoding="utf-8")
        os.replace(tmp, path)
        return True
    except (OSError, TypeError, ValueError) as e:
        log.warning("estado não gravado (%s)", type(e).__name__)
        return False


class PublishedCopies:
    """A cópia publicada de cada arquivo, baixada no máximo uma vez por execução."""

    def __init__(self, base: str | None = None, http=None, timeout: float = PUBLIC_TIMEOUT_S,
                 env=None):
        env = os.environ if env is None else env
        self.base = (base or env.get("HUB_EXPORT_PUBLIC_BASE") or DEFAULT_PUBLIC_BASE).rstrip("/")
        self.http = http or requests.Session()
        self.timeout = timeout
        self._docs: dict[str, dict | None] = {}
        self._unreachable = False

    def get(self, name: str) -> dict | None:
        """O JSON publicado (já descomprimido), ou None se não existe / não deu para ler."""
        if name in self._docs:
            return self._docs[name]
        doc = None
        host = urlsplit(self.base).hostname
        if not self._unreachable:
            try:
                r = self.http.request("GET", f"{self.base}/{name}",
                                      headers={"Cache-Control": "no-cache"}, timeout=self.timeout)
                if r.status_code == 200:
                    parsed = json.loads(maybe_gunzip(r.content).decode("utf-8"))
                    doc = parsed if isinstance(parsed, dict) else None
                elif r.status_code != 404:
                    log.info("cópia publicada de %s: HTTP %s (host %s) — vai enviar",
                             name, r.status_code, host)
            except (requests.ConnectionError, requests.Timeout) as e:
                self._unreachable = True
                log.info("cópia publicada de %s: %s (host %s) — vai enviar",
                         name, type(e).__name__, host)
            except Exception as e:      # gzip/JSON ruim: tratar como "não existe"
                log.info("cópia publicada de %s ilegível (%s) — vai enviar", name, type(e).__name__)
        self._docs[name] = doc
        return doc

    def content_hash(self, name: str) -> str | None:
        doc = self.get(name)
        return content_hash(doc) if doc is not None else None
