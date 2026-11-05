"""CLI do exportador do hub: `python -m hub_export …` (rodar da raiz do repositório).

    --env-file PATH   carrega as variáveis desse .env (sem ele, o .env da raiz, se existir)
    --only a,b        só esses arquivos (config, live, intraday, quotes-daily, news-latest,
                      news-digest, calendar, reports, news-week, news-index; com ou sem .json;
                      "news-week" = as semanas que o exportador escolhe; "news-week-2026-w45" =
                      aquela semana, relida do banco; "all" = todos)
    --all             todos (o padrão)
    --out DIR         grava os arquivos em DIR exatamente como seriam enviados (gzip inclusive)
                      e imprime a tabela nome/bytes/hash; não envia nem mexe no registro de envio
    --dry-run         monta e diz o que enviaria (na vez? mudou? tamanho), sem enviar
    --force           ignora o hash e a cadência
    --rebuild         ignora as cópias publicadas e os caches do estado e relê o banco (depois de
                      um conserto manual de título, por exemplo)
    --weeks N|all     com --out/--dry-run: quantas semanas mais recentes montar (padrão no --out:
                      2, a anterior e a corrente; "all" = o arquivo inteiro desde 2026-w23, ~3–4 MB
                      de Supabase UMA vez). Sem --weeks, o --dry-run simula a escolha do loop.
    --check           com --dry-run: só as conferências do banco (tipos das colunas de data,
                      colunas que existem, permissão das RPCs, contagens), sem montar arquivo
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
MANUAL_BUDGET_S = 600.0      # --out/--dry-run: sem o teto de 90 s do ciclo do robô


def _weeks_arg(value: str):
    v = (value or "").strip().lower()
    if v == "all":
        return "all"
    if v.isdigit() and int(v) >= 1:
        return int(v)
    raise argparse.ArgumentTypeError("use um número ≥ 1 ou all")


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m hub_export",
                                 description="Exportador da Commodities Datahouse (hub do Itaú).")
    ap.add_argument("--env-file", metavar="PATH", help="arquivo .env com as variáveis")
    sel = ap.add_mutually_exclusive_group()
    sel.add_argument("--only", metavar="LISTA",
                     help="config,live,intraday,quotes-daily,news-latest,news-digest,calendar,"
                          "reports,news-week,news-index (ou news-week-AAAA-wNN)")
    sel.add_argument("--all", action="store_true", help="todos os arquivos (padrão)")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--out", metavar="DIR", help="grava os arquivos em DIR, sem enviar")
    mode.add_argument("--dry-run", action="store_true", help="monta e diz o que enviaria")
    ap.add_argument("--force", action="store_true", help="ignora hash e cadência")
    ap.add_argument("--rebuild", action="store_true",
                    help="relê o banco em vez das cópias publicadas e dos caches do estado")
    ap.add_argument("--weeks", type=_weeks_arg, metavar="N|all",
                    help="com --out/--dry-run: semanas mais recentes a montar (padrão no --out: 2)")
    ap.add_argument("--check", action="store_true",
                    help="com --dry-run: só as conferências do banco (tipos, permissões, contagens)")
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
    if args.check and mode != "dry":
        print("--check só roda junto com --dry-run (nunca envia nem grava)", file=sys.stderr)
        return 2
    if args.weeks is not None and mode == "send":
        print("--weeks só vale com --out ou --dry-run (no envio, quem escolhe as semanas é o "
              "exportador; uma semana específica: --only news-week-AAAA-wNN)", file=sys.stderr)
        return 2
    falta = [n for n in ("SUPABASE_URL", "SUPABASE_SERVICE_KEY") if not os.environ.get(n)]
    if falta:
        print("faltam as variáveis do Supabase: " + ", ".join(falta)
              + " (use --env-file com o .env do news-hunter)", file=sys.stderr)
        return 2
    if args.check:
        from .checks import run_checks
        from .supa import SupaReader
        for line in run_checks(SupaReader.from_env()):
            print(line)
        return 0
    if mode == "send":
        from .feeder import missing_env
        falta = missing_env()
        if falta:
            print("faltam as variáveis do Feeder para ENVIAR: " + ", ".join(falta)
                  + " — defina no .env/ambiente, ou use --out DIR / --dry-run", file=sys.stderr)
            return 2

    from .run import BUDGET_S, run_cycle
    weeks = args.weeks if args.weeks is not None else (2 if mode == "out" else None)
    results = run_cycle(only=names, force=args.force, mode=mode, out_dir=args.out,
                        rebuild=args.rebuild, weeks=weeks,
                        budget_s=BUDGET_S if mode == "send" else MANUAL_BUDGET_S)
    print(_table(results))
    return 1 if any(r.failed for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
