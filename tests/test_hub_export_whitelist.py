"""
Lista branca dos arquivos públicos do hub (hub_export/) — o Mindassets é aberto a qualquer
cliente, e o arquivo enviado é o de PRODUÇÃO (não existe "dado de teste" separado).

O que estes testes TRAVAM, e por quê:
  1) nenhum payload, em nível nenhum, tem as chaves proibidas (email, whatsapp, photo, phone,
     token, key, secret, password, signed, _debug, service) — nem quando o banco e a memória do
     ciclo vêm "sujos" com elas (linha inteira despejada é o erro clássico que a lista branca evita);
  2) nenhum valor carrega a URL interna do Supabase nem um segredo;
  3) a rede embaixo dos construtores: se um construtor um dia deixar passar uma chave proibida,
     o run.py recusa o arquivo (erro, nada enviado) em vez de publicá-lo;
  4) a flag do /admin com nome proibido (o único nome de campo que vem do banco) fica de fora
     sem derrubar o config.json.
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import test_hub_export_fixtures as F  # noqa: E402
from hub_export import state as S  # noqa: E402
from hub_export.encode import FORBIDDEN_KEYS, forbidden_keys  # noqa: E402
from hub_export.registry import FILES  # noqa: E402
from hub_export.run import run_cycle  # noqa: E402

SUJEIRA = {"email": "a@b.c", "whatsapp": "+55", "photo": "x.jpg", "phone": "1", "token": "t",
           "key": "k", "secret": "s", "password": "p", "signed": "https://x?sig", "_debug": {},
           "service": "role", "Email": "A@B.C"}


def _sujo(obj):
    """Enfia as chaves proibidas em todo dict, recursivamente."""
    if isinstance(obj, dict):
        out = {k: _sujo(v) for k, v in obj.items()}
        out.update(copy.deepcopy(SUJEIRA))
        return out
    if isinstance(obj, list):
        return [_sujo(v) for v in obj]
    return obj


def mundo_sujo():
    http = F.FakeHTTP()
    # Onda 1b também: as linhas de notícia, o resumo (inclusive os itens jsonb), os relatórios e
    # o calendário (inclusive a regra de recorrência e os links) vêm com as chaves proibidas
    for nome in ("quotes", "commodities", "macro_indicators", "news_articles", "news_digest",
                 "exec_calendar_events", "exec_calendar_categories", "exec_calendar_data_windows"):
        http.database[nome] = _sujo(http.database[nome])
    http.database["rpc_rows"]["reports"] = _sujo(http.database["rpc_rows"]["reports"])
    for fn in ("get_heatmap_tickers", "get_commodity_groups"):   # nas flags, "key" é a coluna
        http.database["rpc"][fn] = _sujo(http.database["rpc"][fn])
    http.database["rpc"]["get_dashboard_flags"] += [{"key": k, "enabled": True}
                                                    for k in ("token", "Secret", "service")]
    mem = {"quotes": _sujo(F.memory_quotes()), "macro": _sujo(F.memory_macro())}
    return http, mem


def payloads(http, mem, state=None):
    from hub_export import weeks as W
    from hub_export.registry import spec_for
    src, _ = F.make_sources(http=http, mem=mem,
                            state=F.previous_state() if state is None else state)
    out = {name: spec.builder(src) for name, spec in FILES.items()}
    for lab in (F.WEEK_OLD, F.WEEK_PREV, F.WEEK_CUR):
        out[W.file_name(lab)] = spec_for(W.file_name(lab)).builder(src)
    return out


def _strings(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield str(k)
            yield from _strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _strings(v)
    elif isinstance(obj, str):
        yield obj


def test_lista_dos_proibidos_e_a_do_brief():
    assert FORBIDDEN_KEYS == {"email", "whatsapp", "photo", "phone", "token", "key", "secret",
                              "password", "signed", "_debug", "service"}


def test_nenhuma_chave_proibida_nos_payloads_das_fixtures():
    for name, obj in payloads(F.FakeHTTP(), F.memory()).items():
        assert forbidden_keys(obj) == [], name


def test_nem_com_banco_e_memoria_sujos():
    http, mem = mundo_sujo()
    for name, obj in payloads(http, mem).items():
        assert forbidden_keys(obj) == [], name
    # o caminho do CLI (sem memória, lendo tudo do banco) também
    http, _ = mundo_sujo()
    for name, obj in payloads(http, None, state=S.empty_state()).items():
        assert forbidden_keys(obj) == [], name


def test_nenhum_valor_leva_url_do_supabase_ou_segredo():
    http, mem = mundo_sujo()
    texto = "\n".join(s for obj in payloads(http, mem).values() for s in _strings(obj))
    for proibido in (F.SUPA_URL, "supabase.co", F.SUPA_KEY, F.FEEDER_KEY):
        assert proibido not in texto


def test_detector_acha_em_qualquer_nivel_e_sem_diferenciar_maiusculas():
    obj = {"a": [{"b": {"Token": 1}}, [{"KEY": 2}]], "ok": {"keys": 1, "tokenizer": 2}}
    assert forbidden_keys(obj) == ["$.a[0].b.Token", "$.a[1][0].KEY"]


def test_run_recusa_publicar_payload_com_chave_proibida(tmp_path, monkeypatch):
    from hub_export import registry
    original = registry.FILES["intraday.json"]
    vazado = registry.Spec(lambda src: {**original.builder(src), "token": "x"}, original.cadence,
                           original.gzip, original.pulse, original.max_bytes)
    monkeypatch.setitem(registry.FILES, "intraday.json", vazado)
    http = F.FakeHTTP()
    res = {r.name: r for r in run_cycle(env=F.env(), http=http, now=F.NOW, memory=F.memory(),
                                         state_file=tmp_path / "st.json", **F.run_kw())}
    assert res["intraday.json"].action == "erro" and "chave proibida" in res["intraday.json"].reason
    assert "intraday.json" not in http.uploads
    assert res["live.json"].action == "enviado"               # os outros seguem


def test_flag_com_nome_proibido_fica_de_fora_sem_derrubar_o_config(caplog):
    http = F.FakeHTTP()
    http.database["rpc"]["get_dashboard_flags"].append({"key": "token", "enabled": True})
    cfg = payloads(http, F.memory())["config.json"]
    assert "token" not in cfg["flags"] and cfg["flags"]["heatmap"] is True
    assert json.dumps(cfg)                                    # serializável
