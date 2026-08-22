# Instablack — memória do agente

Arquivo vivo. **Antes de mexer em dashboard, publish, timezone ou deploy**, leia isto.  
**Depois de cada correção relevante**, atualize a seção "Log de mudanças" no final.

Produção: `https://instablack-production.up.railway.app`  
Stack: FastAPI (web) + Celery workers + Beat + Postgres + Redis (Railway).

---

## Regra de ouro

1. Se posts aparecem no Instagram mas o painel fica em 0 → **não inventar**: olhar `PublishLog`, worker-publish logs e timezone BRT.
2. Se **uma** área do dashboard tem dados e **outra** não → as queries são diferentes; alinhar ao caminho que já funciona (bounds BRT), não “otimizar” fuso no SQL à cega.
3. Redeploy: mudança em `publish.py` / Celery → **worker-publish** (e misc se tocar). Mudança em `dashboard.py` / templates → **web**. Build antigo no worker já causou falso “não funcionou”.
4. Horário do produto é **America/Sao_Paulo (BRT)**. Nunca misturar “dia UTC” com “dia BRT” no KPI.

---

## O que já quebrou (não repetir)

### 1) Meta async publish sem `PublishLog` (corrigido ~`9a14772`)

Fluxo Phase C: `submit_media_container` → Redis pending (`started_at` ISO **string**) → poll → `finalize_media_publish` (post **já no IG**) → `_complete_meta_pending_success` → `_publish_timing(job["started_at"])`.

Bug: `started_at` era `str`, código fazia `started - sched` → `TypeError`. Post ia pro IG, **sem** `PublishLog` / `total_runs` / `last_run_at`.

Sintoma: painel morto, Instagram ok.  
Fix: parse ISO em `_publish_timing`; gravar log antes de notificação; recuperar log se complete falhar após publish.

### 2) KPI / gráfico zerados com logs e Top do Dia ok (corrigido ~`d7ff7b4`)

- **Ok:** `/logs`, Log de atividades, Top do Dia, Total publicado — usam `PublishLog` simples ou `_brt_day_bounds` + `_utc_naive`.
- **Quebrado:** Publicações hoje, Taxa de sucesso, Seu Desempenho — `_batch_status_counts` no Postgres com:

```text
date(timezone('America/Sao_Paulo', timezone('UTC', created_at)))
```

em coluna **timestamptz**. O double-convert jogava posts da **noite de hoje** para o **dia seguinte**; o gráfico (só últimos 7 dias BRT) **descartava** esses counts → card 0 + chart vazio.

Fix atual (`app/routes/dashboard.py`):

- Bucket do gráfico em **Python** com `_brt_date_from_db` (mesmo critério BRT).
- KPI do dia via `_count_logs_by_accounts(..., day=today)` (bounds BRT), igual ranking.

**Nunca** voltar a agrupar dia com `timezone('UTC', timestamptz)` + `America/Sao_Paulo` nesse padrão.

### 3) Insights / activity sob carga

- N+1 em Insights esvaziava o painel sob `statement_timeout` — batch + timeout maior.
- Activity card: preferir HTML server-render de `PublishLog`; poll `since_id` quebrou a UI.
- Avatars: cache `/media/avatars/ig/...`; enqueue com cooldown.

### 3.1) instagrapi/aiograpi bloqueante dentro de `async def` (web)

Rota `async def` chamando instagrapi (ou o `asyncio.run` do wrapper aiograpi) **trava o event loop** do UvicornWorker. O gunicorn roda com `--timeout 120` (`Procfile` / `scripts/railway-web.sh`) e **mata o worker**: a conexão cai e o navegador só mostra erro genérico de rede, com a tela presa em "carregando".

Sintoma real: `/accounts/profile-edit` com link (mais round-trips que a bio) ficava carregando e caía no `catch` do fetch.

Obs.: `account_edit(external_url=…)` responde sucesso mas o link **não** entra no perfil — não reintroduzir sem testar de verdade no app do Instagram.

Regras:

- Trabalho de rede bloqueante em rota async → `await run_in_threadpool(...)` (ou rota `def` normal).
- Lote longo → **uma requisição por conta** (`POST /accounts/profile-edit/one`), com progresso no front. Um POST único com N contas estoura qualquer timeout.
- No front, nunca engolir o erro: mostrar status/mensagem do servidor.

### 3.2) Botão Reconectar sem feedback (SPA)

Modal `#reconnect-session-modal` fora de `#app-content` → navegação SPA não trazia o HTML → JS fazia `if (!modal) return` **sem alert**. Challenge nativo (“Manual verification… challenge_code_handler”) **não** se resolve só com senha do cofre — liberar no app/site do IG e colar sessionid/cookies novos.

### 3.3) Login senha: Phantom TLS + login oficial instagrapi

A pasta `melhorias/` **não é importada**. O runtime é `phantom/` + `core/instagram.py`.

Não usar `Client()` stock no connect (TLS `requests` + `/accounts/login/` morto → 429). Não usar o `LoginFlow` Bloks do Phantom no `EnhancedClient.login` (2FA em loop).

- Connect: `EnhancedClient` (curl_cffi + headers) e `super().login()` do **instagrapi 2.18.16** (CAA prepare / `bloks_caa_login_prepare` restaurado).
- CAA **primeiro** (`_try_caa_login`); legado só se o CAA falhar. Sem código + `two_step` → modal 2FA.
- Um ipify no connect; 2FA com settings não re-checa proxy. `delay_range` [1, 2].
- Teto 85s + gunicorn 180s. Sem locale BR forçado no connect.

### 4) Contas Meta `code=190`

Token inválido / checkpoint (“You cannot access the app till you log in”). Afeta publish e Insights (0 views). **Não** é o mesmo bug de KPI/timezone. Conta fica `needs_login`; skip silencioso sem log ainda pode confundir — preferir `PublishLog` skipped/failed ao pular.

---

### 5) Cofre senha + TOTP (Authenticator)

- Campo `encrypted_totp_secret` em `instagram_accounts` (cifrado com `encrypt_secret`).
- UI: Contas conectadas → **Credenciais / 2FA** (código 6 dígitos ao vivo + copiar) + página `/accounts/vault`.
- Login/reconectar: se Instagram pedir 2FA e houver TOTP, gera o código automaticamente (`app/utils/totp.py` + `pyotp`).
- Nunca devolver o secret em plaintext — só o OTP.
- **View As:** cofre / TOTP / cookies / Meus Apps ficam bloqueados para admin comum. **Dono (`is_owner`)** pode **consultar** cofre e bloco de notas do alvo (GET only) para auditar golpe; POST continua bloqueado.

---

### 6) Segurança — Fase 1 (`c280915`) e Fase 2

**Fase 1 (auth):** `SECRET_KEY` fail-closed em produção; rate limit login/register (Redis); senha atual + `session_version` ao trocar senha; **cada login novo incrementa `session_version`** (1 sessão ativa — quem compartilha senha derruba o outro), **exceto** `is_owner` e usuários com `allow_multi_session` (não bumpam no login; cookies antigos continuam válidos); CSRF + headers HTTP; logout só POST; `session.clear()` no login. Redirect `/login?reason=session` quando a sessão foi invalidada. Troca de senha ainda bumpa para todos.

**Fase 2 (mídia / View As / Redis):**

- `/media/{key}` **não é mais público** só pela key. Acesso: assinatura HMAC `?exp=&sig=` **ou** sessão + ownership (`app/media_access.py`).
- Meta / worker usam `public_media_url` / `absolute_signed_media_url` (TTL ~6h). UI assina avatars/previews (TTL ~2h). Filtro Jinja `signed_media`.
- **Nunca** voltar a servir `/media` anônimo sem sig/owner — Meta precisa da URL assinada, não da R2 crua.
- Celery `rediss://`: `ssl_cert_reqs=CERT_REQUIRED`. Se worker não sobe no Railway Redis: escape hatch `REDIS_SSL_INSECURE=1` (temporário).
- Proxy: `validate_proxy_url` (esquemas http/https/socks*; bloqueia localhost / link-local / `file://`).

Redeploy Fase 2: **web** + **worker-publish** + **worker-misc** + **beat** (Celery TLS + `publish.py` URL assinada).

**Armadilha Starlette 1.x / FastAPI 0.141:** `TemplateResponse` agora é `(request, name, context)`. Chamadas legadas `TemplateResponse("x.html", {"request": ...})` quebram com `TypeError: unhashable type: 'dict'`. Mantemos `CompatJinja2Templates` em `app/templating.py` — não remover sem migrar todas as rotas.

---

## Mapa rápido do dashboard

| UI | Fonte | Notas |
|----|--------|--------|
| Contas / Automações ativas | contas + lista automações | OK |
| Publicações hoje / Taxa | `_count_logs_by_accounts` + dia BRT | Alinhar sempre a isto |
| Seu Desempenho | `_batch_status_counts` → `_brt_date_from_db` | Não reintroduzir SQL timezone quebrado |
| Log de atividades | `_recent_publish_logs` ORDER BY id DESC | Simples e confiável |
| Top do Dia | ranking API + bounds BRT | Referência de “hoje” correto |
| Contas conectadas (N posts) | success ~30d por conta | Número menor que Top do Dia (dia vs 30d / por conta) é esperado |
| Insights API oficial | Meta insights sync | 0 views ≠ bug de PublishLog |

Timezone helpers: `_brt_day_bounds`, `_utc_naive`, `_brt_date_from_db`, `brt_now()` em `app/routes/dashboard.py` / `app/utils/timezone.py`.

---

## Deploy Railway (checklist)

Serviços típicos: **web**, **worker-publish**, **worker-misc**, **beat**.

| Mudou | Redeploy |
|-------|----------|
| `celery_app/tasks/publish.py`, fila publish | worker-publish |
| insights / tasks misc | worker-misc |
| beat schedule / `celery_app/config.py` (TLS Redis) | beat + **todos** os workers |
| `app/routes/*`, templates, estáticos, `/media` | web |
| `app/media_access.py` + `public_media_url` | web **e** worker-publish (Meta URL assinada) |

Confirmar no deploy ativo a linha/commit — já houve caso de worker ainda no build velho enquanto o git estava certo.

---

## Convenções ao editar

- Status de `PublishLog`: `success` \| `failed` \| `skipped` (não traduzir no banco).
- Contas visíveis no dash: `active`, `paused`, `needs_login`, `proxy_down`, `banned`.
- Não commitar `.env`, credenciais, `.venv-test2-req.txt`, `login_instagram.py` (locais).
- Commits só quando o usuário pedir (exceto se ele pedir push/deploy explícito no fluxo).

---

## Log de mudanças

| Data | Commit / nota | O quê |
|------|----------------|-------|
| 2026-07-30 | `9a14772` | Fix `started_at` str no Meta async → PublishLog volta a gravar |
| 2026-07-30 | `d7ff7b4` | Fix bucket BRT KPI + gráfico; não usar double timezone Postgres |
| 2026-07-30 | este arquivo | Criado `AGENT_MEMORY.md` a pedido do usuário para não repetir cagada |
| 2026-07-31 | local | `instagrapi` → **2.18.12** (`requirements.txt`); `login_instagram.py` login user/senha + validação de sessão (sem sessionid hardcoded) |
| 2026-07-31 | local | BadPassword em `@deborateixei091` com proxy ok = rejeição IG (`invalid_credentials`). Cookie browser expira rápido; sessão durável = `login(senha)` **uma vez** + `dump_settings`. Script alinhado a `core.instagram.login_with_credentials`. |
| 2026-08-03 | feature | Cofre virou aba `/accounts/vault` no sidebar (cards por conta). Botão na tabela não abria por modal fora do `#app-content`. |
| 2026-08-03 | fix | Cofre: UI Autenticador (código azul + anel), `/vault/codes` batch, salva chave de verdade; rejeita colar código 6 dígitos no lugar da secret. |
| 2026-08-05 | `c280915` | Segurança Fase 1: SECRET_KEY fail-closed, rate limit auth, CSRF, headers, session_version, senha atual, logout POST. |
| 2026-08-05 | Fase 2 | `/media` assinado + ownership; View As bloqueia vault/TOTP/cookies/meta-apps; Celery `CERT_REQUIRED`; `validate_proxy_url`. Ver §6. |
| 2026-08-05 | deps | `pip-audit` limpo: FastAPI 0.141.1 + Starlette ≥1.3.1, Jinja2 3.1.6, multipart 0.0.31, dotenv 1.2.2. |
| 2026-08-05 | brand | Tema azul → **preto + dourado** (`#D4AF37`); logo fantasma `logo-ghost.png` / favicon. |
| 2026-08-06 | hotfix | Starlette 1.x quebrou `TemplateResponse(name, ctx)` → 500 no `/login`. Compat em `app/templating.py`. |
| 2026-08-06 | bug | Story: form postava em `/automations/new` → erro/re-render virava tela de Reels. Fix: POST `/new/story` + `content_type` forçado + action do form. |
| 2026-08-06 | bug | Story “Envie o arquivo de mídia” com arquivo selecionado: CSRF middleware chamava `request.form()` no multipart (BaseHTTPMiddleware esvazia body). Reels ok (fetch+header). Fix: multipart CSRF só via header; Story/Foto postam via fetch. |
| 2026-08-06 | bug | Reels `body.name: Field required` intermitente: `reel-draft` usava `Form()` bind. Reescrito com `request.form()`; erros de validação em fetch voltam JSON. |
| 2026-08-06 | feat | Instagrapi (senha/sessionid/import) só para owner + `allow_instagrapi`. UI escondida; POST bloqueado; admin “Liberar Instagrapi”. Meta/cookies abertos. |
| 2026-08-06 | feat | Aviso global + `needs_login` para contas Instagrapi legadas (sem cookies web): “sessão expirada / API caiu”. Owner/liberados não veem. |
| 2026-08-06 | feat | Instagrapi UI visível pra todos; sem liberação o login “tenta” 2–4s e falha como erro de autenticação. Só owner/`allow_instagrapi` conecta de verdade. |
| 2026-08-06 | feat | Limite **150 vídeos Reels por usuário** (soma das playlists). Gate em create/upload-batch/direct-upload; UI mostra usados/restantes. |
| 2026-08-06 | feat | Revoga Instagrapi ativo sem liberação (`needs_login` + limpa session); publish bloqueia; admin “Desligar Instagrapi”; menu Gerenciar no admin; olho Top do Dia alinhado no mobile. |
| 2026-08-06 | ux | Drawer mobile com seções + Perfil; esconde nome Instagrapi na UI pública; Top do Dia sem views; login exclusivo (1 sessão — novo login derruba as outras). |
| 2026-08-07 | feat | Story Link Studio aceita login clássico (instagrapi) + cookies web; posição do sticker (x/y) vai no upload mobile. Meta continua sem link. |
| 2026-08-07 | feat | Bloco de notas (`/accounts/notes`): cola lote `user | senha` + URL 2FA (browserscan/#secret), guarda cifrado, mostra código TOTP e senha sob demanda. |
| 2026-08-08 | feat | Ver como: dono lê cofre/bloco do usuário (somente leitura). Ícone PWA Android usa PNG 192/512 do fantasma 3D (não SVG maskable). |
| 2026-08-08 | feat | 4ª API: **aiograpi** (`provider=aiograpi`) — chip “Login async”, wrapper `core/aiograpi_client.py`, publish no worker. Mesmo gate `allow_instagrapi`. Dep `aiograpi==1.12.8`. Redeploy **web + worker-publish**. |
| 2026-08-08 | feat | `/accounts/profile-edit`: bio + foto em lote (instagrapi/aiograpi). Meta fora. Gate `allow_instagrapi`. Redeploy **web**. |
| 2026-08-08 | nota | Editar perfil: **link no perfil removido**. `account_edit(external_url=…)` respondia OK mas o Instagram **não aplicava** o link (silencioso, sem erro). Spintax na bio também foi removido a pedido. Ficou bio + foto. |
| 2026-08-10 | fix | **Reconectar** não abria: modal estava fora de `#app-content` (SPA) e/ou clipado pelo overflow. Modal no content + move p/ `body` ao abrir; mensagem de challenge nativo; fallback alert. Redeploy **web**. |
| 2026-08-10 | feat | Health revive com senha+TOTP do cofre (cooldown Redis 6h); pula se `last_error` for challenge/checkpoint. Redeploy **worker** (fila health/misc). |
| 2026-08-10 | feat | aiograpi: `_stable_uuids(username)` no login sem settings (mesmo fingerprint do instagrapi). |
| 2026-08-10 | feat | Warmup: também curte/comenta posts dos **influenciadores** (`like_influencer` / `comment_influencer`), não só seguidores. |
| 2026-08-11 | feat | **Phantom** integrado (`phantom/`): EnhancedClient no login clássico — headers stealth, `x-ig-nav-chain`, TLS via `curl_cffi` (chrome131_android), login Bloks CAA. Flag `PHANTOM_ENABLED` (default true). Locale headers em **pt_BR**. Redeploy **web + todos workers** que usam instagrapi. |
| 2026-08-11 | fix | Pós-Phantom: health **aiograpi** não pode usar instagrapi/Phantom (marcava `needs_login` e pulava publish). `core/device_fingerprint.py` isola UUID estável; patch story só no 1º client instagrapi. Redeploy **worker-publish + worker-misc**. |
| 2026-08-11 | fix | Recovery one-shot `recover_publish_after_phantom`: reativa Meta/aiograpi `needs_login` (token/sessão OK), limpa locks Meta, `next_run_at=now` em automações **active**. Dispara via beat (60s, Redis NX). Redeploy **beat + worker-misc + worker-publish**. |
| 2026-08-12 | fix | Login user/senha **sem Phantom**: Bloks 2FA não aplicava auth → UI pedia código em loop (só dono “passava”). `allow_phantom=False` em `login_with_credentials`; Phantom não levanta mais `TwoFactorRequired` falso no apply; fallback stock se Bloks falhar com código. Redeploy **web**. |
| 2026-08-13 | fix | Connect senha = **PostagemIG** (`Client().login`, delay [2,5], sem locale BR forçado). CAA só se legado der 429. Redeploy **web**. |
| 2026-08-13 | dep | **instagrapi==2.18.14** (latest PyPI/GitHub; > 2.18.9). Não pinar 2.16.25 do PostagemIG. Redeploy **web + workers**. |
| 2026-08-13 | fix | Connect: teto 85s no login + fail-fast em PleaseWait/429 (sem CAA extra) + msg clara no "Failed to fetch". gunicorn `--timeout 180`. app-v **102**. Redeploy **web**. |
| 2026-08-13 | fix | PleaseWait/429 no legado → **CAA `_try_caa_login`** de novo (AGENT_MEMORY 3.3): sem isso o web não pedia 2FA. Redeploy **web**. |
| 2026-08-14 | fix | Connect instagrapi: Phantom **só TLS/headers**; `login()` volta ao oficial (não LoginFlow). Stock Client no connect = 429. Redeploy **web**. |
| 2026-08-14 | perf | Connect mais rápido: 1× ipify, delay [1,2], CAA-first (sem esperar 429 no legado). Redeploy **web**. |
| 2026-08-19 | hotfix | Web crash: `automations.py` perdeu import `get_effective_user`. Depois: `/accounts/connected` 500 porque `account_folders` não era criada no migrate Postgres (só `folder_id`). CREATE TABLE + página não cai se a tabela faltar. Redeploy **web**. |
| 2026-08-19 | feat | Dashboard KPI **Comentários respondidos** (tabela `comment_auto_replies`). Fotos feed: upload múltiplo + limite **150 mídias** Reels+fotos somadas por usuário (R2). Redeploy **web**. |
| 2026-08-21 | fix | Instagrapi **2.18.16** (CAA login prepare). Owner + `allow_multi_session` mantêm multi-sessão no login. Mobile: botão Atualizar tela (topbar + drawer). app-v **114**. Redeploy **web**. |
| 2026-08-21 | tool | `login_instagram.py`: login **local** (IP residencial) + Phantom + dump `sessions/@_session.json` pra importar no painel (bypass 429 senha no Railway). Import aceita `account.json` com `instagrapi_settings`. |
| 2026-08-21 | feat | **Call** (LiveKit Cloud): sala global voz+tela+chat. Flag `allow_voice_room` (owner libera no Admin). Rotas `/call` + `/call/token`. Envs `LIVEKIT_*`. app-v **115**. Redeploy **web**. |
| 2026-08-21 | ux | Call visual Discord (tiles + falando verde + dock). Mic pede permissão antes; parar tela não derruba sala; join mais rápido (SDK+token+mic em paralelo). app-v **116**. |
| 2026-08-21 | ux | Call: layout Discord (canais + Voz conectada + userbar), tela cheia/mobile. **Entra na sala primeiro**, mic depois (banner). Join não falha se mic bloquear. app-v **117**. |

<!-- Ao corrigir bugs de produção: acrescente uma linha acima e, se for armadilha nova, uma subseção em "O que já quebrou". -->
