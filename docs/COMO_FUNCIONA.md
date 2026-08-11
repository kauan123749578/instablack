# Instablack — APIs, automações e como funciona por baixo dos panos

Documento vivo do produto. Serve para entender **quais APIs do Instagram existem no sistema**, **o que cada uma faz**, e **como a publicação / painel / workers funcionam**.

Produção: `https://instablack-production.up.railway.app`  
Stack: **FastAPI** (web) + **Celery** (workers) + **Beat** (agendador) + **Postgres** + **Redis** + storage (**Cloudflare R2** ou disco local).

Notas operacionais de bugs já corrigidos: `AGENT_MEMORY.md`.  
Deploy Railway: `RAILWAY.md`.  
Meta async (submit/poll): `docs/SCALABILITY_PHASE_C.md`.

---

## 1. Visão geral do que o software faz

O Instablack é um painel SaaS para **conectar várias contas Instagram** e **automatizar publicações** (Reels, Stories, Fotos) em lote, com intervalo ou calendário.

Além disso, o sistema oferece:

| Área | O que faz |
|------|-----------|
| Contas | Conectar / reconectar / pausar / excluir contas; proxy por conta |
| Automações | Agendar Reels, Stories e Fotos em várias contas |
| Story Link Studio | Editor visual do botão de link no Story |
| Editar perfil | Bio e foto de perfil em lote (API privada) |
| Cofre | Guardar senha + chave TOTP (Authenticator) das contas |
| Bloco de notas | Colar lote `user \| senha` + URL 2FA (não conecta a conta) |
| Aquecimento Meta | Intervalo maior entre posts Meta no início |
| Warmup de interação | Curtidas / ações via sessão privada (quando liberado) |
| Camuflagem | Overlay visual no Reel antes de publicar |
| Dashboard / Analytics / Logs | KPIs do dia (BRT), gráficos, Top do Dia, histórico |
| Extensão Chrome | Sincronizar cookies / sessão do navegador |
| Admin | Liberar Instagrapi, Ver como, gerenciar usuários |

Horário do produto: **America/Sao_Paulo (BRT)**.

---

## 2. As 4 formas de API / conexão com o Instagram

No banco, o campo principal é `InstagramAccount.provider`:

| Valor em `provider` | Nome na UI | Biblioteca / caminho |
|---------------------|------------|----------------------|
| `meta` | API oficial | Instagram Graph API — `core/meta_instagram.py` |
| `instagrapi` | Login clássico | `instagrapi` — `core/instagram.py` |
| `aiograpi` | Login async (teste) | `aiograpi` — `core/aiograpi_client.py` |
| *(ainda `instagrapi`)* | Cookies web | Mesmo login clássico + `encrypted_web_cookies` — `core/web_cookies.py`, `core/story_web.py`, `core/reel_web.py` |

> **Importante:** cookies web **não** são um `provider` separado. A conta continua `provider=instagrapi`, mas com cookies do navegador salvos. Na tela de Contas conectadas aparece o badge **Cookies web**.

### 2.1 Comparativo rápido

| Capacidade | Meta (oficial) | Login clássico (instagrapi) | Login async (aiograpi) | Cookies web |
|------------|----------------|-----------------------------|------------------------|-------------|
| Publicar Reel | Sim | Sim | Sim | Sim (pode usar upload web) |
| Publicar Story | Sim | Sim | Sim | Sim |
| Story com link clicável | Não (sem sticker custom) | Sim (mobile + desenho do botão) | Limitado (StoryLink fixo) | Sim (nativo, estilo INSSIST) |
| Publicar Foto no feed | Sim | Sim | Sim | Via sessão |
| Bio em lote | Não | Sim | Sim | Via sessão clássica |
| Foto de perfil em lote | Não | Sim | Sim | Via sessão clássica |
| Link do perfil (`external_url`) | — | **Removido** (API mentia sucesso) | **Removido** | — |
| Insights / views no painel | Sim (Graph) | Parcial (stats privadas) | Parcial | Parcial |
| Proxy | Opcional | **Obrigatório** | **Obrigatório** | **Obrigatório** |
| Liberação admin | Todos | Owner / `allow_instagrapi` | Mesmo gate do clássico | Aberto (não revoga como mobile puro) |

---

## 3. Detalhe de cada API

### 3.1 API oficial (Meta / Graph)

**Como conectar**

1. Cadastre o app em **Meus Apps** (`UserMetaApp`).
2. Em Adicionar conta → **Token / OAuth**.
3. OAuth (`/accounts/meta/connect` → callback) ou colar token.
4. Guarda: `encrypted_meta_access_token`, `meta_ig_user_id`, `user_meta_app_id`.

**Arquivos:** `core/meta_instagram.py`, `app/routes/accounts.py`, `app/routes/meta_apps.py`.

**Como publica (por baixo dos panos)**

1. Worker baixa / prepara mídia e gera **URL pública assinada** (`/media/...` com HMAC).
2. Chama Graph: cria container (`submit_media_container`).
3. Em modo async (Phase C): grava job no Redis → task `meta_poll_container` consulta status → `finalize_media_publish`.
4. Grava `PublishLog` com sucesso/falha (incluindo tempos de fila).

**Limitações**

- Conta Business/Creator ligada ao app Meta.
- Intervalo mínimo típico de **60 min** entre posts (anti-spam).
- Story **sem** sticker de link customizado do Studio.
- Token inválido (`code=190`) → conta `needs_login`.

---

### 3.2 Login clássico (instagrapi)

**Como conectar** (Adicionar conta → grupo Login clássico)

Usa o pacote **Phantom** (`phantom/`) por cima do instagrapi quando `PHANTOM_ENABLED=true` (padrão):

- TLS/JA3 via `curl_cffi` (impersonate `chrome131_android`)
- Headers extras do app Android + `x-ig-nav-chain`
- Login Bloks CAA (com 2FA) em vez do fluxo legado frágil

Se Phantom/curl_cffi falhar no boot do client, cai no `instagrapi.Client` normal (log de warning).

| Método (`auth_method`) | O que envia |
|------------------------|-------------|
| Usuário e senha | `@` + senha (+ TOTP opcional) |
| Session ID | Cookie `sessionid` do navegador |
| session.json | Dump de settings do instagrapi |

**Gate de acesso:** só **owner** ou usuário com `allow_instagrapi=true` conecta de verdade. Os demais veem a UI, mas o login “tenta” alguns segundos e falha como autenticação inválida (`app/utils/instagrapi_access.py`).

**O que fica no banco**

- `session_json` — sessão serializada (cookies / device / authorization).
- `encrypted_password` / `encrypted_totp_secret` (opcional, cofre).
- `proxy` **obrigatório**.

**Arquivo principal:** `core/instagram.py`.

**O que consegue publicar**

- Reel (`clip_upload` ou caminho web se houver cookies).
- Story foto/vídeo; com link: tenta API web se tiver `csrftoken`, senão mobile + botão desenhado na mídia.
- Foto no feed.
- Editar bio e foto de perfil (`account_set_biography`, `account_change_picture`).

---

### 3.3 Login async (aiograpi) — 4ª API de teste

Fork **async** da mesma família de API privada do Instagram ([aiograpi](https://github.com/subzeroid/aiograpi)).

**Como conectar:** chip **Login async** → user/senha + proxy (mesmo gate `allow_instagrapi`).

**Por baixo dos panos**

- Wrapper sync em `core/aiograpi_client.py` usa `asyncio.run(...)` (ou thread se já houver loop).
- Sessão também vai em `session_json`, com `provider=aiograpi`.
- No worker de publish, se `provider == "aiograpi"`, usa `aio_ig.publish_reel` / `publish_story` / `publish_photo`.

**Para quê serve:** testar estabilidade / ban / velocidade lado a lado com instagrapi, sem trocar o resto do produto.

---

### 3.4 Cookies web (Cookie-Editor / extensão)

**Como conectar**

- Adicionar conta → **Cookies JSON**, ou
- Extensão Chrome **Instablack Session Sync** (`extension/` + `/api/extension/*`).

Precisa no mínimo de `sessionid` + `csrftoken` (idealmente também mid, ig_did, ds_user_id…).

**Por baixo dos panos**

1. Parse em `core/web_cookies.py`.
2. Login via `login_with_sessionid` → `session_json`.
3. Cookies cifrados em `encrypted_web_cookies`.
4. No publish de Story com link / Reel web: `core/story_web.py` e `core/reel_web.py` usam a sessão do navegador.

**Vantagem:** Story com botão de link **nativo** (parecido com ferramentas tipo INSSIST/Opalite). Contas só com cookies web **não** são revogadas quando o admin desliga Instagrapi mobile.

---

## 4. Automações de publicação

**Modelo:** `Automation` (`models/models.py`).  
**UI:** `app/routes/automations.py`.  
**Worker:** `celery_app/tasks/publish.py`.  
**Agendador:** `celery_app/beat.py` (`tick` periódico).

### 4.1 Tipos de conteúdo

| `content_type` | O que publica |
|----------------|---------------|
| `reel` | Vídeo Reel (+ capa opcional) |
| `story` | Foto ou vídeo no Story (+ link/layout opcional) |
| `photo` | Foto no feed |

Uma automação aponta para **N contas** e **1 mídia** (ou playlist de várias mídias).

### 4.2 Como o agendamento funciona

```
Usuário cria automação
        │
        ▼
next_run_at = agora (ou horário do calendário)
        │
        ▼
Celery Beat → tick() a cada ~60s
        │
        ├─ reclama automações vencidas (lock Redis)
        ├─ já marca o próximo next_run_at (evita double fire)
        └─ enfileira execute_automation
                │
                ▼
        Para cada conta: publish_to_account
        (com stagger / jitter entre contas)
                │
                ▼
        Download mídia → limpa metadados → publica via API da conta
                │
                ▼
        PublishLog (success / failed / skipped)
```

**Modos de agenda**

- **Intervalo** (`schedule_type=interval`): a cada X minutos (ex. 30, 60, 120…). Meta tem piso de 60 min.
- **Calendário** (`calendar`): dias da semana + horários.
- **Agora** (`now`): dispara na hora (útil no Studio / “publicar já”).

Extras: jitter, descanso após N posts (`posts_per_batch` + `rest_minutes`).

### 4.3 Playlist de vídeos

- Campos: `videos_json` + `current_index` (e `video_key` legado).
- No tick, a automação **reserva** o próximo vídeo (`FOR UPDATE`) antes de enfileirar o publish — o índice não depende do worker lembrar de avançar depois.
- Limite de produto: **150 vídeos de Reels por usuário** no total das playlists (`app/utils/reel_video_limits.py`).

### 4.4 Anti-farm / stagger / legendas

- Preferências em `User.anti_farm_prefs_json` / `app/utils/anti_farm.py`.
- **Stagger:** espaça o countdown entre contas na mesma rodada.
- **Legendas:** texto fixo e/ou lista (`caption` / `captions_json`).
- Meta: cooldown em Redis + limite de concorrência (anti-spam Graph).

### 4.5 Camuflagem de Reel

- Overlay de imagem sobre o vídeo (`camouflage_cover_key`, opacidade).
- Aplicado no worker com **ffmpeg** (`core/media_prepare.apply_camouflage_overlay`), com slot Redis `FFMPEG_MAX_CONCURRENT`.
- Página auxiliar: `/camuflagem`.

### 4.6 Story Link Studio

Rota: `/automations/story-studio`.

- Contas elegíveis: cookies web **ou** login clássico (liberado).
- Meta **não** entra (não tem sticker de link custom).
- Você posiciona o botão (x/y/tamanho) → `story_layout_json`.
- Publica agora ou agenda Story.

### 4.7 Fluxo de publish: Meta vs APIs privadas

| Etapa | Meta | Instagrapi / Aiograpi / Cookies |
|-------|------|----------------------------------|
| Mídia | URL assinada puxada pelo Graph | Arquivo local após download do R2 |
| Limpeza | Pode reescrever key (camuflagem) | Sempre `prepare_clean_media` (falha = não publica) |
| Proxy | Opcional | Obrigatório + teste de vazamento |
| Story link | Não | Cookies web preferidos; mobile fallback |
| Execução | Async submit → poll | Síncrono dentro da task |
| Gate | Token + app Meta | Sessão válida (+ liberação Instagrapi se for mobile puro) |

### 4.8 Tasks Celery relevantes

| Task | Fila típica | Função |
|------|-------------|--------|
| `celery_app.beat.tick` | beat | Dispara automações vencidas |
| `execute_automation` | publish | Orquestra a rodada |
| `publish_to_account` / `publish_once` | publish | Publica 1 conta |
| `meta_poll_container` | publish | Finaliza container Meta async |
| `check_all_accounts` / `check_account_health` | health | Sessão / proxy / token |
| `sync_all_views` | default | Insights / views |
| `refresh_missing_profile_pics` | default | Avatares |
| `run_warmup_job` | default | Warmup de interação |

No Railway costuma haver:

- **web** — HTTP
- **worker-publish** — só fila `publish`
- **worker-misc** — beat/health/default (conforme config)
- **beat** — agendador (1 réplica)

---

## 5. Outras funções do produto

### 5.1 Editar perfil (`/accounts/profile-edit`)

- Bio (mesmo texto em todas as selecionadas) e/ou foto de perfil.
- Contas Meta fora.
- Gate: `allow_instagrapi`.
- **1 conta por requisição** (`POST /accounts/profile-edit/one`) com progresso na UI — evita timeout do gunicorn.
- Trabalho bloqueante roda em `run_in_threadpool` (instagrapi/aiograpi não podem travar o event loop async).

**Não use / não reintroduza:** edição de link do perfil. A API respondia OK e o Instagram **não** aplicava.

### 5.2 Cofre e Bloco de notas

- **Cofre** (`/accounts/vault`): senha + secret TOTP cifrados; UI mostra só o código 6 dígitos.
- **Notas** (`/accounts/notes`): colar lote de credenciais para consulta — **não** conecta Instagram sozinho.
- No login/reconectar, se pedir 2FA e houver TOTP, o painel gera o código automaticamente.

### 5.3 Aquecimento (dois significados)

1. **Aquecimento Meta** (`/aquecimento`): aumenta o intervalo mínimo de posts Meta (ex. 180 min) nos primeiros dias.
2. **Warmup de interação** (`WarmupJob` + `core/warmup.py`): ações aleatórias via sessão privada (precisa proxy + sessão).

### 5.4 Dashboard e fuso

- “Publicações hoje”, taxa de sucesso, gráficos usam dia **BRT**.
- Fonte da verdade: tabela `PublishLog`.
- **Nunca** voltar ao padrão SQL quebrado: `timezone('UTC', timestamptz)` + `America/Sao_Paulo` para bucket de dia.

### 5.5 Extensão Chrome

- Manifest em `extension/`.
- Pareamento no painel (`/accounts/extension`).
- Envia cookies + fingerprint do browser; bloqueada sob “Ver como” para admin comum.

### 5.6 Admin e Ver como

- Admin libera/bloqueia Instagrapi por usuário.
- **Ver como:** admin enxerga o painel do cliente. Cofre/notas/cookies/apps ficam bloqueados para admin comum; o **dono (`is_owner`)** pode **ler** cofre/notas (GET); mutações continuam bloqueadas.
- Login único: novo login sobe `session_version` e derruba a sessão antiga.

### 5.7 Proxy

- Formato aceito: `ip:porta:user:senha`, http, socks5.
- Teste de vazamento: se o IP do servidor vaza, conta pode ir para `proxy_down`.
- Obrigatório em APIs privadas; opcional na Meta.

### 5.8 Storage de mídia

- `core/storage.py`: local, S3/R2 ou dual.
- Upload do browser com URL pré-assinada quando R2.
- Download de `/media/{key}` **não é público**: precisa assinatura HMAC ou sessão + ownership (`app/media_access.py`).

---

## 6. Arquitetura por baixo dos panos

```
┌──────────────┐     ┌─────────────────────┐     ┌────────────┐
│ Browser /    │────▶│ FastAPI (web)       │────▶│ Postgres   │
│ Extensão     │     │ Jinja + rotas       │     │ contas,    │
└──────────────┘     └──────────┬──────────┘     │ automações │
                                │                │ PublishLog │
                                │                └────────────┘
                                ▼
                     ┌─────────────────────┐
                     │ Redis               │
                     │ broker Celery +     │
                     │ locks + Meta pending│
                     └──────────┬──────────┘
                                │
              ┌─────────────────┼─────────────────┐
              ▼                 ▼                 ▼
        ┌──────────┐    ┌──────────────┐   ┌──────────┐
        │ Beat     │    │ worker-      │   │ worker-  │
        │ tick()   │───▶│ publish      │   │ misc     │
        └──────────┘    │ Meta/IG API  │   │ health/  │
                        └──────┬───────┘   │ insights │
                               │           └──────────┘
                               ▼
                        ┌──────────────┐
                        │ R2 / disco   │
                        │ vídeos/fotos │
                        └──────────────┘
```

### 6.1 Sessão Instagram

| Tipo | Onde mora |
|------|-----------|
| Clássico / async | `session_json` (dump settings) |
| Cookies web | `encrypted_web_cookies` (+ browser opcional) |
| Meta | token cifrado + `meta_ig_user_id` |

Se a sessão cair no upload, a conta vai para `needs_login` e o publish grava falha.

### 6.2 PublishLog (fonte da verdade do painel)

Campos importantes: `status` (`success` \| `failed` \| `skipped`), `content_type`, `media_id` / `media_url`, fingerprint/hashes da limpeza, tempos de fila/atraso.

Se o post aparece no Instagram e o painel fica em 0 → olhar **PublishLog + logs do worker-publish**, não inventar KPI.

### 6.3 Limpeza de metadados

- `core/media_prepare.py` + `core/metadata.py`.
- Vídeo: ffmpeg remove metadados + pequena variação (assinatura diferente a cada envio).
- Imagem: remove EXIF.
- Se a limpeza falhar, o publish **não** sobe o original “sujo” (fail closed).

### 6.4 Segurança relevante

- Senhas / tokens / cookies / TOTP cifrados (`encrypt_secret`).
- CSRF + headers; rate limit em auth.
- `SECRET_KEY` fail-closed em produção.
- View As não vaza segredos (exceto leitura do dono no cofre/notas).

---

## 7. Mapa de arquivos (onde olhar no código)

| Tema | Caminhos |
|------|----------|
| APIs IG | `core/instagram.py`, `core/aiograpi_client.py`, `core/meta_instagram.py`, `core/web_cookies.py`, `core/story_web.py`, `core/reel_web.py` |
| Contas | `app/routes/accounts.py`, `accounts_profile.py`, `account_notes.py`, `meta_apps.py`, `extension_api.py` |
| Automações | `app/routes/automations.py`, `app/utils/anti_farm.py`, `intervals.py` |
| Workers | `celery_app/beat.py`, `celery_app/tasks/publish.py`, `health.py`, `insights.py`, `warmup.py` |
| Dados | `models/models.py`, `core/database.py`, `core/storage.py`, `core/media_prepare.py` |
| Painel | `app/routes/dashboard.py`, `app/utils/timezone.py` |
| Gate Instagrapi | `app/utils/instagrapi_access.py` |

---

## 8. O que NÃO documentar como “funcionando”

| Item | Status |
|------|--------|
| Editar link do perfil via API privada | Removido — Instagram ignorava o sucesso da API |
| Story com link custom na Meta | Não suportado |
| Story **vídeo** + link via API web | Limitado / não suportado como foto+link |
| Spintax na bio | Removido a pedido |
| Instagrapi mobile sem `allow_instagrapi` | UI visível; login real bloqueado / sessões revogadas |
| Bucket de dia com double `timezone()` no Postgres | Bug antigo — não reintroduzir |

---

## 9. Resumo em uma frase

O Instablack conecta contas Instagram por **Meta (oficial)**, **instagrapi**, **aiograpi** ou **cookies web**, agenda Reels/Stories/Fotos no **Celery Beat**, publica em workers com limpeza de metadados e proxy, e usa **`PublishLog` + fuso BRT** como verdade do painel.
