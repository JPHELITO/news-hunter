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

QUEM ESCOLHE (2026-09-03)
-------------------------
O julgamento passou a ser DIVIDIDO. Na tela do clipping o analista marca com ★ até 3 notícias
que ele quer ver nos destaques; essas a IA é obrigada a escrever, na ordem em que ele marcou,
e ela escolhe apenas as vagas que sobrarem. Marcar zero é o comportamento antigo — ela escolhe
as três. É o melhor dos dois: quando ele já sabe qual é a notícia do dia, não precisa torcer
para o modelo concordar; quando não sabe, o modelo continua lendo tudo e decidindo.

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
import time
from datetime import datetime, timezone
from urllib.parse import quote

import requests

log = logging.getLogger(__name__)

# Teto diário de cada pool, do que foi medido em 2026-08/09 (ver a memória do projeto).
# Gemini: RPD 500 POR PROJETO Google — por isso as duas chaves são pools separados.
CAPACIDADE = {
    "gemini_clipping": 500,    # projeto dedicado ao clipping (~7 chamadas/dia)
    "gemini_takes":    500,    # projeto dos takes (~250-280/dia)
    # 2º modelo NO MESMO projeto do clipping (a cota do Google é POR MODELO). Entrou em
    # 24/11/2026: o 3.1 deu "503 high demand" nos dois projetos e o blast caiu na Z.AI (1m41s),
    # enquanto o resumo da hora, que já usava o 3.5 de reserva, passou na mesma manhã.
    "gemini_clipping_b": 500,
    "gemini_takes_b":    500,  # 2º modelo no projeto dos takes (o robô dos takes só usa de reserva)
    "groq":             1000,  # outra EMPRESA (não é Google): 1.000 pedidos/dia, 8 mil tokens/min
    "zai":            1000,    # ocioso, porém LENTO (55-66s por chamada)
}
# Os OUTROS modelos do Google entram como portas extras (`gemini_extra1`, `gemini_extra2`…): cada
# MODELO tem a sua própria lotação e a sua própria cota grátis. Sem contador — usamos 1 a 3 por dia.

# ── POR QUE TANTAS PORTAS (24/11/2026, 2 de 3 "Gerar tudo" da tarde saíram SEM destaques) ──────
# 15:37 e 16:14 BRT: o Google respondia "503 high demand" no 3.1-flash-lite E no 3.5-flash-lite,
# nos DOIS projetos (lotação do Google — nossa cota estava em 15%), e a Z.AI, último degrau, dava
# "429" porque o robô dos takes tinha caído nela pelo mesmo motivo. A cadeia tentou 5 vezes em ~30 s
# e DESISTIU. No mesmo horário o robô dos takes conseguia respostas do 3.5-flash-lite (ele insiste),
# e às 16:40 o 3.5-flash e o 3-flash-preview responderam de primeira. Lição: lotação do Google
# passa; desistir em 30 s, não. Ver `highlights` (insiste em voltas por até PACIENCIA_S).
EXTRA_GEMINI = [m.strip() for m in os.environ.get(
    "BLAST_EXTRA_GEMINI", "gemini-3.5-flash,gemini-3-flash-preview").split(",") if m.strip()]
GROQ_MODEL = os.environ.get("BLAST_GROQ_MODEL", "openai/gpt-oss-120b")


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
        from hunter.llm_take import PROVIDERS as _P
        gem_b = (_P.get("gemini_b") or {}).get("model") or "gemini-3.5-flash-lite"
    except Exception:
        gem_b = "gemini-3.5-flash-lite"
    try:
        r = requests.get(f"{url}/rest/v1/news_articles?select=take_llm_model"
                         f"&take_llm_at=gte.{t0}&limit=5000", headers=h, timeout=20)
        r.raise_for_status()
        for row in r.json():
            m = str(row.get("take_llm_model") or "")
            if m == gem_b:                       # a cota do Google é POR MODELO
                carga["gemini_takes_b"] += 1
            elif m.startswith("gemini"):
                carga["gemini_takes"] += 1
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
# A Mistral saiu em 2026-10-21: conta grátis com limite zero, passou a exigir cartão.
# Os modelos extras do Google ficam DEPOIS do titular (3.1-flash-lite, o que escreve os destaques
# desde setembro) e ANTES do 2º modelo lite; Groq e Z.AI são as portas fora do Google.
QUALIDADE = {"gemini_clipping": 0, "gemini_takes": 0, "gemini_extra": 0.5,
             "gemini_clipping_b": 1, "gemini_takes_b": 1, "groq": 2, "zai": 3}

# INSISTIR, em voltas por todas as portas, até alguém responder (24/11/2026 — ver o topo).
# Normal: a 1ª porta responde em segundos e nada disto aparece. Google lotado: o robô passa por
# todas, espera um pouco e passa de novo, por até PACIENCIA_S. A aba espera junto (até 16 min).
PACIENCIA_S = float(os.environ.get("BLAST_PACIENCIA_S", "420"))
ESPERAS_S = (10, 20, 30, 45, 60)             # entre uma volta e outra; depois fica em 60
# Porta que NÃO EXISTE ou recusa a CHAVE não é tentada de novo (não é lotação, não passa sozinha).
# 413 = pedido grande demais para aquela porta (Groq).
FECHA_PORTA = {400, 401, 403, 404, 413}

_agora = time.monotonic                      # trocáveis nos testes (relógio falso)
_dormir = time.sleep


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
    gem_b = (_P.get("gemini_b") or {}).get("model") or "gemini-3.5-flash-lite"
    zai = (_P.get("zai") or {}).get("model") or "glm-4.5-flash"

    clip_key = os.environ.get("CLIPPING_TRANSLATE_KEY", "")
    takes_key = os.environ.get("GEMINI_API_KEY", "")
    defs = {
        "gemini_clipping": {"rotulo": "gemini (projeto clipping)", "kind": "gemini", "lento": False,
                            "key": clip_key, "model": gem},
        "gemini_takes":    {"rotulo": "gemini (projeto takes)", "kind": "gemini", "lento": False,
                            "key": takes_key, "model": gem},
    }
    # outros modelos do Google no projeto do clipping (cada modelo = lotação e cota próprias)
    for i, m in enumerate(x for x in EXTRA_GEMINI if x not in (gem, gem_b)):
        defs[f"gemini_extra{i + 1}"] = {"rotulo": f"gemini {m} (projeto clipping)", "kind": "gemini",
                                        "lento": False, "key": clip_key, "model": m,
                                        "qualidade": "gemini_extra", "timeout": 60}
    defs.update({
        "gemini_clipping_b": {"rotulo": "gemini 2º modelo (projeto clipping)", "kind": "gemini",
                              "lento": False, "key": clip_key, "model": gem_b},
        "gemini_takes_b": {"rotulo": "gemini 2º modelo (projeto takes)", "kind": "gemini",
                           "lento": False, "key": takes_key if gem_b != gem else "", "model": gem_b},
        # outra empresa: não cai junto com o Google. Teto de 8 mil tokens POR MINUTO → recebe as
        # notícias com o texto encurtado (`prompt_max`); o gpt-oss "pensa" antes de responder, e
        # esse pensamento conta no teto → esforço baixo e saída limitada.
        "groq": {"rotulo": "groq (gpt-oss)", "kind": "openai", "lento": False,
                 "key": os.environ.get("GROQ_API_KEY", ""), "model": GROQ_MODEL,
                 "url": "https://api.groq.com/openai/v1/chat/completions", "timeout": 60,
                 "prompt_max": 14000, "extra": {"reasoning_effort": "low", "max_tokens": 2000}},
        "zai":             {"rotulo": "z.ai (GLM)", "kind": "openai", "lento": True,
                            "key": os.environ.get("ZAI_API_KEY", ""), "model": zai,
                            "url": "https://api.z.ai/api/paas/v4/chat/completions", "timeout": 150},
    })
    carga = _carga_hoje()

    def _folga(nome):
        teto = CAPACIDADE.get(nome, 1) or 1
        usado = carga.get(nome)
        if usado is None and nome == "gemini_clipping_b":
            # sem contador próprio: usa a estimativa do projeto (conservador — mesmo projeto)
            usado = carga.get("gemini_clipping", 0)
        return max(0.0, 1.0 - (usado or 0) / teto)

    vivos = [(nome, d) for nome, d in defs.items() if d["key"]]
    folgados = [(n, d) for n, d in vivos if _folga(n) >= FOLGA_MINIMA]
    if not folgados:
        # Todo mundo apertado: em vez de desistir do blast, usa o menos pior e diz no log.
        log.warning("blast: nenhum provedor com folga >= %.0f%% (carga %s) — vou no mais ocioso",
                    FOLGA_MINIMA * 100, carga)
        folgados = sorted(vivos, key=lambda it: -_folga(it[0]))[:1]

    ordem = sorted(folgados, key=lambda it: (1 if it[1]["lento"] else 0,
                                             QUALIDADE.get(it[1].get("qualidade") or it[0], 9),
                                             -_folga(it[0])))
    log.info("blast: carga de hoje %s -> ordem %s", carga, [n for n, _ in ordem])
    return [dict(d, nome=n, folga=round(_folga(n), 3)) for n, d in ordem]


SYSTEM = (
    "Você é um analista sell-side de commodities do Itaú BBA, cobrindo Steel & Mining, "
    "Pulp & Paper e mercados globais, com foco em China, Brasil, EUA e Europa. "
    "Escreve para investidores institucionais."
)

# Bloco que só existe quando o analista marcou alguma notícia (★) na tela do clipping.
# A escolha dele NÃO é sugestão: a IA escreve aquela notícia e escolhe apenas o que sobrar.
MARCADAS = """
O analista JÁ ESCOLHEU {k} — são as marcadas com ★ na lista abaixo, e elas são OBRIGATÓRIAS:
- escreva um destaque para cada ★, na ordem do número (★1 primeiro, depois ★2, depois ★3);
- nenhuma ★ fica de fora, e duas ★ nunca viram o mesmo destaque (uma ★ pode, sim, incorporar
  uma notícia SEM ★ que conte a mesma história);
- {resto}
"""

INSTRUCAO = """Abaixo está a seleção de notícias do clipping de hoje — as MESMAS que vão para o
Word e o e-mail — com o setor e o take direcional que o analista carimbou, e o TEXTO de cada uma.

Sua tarefa: escrever até {n} destaques, em português, para o blast da manhã.
{marcadas}
O que faz um destaque BOM:
- traz NÚMERO quando o texto tem (preço, tonelagem, variação %, prazo); é o que separa um destaque
  de uma manchete reescrita;
- pode JUNTAR duas ou três notícias que contam a mesma história (ex.: dois assessments na mesma
  direção, ou um preço e a razão dele) — se elas se explicam, valem mais juntas que separadas;
- escolhe pela RELEVÂNCIA para quem investe em Steel & Mining e Pulp & Paper, não pela ordem da
  lista nem pelo tamanho do texto;
- vale ler o conjunto: se três notícias apontam para o mesmo lado, isso é o destaque.

Critérios de seleção:
{limite}
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

Escreva os MESMOS destaques também em INGLÊS, para o e-mail dos clientes (lista "highlights_en"):
- mesmo conteúdo, mesma ordem e mesmo número de itens da lista em português;
- números no padrão americano (1,234.5), as mesmas regras de formato (USD XX/ton, YoY, QoQ);
- inglês de research sell-side, natural — não uma tradução palavra por palavra.

Responda SOMENTE com JSON: {{"destaques": ["...", "..."], "highlights_en": ["...", "..."]}}

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
                    "destaques": {"type": "ARRAY", "items": {"type": "STRING"}},
                    "highlights_en": {"type": "ARRAY", "items": {"type": "STRING"}}},
                    "required": ["destaques"]}},
        }
        r = requests.post(url, json=payload, timeout=cfg.get("timeout", 90),
                          headers={"x-goog-api-key": cfg["key"], "Content-Type": "application/json"})
        cfg["_status"] = r.status_code
        if not r.ok:
            log.warning("blast: %s HTTP %s %s", cfg["rotulo"], r.status_code, _motivo(r))
            return None
        raw = r.json()["candidates"][0]["content"]["parts"][0]["text"]
    else:
        payload = {"model": cfg["model"], "temperature": 0.2,
                   "response_format": {"type": "json_object"},
                   "messages": [{"role": "system", "content": SYSTEM},
                                {"role": "user", "content": prompt}],
                   **(cfg.get("extra") or {})}
        r = requests.post(cfg["url"], json=payload, timeout=cfg.get("timeout", 150),
                          headers={"Authorization": f"Bearer {cfg['key']}",
                                   "Content-Type": "application/json"})
        cfg["_status"] = r.status_code
        if not r.ok:
            log.warning("blast: %s HTTP %s %s", cfg["rotulo"], r.status_code, _motivo(r))
            return None
        raw = r.json()["choices"][0]["message"]["content"]

    try:
        out = json.loads(raw)
    except Exception:                       # modelos "thinking" embrulham o JSON
        try:
            from hunter.llm_take import _parse_json_dict
            out = _parse_json_dict(raw, "destaques")
        except Exception:
            out = None
        if isinstance(out, dict):
            log.info("blast: %s embrulhou o JSON — lido mesmo assim", cfg["rotulo"])
    if not isinstance(out, dict):
        log.warning("blast: %s devolveu JSON inválido", cfg["rotulo"])
        return None
    from hunter.llm_take import _text_items     # texto solto no lugar da lista não vira letras
    destaques = [d.strip().rstrip(";.").strip() for d in _text_items(out.get("destaques"))]
    destaques = [d for d in destaques if d]
    en = [d.strip().rstrip(";.").strip() for d in _text_items(out.get("highlights_en"))]
    if not destaques and out.get("destaques") != []:
        return None                         # veio lixo no lugar das frases: é falha, não resposta
    # lista VAZIA de verdade ([]) é uma resposta ("nada relevante hoje"), não uma falha — volta
    # vazia e quem chama decide (pergunta a mais um modelo antes de aceitar)
    res = _Destaques(destaques)
    res.en = [d for d in en if d] if destaques else []
    return res


def _motivo(r) -> str:
    """O começo da explicação do erro (para o registro: '429 concurrency', '503 high demand'…)."""
    try:
        j = r.json()
        e = j.get("error") if isinstance(j, dict) else None
        if isinstance(e, dict):
            return str(e.get("message") or e.get("code") or "")[:140]
        return str(e or j)[:140]
    except Exception:
        return (getattr(r, "text", "") or "")[:140]


class _Destaques(list):
    """A lista em português (é o que o blast sempre devolveu) com o inglês PENDURADO em `.en`.

    Uma lista de verdade, de propósito: quem já comparava `_chamar(...) == [...]` continua
    certo, e o inglês vai junto sem mudar o formato de ninguém."""
    en: list = []


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


def _pin(n: dict) -> int:
    """A ordem que o analista deu a esta notícia no ★ (0 = não marcada). Tolera '2' e True."""
    try:
        return max(0, int(n.get("pin") or 0))
    except (TypeError, ValueError):
        return 0


def _linha(n: dict, corpo_max: int = CORPO_MAX_CHARS) -> str:
    """Uma notícia como a IA a enxerga: manchete, fonte, setor, take e o TEXTO dela."""
    take = {"+": "positivo", "-": "negativo", "=": "neutro"}.get(n.get("take"), "sem take")
    pin = _pin(n)
    cab = ((f"★{pin} " if pin else "")
           + f"[{n.get('sector') or 'NR'} | take {take} | {n.get('source_name') or '?'}] "
           f"{n.get('title') or ''}")
    corpo = _limpar(n.get("body") or "")
    if not corpo:
        return cab
    if len(corpo) > corpo_max:
        corpo = corpo[:corpo_max].rsplit(" ", 1)[0] + "…"
    return cab + "\n    " + corpo


def _montar_noticias(noticias: list, prompt_max: int | None = None) -> str:
    """
    O bloco de notícias do prompt, respeitando o teto total (`prompt_max`; a Groq usa um menor).

    Encolhe o corpo de TODAS por igual em vez de cortar as últimas fora: uma notícia que
    some do prompt não pode ser escolhida, e quem decide o que é relevante é a IA — não a
    ordem em que o analista arrastou os itens.
    """
    teto = prompt_max or PROMPT_MAX_CHARS
    corpo_max = CORPO_MAX_CHARS
    while True:
        bloco = "\n".join(_linha(x, corpo_max) for x in noticias)
        if len(bloco) <= teto or corpo_max <= 300:
            if len(bloco) > teto:
                log.warning("blast: %d notícias estouram o teto mesmo com corpo curto — "
                            "vou mandar %d caracteres", len(noticias), len(bloco))
            return bloco
        corpo_max = int(corpo_max * 0.7)
        log.info("blast: prompt grande (%d chars) — encolhendo o corpo p/ %d por notícia",
                 len(bloco), corpo_max)


def _normalizar_pins(noticias: list, n: int) -> int:
    """
    Renumera as ★ para 1..k e joga fora o excedente. Devolve k.

    A tela já limita a 3, mas o payload vem do navegador: se chegasse ★5 (rascunho antigo,
    duas abas abertas, alguém mexendo no JSON), o prompt pediria à IA cinco destaques
    obrigatórios num teto de três — e ela obedeceria a um, calada, sem ninguém saber qual.
    """
    marcadas = sorted((x for x in noticias if _pin(x)), key=_pin)
    for i, x in enumerate(marcadas):
        x["pin"] = i + 1 if i < n else 0
    return min(len(marcadas), n)


def _regras_da_escolha(k: int, n: int) -> tuple:
    """(bloco das ★, linha do limite) — o combinado muda conforme quantas o analista marcou."""
    if k <= 0:
        return "", f"- no máximo {n} destaques; pode haver menos, e pode haver nenhum;"
    if k >= n:
        return (MARCADAS.format(k=(f"{k} delas" if k > 1 else "uma delas"),
                                resto="não escolha nenhuma outra notícia: os destaques são "
                                      "exatamente as ★."),
                f"- exatamente {n} destaques, um para cada ★;")
    r = n - k
    resto = (f"o outro destaque você escolhe entre as notícias SEM ★, pelos critérios abaixo, "
             f"e ele vem depois das ★;" if r == 1 else
             f"os outros {r} destaques você escolhe entre as notícias SEM ★, pelos critérios "
             f"abaixo, e eles vêm depois das ★;")
    limite = (f"- {k} destaque{'s' if k > 1 else ''} obrigatório{'s' if k > 1 else ''} das ★ "
              f"mais até {r} escolhido{'s' if r > 1 else ''} por você — no máximo {n} no total;")
    return MARCADAS.format(k=(f"{k} delas" if k > 1 else "uma delas"), resto=resto), limite


def _ingles(pt: list, en: list) -> tuple[list, str]:
    """Os destaques em inglês para o e-mail: (lista, de onde veio).

    1º o que veio junto (da IA, na MESMA chamada, ou o que o analista editou na aba); com o
    mesmo número de itens, só os VAZIOS são traduzidos — o resto é respeitado. Número de itens
    diferente = a posição não é confiável, e tudo é traduzido. Tradução = Google, sem IA e sem
    cota. Nada aqui levanta exceção: sem inglês, o e-mail sai sem a seção de destaques."""
    if not pt:
        return [], ""
    en = list(en or [])
    alvo = [str(x or "").strip() for x in en] if len(en) == len(pt) else [""] * len(pt)
    if all(alvo):
        return alvo, "ia"
    faltam = [i for i, a in enumerate(alvo) if not a]
    # 1º o TRADUTOR DO CLIPPING (o mesmo das matérias bilíngues: projeto Gemini dedicado, ~7 de
    # 500/dia) — 1 chamada pequena, só quando falta inglês. 24/11/2026: da nuvem do GitHub o Google
    # grátis recusou até UM pedido ("too many requests"), por isso ele virou só a reserva.
    trad, fonte = None, "traducao"
    try:
        from .build import _translate_via_llm
        got = _translate_via_llm("Daily highlights", [pt[i] for i in faltam], "Portuguese")
        if got and len(got[1]) == len(faltam) and all(str(x).strip() for x in got[1]):
            trad = got[1]
    except Exception as e:
        log.info("blast: tradutor do clipping indisponível (%s) — vou no Google", e)
    if trad is None:
        try:
            trad, fonte = _google_lote([pt[i] for i in faltam]), "google"
        except Exception as e:
            log.warning("blast: sem destaques em inglês (%s)", e)
            return [], ""
    out = list(alvo)
    for i, t in zip(faltam, trad):
        out[i] = str(t or "").strip().rstrip(";.").strip()
    if all(out):
        log.info("blast: %d de %d destaque(s) em inglês traduzido(s) (%s)", len(faltam), len(pt), fonte)
        return out, fonte
    return [], ""


def _google_lote(textos: list) -> list:
    """Google grátis com o MESMO cuidado do clipping (`build._translate_to_english`): UM pedido
    para todos (linhas separadas por \n), ritmo mínimo entre chamadas e novas tentativas com
    espera (2, 4, 8 s). Medido em 24/11/2026: da nuvem do GitHub, 3 pedidos seguidos levaram
    "too many requests" e o e-mail saiu sem os destaques."""
    import time
    from deep_translator import GoogleTranslator
    from .build import _LAST_TRANSLATE, _PACE_S
    if not textos:
        return []
    junto = "\n".join(str(t).replace("\n", " ") for t in textos)
    erro = None
    for tentativa in range(4):
        espera = _PACE_S - (time.time() - _LAST_TRANSLATE[0])
        if espera > 0:
            time.sleep(espera)
        try:
            r = GoogleTranslator(source="pt", target="en").translate(junto)
            _LAST_TRANSLATE[0] = time.time()
            partes = [x.strip() for x in str(r or "").split("\n")]
            if len(partes) == len(textos) and all(partes):
                return partes
            erro = f"o Google devolveu {len(partes)} linha(s) para {len(textos)}"
        except Exception as e:
            _LAST_TRANSLATE[0] = time.time()
            erro = str(e)[:160]
        if tentativa < 3:
            time.sleep(2 * (2 ** tentativa))
    raise RuntimeError(erro or "Google sem resposta")


def highlights(noticias: list, n: int = 3) -> dict:
    """
    Escreve os destaques. Devolve {'destaques': [...], 'destaques_en': [...], 'en_fonte': 'ia'|
    'google'|'', 'provedor': 'nome', 'erro': None}.

    As notícias que o analista marcou com ★ na tela do clipping são OBRIGATÓRIAS: a IA escreve
    uma frase para cada e só escolhe o que sobrar (nenhuma ★ = ela escolhe as três, como antes).
    A ordem das ★ é a ordem em que ele clicou, e é a ordem no blast.

    UMA chamada traz as duas línguas (português p/ o WhatsApp, inglês p/ o e-mail).

    INSISTE (24/11/2026): passa por TODAS as portas (Gemini titular nos 2 projetos, outros modelos
    do Google, o 2º modelo lite, Groq e Z.AI) e, se nenhuma responder, espera um pouco (10 s, 20 s,
    30 s, 45 s, 60 s…) e passa de novo — por até `PACIENCIA_S` (7 min). Lotação do Google e "429"
    da Z.AI passam sozinhos; desistir em 30 s, como antes, foi o que deixou o clipping sem
    destaques. Porta que não existe ou recusa a chave (`FECHA_PORTA`) sai da roda. Resposta
    VAZIA ("nada relevante") de dois modelos diferentes é aceita como resposta.

    Nunca levanta exceção: o blast tem de sair mesmo sem IA — sem destaque ele ainda entrega
    preços e manchetes. Quem chama (a aba) avisa em VERMELHO quando não houver destaque.
    """
    vazio = {"destaques": [], "destaques_en": [], "en_fonte": "", "provedor": None}
    if not noticias:
        return {**vazio, "erro": "nenhuma notícia selecionada"}
    noticias = [dict(x) for x in noticias]          # não mexe no dicionário de quem chamou
    k = _normalizar_pins(noticias, n)
    marcadas, limite = _regras_da_escolha(k, n)
    prompts: dict = {}

    def _prompt(cfg):
        teto = cfg.get("prompt_max") or PROMPT_MAX_CHARS
        if teto not in prompts:
            prompts[teto] = INSTRUCAO.format(n=n, marcadas=marcadas, limite=limite,
                                             noticias=_montar_noticias(noticias, teto))
        return prompts[teto]

    portas = escolher_provedores()
    if not portas:
        return {**vazio, "erro": "nenhum provedor configurado"}
    t0 = _agora()
    fechadas: set = set()
    vazias: list = []                                # quem respondeu "nada relevante"
    ultimo: dict = {}                                # rótulo -> o que aconteceu na última vez
    volta = 0
    while True:
        volta += 1
        for cfg in portas:
            if cfg["nome"] in fechadas or cfg["rotulo"] in vazias:
                continue
            if volta > 1 and _agora() - t0 > PACIENCIA_S:
                break
            log.info("blast: pedindo %d destaques a %s (volta %d, folga %.0f%%, %d marcada(s) pelo analista)",
                     n, cfg["rotulo"], volta, cfg["folga"] * 100, k)
            cfg.pop("_status", None)
            try:
                got = _chamar(cfg, _prompt(cfg))
            except Exception as e:                    # tempo esgotado, rede: passa, tenta na volta seguinte
                log.warning("blast: %s levantou %s", cfg["rotulo"], e)
                ultimo[cfg["rotulo"]] = type(e).__name__
                continue
            st = cfg.get("_status") or 0
            if got is not None and len(got) == 0:
                vazias.append(cfg["rotulo"])
                ultimo[cfg["rotulo"]] = "nada relevante"
                if len(vazias) >= 2:                  # dois modelos concordam: hoje não há destaque
                    return {**vazio, "erro": "a IA não achou destaque relevante ("
                            + " e ".join(vazias) + ")"}
                continue
            if got:
                pt = list(got)[:n]
                # só resposta de IA de verdade (`_Destaques`) passa pelo inglês; lista crua (testes,
                # código antigo) fica sem — e o teste não sai fazendo tradução pela internet
                en, fonte = (_ingles(pt, list(got.en or [])[:n]) if isinstance(got, _Destaques)
                             else ([], ""))
                seg = _agora() - t0
                if volta > 1 or seg > 60:
                    log.info("blast: destaques por %s na volta %d, depois de %.0f s", cfg["rotulo"], volta, seg)
                return {"destaques": pt, "destaques_en": en, "en_fonte": fonte,
                        "provedor": cfg["rotulo"], "erro": None}
            ultimo[cfg["rotulo"]] = f"HTTP {st}" if st else "resposta inválida"
            if st in FECHA_PORTA:
                fechadas.add(cfg["nome"])
                log.warning("blast: %s fora desta rodada (HTTP %s não passa sozinho)", cfg["rotulo"], st)
        abertas = [c for c in portas if c["nome"] not in fechadas and c["rotulo"] not in vazias]
        espera = ESPERAS_S[min(volta - 1, len(ESPERAS_S) - 1)]
        if not abertas or _agora() - t0 + espera > PACIENCIA_S:
            break
        log.warning("blast: nenhuma IA respondeu na volta %d (%s) — tento de novo em %d s",
                    volta, ", ".join(f"{r}: {o}" for r, o in ultimo.items()), espera)
        _dormir(espera)
    minutos = (_agora() - t0) / 60
    resumo = ", ".join(f"{r}: {o}" for r, o in ultimo.items()) or "nenhuma resposta"
    if vazias:                                       # um disse "nada relevante", o resto não respondeu
        resumo = f"{vazias[0]}: nada relevante · " + resumo
    return {**vazio, "erro": f"nenhuma IA respondeu em {minutos:.1f} min ({volta} volta(s)) — {resumo}"}
