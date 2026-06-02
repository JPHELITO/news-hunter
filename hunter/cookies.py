"""Diretório de cookies — configurável via env var COOKIES_DIR."""
import os
from pathlib import Path


def get_cookies_dir() -> Path:
    """Retorna o diretório de cookies.

    Prioridade:
    1. Variável de ambiente COOKIES_DIR (usada no GitHub Actions)
    2. ../news_generator/cookies/ relativo à raiz do projeto (local)
    """
    env = os.environ.get("COOKIES_DIR", "").strip()
    if env:
        d = Path(env)
        d.mkdir(parents=True, exist_ok=True)
        return d
    # Compatível com a estrutura local do clipinator
    root = Path(__file__).resolve().parent.parent.parent
    d = root / "news_generator" / "cookies"
    d.mkdir(parents=True, exist_ok=True)
    return d
