# -*- coding: utf-8 -*-
"""
Monta o e-mail do clipping a partir do Word (.docx) gerado pela dashboard.

= o seu "abrir o Word, CTRL-A, CTRL-ALT-V (Texto Formatado RTF) no e-mail", automatizado.
Por que assim e nao o .eml? O Outlook renderiza HTML muito mal e NAO mostra imagens
embutidas em base64 (a logo vira "X"). Colar o conteudo do Word como conteudo formatado
NATIVO (RTF) fica identico ao Word — com logo e graficos, que viram imagens de verdade.

Dois modos (o script escolhe sozinho):
  A) Outlook acessivel  -> ja abre um RASCUNHO pronto, igual ao Word. So conferir e enviar.
  B) Outlook indisponivel -> copia tudo e mantem aberto; voce da CTRL-V no seu e-mail.
O script NUNCA envia nada.

Uso:
  1) Baixe o clipping_AAAAMMDD.docx na dashboard (cai no Downloads).
  2) De 2 cliques em "Montar email do clipping.bat" (pega o .docx mais novo do Downloads),
     ou arraste um .docx especifico pra cima do .bat.

Requisitos (ja presentes nesta maquina): Word + pywin32 (Outlook desktop p/ o modo A).
"""
import os
import sys
import glob
import re
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


def log(msg=""):
    print(msg, flush=True)


def pause(msg="\nEnter para fechar..."):
    try:
        input(msg)
    except Exception:
        pass


def die(msg):
    log(msg)
    pause()
    sys.exit(1)


def find_docx():
    """.docx passado como argumento (arrastar no .bat); senao o clipping_*.docx mais novo do Downloads."""
    for a in sys.argv[1:]:
        if a.lower().endswith(".docx"):
            if os.path.isfile(a):
                return a
            die(f"Arquivo nao encontrado: {a}")
    dl = os.path.join(os.path.expanduser("~"), "Downloads")
    cands = glob.glob(os.path.join(dl, "clipping_*.docx"))
    if not cands:
        die("Nenhum clipping_*.docx no Downloads.\n"
            "Baixe o Word do clipping na dashboard primeiro, ou arraste o .docx pra cima do .bat.")
    return max(cands, key=os.path.getmtime)


def subject_from(path):
    m = re.search(r"clipping_(\d{4})(\d{2})(\d{2})", os.path.basename(path))
    if m:
        y, mo, d = m.groups()
        return f"*** ITAU BBA Daily News: LatAm Steel & Mining, Pulp & Paper - {mo}/{d}/{y} ***"
    return "*** ITAU BBA Daily News: LatAm Steel & Mining, Pulp & Paper ***"


def get_outlook(win32):
    """SO anexa a um Outlook JA ABERTO. None se nao estiver aberto.

    De proposito NAO tenta subir o Outlook via COM: um cold-start costuma travar na
    tela de perfil e pendurar tudo. Se o Outlook nao estiver aberto -> cai no Modo B.
    """
    try:
        return win32.GetActiveObject("Outlook.Application")
    except Exception:
        return None


def build_draft(selftest=False):
    import win32com.client as win32

    docx = os.path.abspath(find_docx())
    subject = subject_from(docx)
    log(f"Word:    {docx}")
    log(f"Assunto: {subject}")
    log("")

    # Word ISOLADO (instancia dedicada — nao mexe no Word que voce ja tem aberto).
    word = win32.DispatchEx("Word.Application")
    word.Visible = False
    try:
        word.DisplayAlerts = 0
    except Exception:
        pass

    doc = word.Documents.Open(docx, ReadOnly=True, AddToRecentFiles=False)
    doc.Content.Copy()  # = CTRL-A + CTRL-C — poe RTF + HTML + imagens na area de transferencia

    # ---- Modo A: Outlook monta o rascunho sozinho ----
    outlook = get_outlook(win32)
    if outlook is not None:
        try:
            mail = outlook.CreateItem(0)  # 0 = olMailItem
            mail.Subject = subject
            editor = mail.GetInspector.WordEditor  # o corpo do e-mail e um documento do Word
            if editor is None:
                raise RuntimeError("O Outlook nao esta com o Word como editor.")
            editor.Range(0, 0).Paste()  # = CTRL-ALT-V (Texto Formatado RTF)
            if selftest:
                txt = ""
                try:
                    txt = editor.Range().Text or ""
                except Exception:
                    pass
                mail.Close(1)  # olDiscard — descarta (nada aberto, nada enviado)
                _close(doc, word)
                log(f"[selftest] Modo A OK: colado {len(txt)} chars; rascunho descartado.")
                return
            mail.Display(False)  # abre o rascunho; NAO envia
            _close(doc, word)
            log("OK -> rascunho aberto no Outlook, identico ao Word (com logo e imagens).")
            log("Confira os DESTINATARIOS e clique Enviar voce mesmo. (o script nao envia nada)")
            return
        except Exception as e:
            log(f"(Outlook automatico nao rolou: {e})")
            log("Sem problema — vamos no modo copiar/colar.")
            log("")

    # ---- Modo B: conteudo ja copiado; voce cola. Mantem o Word vivo ate voce colar. ----
    if selftest:
        _close(doc, word)
        log("[selftest] Modo B: Word copiou OK (Outlook indisponivel neste ambiente).")
        return
    log("Copiei o clipping INTEIRO (formatado, com imagens).")
    log("Agora, no seu e-mail (novo e-mail ou responder):")
    log("   clique no corpo do e-mail e aperte  CTRL+V")
    log(f"Assunto sugerido:  {subject}")
    pause("\nDepois de colar no e-mail, aperte ENTER aqui para fechar. ")
    _close(doc, word)


def _close(doc, word):
    try:
        if doc is not None:
            doc.Close(False)
    except Exception:
        pass
    try:
        word.Quit()
    except Exception:
        pass


def main():
    selftest = "--selftest" in sys.argv
    try:
        build_draft(selftest=selftest)
    except SystemExit:
        raise
    except Exception as e:
        log("")
        log(f"ERRO: {e}")
        log("Dicas: deixe o OUTLOOK aberto e rode de novo. Se persistir, me mande esta mensagem.")
        if not selftest:
            pause()
        sys.exit(1)


if __name__ == "__main__":
    main()
