"""
Os 3 NEWS HIGHLIGHTS do blast de WhatsApp — a única parte do blast que precisa de IA.

DIVISÃO DE TRABALHO (decidida em 2026-09-01, e é o coração do desenho)
---------------------------------------------------------------------
O blast tem três seções. Duas delas são DADO, não julgamento:

  HOW MARKETS ARE TRADING  -> preços que já estão no nosso banco, ao vivo
  HEADLINES S&M / P&P      -> as manchetes que o próprio analista selecionou, com o take
                              e o setor que ele mesmo carimbou

Essas duas o front monta sozinho, na hora, sem IA nenhuma e sem risco de o modelo
reescrever número ou "corrigir" manchete. Sobra UMA pergunta que é genuinamente de
julgamento — quais são as 3 notícias que realmente importam hoje? — e é só ela que vem
para cá. Uma chamada de IA por blast.

QUAL IA ATENDE
--------------
A menos sobrecarregada, medida no banco e não no chute (ver `escolher_provedores`). O
princípio é o oposto do que quebrou a cadeia dos takes em agosto: lá a cascata usava o
primeiro que RESPONDIA, e o melhor modelo ficava intocado no fim da fila. Aqui a ordem é
recalculada a cada chamada, a partir do consumo do dia.

FORMATO
-------
A IA devolve JSON com as frases, e ponto. A pontuação do blast (o "-" na frente, o ";" no
fim de cada uma e o "." na última) é aplicada pelo front. Modelo não deve estar brigando
com regra de formatação — foi metade do prompt antigo do Copilot, e é trabalho de código.
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from urllib.parse import quote

import requests

log = logging.getLogger(__name__)

# Teto diário de cada pool, do que foi medido em 2026-08/09 (ver a memória do projeto).
# Gemini: RPD 500 POR PROJETO Google — por isso as duas chaves são pools separados.
CAPACIDADE = {
    "gemini_clipping": 500,    # projeto dedicado ao clipping (~7 chamadas/dia)
    "gemini_takes":    500,    # projeto dos takes (~250-280/dia)
    "mistral":         300,    # de volta desde 01/09; teto conservador, não medido
    "zai":            1000,    # ocioso, porém LENTO (55-66s por chamada)
}


def _carga_hoje() -> dict:
    """
    Quantas chamadas cada pool já gastou HOJE. Só conta o que dá para medir de verdade.

    Os takes gravam quem os classificou em `news_articles.take_llm_model`, então o consumo
    do projeto compartilhado e o da Mistral/Z.AI saem do banco. O projeto DEDICADO do
    clipping não tem contador — e não precisa: ele roda ~7 vezes por dia contra 500, então
    qualquer estimativa razoável o deixa em primeiro. Contamos os jobs de clipping do dia
    vezes 7 para não fingir que é zero.
    """
    carga = {k: 0 for k in CAPACIDADE}
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    if not (url and key):
        return carga
    h = {"apikey": key, "Authorization": f"Bearer {key}"}
    # ⚠️ quote(): sem escapar, o "+00:00" do ISO vira ESPAÇO na query e o PostgREST
    # devolve 400. O erro caía no except e a carga voltava zerada em silêncio — ou seja,
    # o "menos sobrecarregado" viraria sempre a ordem padrão, sem ninguém notar.
    t0 = quote(datetime.now(timezone.utc).replace(hour=0, minute=0, second=0,
                                                  microsecond=0).isoformat())
    try:
        r = requests.get(f"{url}/rest/v1/news_articles?select=take_llm_model"
                         f"&take_llm_at=gte.{t0}&limit=5000", headers=h, timeout=20)
        r.raise_for_status()
        for row in r.json():
            m = str(row.get("take_llm_model") or "")
            if m.startswith("gemini"):
                carga["gemini_takes"] += 1
            elif m.startswith("mistral"):
                carga["mistral"] += 1
            elif m.startswith("glm"):
                carga["zai"] += 1
    except Exception as e:
        log.warning("blast: nao consegui medir a carga dos takes (%s) - sigo pela ordem padrao", e)
    try:
        r = requests.get(f"{url}/rest/v1/clipping_jobs?select=id&created_at=gte.{t0}&limit=200",
                         headers=h, timeout=20)
        r.raise_for_status()
        carga["gemini_clipping"] = len(r.json()) * 7        # ~7 traduções por clipping
    except Exception:
        pass
    return carga


# Abaixo desta folga o pool está "sobrecarregado" e sai da disputa.
FOLGA_MINIMA = float(os.environ.get("BLAST_FOLGA_MINIMA", "0.20"))

# Erro medido de cada família em 2026-08-06 (% de julgamento claramente errado, nas fontes
# curadas): gemini 0,8% · mistral 4,7% · glm ~25%. Menor é melhor.
QUALIDADE = {"gemini_clipping": 0, "gemini_takes": 0, "mistral": 1, "zai": 2}


def escolher_provedores() -> list:
    """
    Quem atende o blast: **o mais ocioso ENTRE OS BONS**, não o mais ocioso na marra.

    A regra literal "sempre o mais ocioso" foi testada e descartada em 2026-09-01: ela
    colocava a Mistral em primeiro só porque estava zerada, e a Mistral erra 4,7% contra
    0,8% do Gemini. Deixar o pior modelo escrevendo o texto que vai para o cliente é
    exatamente o erro de agosto de cabeça para baixo — lá a cascata premiava quem
    respondia primeiro, aqui premiaria quem trabalhava menos. Nos dois casos a qualidade
    não era a variável de decisão, e deveria ser.

    Então a ordem é, nesta sequência:
      1) pools SOBRECARREGADOS saem (folga < FOLGA_MINIMA) — é aqui que mora o pedido de
         não pesar em quem está cheio;
      2) modelos LENTOS por último (a Z.AI leva ~1 min; o analista está às 6 da manhã);
      3) melhor QUALIDADE medida primeiro;
      4) empatou em qualidade, ganha o MAIS OCIOSO — o que separa as duas chaves Gemini e
         faz o balanceamento de carga acontecer de fato.

    Na prática do dia a dia isso dá o projeto dedicado do clipping (98% livre e melhor
    modelo), e a carga só migra quando ele realmente encher.
    """
    from hunter.llm_take import PROVIDERS as _P
    gem = (_P.get("gemini") or {}).get("model") or "gemini-3.1-flash-lite"
    zai = (_P.get("zai") or {}).get("model") or "glm-4.5-flash"
    mis = (_P.get("mistral") or {}).get("model") or "mistral-medium-latest"

    defs = {
        "gemini_clipping": {"rotulo": "gemini (projeto clipping)", "kind": "gemini", "lento": False,
                            "key": os.environ.get("CLIPPING_TRANSLATE_KEY", ""), "model": gem},
        "gemini_takes":    {"rotulo": "gemini (projeto takes)", "kind": "gemini", "lento": False,
                            "key": os.environ.get("GEMINI_API_KEY", ""), "model": gem},
        "mistral":         {"rotulo": "mistral", "kind": "openai", "lento": False,
                            "key": os.environ.get("MISTRAL_API_KEY", ""), "model": mis,
                            "url": "https://api.mistral.ai/v1/chat/completions"},
        "zai":             {"rotulo": "z.ai (GLM)", "kind": "openai", "lento": True,
                            "key": os.environ.get("ZAI_API_KEY", ""), "model": zai,
                            "url": "https://api.z.ai/api/paas/v4/chat/completions"},
    }
    carga = _carga_hoje()

    def _folga(nome):
        teto = CAPACIDADE.get(nome, 1) or 1
        return max(0.0, 1.0 - carga.get(nome, 0) / teto)

    vivos = [(nome, d) for nome, d in defs.items() if d["key"]]
    folgados = [(n, d) for n, d in vivos if _folga(n) >= FOLGA_MINIMA]
    if not folgados:
        # Todo mundo apertado: em vez de desistir do blast, usa o menos pior e diz no log.
        log.warning("blast: nenhum provedor com folga >= %.0f%% (carga %s) — vou no mais ocioso",
                    FOLGA_MINIMA * 100, carga)
        folgados = sorted(vivos, key=lambda it: -_folga(it[0]))[:1]

    ordem = sorted(folgados, key=lambda it: (1 if it[1]["lento"] else 0,
                                             QUALIDADE.get(it[0], 9),
                                             -_folga(it[0])))
    log.info("blast: carga de hoje %s -> ordem %s", carga, [n for n, _ in ordem])
    return [dict(d, nome=n, folga=round(_folga(n), 3)) for n, d in ordem]


SYSTEM = (
    "Você é um analista sell-side de commodities do Itaú BBA, cobrindo Steel & Mining, "
    "Pulp & Paper e mercados globais, com foco em China, Brasil, EUA e Europa. "
    "Escreve para investidores institucionais."
)

INSTRUCAO = """Abaixo está a seleção de notícias do clipping de hoje — as MESMAS que vão para o
Word e o e-mail — com o setor e o take direcional que o analista carimbou, e o TEXTO de cada uma.

Sua tarefa: escrever até {n} destaques, em português, para o blast da manhã.

O que faz um destaque BOM:
- traz NÚMERO quando o texto tem (preço, tonelagem, variação %, prazo); é o que separa um destaque
  de uma manchete reescrita;
- pode JUNTAR duas ou três notícias que contam a mesma história (ex.: dois assessments na mesma
  direção, ou um preço e a razão dele) — se elas se explicam, valem mais juntas que separadas;
- escolhe pela RELEVÂNCIA para quem investe em Steel & Mining e Pulp & Paper, não pela ordem da
  lista nem pelo tamanho do texto;
- vale ler o conjunto: se três notícias apontam para o mesmo lado, isso é o destaque.

Critérios de seleção:
- no máximo {n} destaques; pode haver menos, e pode haver nenhum;
- priorize China, Brasil, EUA, Europa, empresas relevantes, preços, regulação, logística, clima,
  geopolítica, oferta, demanda, custos e dinâmica competitiva;
- notícia recorrente de preço ou utilização só entra se houver variação relevante, movimento forte,
  surpresa, aceleração ou desaceleração material;
- movimento marginal ou rotineiro NÃO entra;
- notícia institucional (evento, nomeação, estudo, prêmio) NÃO entra.

Como escrever:
- uma frase por destaque, densa, do tamanho de uma linha e meia no máximo;
- evite linguagem determinística: prefira "pode" a "deve";
- NÃO invente número que não esteja no texto, e não arredonde a ponto de mudar o sentido;
- cotações como USD XX, BRL XX, EUR XX; tonelada SEMPRE como USD XX/ton (nunca /t); comparações como YoY, QoQ, WoW;
- não explique seu raciocínio nem liste o que descartou;
- NÃO coloque marcador, numeração, ponto e vírgula ou ponto final: só a frase.

Responda SOMENTE com JSON: {{"destaques": ["...", "..."]}}

NOTÍCIAS:
{noticias}"""


def _chamar(cfg: dict, prompt: str):
    """Uma chamada. Devolve a lista de destaques ou None se o provedor falhou."""
    if cfg["kind"] == "gemini":
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{cfg['model']}:generateContent")
        payload = {
            "systemInstruction": {"parts": [{"text": SYSTEM}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.2, "maxOutputTokens": 2048,
                "responseMimeType": "application/json",
                "responseSchema": {"type": "OBJECT", "properties": {
                    "destaques": {"type": "ARRAY", "items": {"type": "STRING"}}},
                    "required": ["destaques"]}},
        }
        r = requests.post(url, json=payload, timeout=90,
                          headers={"x-goog-api-key": cfg["key"], "Content-Type": "application/json"})
        if not r.ok:
            log.warning("blast: %s HTTP %s", cfg["rotulo"], r.status_code)
            return None
        raw = r.json()["candidates"][0]["content"]["parts"][0]["text"]
    else:
        payload = {"model": cfg["model"], "temperature": 0.2,
                   "response_format": {"type": "json_object"},
                   "messages": [{"role": "system", "content": SYSTEM},
                                {"role": "user", "content": prompt}]}
        r = requests.post(cfg["url"], json=payload, timeout=150,
                          headers={"Authorization": f"Bearer {cfg['key']}",
                                   "Content-Type": "application/json"})
        if not r.ok:
            log.warning("blast: %s HTTP %s", cfg["rotulo"], r.status_code)
            return None
        raw = r.json()["choices"][0]["message"]["content"]

    try:
        out = json.loads(raw)
    except Exception:                       # modelos "thinking" embrulham o JSON
        try:
            from hunter.llm_take import _json_candidates
            out = next((c for c in _json_candidates(raw)
                        if isinstance(c, dict) and "destaques" in c), None)
        except Exception:
            out = None
    if not isinstance(out, dict):
        return None
    destaques = [str(d).strip().rstrip(";.").strip()
                 for d in (out.get("destaques") or []) if str(d).strip()]
    return destaques or None


# Quanto de cada notícia vai para a IA. Medido em 2026-09-01: os corpos guardados em
# `clipping_bodies` têm 600 a 3.000 caracteres, então 2.500 pega a matéria quase inteira na
# maioria e o começo (onde moram o lead e os números) no resto. O TETO TOTAL existe para um
# clipping gordo não virar um prompt de 40 mil tokens sem ninguém perceber: 60 mil caracteres
# são ~15 mil tokens, ou seja, o custo de duas manchetes classificadas. Barato continua barato.
CORPO_MAX_CHARS  = int(os.environ.get("BLAST_CORPO_MAX", "2500"))
PROMPT_MAX_CHARS = int(os.environ.get("BLAST_PROMPT_MAX", "60000"))


def _limpar(txt: str) -> str:
    """HTML do corpo -> texto corrido, sem tag e sem espaço duplicado."""
    if not txt:
        return ""
    try:
        from .html_utils import plain_text
        txt = plain_text(txt)
    except Exception:
        txt = re.sub(r"<[^>]+>", " ", txt)
    return re.sub(r"[ \t\r\n\f\v]+", " ", txt).strip()


def _linha(n: dict, corpo_max: int = CORPO_MAX_CHARS) -> str:
    """Uma notícia como a IA a enxerga: manchete, fonte, setor, take e o TEXTO dela."""
    take = {"+": "positivo", "-": "negativo", "=": "neutro"}.get(n.get("take"), "sem take")
    cab = (f"[{n.get('sector') or 'NR'} | take {take} | {n.get('source_name') or '?'}] "
           f"{n.get('title') or ''}")
    corpo = _limpar(n.get("body") or "")
    if not corpo:
        return cab
    if len(corpo) > corpo_max:
        corpo = corpo[:corpo_max].rsplit(" ", 1)[0] + "…"
    return cab + "\n    " + corpo


def _montar_noticias(noticias: list) -> str:
    """
    O bloco de notícias do prompt, respeitando o teto total.

    Encolhe o corpo de TODAS por igual em vez de cortar as últimas fora: uma notícia que
    some do prompt não pode ser escolhida, e quem decide o que é relevante é a IA — não a
    ordem em que o analista arrastou os itens.
    """
    corpo_max = CORPO_MAX_CHARS
    while True:
        bloco = "\n".join(_linha(x, corpo_max) for x in noticias)
        if len(bloco) <= PROMPT_MAX_CHARS or corpo_max <= 300:
            if len(bloco) > PROMPT_MAX_CHARS:
                log.warning("blast: %d notícias estouram o teto mesmo com corpo curto — "
                            "vou mandar %d caracteres", len(noticias), len(bloco))
            return bloco
        corpo_max = int(corpo_max * 0.7)
        log.info("blast: prompt grande (%d chars) — encolhendo o corpo p/ %d por notícia",
                 len(bloco), corpo_max)


def highlights(noticias: list, n: int = 3) -> dict:
    """
    Escreve os destaques. Devolve {'destaques': [...], 'provedor': 'nome', 'erro': None}.

    Nunca levanta exceção: o blast tem de sair mesmo sem IA — sem destaque ele ainda entrega
    preços e manchetes, que é a maior parte do valor. Seção vazia é prevista no formato.
    """
    if not noticias:
        return {"destaques": [], "provedor": None, "erro": "nenhuma notícia selecionada"}
    prompt = INSTRUCAO.format(n=n, noticias=_montar_noticias(noticias))
    erro = "nenhum provedor configurado"
    for cfg in escolher_provedores():
        log.info("blast: pedindo %d destaques a %s (folga %.0f%%)",
                 n, cfg["rotulo"], cfg["folga"] * 100)
        try:
            got = _chamar(cfg, prompt)
        except Exception as e:
            erro = f"{cfg['rotulo']}: {type(e).__name__}"
            log.warning("blast: %s levantou %s", cfg["rotulo"], e)
            continue
        if got:
            return {"destaques": got[:n], "provedor": cfg["rotulo"], "erro": None}
        erro = f"{cfg['rotulo']} nao devolveu destaques"
    return {"destaques": [], "provedor": None, "erro": erro}
