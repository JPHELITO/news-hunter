"""Regras de TELA do site atual que o exportador passa a resolver (Onda 1b) — módulo de referência.

Portadas 1:1 de `IBBA-Research-Dashboard/index.html` (linhas do `staging` e055467, as que o
PLANO e a pesquisa citam; a produção `main` c40df79 tem o MESMO código ~8 linhas acima — o hub
segue a produção, decisão U2):
  • rótulo da fonte `_label` e os apelidos `_LABEL_ALIAS` ............ 1930-1948
  • setor exibido `_resolveSec`, `_SM_SOURCES`, `_PP_SOURCES` ......... 1952-1971
  • selo PRIMARY `_PRIMARY_SOURCES` (nome CRU, não o rótulo) ......... 1959
  • empresas cobertas `_PULSE_CO` (token do classificador → ticker de tela,
    ticker de cotação, mesa) ........................................ 2268-2276
  • início da janela do Market Pulse `_pulseWindowStartMs` ........... 2303-2321
    (`PULSE_CLOSE_HOUR` 2264; teste do site: tests/test_news_pulse.html:41-55)
As mesmas listas estão em `news.html:461-502` e `market.html` (o site tem DUAS cópias; agora o
exportador é a terceira). Fonte nova de setor único no site = mudar aqui também — o teste
`test_hub_export_news_rules.py` trava os conjuntos copiados.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError:            # pragma: no cover - Python < 3.9
    ZoneInfo = None
    ZoneInfoNotFoundError = Exception

# ── rótulo da fonte (index.html:1930-1948) ─────────────────────────────────────────────────────
LABEL_ALIAS = {
    "CVM": "CVM filing",   # comunicado oficial da empresa (hunter/cvm_filings.py)
    "ASX": "ASX filing",   # anúncio sensível a preço (hunter/asx_announcements.py)
    "Bloomberg.com": "Bloomberg", "Reuters Brasil": "Reuters",
    "Kallanish Commodities": "Kallanish", "Kallanish Steel": "Kallanish",
    "Shanghai Metals Market": "SMM", "World Steel Association": "World Steel",
    "O Estado de S. Paulo": "Estadão", "Folha de São Paulo": "Folha de S.Paulo",
    "IBRAM - Mineração do Brasil": "IBRAM",
    "S&P Global": "S&P Platts", "S&P Global Commodity Insights": "S&P Platts",
}
_DOMAIN_LABEL_SOURCES = ("Google News", "Google Notícias", "")

# ── setor exibido (index.html:1952-1971) ───────────────────────────────────────────────────────
SM_SOURCES = frozenset({
    "S&P Platts", "Platts", "Notícias de Mineração", "IBRAM", "ANM", "Brasil Mineral",
    "Mining.com", "Australian Mining", "SMM", "GMK Center", "Siderurgia Brasil", "SteelRadar",
    "Instituto Aço Brasil",
    # terras raras (Aclara/Viridis, 26/10/2026)
    "ASX filing", "Aclara Resources", "Fastmarkets Insights", "Northern Miner", "Financial Times",
    "Poços Já"})
PP_SOURCES = frozenset({"Fastmarkets", "PaperAge", "Portal Celulose", "ABTCP", "China Pulp & Paper",
                        "Papnews", "Ibá"})
PRIMARY_SOURCES = frozenset({"CVM", "ASX", "Aclara Resources"})
DESKS = ("sm", "pp", "nr")


def label(source_name, domain) -> str:
    """`_label`: apelido conhecido → o apelido; Google News/Google Notícias/vazio → a 1ª parte do
    domínio (sem o PRIMEIRO `www.`) com inicial maiúscula; sem domínio → "—"; senão o nome cru."""
    sn = source_name if isinstance(source_name, str) else ""
    if LABEL_ALIAS.get(sn):
        return LABEL_ALIAS[sn]
    if sn in _DOMAIN_LABEL_SOURCES:
        d = (domain if isinstance(domain, str) else "").replace("www.", "", 1).split(".")[0]
        return d[:1].upper() + d[1:] if d else "—"
    return sn


def desk(source_label: str, sector) -> str:
    """`_resolveSec` (recebe o RÓTULO já resolvido): fonte de setor único → o carimbo dela; senão
    o `sector` do banco (steel/mining → sm, pp → pp); o resto → nr. Nunca fica em branco."""
    if source_label in SM_SOURCES:
        return "sm"
    if source_label in PP_SOURCES:
        return "pp"
    if sector in ("steel", "mining"):
        return "sm"
    if sector == "pp":
        return "pp"
    return "nr"


def is_primary(source_name) -> bool:
    """Selo PRIMARY: o `source_name` CRU (não o rótulo) é de comunicado oficial."""
    return isinstance(source_name, str) and source_name in PRIMARY_SOURCES


# ── empresas cobertas (index.html:2268-2276; o mesmo mapa de hunter/digest.py:58-61) ───────────
# (token do classificador em take_covered_companies, ticker de tela, ticker em `quotes`, mesa)
PULSE_CO = (
    ("VALE", "VALE3", "VALE3.SA", "sm"), ("CSN", "CSNA3", "CSNA3.SA", "sm"),
    ("CMIN", "CMIN3", "CMIN3.SA", "sm"), ("GERDAU", "GGBR4", "GGBR4.SA", "sm"),
    ("USIMINAS", "USIM5", "USIM5.SA", "sm"), ("TERNIUM", "TX", "TX", "sm"),
    ("AURA", "AURA33", "AURA33.SA", "sm"), ("SOUTHERN COPPER", "SCCO", "SCCO", "sm"),
    ("GRUPO MEXICO", "GMEXICOB", "GMEXICOB.MX", "sm"),
    ("SUZANO", "SUZB3", "SUZB3.SA", "pp"), ("KLABIN", "KLBN11", "KLBN11.SA", "pp"),
    ("IRANI", "RANI3", "RANI3.SA", "pp"), ("CMPC", "CMPC", "CMPC.SN", "pp"),
    ("COPEC", "COPEC", "COPEC.SN", "pp"),
    # terras raras — cobertura formal desde 23/10/2026
    ("ACLARA", "ARA", "ARA.TO", "sm"), ("VIRIDIS", "VMM", "VMM.AX", "sm"),
)


def covered_companies() -> list[dict]:
    """`config.coveredCompanies` (Apêndice E.8): na ordem do site. A chave é `code` — `token` é
    chave proibida no arquivo público."""
    return [{"code": c, "ticker": t, "quote": q, "desk": d} for c, t, q, d in PULSE_CO]


# ── janela do Market Pulse (index.html:2303-2321) ──────────────────────────────────────────────
PULSE_CLOSE_HOUR = 17             # fechamento da B3 em Brasília (index.html:2264)
SP_TZ = "America/Sao_Paulo"


def _sp_today_and_offset(now: datetime) -> tuple[int, int, int, int]:
    """(ano, mês, dia) de hoje em São Paulo e o deslocamento do fuso em minutos AGORA — como o
    `Intl` do site; sem a base de fusos, o mesmo plano B do site: UTC−3 fixo (sem horário de
    verão desde 2019)."""
    try:
        if ZoneInfo is None:
            raise ZoneInfoNotFoundError(SP_TZ)
        local = now.astimezone(ZoneInfo(SP_TZ))
        return local.year, local.month, local.day, int(local.utcoffset().total_seconds() // 60)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        t = now - timedelta(hours=3)
        return t.year, t.month, t.day, -180


def pulse_window_start(now: datetime) -> datetime:
    """17:00 de Brasília do último dia útil ANTES de hoje: sábado e domingo recuam para sexta;
    feriado não conta; a janela só vira à MEIA-NOITE de São Paulo (na segunda, o dia inteiro,
    começa sexta 17:00 — até ~79 h)."""
    now = now.astimezone(timezone.utc)
    y, m, d, off = _sp_today_and_offset(now)
    day = (datetime(y, m, d, tzinfo=timezone.utc) - timedelta(minutes=off)
           - timedelta(days=1))                              # 00:00 de SP de ONTEM, em UTC
    wd = (day + timedelta(minutes=off)).weekday()            # dia da semana EM SP (0 = segunda)
    if wd == 6:                                              # domingo → sexta
        day -= timedelta(days=2)
    elif wd == 5:                                            # sábado → sexta
        day -= timedelta(days=1)
    return day + timedelta(hours=PULSE_CLOSE_HOUR)
