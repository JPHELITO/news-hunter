# local_tools — ferramentas LOCAIS de renovação de sessão (BACKUP)

Rodam no **PC do usuário** (não no CI). Isto é **backup** — a cópia usada de verdade mora em
`C:\Users\<voce>\IBBA-Dashboard\` (ao lado da pasta `news-hunter` e do `.env`).

## refresh_valor.py + "Atualizar Valor.bat"
Renovam a sessão do **Valor**: leem a sessão já logada no seu **Chrome** (cookie `GLBID`,
decripta LOCAL via DPAPI + AES-GCM), verificam se destrava o conteúdo pago e salvam no store
(Supabase `source_sessions`). O login do Globo tem **anti-bot** → não dá pra automatizar o login;
por isso a renovação LÊ os cookies do Chrome. Sem segredos no arquivo (lê `news-hunter/.env`).

## Se você trocar/perder o PC (recuperação):
1. Instale Python + `pip install cryptography requests python-dotenv playwright`
2. Recrie a pasta `IBBA-Dashboard/` com os 2 repos dentro (`news-hunter`, `IBBA-Research-Dashboard`)
3. Recrie `news-hunter/.env` com `SUPABASE_URL` + `SUPABASE_SERVICE_KEY` (pegue dos **Secrets do GitHub**)
4. Copie estes 2 arquivos para a **raiz** de `IBBA-Dashboard/`
5. Entre no valor.globo.com pelo Chrome (login de assinante) → duplo-clique em "Atualizar Valor.bat"
