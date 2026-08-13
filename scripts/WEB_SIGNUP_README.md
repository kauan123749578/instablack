# Lab — criar conta Instagram (web)

## Problema: HTTP Toolkit free NAO deixa copiar

Nao e culpa sua. A tabela REQUEST BODY e so leitura.
Export cURL muitas vezes e **Pro** (pago).

---

## Solucao facil: `run-simple` (SEM copiar nada)

Usa API web antiga do Instagram. So precisa do email.

```powershell
python scripts/web_signup_lab.py run-simple --email primewqii@gmail.com
```

Automatico:
- Nome feminino aleatorio
- Senha: `986004`
- Nascimento: `05/07/2005`

Fluxo:
1. Envia email de verificacao
2. Voce cola o codigo de 6 digitos no terminal
3. **Depois** do codigo: busca @ nas sugestoes do Instagram
4. Cria a conta

> Username so vem **apos** confirmar o email (fluxo CAA). Antes disso o IG
> devolve lista vazia — isso era o bug.

### Capturar de novo (HTTP Toolkit / DevTools)

```powershell
python scripts/web_signup_lab.py capture-guide
```

Faca cadastro **manual** no Chrome com intercept ligado. Anote os POST:
`send_verify_email` → `check_confirmation_code` → `username_suggestions` → `web_create_ajax` / `graphql`

DevTools (gratis): F12 > Network > Copy as cURL → `import-curl`

Debug:
```powershell
python scripts/web_signup_lab.py probe-signup --email wqiikauan@gmail.com
```

---

## Modo CAA GraphQL (o que voce capturou no HTTP Toolkit)

So precisa se `run-simple` nao funcionar.

Alternativa sem copiar body inteiro — **digite o doc_id** que aparece na tela:

```powershell
python scripts/web_signup_lab.py set-doc register 27029416779977343 useCAARegistrationFormSubmitMutation
```

(Precisa tambem do doc_id do request de confirmacao — olhe na tela igual.)

---

## Chrome DevTools (se quiser copiar de graca)

1. F12 na pagina do Instagram (mesmo browser)
2. Aba **Network**
3. Clica Cadastrar
4. Clica no POST graphql
5. Botao direito → **Copy as cURL**
6. Cola num `.txt` → `import-curl`

Isso copia de graca, o HTTP Toolkit free nao copia.
