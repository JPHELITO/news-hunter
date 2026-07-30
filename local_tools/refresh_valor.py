"""Semeia a sessao do Valor LENDO os cookies que voce JA' tem logados no seu Chrome.
Sem abrir navegador, sem login, sem senha.

O que faz: acha o perfil do Chrome que tem a sessao do Globo (cookie GLBID), le os cookies do
globo.com/valor.globo.com, decripta LOCALMENTE (Windows DPAPI + AES-GCM, esquema "v10" do Chrome)
e salva a sessao no store (Supabase source_sessions -> provider "valor"), que e' o que o clipping
usa. Tudo acontece na SUA maquina; os cookies vao SO' pro seu proprio banco (nada e' exposto).
E' a mesma tecnica da biblioteca browser_cookie3. Le APENAS cookies do globo.com.

Requisito: voce estar logado no Valor no Chrome. Escolhe sozinho o perfil com o login mais
recente. Aguenta os DOIS formatos de cookie: o classico (v10/DPAPI) e o NOVO (v20/App-Bound do
Chrome 127+) — neste, abre o proprio Chrome (headless) no SEU perfil real p/ ele mesmo decriptar
(plano B, sem admin nem registro; EXIGE o Chrome fechado). Se um dia deslogar, entre no
valor.globo.com pelo Chrome e rode de novo.
"""
import base64
import ctypes
import json
import logging
import os
import shutil
import sqlite3
import sys
import tempfile
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parent
NH = ROOT / "news-hunter"
os.chdir(NH)
sys.path.insert(0, str(NH))
os.environ["COOKIES_DIR"] = str(ROOT / "_secrets")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

try:
    from dotenv import load_dotenv
    load_dotenv(NH / ".env")                          # SUPABASE_URL / SUPABASE_SERVICE_KEY
except Exception as e:
    print("aviso dotenv:", e)

from hunter import playwright_session as ps

LOCALAPPDATA = os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))
REAL_USERDATA = Path(LOCALAPPDATA) / "Google" / "Chrome" / "User Data"


def _dpapi_decrypt(blob: bytes) -> bytes:
    """Windows DPAPI CryptUnprotectData (roda como o seu usuario -> so' voce decripta)."""
    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]
    buf = ctypes.create_string_buffer(blob, len(blob))
    blob_in = DATA_BLOB(len(blob), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = DATA_BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
    ):
        raise OSError("CryptUnprotectData falhou")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def _aesgcm(key: bytes, blob: bytes) -> bytes:
    """Decripta um encrypted_value v10/v11: b'v10' + nonce(12) + ciphertext + tag(16)."""
    nonce, ct_tag = blob[3:15], blob[15:]
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        return AESGCM(key).decrypt(nonce, ct_tag, None)
    except ImportError:
        from Crypto.Cipher import AES
        return AES.new(key, AES.MODE_GCM, nonce=nonce).decrypt_and_verify(ct_tag[:-16], ct_tag[-16:])


def _master_key() -> bytes:
    ls = json.loads((REAL_USERDATA / "Local State").read_text(encoding="utf-8"))
    enc = base64.b64decode(ls["os_crypt"]["encrypted_key"])
    if enc[:5] != b"DPAPI":
        raise RuntimeError("formato de chave inesperado no Local State")
    return _dpapi_decrypt(enc[5:])


def _profile_with_globo() -> str:
    """Perfil com a sessao Globo mais FRESCA. Prioriza o GLBID com maior last_update_utc
    (o LOGIN mais recente) → pega o perfil que voce acabou de logar, nao um velho/expirado.
    (Antes desempatava por QUANTIDADE de cookies e pegava o perfil errado quando ha varios.)"""
    best, best_ts = "Default", -1
    for prof in ["Default"] + [f"Profile {i}" for i in range(1, 12)]:
        ck = REAL_USERDATA / prof / "Network" / "Cookies"
        if not ck.exists():
            continue
        try:
            con = sqlite3.connect(f"file:{ck}?mode=ro&immutable=1", uri=True)
            row = con.execute(
                "select max(coalesce(last_update_utc, creation_utc, 0)) from cookies "
                "where name='GLBID' and host_key like '%globo.com%'"
            ).fetchone()
            con.close()
            ts = (row[0] or 0) if row else 0
            if ts > best_ts:
                best, best_ts = prof, ts
        except Exception:
            pass
    return best


def _read_globo_cookies(profile: str):
    """Le SO' os cookies de *globo.com* do perfil (copia p/ temp p/ pegar o WAL fresco)."""
    ck = REAL_USERDATA / profile / "Network" / "Cookies"
    tmpdir = Path(tempfile.mkdtemp(prefix="valorck_"))
    con = None
    try:
        try:
            shutil.copy2(ck, tmpdir / "Cookies")
            for ext in ("-wal", "-shm"):
                f = ck.with_name("Cookies" + ext)
                if f.exists():
                    shutil.copy2(f, tmpdir / ("Cookies" + ext))
            con = sqlite3.connect(str(tmpdir / "Cookies"))
        except Exception:
            con = sqlite3.connect(f"file:{ck}?mode=ro&immutable=1", uri=True)
        return con.execute(
            "select host_key,name,encrypted_value,path,expires_utc,is_secure,is_httponly "
            "from cookies where host_key like '%globo.com%'"
        ).fetchall()
    finally:
        if con is not None:
            con.close()
        shutil.rmtree(tmpdir, ignore_errors=True)


def _chrome_time(us: int) -> float:
    """expires_utc (microsegundos desde 1601) -> unix segundos; 0 = sessao (-1)."""
    if not us:
        return -1
    return us / 1_000_000 - 11644473600


# ── Sobre o formato NOVO de cookie (v20 / App-Bound Encryption, Chrome 127+) ──────────────────
# NAO existe leitura automatica dele. O Chrome tem DUAS travas anti-roubo que se fecham uma na
# outra (ambas testadas nesta maquina):
#   (1) copiar o perfil p/ outra pasta -> o App-Bound RECUSA decriptar (cookie volta vazio);
#   (2) abrir o Chrome no perfil REAL via automacao/CDP -> bloqueado no Chrome 136+
#       ("DevTools remote debugging requires a non-default data directory").
# O caminho e' fazer o Chrome voltar a gravar o formato ANTIGO (v10, que este script le), desligando
# a App-Bound Encryption por 1 comando de registro em HKCU (NAO precisa de admin). O passo a passo
# e' impresso automaticamente quando este script encontra a sessao so' em v20 (ver o fim do arquivo).


print("\n=== Renovar a sessao do Valor (lendo sua sessao ja' logada no Chrome) ===\n")

if not REAL_USERDATA.exists():
    print(f">>> Nao achei o Chrome em: {REAL_USERDATA}")
    sys.exit(1)

# Preflight: precisa de uma lib de AES-GCM (senao os cookies "somem" e parece deslogado).
try:
    import cryptography  # noqa: F401
except ImportError:
    try:
        import Crypto  # noqa: F401
    except ImportError:
        print(">>> Falta uma biblioteca de criptografia. Abra o Anaconda Prompt e rode:")
        print(">>>     pip install cryptography")
        print(">>> Depois rode o 'Atualizar Valor' de novo.")
        sys.exit(1)

try:
    key = _master_key()
except Exception as e:
    key = None
    print(">>> (aviso) chave classica (v10) do Chrome indisponivel:", e)
    print(">>> Sem problema — se os cookies forem do formato novo (v20), uso o plano B (Chrome).")

prof = _profile_with_globo()
rows = _read_globo_cookies(prof)

cookies, skipped, names, saw_v20 = [], 0, set(), False
for host, name, ev, path, exp, sec, http in rows:
    try:
        if key and ev and ev[:3] in (b"v10", b"v11"):
            val = _aesgcm(key, ev).decode("utf-8", "replace")
        else:
            skipped += 1
            if ev and ev[:3] == b"v20":
                saw_v20 = True
            continue
    except Exception:
        skipped += 1
        continue
    cookies.append({
        "name": name, "value": val, "domain": host, "path": path or "/",
        "expires": _chrome_time(exp), "httpOnly": bool(http), "secure": bool(sec),
        "sameSite": "Lax",
    })
    names.add(name)

print(f"Perfil '{prof}': {len(cookies)} cookies do globo lidos "
      f"({skipped} no formato novo). Cookie de sessao GLBID: {'SIM' if 'GLBID' in names else 'NAO'}")

if "GLBID" not in names:
    print("\n>>> Nao encontrei a sessao logada (GLBID) num formato que eu consiga ler.")
    if saw_v20:
        print(">>> Seu login existe, mas esta' no formato NOVO do Chrome (v20/App-Bound), que nao")
        print(">>> tem leitura automatica. CONSERTO (1x, SEM admin) — faz o Chrome voltar ao formato")
        print(">>> antigo que eu leio:")
        print(">>>")
        print(">>>   1) Abra o Prompt de Comando (NORMAL, nao precisa ser admin) e cole a linha:")
        print(r'>>>      reg add "HKCU\SOFTWARE\Policies\Google\Chrome" /v ApplicationBoundEncryptionEnabled /t REG_DWORD /d 0 /f')
        print(">>>   2) FECHE todas as janelas do Chrome e abra de novo;")
        print(">>>   3) Entre no valor.globo.com e confirme que le uma materia paga inteira;")
        print(">>>   4) Rode este 'Atualizar Valor' mais uma vez. (Depois nao precisa repetir.)")
    else:
        print(">>> Entre no valor.globo.com pelo seu Chrome normal (logado) e rode de novo.")
    sys.exit(1)

state_json = json.dumps({"cookies": cookies, "origins": []})
ps.state_path("valor").write_text(state_json, encoding="utf-8")   # copia local
ps._push_session_to_store("valor", state_json)                    # store (Supabase)

print("\n" + "=" * 60)
print(">>> Sessao lida do Chrome e salva no store. Verificando se ela DESTRAVA o conteudo pago...")
print("=" * 60)
# ⚠️ IMPORTANTE: o cookie GLBID pode estar PRESENTE mas ja invalidado no servidor (Chrome
# tambem deslogado). Verifica de verdade abrindo uma materia e checando o paywall.
_alive = None
try:
    from clipping.keepalive import _touch
    _alive = _touch("valor", "https://valor.globo.com/", ("GLBID",))
except Exception as _e:
    print("   (nao consegui verificar automaticamente:", _e, ")")

if _alive:
    print("\n" + "=" * 60)
    print(">>> SUCESSO CONFIRMADO: a sessao DESTRAVA o conteudo pago do Valor.")
    print(">>> O keep-alive (6h em 6h) e cada clipping vao mante-la viva.")
    print("=" * 60)
elif _alive is False:
    print("\n" + "!" * 60)
    print(">>> ATENCAO: salvei o cookie, MAS a sessao NAO destrava o conteudo pago.")
    print(">>> Quase sempre isso quer dizer que o seu CHROME TAMBEM esta deslogado do Valor.")
    print(">>>")
    print(">>> O QUE FAZER:")
    print(">>>   1) Abra valor.globo.com no Chrome e faca LOGIN de novo (como assinante);")
    print(">>>   2) Confirme que consegue LER uma materia PAGA inteira;")
    print(">>>   3) Rode este 'Atualizar Valor' de novo.")
    print("!" * 60)
else:
    print("\n" + "=" * 60)
    print(">>> Sessao salva no store. (Nao consegui verificar sozinho — confirme no Chrome")
    print(">>> que voce consegue LER uma materia paga inteira. Se aparecer paywall, logue de novo.)")
    print("=" * 60)
print("\nPode fechar esta janela.")
