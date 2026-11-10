"""mw-filings.json (COLD com sonda, conteúdo gzip com nome .json) — PLANO, Apêndice F.11.

`{ schemaVersion, generatedAt, updatedAt, items: [{ id, company, headline, category, docDate,
   publishedAt, flag, url, newsworthy, docTitle, docExcerpt }] }`

Os 600 comunicados mais recentes das empresas visíveis (B3, Plantão de Notícias → documento na CVM),
`publishedAt` decrescente (empate: `id` decrescente). `id` = o id do Plantão (público). `url` = o
`cvm_url` pelo filtro de URL (o link do ENET `rad.cvm.gov.br/…frmExibirArquivoIPEExterno.aspx?ID=…&flnk`
passa); recusado ou ausente → `null` e o item fica sem link (o site mostrava um `<a>` sem `href`:
defeito M19). `newsworthy` = `is_newsworthy` do robô. `headline`, `docTitle` e `docExcerpt` (a dica do
link, até 1.500 caracteres — o trecho do PDF, que traz o contato de RI) vão com e-mails e telefones
mascarados (U6).

`updatedAt` = o maior `updated_at` (a hora da inserção: o robô só insere, comunicado é imutável) entre
os comunicados visíveis — a marca d'água da leitura incremental e da partida a frio (fora do hash).
NUNCA saem `b3_code`, `cvm_url` cru e `updated_at` por item.
"""
from __future__ import annotations

from ..contacts import mask_contacts
from ..encode import iso_date, iso_utc, num
from ..urls import safe_url

COLS = ("id,company,headline,category,doc_date,published_at,flag,cvm_url,is_newsworthy,doc_title,"
        "doc_excerpt,updated_at")
ORDER = "published_at.desc,id.desc"                   # = a da RPC, com o id desempatando
LIMIT = 600                                           # os 600 mais recentes (a tela pede 600)
RPC_LIMIT = 2000                                      # o teto da RPC (sonda e leitura incremental)
FORBIDDEN = frozenset({"cnpj", "cvm_code", "b3Code", "b3_code", "cvmUrl", "cvm_url", "updated_at"})


def _text(v) -> str | None:
    return (v.strip() or None) if isinstance(v, str) else None


def item(r: dict) -> dict | None:
    """Uma linha da RPC → o item do arquivo; None sem id, empresa ou data de publicação."""
    fid = num(r.get("id"))
    co = _text(r.get("company"))
    pub = iso_utc(r.get("published_at"))
    if fid is None or not float(fid).is_integer() or not co or not pub:
        return None
    return {"id": int(fid), "company": co, "headline": mask_contacts(_text(r.get("headline"))),
            "category": _text(r.get("category")), "docDate": iso_date(r.get("doc_date")),
            "publishedAt": pub, "flag": _text(r.get("flag")), "url": safe_url(r.get("cvm_url")),
            "newsworthy": r.get("is_newsworthy") is True,
            "docTitle": mask_contacts(_text(r.get("doc_title"))),
            "docExcerpt": mask_contacts(_text(r.get("doc_excerpt")))}


def sort_items(items: list[dict]) -> list[dict]:
    """`publishedAt` decrescente, `id` decrescente no empate (a ordem do arquivo)."""
    return sorted(items, key=lambda it: (it["publishedAt"], it["id"]), reverse=True)


def publish(it: dict) -> dict:
    """O item como vai ao arquivo: a máscara e o filtro de URL de novo (idempotentes — a semente da
    cópia publicada passa pela regra de hoje)."""
    return {**it, "headline": mask_contacts(it.get("headline")), "url": safe_url(it.get("url")),
            "docTitle": mask_contacts(it.get("docTitle")),
            "docExcerpt": mask_contacts(it.get("docExcerpt"))}


def build(src) -> dict:
    data = src.mw().filings()
    return {"schemaVersion": 1, "generatedAt": src.generated_at, "updatedAt": data["updatedAt"],
            "items": [publish(it) for it in data["items"]]}
