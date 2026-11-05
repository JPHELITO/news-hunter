"""Conferências SÓ-LEITURA do banco: `python -m hub_export --env-file <.env> --dry-run --check`.

Respondem, com leituras mínimas pela API REST (poucas linhas, `select` explícito, contagem por
cabeçalho), o que o exportador da Onda 1b precisa saber e que nenhum repositório documenta
(`news_articles` não tem DDL versionado — pesquisa 1b-dados §0.1):
  • o TIPO de `published_at`, `found_at` e `take_llm_at`: um filtro `eq.<texto qualquer>` é
    recusado (HTTP 400, código 22007) por coluna de data/hora e aceito por coluna `text` — com
    `text`, o `gte.` do PostgREST compara letra a letra, e o exportador precisaria de outra margem;
  • se existem `id` e `created_at` (marca d'água do banco, se houver);
  • volumes (incluídas, sem `published_at`, primeiras datas), o resumo da hora, o calendário;
  • a permissão da service key na RPC `get_recent_reports_v3` (revoga `anon`, concede
    `authenticated`: a `service_role` deve manter EXECUTE pelo padrão do Supabase).
Não monta arquivo, não envia nada, não grava estado. Imprime só nomes de tipo, contagens, datas
e status HTTP — nunca URL, chave ou conteúdo de linha.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

from .encode import iso_utc
from .supa import SupaError

_TYPE_RE = re.compile(r"type ([a-z ]+?)(?::|$)")


def _col_type(supa, table: str, col: str) -> str:
    try:
        supa.select(table, "url", tag="check", limit=1, **{col: "eq.nao-e-data"})
        return "text (aceitou um texto qualquer: o gte. do PostgREST seria lexicográfico)"
    except SupaError as e:
        if e.status == 400 and e.code in ("22007", "22008"):
            m = _TYPE_RE.search(e.detail or "")
            return m.group(1).strip() if m else "data/hora (tipo não informado)"
        if e.code == "42703":
            return "coluna ausente"
        return f"indeterminado ({e})"


def _has_col(supa, table: str, col: str) -> str:
    try:
        supa.select(table, col, tag="check", limit=1)
        return "existe"
    except SupaError as e:
        return "ausente" if e.code == "42703" else f"indeterminado ({e})"


def _first(supa, table: str, col: str, **flt) -> str:
    try:
        rows = supa.select(table, col, tag="check", order=f"{col}.asc.nullslast", limit=1, **flt)
    except SupaError as e:
        return f"erro ({e})"
    return (iso_utc(rows[0].get(col)) or "—") if rows else "—"


def _count(supa, table: str, sel: str, **flt) -> str:
    try:
        n = supa.count(table, sel, tag="check", **flt)
    except SupaError as e:
        return f"erro ({e})"
    return "?" if n is None else f"{n:,}".replace(",", ".")


def _before_archive(supa) -> str:
    """Incluídas cuja data de referência cai ANTES da 1ª semana do arquivo (2026-w23): não vão a
    arquivo semanal nenhum. Candidatas = publicadas antes do início e achadas até 8 dias depois
    (lidas só url e as duas datas); a regra da data esquisita decide em Python."""
    from .exporters.news_common import bucket_iso, item_from_row
    from .weeks import FIRST_WEEK, label_of, start
    s = start(FIRST_WEEK)
    lim = (s + timedelta(days=8)).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        rows = supa.select("news_articles", "url,title,published_at,found_at", tag="check",
                           include_in_report="eq.true", published_at=f"lt.{iso_utc(s)}",
                           found_at=f"lt.{lim}")
    except SupaError as e:
        return f"datas antes do arquivo: erro ({e})"
    fora = 0
    for r in rows:
        item, _ = item_from_row(r)
        if item and label_of(datetime.strptime(bucket_iso(item), "%Y-%m-%dT%H:%M:%SZ")) < FIRST_WEEK:
            fora += 1
    return (f"{fora} incluída(s) com data de referência antes da {FIRST_WEEK} (fora de todo "
            f"arquivo semanal; {len(rows)} candidata(s) lidas)")


def run_checks(supa) -> list[str]:
    """As linhas do relatório, na ordem. Cada conferência isola o próprio erro."""
    out = []
    t = "news_articles"
    for col in ("published_at", "found_at", "take_llm_at"):
        out.append(f"{t}.{col}: {_col_type(supa, t, col)}")
    for col in ("id", "created_at"):
        out.append(f"{t}.{col}: {_has_col(supa, t, col)}")
    out.append(f"{t}: {_count(supa, t, 'url', include_in_report='eq.true')} incluídas, "
               f"{_count(supa, t, 'url', include_in_report='eq.true', published_at='is.null')} "
               f"sem published_at, {_count(supa, t, 'url')} no total")
    out.append(f"{t}: 1º found_at das incluídas {_first(supa, t, 'found_at', include_in_report='eq.true')}"
               f" · 1º published_at {_first(supa, t, 'published_at', include_in_report='eq.true')}")
    out.append(f"{t}: {_count(supa, t, 'url', include_in_report='eq.true', take_llm='is.null')} "
               f"incluídas sem take da IA")
    out.append(f"{t}: {_before_archive(supa)}")

    try:
        rows, n = supa.select_count("news_digest", "slot_start", tag="check",
                                    order="slot_start.desc", limit=1)
        last = iso_utc(rows[0].get("slot_start")) if rows else "—"
        out.append(f"news_digest: {n} janela(s), a mais nova {last}")
    except SupaError as e:
        out.append(f"news_digest: erro ({e})")

    try:
        rows = supa.rpc("get_recent_reports_v3", "name,sector,link,date,companies", {"p_limit": 20},
                        tag="check")
        com_link = sum(1 for r in rows if r.get("link"))
        out.append(f"rpc get_recent_reports_v3 (service key): HTTP 200, {len(rows)} relatório(s), "
                   f"{com_link} com link")
    except SupaError as e:
        out.append(f"rpc get_recent_reports_v3 (service key): {e}")

    try:
        rows, n = supa.select_count("exec_calendar_events", "updated_at", tag="check",
                                    is_visible="eq.true", order="updated_at.desc.nullslast",
                                    limit=1)
        last = iso_utc(rows[0].get("updated_at")) if rows else "—"
        out.append(f"exec_calendar_events: {n} visível(is), max(updated_at) {last}, "
                   f"{_count(supa, 'exec_calendar_events', 'id')} no total, "
                   f"{_count(supa, 'exec_calendar_events', 'id', recurrence='not.is.null')} recorrente(s)")
    except SupaError as e:
        out.append(f"exec_calendar_events: erro ({e})")
    for fn, sel in (("get_exec_calendar_categories", "id,is_active"),
                    ("get_exec_calendar_data_windows", "id")):
        try:
            out.append(f"rpc {fn}: {len(supa.rpc(fn, sel, tag='check'))} linha(s)")
        except SupaError as e:
            out.append(f"rpc {fn}: {e}")

    try:
        flags = {r.get("key"): r.get("enabled") for r in supa.rpc("get_dashboard_flags", "key,enabled",
                                                                   tag="check")}
        out.append("flags: " + ", ".join(f"{k}={flags.get(k)}" for k in
                                         ("home_digest", "hub_home_digest", "exec_calendar",
                                          "hub_exec_calendar", "news_feed", "market_pulse")))
    except SupaError as e:
        out.append(f"flags: erro ({e})")
    out.append(f"Supabase: {supa.calls} chamada(s), {supa.wire_bytes:,} B de corpo pela rede + "
               f"{supa.header_bytes:,} B de cabeçalhos".replace(",", "."))
    return out
