"""Atualiza a CÓPIA GUARDADA do motor de desenho dos relatórios (clipping/assets/report-kit/).

O HTML do WhatsApp (clipping/whatsapp.py) usa primeiro o `reports-lib.js` PUBLICADO na
dashboard (raw do GitHub, `main`). Esta cópia só entra se a rede falhar — mas tem de existir
e ter o `renderNews`. Rode depois de mudar o desenho na dashboard, se quiser a reserva em dia:

    python -m clipping.sync_report_kit                       # baixa a versão publicada (main)
    python -m clipping.sync_report_kit --from ..\\_wt_reports  # copia de uma pasta local
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
from pathlib import Path

from .whatsapp import KIT_FILES, KIT_URL, VENDOR_DIR


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="src", help="pasta do repositório da dashboard (senão baixa a publicada)")
    a = ap.parse_args()
    VENDOR_DIR.mkdir(parents=True, exist_ok=True)
    for rel in KIT_FILES.values():
        dst = VENDOR_DIR / Path(rel).name
        if a.src:
            shutil.copyfile(Path(a.src) / rel, dst)
        else:
            import requests
            r = requests.get(f"{KIT_URL}/{rel}", timeout=30)
            r.raise_for_status()
            dst.write_bytes(r.content)
        print(f"{dst.name:18s} {dst.stat().st_size:>8d} bytes  sha1 {hashlib.sha1(dst.read_bytes()).hexdigest()[:12]}")
    lib = (VENDOR_DIR / "reports-lib.js").read_text(encoding="utf-8")
    if "renderNews" not in lib:
        raise SystemExit("⚠️ a cópia não tem renderNews — o HTML do WhatsApp cairia sem a versão publicada")
    print("ok — cópia guardada atualizada")


if __name__ == "__main__":
    main()
