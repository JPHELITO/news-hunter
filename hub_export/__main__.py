"""CLI do exportador do hub: `python -m hub_export …` (rodar da raiz do repositório).

    --env-file PATH   carrega as variáveis desse .env (sem ele, o .env da raiz, se existir)
    --only a,b        só esses arquivos (config, live, intraday, quotes-daily; com ou sem .json;
                      "all" = todos)
    --all             todos (o padrão)
    --out DIR         grava os arquivos em DIR exatamente como seriam enviados (gzip inclusive)
                      e imprime a tabela nome/bytes/hash; não envia nem mexe no registro de envio
    --dry-run         monta e diz o que enviaria (na vez? mudou? tamanho), sem enviar
    --force           ignora o hash e a cadência
Sem --out/--dry-run = ENVIA (exige DASHBOARD_FEEDER_URL, DASHBOARD_FEEDER_PROJECT_ID e
DASHBOARD_FEEDER_API_KEY). Código de saída ≠ 0 se algum arquivo falhar.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m hub_export",
                                 description="Exportador da Commodities Datahouse (hub do Itaú).")
    ap.add_argument("--env-file", metavar="PATH", help="arquivo .env com as variáveis")
    sel = ap.add_mutually_exclusive_group()
    sel.add_argument("--only", metavar="LISTA", help="config,live,intraday,quotes-daily")
    sel.add_argument("--all", action="store_true", help="todos os arquivos (padrão)")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--out", metavar="DIR", help="grava os arquivos em DIR, sem enviar")
    mode.add_argument("--dry-run", action="store_true", help="monta e diz o que enviaria")
    ap.add_argument("--force", action="store_true", help="ignora hash e cadência")
    return ap


def _load_env(env_file: str | None) -> str | None:
    """Carrega o .env pedido (sobrescreve o ambiente) ou o da raiz (não sobrescreve).
    Devolve uma mensagem de erro ou None. Nunca imprime valor nenhum."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return "python-dotenv não instalado" if env_file else None
    if env_file:
        if not Path(env_file).is_file():
            return f"--env-file: arquivo não encontrado ({env_file})"
        load_dotenv(env_file, override=True)
    elif (ROOT / ".env").is_file():
        load_dotenv(ROOT / ".env", override=False)
    return None


def _table(results) -> str:
    head = ["arquivo", "ação", "na vez", "mudou", "enviado", "bruto", "gzip", "sha256", "itens"]
    rows = []
    for r in results:
        rows.append([r.name, r.action,
                     "-" if r.due is None else ("sim" if r.due else "não"),
                     "-" if r.changed is None else ("sim" if r.changed else "não"),
                     "-" if r.bytes is None else f"{r.bytes:,}".replace(",", "."),
                     "-" if r.raw_bytes is None else f"{r.raw_bytes:,}".replace(",", "."),
                     "-" if r.gz_bytes is None else f"{r.gz_bytes:,}".replace(",", "."),
                     (r.hash or "-")[:12],
                     r.items or (r.reason or "")])
    widths = [max(len(str(x)) for x in col) for col in zip(head, *rows)]
    fmt = "  ".join("{:<%d}" % w for w in widths)
    lines = [fmt.format(*head), fmt.format(*["-" * w for w in widths])]
    lines += [fmt.format(*row) for row in rows]
    for r in results:
        if r.action == "erro":
            lines.append(f"{r.name}: {r.reason}")
    return "\n".join(lines)


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    sys.path.insert(0, str(ROOT))          # `hunter` importável mesmo fora da raiz

    erro = _load_env(args.env_file)
    if erro:
        print(erro, file=sys.stderr)
        return 2

    from .registry import resolve
    try:
        names = resolve(None if args.all else args.only)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2

    mode = "out" if args.out else ("dry" if args.dry_run else "send")
    falta = [n for n in ("SUPABASE_URL", "SUPABASE_SERVICE_KEY") if not os.environ.get(n)]
    if falta:
        print("faltam as variáveis do Supabase: " + ", ".join(falta)
              + " (use --env-file com o .env do news-hunter)", file=sys.stderr)
        return 2
    if mode == "send":
        from .feeder import missing_env
        falta = missing_env()
        if falta:
            print("faltam as variáveis do Feeder para ENVIAR: " + ", ".join(falta)
                  + " — defina no .env/ambiente, ou use --out DIR / --dry-run", file=sys.stderr)
            return 2

    from .run import run_cycle
    results = run_cycle(only=names, force=args.force, mode=mode, out_dir=args.out)
    print(_table(results))
    return 1 if any(r.failed for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
