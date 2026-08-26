"""
Histórico DIÁRIO completo de cada papel da aba Market (tabela `quote_history`).

POR QUE ESTE MÓDULO EXISTE
--------------------------
`quotes.daily` é a série LEVE (mensal antigo + 1 ano diário, ~11 KB por papel): a home
baixa os 47 de uma vez, então ela não pode engordar. Mas o cliente às vezes quer olhar
20 anos com detalhe diário — e aí entra a `quote_history`, carregada sob demanda só
para os papéis que estão no gráfico.

TRÊS ARMADILHAS DO YAHOO, TODAS MEDIDAS (não reintroduzir)
----------------------------------------------------------
1. **`range=max&interval=1d` NÃO devolve diário.** O Yahoo ignora o `interval` quando
   a janela é longa e rebaixa para barras mensais: VALE3 volta com 320 pontos em 26
   anos (~30 dias de espaçamento). O caminho que devolve diário de verdade é
   `period1`/`period2` em epoch — a mesma VALE3 volta com 6.681 pontos, espaçamento
   1,45 dia (= dias úteis). É por isso que `fetch_full()` usa period1/period2.

2. **Histórico NÃO é imutável: o split reescreve a série para trás.** O campo que
   guardamos é `indicators.quote[0].close`, que é ajustado por DESDOBRAMENTO mas não
   por dividendo. Medido: o fechamento da NVDA em 01/03/2024 aparece hoje como 82,28;
   na época foi ~822 (split 10:1 em 10/06/2024). Ou seja, um robô que só faz append
   ficaria com a metade velha em outra escala e o gráfico ganharia um DEGRAU FALSO,
   sem erro nenhum no log. Por isso `fetch_recent()` pede `events=div,split` (custa
   zero: a resposta inteira tem 1,7 KB) e quem detecta um split novo re-puxa a série
   inteira daquele papel.

3. **Feed morto se disfarça de mercado parado.** Medido no apagão da Bolsa de Santiago
   (SGO): de 20/07 a 25/08/2026 o Yahoo devolveu 27 barras diárias de CMPC/COPEC/CAP com
   `open = high = low = close` = o último preço real e **volume zero**. Não houve erro,
   HTTP 200, série completa — e o gráfico virou uma linha reta enquanto a CAP caía 16% e
   a COPEC subia 7% de verdade. O `close` da barra chegou a ficar **19% errado**.
   Por isso `_closes()` DESCARTA barra fantasma: volume zero **e** OHLC todos iguais.
   Um dia sem negócio nenhum também cai fora — e deve mesmo: "ninguém negociou" não é
   "fechou no mesmo preço", e um buraco na série é honesto onde um carimbo é mentira.
   (A série INTRADIÁRIA do mesmo Yahoo continuou correta o tempo todo: é dela que o
   `scripts/repair_phantom_bars.py` reconstrói o que foi perdido.)

ECONOMIA QUE ISTO TRAZ (medido)
-------------------------------
A manutenção antiga re-baixava a série toda (`1y` + `max`) de cada papel, todo dia:
61 KB por papel = 2,8 MB/dia só para descobrir UM fechamento novo. O append usa a
janela de 5 dias: 1,7 KB por papel = 79 KB/dia. **36× menos.** Histórico é histórico.
"""
from __future__ import annotations

import logging
import os
import time

import requests

log = logging.getLogger(__name__)

# Mesma rotação de host/retry do prices.py: o Yahoo devolve 429/401 para IP de
# datacenter (GitHub Actions) e alterna melhor entre os dois hosts.
_HOSTS = ["https://query1.finance.yahoo.com", "https://query2.finance.yahoo.com"]
_RETRIES = 3
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json",
}
DAY = 86400


# ───────────────────────────── Yahoo ─────────────────────────────
def _chart(symbol: str, params: dict, timeout: int = 30) -> dict | None:
    """GET no chart endpoint com rotação de host + backoff. Devolve o `result[0]`."""
    last = None
    for attempt in range(_RETRIES):
        host = _HOSTS[attempt % len(_HOSTS)]
        url = f"{host}/v8/finance/chart/{symbol}"
        try:
            r = requests.get(url, params=params, headers=_HEADERS, timeout=timeout)
            if r.status_code in (429, 401, 403):
                last = f"HTTP {r.status_code}"
                time.sleep(1.5 * (attempt + 1))
                continue
            r.raise_for_status()
            res = (r.json().get("chart", {}) or {}).get("result") or []
            return res[0] if res else None
        except Exception as e:      # rede, JSON quebrado, etc.
            last = repr(e)
            time.sleep(1.0 * (attempt + 1))
    log.warning("quote_history: %s falhou após %d tentativas (%s)", symbol, _RETRIES, last)
    return None


def eh_fantasma(o, h, l, c, v) -> bool:
    """A barra é um CARIMBO do feed, não um pregão? (armadilha 3 da docstring do módulo)

    Assinatura: `open == high == low == close` **e** volume zero. As duas condições juntas,
    nunca uma só — volume zero sozinho derrubaria índice (^BVSP não reporta volume) e OHLC
    igual sozinho derrubaria papel travado em leilão legítimo.

    Conservador de propósito: se o Yahoo não mandou volume ou não mandou OHLC, devolve
    False e a barra PASSA. Melhor deixar entrar uma fantasma do que descartar pregão bom.
    """
    if v is None or v > 0:
        return False
    if o is None or h is None or l is None or c is None:
        return False
    return o == h == l == c


def _closes(res: dict | None, symbol: str = "") -> list[list]:
    """`result` do Yahoo → [[epoch, close], ...] sem nulos e SEM barra fantasma, crescente."""
    if not res:
        return []
    ts = res.get("timestamp") or []
    quote = ((res.get("indicators", {}) or {}).get("quote") or [{}])[0]
    closes = quote.get("close") or []
    col = lambda k: (quote.get(k) or [None] * len(ts))
    o, h, l, v = col("open"), col("high"), col("low"), col("volume")
    out, fantasmas = [], 0
    for i, (t, c) in enumerate(zip(ts, closes)):
        if c is None:
            continue
        if eh_fantasma(o[i], h[i], l[i], c, v[i]):
            fantasmas += 1
            continue
        out.append([int(t), round(float(c), 4)])
    if fantasmas:
        log.warning("quote_history: %s — %d barra(s) FANTASMA descartada(s) "
                    "(volume 0 e OHLC iguais: feed parado, não mercado parado)",
                    symbol or "?", fantasmas)
    out.sort(key=lambda p: p[0])
    return out


def _splits(res: dict | None) -> list[int]:
    """Epochs dos splits reportados na janela pedida (só vem com events=split)."""
    if not res:
        return []
    ev = (res.get("events") or {}).get("splits") or {}
    out = []
    for v in ev.values():
        d = (v or {}).get("date")
        if d is not None:
            out.append(int(d))
    return sorted(out)


def fetch_full(symbol: str) -> list[list]:
    """Série diária COMPLETA. period1/period2 — NUNCA range=max (ver docstring do módulo)."""
    res = _chart(symbol, {"period1": 0, "period2": int(time.time()), "interval": "1d"})
    return _closes(res, symbol)


def fetch_recent(symbol: str, days: int = 7) -> tuple[list[list], list[int]]:
    """Janela curta do dia a dia: (fechamentos, epochs de split). ~1,7 KB de resposta."""
    res = _chart(symbol, {"range": f"{max(1, int(days))}d", "interval": "1d",
                          "events": "div,split"}, timeout=15)
    return _closes(res, symbol), _splits(res)


# ───────────────────────────── merge ─────────────────────────────
def _day(ts: int) -> int:
    return int(ts) // DAY


def merge(existing: list[list], incoming: list[list]) -> list[list]:
    """Costura a janela nova na série guardada, POR DIA (o epoch do Yahoo carrega o
    horário de abertura do pregão e varia com horário de verão — comparar epoch cru
    duplicaria o mesmo dia). O ponto novo vence o antigo do mesmo dia: fechamento em
    formação de hoje é substituído pelo definitivo amanhã."""
    by_day = {_day(t): [int(t), v] for t, v in (existing or [])}
    for t, v in (incoming or []):
        by_day[_day(t)] = [int(t), v]
    return [by_day[k] for k in sorted(by_day)]


# ─────────────────────────── Supabase ────────────────────────────
def _env() -> tuple[str, str]:
    return (os.environ.get("SUPABASE_URL", "").rstrip("/"),
            os.environ.get("SUPABASE_SERVICE_KEY", ""))


def _headers(key: str, extra: dict | None = None) -> dict:
    h = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    if extra:
        h.update(extra)
    return h


def load_state() -> dict[str, dict] | None:
    """Estado leve de todos os papéis (SEM trazer o jsonb gigante da série).

    Devolve **None** quando a tabela ainda NÃO EXISTE — diferente de {} (existe e está
    vazia). Quem chama usa isso para cair no caminho antigo: o código vai para o ar
    antes de o SQL ser rodado, e nesse intervalo a home não pode ficar sem série.
    """
    url, key = _env()
    if not url or not key:
        return None
    try:
        r = requests.get(
            f"{url}/rest/v1/quote_history?select=ticker,points,first_ts,last_ts,last_split_ts,updated_at",
            headers=_headers(key), timeout=20)
        if r.status_code in (404, 406) or "PGRST205" in (r.text or ""):
            log.info("quote_history: tabela ainda não existe (rodar admin/supabase_quote_history.sql)")
            return None
        if not r.ok:
            log.warning("quote_history: leitura de estado %s: %s", r.status_code, r.text[:160])
            return None
        return {row["ticker"]: row for row in r.json()}
    except Exception as e:
        log.warning("quote_history: leitura de estado falhou: %s", e)
        return None


def load_series(ticker: str) -> list[list]:
    """Série guardada de UM papel. Usada só onde é inevitável (derivar a série leve
    quando o append não devolveu nada); o dia a dia usa append(), que não baixa nada."""
    url, key = _env()
    if not url or not key:
        return []
    try:
        r = requests.get(f"{url}/rest/v1/quote_history?ticker=eq.{ticker}&select=daily",
                         headers=_headers(key), timeout=30)
        if not r.ok:
            return []
        rows = r.json()
        return (rows[0].get("daily") or []) if rows else []
    except Exception as e:
        log.warning("quote_history: leitura de %s falhou: %s", ticker, e)
        return []


def append(ticker: str, points: list[list], last_split_ts: int | None = None,
           replace: bool = False) -> dict | None:
    """Manda SÓ os fechamentos novos; o Postgres costura (append_quote_history).
    Devolve {'n_points','ts_first','ts_last'} ou None. Não baixa a série guardada —
    é o ponto do desenho: histórico que já aconteceu não trafega todo dia."""
    url, key = _env()
    if not url or not key or not points:
        return None
    body = {"p_ticker": ticker, "p_points": points}
    if replace:
        body["p_replace"] = True
    if last_split_ts is not None:
        body["p_last_split_ts"] = int(last_split_ts)
    try:
        r = requests.post(f"{url}/rest/v1/rpc/append_quote_history", json=body,
                          headers=_headers(key), timeout=45)
        if not r.ok:
            log.warning("quote_history: append %s erro %s: %s", ticker, r.status_code, r.text[:200])
            return None
        out = r.json()
        return (out[0] if isinstance(out, list) and out else out) or None
    except Exception as e:
        log.warning("quote_history: append %s exceção: %s", ticker, e)
        return None


def save(ticker: str, daily: list[list], last_split_ts: int | None = None) -> bool:
    """Grava a série INTEIRA (backfill e pós-split), substituindo o que estivesse lá.
    Passa pelo MESMO caminho do append para a série leve de quotes.daily ser
    regravada no mesmo passo — as duas nunca divergem."""
    return bool(append(ticker, daily, last_split_ts=last_split_ts, replace=True))
