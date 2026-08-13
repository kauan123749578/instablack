# Web Signup Lab — status (12 Aug 2026)

**Último teste:** ~10:05 (BRT)  
**Próximo passo:** pausa de algumas horas (Instagram rate limit / anti-bot)

---

## Objetivo

Criar conta Instagram de teste via script (`scripts/web_signup_lab.py`), replicando fluxo web (HTTP Toolkit / CAA GraphQL) e híbrido com instagrapi.

---

## O que funciona

| Passo | Endpoint / fluxo | Status |
|-------|-------------------|--------|
| Bootstrap (cookies, `ig_did`, `mid`, `lsd`) | GET `/accounts/emailsignup/` | OK |
| Enviar email | `send_verify_email` (web) | OK (exceto captcha/proxy) |
| Validar código 6 dígitos | `check_confirmation_code` | OK → gera `signup_code` |
| Criar conta (web antigo) | `web_create_ajax` | **Morto / 429** |
| Criar conta (instagrapi) | `accounts/create/` | Testado no híbrido — **429 + spam** |
| CAA GraphQL register | `useCAARegistrationFormSubmitMutation` | **Variables inválidas** (chute sem body capturado) |
| instagrapi `signup_caa_email` | Bloks mobile | Email+código OK, **create falhou** |
| instagrapi `signup()` legado | `check_email` primeiro | **429** na proxy nova (10:05) |

---

## Cadastro guardado — primewqii@gmail.com (PRIORIDADE)

Arquivo: `scripts/web_signup_pending/primewqii_gmail_com.json`

| Campo | Valor |
|-------|--------|
| email | primewqii@gmail.com |
| nome | Camila Rodrigues |
| username | camila1067 |
| verify_code | 417053 |
| signup_code | dJrX4GQI |
| senha | 986004 |
| nascimento | 05/07/2005 |

**Retomar (sem proxy, mesma rede de quando validou o código):**

```powershell
python scripts/web_signup_lab.py restore-signup primewqii_gmail_com
python scripts/web_signup_lab.py finish-signup --email primewqii@gmail.com
```

O `finish-signup` tenta na ordem: `web_create_ajax` → `accounts/create/` (instagrapi) → CAA GraphQL confirm.

**Não usar proxy** no finish do primewqii — cookies/sessão foram da rede local; proxy + cookies antigos gerou `"spam": true` / scraping.

---

## Testes wqiikauan@gmail.com (email “queimado” hoje)

| Horário (~) | Comando | Resultado |
|-------------|---------|-----------|
| ~09:49 | `run-simple` + proxy `92.112.170.40` | `require_captcha` no email |
| ~09:56 | `run-instagrapi --caa` + proxy `92.112.170.40` | Código OK, `CAA signup did not return created_user` |
| ~10:05 | `new-session` + `run-instagrapi` + proxy `45.38.101.134` | **429** em `users/check_email` (nem enviou email) |

Proxies usadas (user `uyjsasdy`): `92.112.170.40:6009`, `45.38.101.134:6067`

---

## Bloqueios Instagram observados

- **HTTP 429** — rate limit (muitas tentativas no mesmo IP)
- **`feedback_required` + `"spam": true`** — automação/scraping detectado
- **`require_captcha`** — web signup com proxy
- **`noncoercible_variable_value`** — CAA GraphQL register (JSON `variables` errado sem captura HTTP Toolkit)

Script atualizado para **parar retries** quando detecta spam/scraping (não esperar 90s+180s à toa).

---

## Comandos do lab

```powershell
# Cadastro web (email → código → create híbrido)
python scripts/web_signup_lab.py run-simple --email EMAIL

# Com proxy (--proxy ANTES do subcomando)
python scripts/web_signup_lab.py --proxy "http://user:pass@host:port" run-simple --email EMAIL

# Instagrapi mobile (legado)
python scripts/web_signup_lab.py --proxy "http://..." run-instagrapi --email EMAIL

# Instagrapi Bloks CAA (experimental)
python scripts/web_signup_lab.py --proxy "http://..." run-instagrapi --email EMAIL --caa

# Guardar / restaurar cadastro parcial
python scripts/web_signup_lab.py archive-signup primewqii
python scripts/web_signup_lab.py restore-signup primewqii_gmail_com
python scripts/web_signup_lab.py list-pending
python scripts/web_signup_lab.py new-session

# Continuar sem novo email
python scripts/web_signup_lab.py finish-signup --email primewqii@gmail.com
```

---

## Arquivos relevantes

| Arquivo | Uso |
|---------|-----|
| `scripts/web_signup_lab.py` | Script principal (web + híbrido + instagrapi) |
| `scripts/WEB_SIGNUP_README.md` | Guia de uso / HTTP Toolkit |
| `scripts/WEB_SIGNUP_STATUS.md` | Este status |
| `scripts/web_signup_pending/*.json` | Cadastros guardados (gitignored) |
| `scripts/web_signup_state.json` | Sessão atual (gitignored) |
| `scripts/web_signup_capture.json` | doc_ids CAA se importar (gitignored) |

**Register doc_id conhecido (HTTP Toolkit):** `27029416779977343` (`useCAARegistrationFormSubmitMutation`)  
**Confirm doc_id:** ainda não capturado — necessário para fallback CAA GraphQL.

---

## Onde paramos (10:05)

1. **primewqii** — email + código validados; `signup_code` salvo; create bloqueado por 429/spam. **Guardado.** Retomar após pausa **sem proxy**.
2. **wqiikauan** — várias tentativas; proxies e email provavelmente limitados. **Pausar.**
3. **CAA GraphQL register** — não funciona sem importar body/cURL do HTTP Toolkit ou Chrome DevTools.
4. **Decisão:** descanso de algumas horas antes de novos testes.

---

## Próximos passos (depois da pausa)

1. **primewqii:** `restore-signup` + `finish-signup` **sem proxy** (rede de casa).
2. Se ainda 429: esperar **24h**; não insistir no mesmo dia.
3. Teste novo (se quiser): **email novo** + **IP proxy novo** + `run-simple` (web), não `run-instagrapi` (mobile bloqueia no `check_email`).
4. Opcional: capturar `confirm` doc_id no HTTP Toolkit / Chrome para fallback CAA.
5. Opcional: import cURL — `import-curl register register.curl.txt`

---

## Defaults do lab

- Senha: `986004`
- Nascimento: `05/07/2005`
- Nome/username: gerados automaticamente (feminino)
