# -*- coding: utf-8 -*-
"""
RESUMO DA ÚLTIMA HORA — o "o que está rolando" da coluna ao lado do feed da Home (2026-10-21).

O QUE FAZ
  De hora em hora (janelas 07–08 … 19–20, horário de Brasília) e uma vez para a madrugada
  (20h da véspera → 07h), escolhe até 4 histórias da janela por uma REGRA FIXA, sem IA, e pede
  a UMA IA, numa chamada curta, duas frases em inglês de até 45 palavras, feitas só com os
  títulos. Grava uma linha em `news_digest`. A Home lê pela RPC `get_news_digest`: admin
  sempre; cliente só com a flag `home_digest` ligada (SQL em
  IBBA-Research-Dashboard/admin/supabase_news_digest.sql).

QUANDO RODA
  Dentro do hunt-loop (hunt.py, a cada 5 min), DEPOIS do passo dos takes, com a mesma trava
  (LLM_TAKES_ENABLED=1 + GITHUB_ACTIONS=true). O agendador do GitHub não serve: o cron de hora
  em hora do watchdog rodou ~5 vezes por dia. A janela que termina às E fica pronta às E+10min
  (dá tempo de a IA classificar as últimas manchetes) e aceita tentativas da IA até E+25min;
  de E+25 a E+40 publica só a LISTA das manchetes escolhidas, sem IA; depois disso a janela é
  pulada (robô parado: a Home continua mostrando a anterior, com a hora dela).

QUANTO CUSTA (estudo de 21/10/2026, artifact AHm6tb4vcwz2xQ7HQGxwg4)
  ≈ 300–450 tokens por chamada, contra 7.600–8.600 de cada take; no máximo 14 chamadas por dia.
  Usa primeiro o projeto Gemini do CLIPPING (CLIPPING_TRANSLATE_KEY: 500 por dia, usa ~7–10).
  O projeto dos takes só entra se a classificação do dia estiver abaixo de 80% do teto diário
  (LLM_DAILY_BUDGET). Mistral (limite zero na conta) e Z.AI (lenta, ~1 min) ficam fora. Janela
  sem nada relevante não chama IA nenhuma. Os tokens de cada chamada ficam gravados na linha.

SEGURANÇA DO TEXTO (é texto automático na frente do cliente)
  A IA só recebe títulos. A resposta é DESCARTADA (e a hora sai como lista) se tiver mais de
  70 palavras, número que não está nos títulos ou ticker de coberta que não foi dado.
  Nada aqui levanta exceção: o robô de notícias não pode cair por causa do resumo.
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import requests

log = logging.getLogger(__name__)

BRT = timezone(timedelta(hours=-3))       # o Brasil não tem horário de verão desde 2019
HOUR_FIRST, HOUR_LAST = 7, 19             # janelas de hora em hora começam às 7h … 19h (a última termina às 20h)
NIGHT_FROM, NIGHT_TO = 20, 7              # madrugada: 20h da véspera → 7h, publicada às 7h10
READY_MIN, RETRY_MIN, STALE_MIN = 10, 25, 40
MAX_ITEMS = {"hour": 4, "overnight": 5}
MAX_WORDS = {"hour": 45, "overnight": 55}
REJECT_WORDS = {"hour": 70, "overnight": 80}
TIMEOUT_S = 30
KEEP_DAYS = 45                            # linhas mais velhas que isso saem (uma vez por dia, na madrugada)
TAKES_BUDGET_SHARE = 0.80                 # projeto dos takes só entra abaixo de 80% do teto diário deles

# As cobertas como o classificador grava em take_covered_companies → ticker (o mesmo mapa da Home, _PULSE_CO)
COV = {"VALE": "VALE3", "CSN": "CSNA3", "CMIN": "CMIN3", "GERDAU": "GGBR4", "USIMINAS": "USIM5",
       "TERNIUM": "TX", "AURA": "AURA33", "SOUTHERN COPPER": "SCCO", "GRUPO MEXICO": "GMEXICOB",
       "SUZANO": "SUZB3", "KLABIN": "KLBN11", "IRANI": "RANI3", "CMPC": "CMPC", "COPEC": "COPEC"}
ALL_TICKERS = set(COV.values())
TIER1 = ("platts", "fastmarkets", "valor", "cvm", "b3", "reuters", "bloomberg")
_STOP = set("""a an the of to in on for at by with from and or as is are be its it this that after amid over
o a os as de da do das dos em no na nos nas por para com e ou que se um uma ao aos à às sobre após diz""".split())

SYSTEM = ("You write very short news briefs for institutional investors who follow Latin American "
          "steel, mining and pulp & paper equities.")
PROMPT = """Write a brief of at most {max_words} words, in at most 2 sentences, about the headlines below ({label}, Brasilia time).
Rules: use only facts stated in the headlines, with no added causes, context, forecasts or numbers; when tickers are given, put them in parentheses after the company name, e.g. Vale (VALE3); lead with what matters most for those companies and join headlines that tell the same story; neutral, factual tone, no advice; write in English even if a headline is in Portuguese.{previous}
Headlines (take = the read for the covered companies: + positive, - negative, = neutral):
{lines}
Answer only with JSON: {{"summary": "..."}}"""


# ── janelas ──────────────────────────────────────────────────────────────────────────────────────
def slot_bounds(kind: str, end: datetime) -> tuple:
    """(início, fim) da janela que termina em `end` (BRT)."""
    return (end - timedelta(hours=11), end) if kind == "overnight" else (end - timedelta(hours=1), end)


def due_slots(now: datetime | None = None) -> list:
    """As janelas que estão para sair AGORA: [(kind, início, fim, fase)], fase 'ai' ou 'list'."""
    now = (now or datetime.now(timezone.utc)).astimezone(BRT)
    top = now.replace(minute=0, second=0, microsecond=0)
    out = []
    for end in (top, top - timedelta(hours=1)):
        kind = None
        if HOUR_FIRST + 1 <= end.hour <= HOUR_LAST + 1:
            kind = "hour"
        elif end.hour == NIGHT_TO:
            kind = "overnight"
        if not kind:
            continue
        mins = (now - end).total_seconds() / 60
        if READY_MIN <= mins < STALE_MIN:
            s, e = slot_bounds(kind, end)
            out.append((kind, s, e, "ai" if mins < RETRY_MIN else "list"))
    return out


def slot_label(kind: str, start: datetime, end: datetime) -> str:
    return f"{start.astimezone(BRT):%H:%M}-{end.astimezone(BRT):%H:%M}" + (" overnight" if kind == "overnight" else "")


# ── a regra fixa (sem IA) ────────────────────────────────────────────────────────────────────────
def _ts(a):
    s = a.get("found_at") or a.get("published_at")
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")) if s else None
    except ValueError:
        return None


def _toks(t):
    t = re.sub(r"[^\w\s]", " ", (t or "").lower())
    return {w for w in t.split() if len(w) > 2 and w not in _STOP}


def _covs(a):
    return sorted({c.strip().upper() for c in str(a.get("take_covered_companies") or "").split(";") if c.strip()})


def _tier1(a):
    s = f"{a.get('source_name') or ''} {a.get('domain') or ''}".lower()
    return any(k in s for k in TIER1)


def _take(a):
    t = a.get("take_llm")
    return t if t in ("+", "-", "=") else None


def _cluster(items):
    groups = []
    for a in sorted(items, key=lambda x: _ts(x) or datetime.min.replace(tzinfo=timezone.utc)):
        ta, ca = _toks(a.get("title")), set(_covs(a))
        for g in groups:
            j = len(ta & g["toks"]) / max(1, len(ta | g["toks"]))
            if j >= 0.45 or (ca and ca & g["covs"] and len(ta & g["toks"]) >= 2):
                g["items"].append(a); g["toks"] |= ta; g["covs"] |= ca
                break
        else:
            groups.append({"items": [a], "toks": set(ta), "covs": set(ca)})
    return groups


def _score(g):
    """Nota da história + a manchete que a representa. ≥ 2 entra (precisa de take ou de coberta)."""
    its = g["items"]
    srcs = sorted({(a.get("source_name") or a.get("domain") or "?") for a in its})
    best = max((2 if _take(a) in ("+", "-") else 1 if _take(a) == "=" else 0) for a in its)
    s = (3 if g["covs"] else 0) + best + (1 if any(_tier1(a) for a in its) else 0) + min(2, len(srcs) - 1)
    # "no take" = a IA leu e achou que não mexe com as cobertas: só entra se mais de uma fonte deu a história
    if all(a.get("take_llm") == "no take" for a in its) and len(srcs) < 2:
        s = min(s, 1)
    # boletim diário genérico ("[SMM ...] 20260909"): título sem notícia
    if all(re.search(r"\b20\d{6}\b", a.get("title") or "") for a in its):
        s -= 2
    rep = sorted(its, key=lambda a: (_tier1(a), bool(_covs(a)), _take(a) in ("+", "-"), -len(a.get("title") or "")),
                 reverse=True)[0]
    return s, rep, srcs


def select(items, previous_titles=(), kind="hour"):
    """Até MAX_ITEMS histórias da janela, sem repetir o que entrou nas 3 janelas anteriores."""
    prev = [(_toks(t), set()) for t in previous_titles if t]
    scored = []
    for g in _cluster([a for a in items if (a.get("title") or "").strip()]):
        s, rep, srcs = _score(g)
        if s < 2:
            continue
        tk = _toks(rep.get("title"))
        if any(len(tk & p) / max(1, len(tk | p)) >= 0.3 for p, _ in prev):
            continue
        scored.append((s, _ts(rep) or datetime.min.replace(tzinfo=timezone.utc), rep, srcs, sorted(g["covs"])))
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    out = []
    for s, _, rep, srcs, covs in scored[:MAX_ITEMS.get(kind, 4)]:
        out.append({"url": rep.get("url"), "title": (rep.get("title") or "").strip()[:240],
                    "source": ", ".join(srcs[:3]), "take": _take(rep),
                    "tickers": [COV.get(c, c) for c in covs], "at": rep.get("found_at") or rep.get("published_at"),
                    "score": s})
    return out


# ── o pedido à IA e a conferência da resposta ────────────────────────────────────────────────────
def build_prompt(items, kind, label, previous=None):
    """`previous`: o(s) resumo(s) das janelas anteriores — texto ou lista, o mais recente primeiro (usa até 2)."""
    lines = []
    for it in items:
        tk = f"; tickers: {', '.join(it['tickers'])}" if it.get("tickers") else ""
        lines.append(f"- [{it.get('take') or 'no take'}] {it['title']} ({it.get('source') or '?'}{tk})")
    if isinstance(previous, str):
        previous = [previous]
    previous = [p.strip()[:400] for p in (previous or []) if p and p.strip()][:2]
    prev = ""
    if previous:
        ditos = " / ".join(f'"{p}"' for p in previous)
        prev = f"\nEarlier briefs said: {ditos}. Do not repeat them; mention a story again only for what a headline adds."
    return PROMPT.format(max_words=MAX_WORDS.get(kind, 45), label=label, previous=prev, lines="\n".join(lines))


_MIXED = re.compile(r"\b(?=\w*[A-Za-z])(?=\w*\d)\w+\b")          # palavras com letra E número (VALE3, 2Q26, 1st)
_NUM = re.compile(r"\d+(?:[.,]\d+)*")


def _numbers(text: str) -> set:
    """Os números de um texto, normalizados (1,36 = 1.36; 15,000 = 15.000 = 15000; '15 mil' também 15000)."""
    text = _MIXED.sub(" ", text or "")
    out = set()
    for m in _NUM.finditer(text):
        s = m.group(0)
        parts = re.split(r"[.,]", s)
        if len(parts) > 1 and all(len(p) == 3 for p in parts[1:]):
            n = "".join(parts)
        elif len(parts) == 2:
            n = parts[0] + "." + parts[1]
        else:
            n = "".join(parts)
        n = n.lstrip("0") or "0"
        if n.startswith("."):
            n = "0" + n
        out.add(n)
        rest = text[m.end():m.end() + 5].lower()
        if rest.startswith((" mil", " k")) and "." not in n:
            out.add(n + "000")
    return out


def check_summary(summary: str, items: list, kind: str = "hour") -> str | None:
    """None se o texto pode sair; senão, o motivo da recusa."""
    s = (summary or "").strip()
    if not s:
        return "vazio"
    if len(re.findall(r"[A-Za-zÀ-ÿ]{2,}", s)) < 6:   # "..." do molde do raciocínio, ou meia frase
        return "curto demais"
    if len(s.split()) > REJECT_WORDS.get(kind, 70):
        return f"{len(s.split())} palavras"
    titulos = " ".join(it.get("title") or "" for it in items)
    sobra = _numbers(s) - _numbers(titulos)
    if sobra:
        return f"número fora dos títulos: {sorted(sobra)}"
    dados = {t for it in items for t in (it.get("tickers") or [])}
    citados = {t for t in re.findall(r"\b[A-Z][A-Z0-9]{1,8}\b", s)
               if t in ALL_TICKERS or re.fullmatch(r"[A-Z]{4}\d{1,2}", t)}
    if citados - dados:
        return f"ticker não dado: {sorted(citados - dados)}"
    return None


def _clean(summary: str) -> str:
    s = re.sub(r"\s+", " ", summary or "").strip().strip('"').strip()
    return s


def parse_answer(raw) -> str | None:
    """O texto do campo summary. Aceita JSON puro e JSON embrulhado (modelo que 'pensa' antes, cercas ```)."""
    if not raw:
        return None
    try:
        from .llm_take import _json_candidates
        cands = list(_json_candidates(raw))
    except Exception:
        cands = [raw]
    for c in cands:
        try:
            obj = json.loads(c)
        except Exception:
            continue
        if isinstance(obj, dict) and isinstance(obj.get("summary"), str) and obj["summary"].strip():
            return _clean(obj["summary"])
    return None


def _gemini(cfg: dict, prompt: str):
    """(texto bruto, tokens de entrada, tokens de saída) ou None."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{cfg['model']}:generateContent"
    payload = {
        "systemInstruction": {"parts": [{"text": SYSTEM}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.2, "maxOutputTokens": 1024, "responseMimeType": "application/json",
            "responseSchema": {"type": "OBJECT", "properties": {"summary": {"type": "STRING"}},
                               "required": ["summary"]}},
    }
    r = requests.post(url, json=payload, timeout=TIMEOUT_S,
                      headers={"x-goog-api-key": cfg["key"], "Content-Type": "application/json"})
    if not r.ok:
        log.warning("Resumo da hora: %s HTTP %s", cfg["rotulo"], r.status_code)
        return None
    j = r.json()
    raw = j["candidates"][0]["content"]["parts"][0]["text"]
    u = j.get("usageMetadata") or {}
    return raw, u.get("promptTokenCount"), u.get("candidatesTokenCount")


def write_summary(items, kind, label, previous, url=None, H=None):
    """Pede o texto à IA. Devolve {'summary','model','tokens_in','tokens_out'} ou None (falhou ou foi recusado)."""
    from .llm_take import PROVIDERS
    model = (PROVIDERS.get("gemini") or {}).get("model") or "gemini-3.1-flash-lite"
    prompt = build_prompt(items, kind, label, previous)
    tentativas = []
    k1 = os.environ.get("CLIPPING_TRANSLATE_KEY", "")
    if k1:
        tentativas.append({"rotulo": "gemini (projeto clipping)", "key": k1, "model": model})
    k2 = os.environ.get("GEMINI_API_KEY", "")
    for i in range(2):
        if i == 1:                                  # o projeto dos takes só se o do clipping não deu
            if not k2 or not _takes_have_room(url, H):
                break
            tentativas.append({"rotulo": "gemini (projeto takes)", "key": k2, "model": model})
        if i >= len(tentativas):
            continue
        cfg = tentativas[i]
        try:
            got = _gemini(cfg, prompt)
        except Exception as e:
            log.warning("Resumo da hora: %s levantou %s", cfg["rotulo"], type(e).__name__)
            continue
        if not got:
            continue
        raw, tin, tout = got
        text = parse_answer(raw)
        motivo = check_summary(text, items, kind) if text else "resposta sem summary"
        if motivo:
            log.warning("Resumo da hora: texto de %s recusado (%s)", cfg["rotulo"], motivo)
            continue
        return {"summary": text, "model": f"{model} · {cfg['rotulo'].split('(')[-1].rstrip(')')}",
                "tokens_in": tin, "tokens_out": tout}
    return None


def _takes_have_room(url, H) -> bool:
    try:
        from .llm_shadow import DAILY_BUDGET, _used_today
        used = _used_today(url, H) if url else 0
        ok = used < TAKES_BUDGET_SHARE * DAILY_BUDGET
        if not ok:
            log.info("Resumo da hora: projeto dos takes com %d/%d hoje — não uso", used, DAILY_BUDGET)
        return ok
    except Exception:
        return False


# ── banco ────────────────────────────────────────────────────────────────────────────────────────
def _enabled() -> bool:
    return (os.environ.get("LLM_TAKES_ENABLED") == "1" and os.environ.get("GITHUB_ACTIONS") == "true"
            and os.environ.get("DIGEST_ENABLED", "1") != "0")


def _supa():
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    if not (url and key):
        return None, None
    return url, {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def _iso(d: datetime) -> str:
    return d.astimezone(timezone.utc).isoformat()


def _done_path():
    base = os.environ.get("RUNNER_TEMP")
    return os.path.join(base, "digest_done.json") if base else None


def _done_load() -> set:
    p = _done_path()
    try:
        with open(p, encoding="utf-8") as f:
            return set(json.load(f))
    except Exception:
        return set()


def _done_add(slot_iso: str) -> None:
    p = _done_path()
    if not p:
        return
    s = _done_load(); s.add(slot_iso)
    try:
        with open(p, "w", encoding="utf-8") as f:
            json.dump(sorted(s)[-60:], f)
    except Exception:
        pass


def _existing(url, H, starts) -> set | None:
    """Quais janelas já estão gravadas. None = a tabela não existe / o banco não respondeu."""
    lst = ",".join(f'"{_iso(s)}"' for s in starts)
    try:
        r = requests.get(f"{url}/rest/v1/news_digest?select=slot_start&slot_start=in.({quote(lst, safe=',')})",
                         headers=H, timeout=15)
    except Exception as e:
        log.warning("Resumo da hora: banco não respondeu (%s)", type(e).__name__)
        return None
    if not r.ok:
        if r.status_code in (400, 404) and "news_digest" in r.text:
            log.warning("Resumo da hora: tabela news_digest ausente — rode admin/supabase_news_digest.sql")
        else:
            log.warning("Resumo da hora: consulta %s: %s", r.status_code, r.text[:160])
        return None
    out = set()
    for row in r.json():
        try:
            out.add(_iso(datetime.fromisoformat(str(row["slot_start"]).replace("Z", "+00:00"))))
        except Exception:
            pass
    return out


def _news(url, H, start, end):
    q = (f"{url}/rest/v1/news_articles?select=url,title,source_name,domain,take_llm,take_covered_companies,"
         f"found_at,published_at&include_in_report=eq.true&found_at=gte.{quote(_iso(start))}"
         f"&found_at=lt.{quote(_iso(end))}&order=found_at.asc&limit=400")
    r = requests.get(q, headers=H, timeout=20)
    r.raise_for_status()
    return r.json()


def _previous(url, H, start):
    q = (f"{url}/rest/v1/news_digest?select=slot_start,slot_end,status,summary,items"
         f"&slot_start=lt.{quote(_iso(start))}&order=slot_start.desc&limit=3")
    try:
        r = requests.get(q, headers=H, timeout=15)
        return r.json() if r.ok else []
    except Exception:
        return []


def _save(url, H, row) -> bool:
    try:
        r = requests.post(f"{url}/rest/v1/news_digest?on_conflict=slot_start", json=[row],
                          headers={**H, "Prefer": "resolution=merge-duplicates,return=minimal"}, timeout=15)
    except Exception as e:
        log.warning("Resumo da hora: gravação falhou (%s)", type(e).__name__)
        return False
    if not r.ok:
        log.warning("Resumo da hora: gravação %s: %s", r.status_code, r.text[:160])
    return r.ok


def _prune(url, H, now) -> None:
    cut = quote(_iso(now - timedelta(days=KEEP_DAYS)))
    try:
        requests.delete(f"{url}/rest/v1/news_digest?slot_start=lt.{cut}",
                        headers={**H, "Prefer": "return=minimal"}, timeout=15)
    except Exception:
        pass


def process_slot(url, H, kind, start, end, phase, now) -> str:
    """Monta e grava UMA janela. Devolve o que aconteceu: 'ok' | 'list' | 'quiet' | 'retry' | 'erro'."""
    try:
        news = _news(url, H, start, end)
    except Exception as e:
        log.warning("Resumo da hora: notícias da janela não vieram (%s)", type(e).__name__)
        return "erro"
    prev_rows = _previous(url, H, start)
    prev_titles = [it.get("title") for row in prev_rows for it in (row.get("items") or []) if isinstance(it, dict)]
    items = select(news, prev_titles, kind)
    row = {"slot_start": _iso(start), "slot_end": _iso(end), "kind": kind, "items": items,
           "summary": None, "model": None, "tokens_in": None, "tokens_out": None}
    label = slot_label(kind, start, end)
    if not items:
        ok = _save(url, H, dict(row, status="quiet"))
        log.info("Resumo da hora: %s sem nada relevante (%d notícias)", label, len(news))
        return "quiet" if ok else "erro"
    if phase == "ai":
        # os resumos das 3 janelas anteriores (até 2): a mesma história volta só com o que ela trouxer de novo
        anteriores = [r.get("summary") for r in prev_rows if r.get("status") == "ok" and r.get("summary")][:2]
        got = write_summary(items, kind, label, anteriores, url, H)
        if not got:
            log.info("Resumo da hora: %s sem texto da IA — tento de novo na próxima rodada", label)
            return "retry"
        ok = _save(url, H, dict(row, status="ok", **got))
        log.info("Resumo da hora: %s publicado (%s, %s+%s tokens, %d manchetes)", label, got["model"],
                 got["tokens_in"], got["tokens_out"], len(items))
        return "ok" if ok else "erro"
    ok = _save(url, H, dict(row, status="list"))
    log.info("Resumo da hora: %s saiu só como lista (a IA não respondeu a tempo)", label)
    return "list" if ok else "erro"


def run_digest(now: datetime | None = None) -> None:
    """O passo do hunt-loop. Não-fatal: qualquer erro vira log, nunca exceção."""
    try:
        if not _enabled():
            return
        now = (now or datetime.now(timezone.utc)).astimezone(BRT)
        slots = due_slots(now)
        if not slots:
            return
        feitos = _done_load()
        pend = [s for s in slots if _iso(s[1]) not in feitos]
        if not pend:
            return
        url, H = _supa()
        if not url:
            return
        ja = _existing(url, H, [s[1] for s in pend])
        if ja is None:
            return                                   # sem tabela (SQL não rodado) ou banco fora: não gasta IA
        for kind, start, end, phase in pend:
            if _iso(start) in ja:
                _done_add(_iso(start)); continue
            res = process_slot(url, H, kind, start, end, phase, now)
            if res in ("ok", "list", "quiet"):
                _done_add(_iso(start))
                if kind == "overnight":
                    _prune(url, H, now)
    except Exception as e:
        log.warning("Resumo da hora: passo falhou (não-fatal): %s", e)
