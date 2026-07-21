# Deploy no Railway — instablack

Guia para subir o instablack em produção.

## Arquitetura

| Componente | Função |
|---|---|
| **web** | FastAPI (painel) |
| **worker** | Celery worker (publicações) |
| **beat** | Celery beat (agendador) |
| **PostgreSQL** | Banco de dados |
| **Redis** | Broker do Celery |
| **Cloudflare R2** | Mídia compartilhada (web + worker) — recomendado |

---

## 1. Criar projeto no Railway

1. Acesse [railway.app](https://railway.app) e crie um projeto.
2. Conecte o repositório: [github.com/kauan123749578/instablack](https://github.com/kauan123749578/instablack)
3. Adicione plugins **PostgreSQL** e **Redis**.

---

## 2. Criar os 3 services

Crie **3 services** no mesmo repositório:

### Service `web`

```bash
bash scripts/railway-web.sh
```

Gere domínio público em **Settings → Networking**.

### Service `worker`

```bash
bash scripts/railway-worker.sh
```

### Service `beat`

```bash
bash scripts/railway-beat.sh
```

**Apenas 1 réplica** do beat.

---

## 3. Storage — Cloudflare R2 (recomendado)

Web e worker precisam dos **mesmos vídeos**. Com R2, não precisa de Volume no Railway.

### 3.1 Criar bucket

1. [Cloudflare Dashboard](https://dash.cloudflare.com) → **R2** → **Create bucket**
2. Nome exemplo: `instablack-media`
3. Anote o **Account ID** (Overview do R2)

### 3.2 Token de API

1. R2 → **Manage R2 API Tokens** → **Create API token**
2. Permissão: **Object Read & Write** no bucket
3. Copie **Access Key ID** e **Secret Access Key**

### 3.3 Verificar após deploy

```
https://instablack-production.up.railway.app/readyz
```

Deve retornar `"storage": "s3:instablack-media"` e `"status": "ok"`.

### 3.4 CORS do bucket (obrigatório para upload direto)

No bucket R2, abra **Settings → CORS Policy** e salve:

```json
[
  {
    "AllowedOrigins": [
      "https://SEU-DOMINIO.up.railway.app"
    ],
    "AllowedMethods": [
      "PUT"
    ],
    "AllowedHeaders": [
      "*"
    ],
    "ExposeHeaders": [
      "ETag"
    ],
    "MaxAgeSeconds": 3600
  }
]
```

Troque a origem pelo domínio público exato do service `web` (sem barra no final).
Se usar domínio próprio, adicione-o também em `AllowedOrigins`. Sem essa política,
o navegador bloqueia o envio direto mesmo que as credenciais S3 estejam corretas.

### 3.5 Segunda conta R2 (opcional, ~20 GB no total)

Para somar espaço de outra conta Cloudflare, crie um segundo bucket + token de API
e configure as mesmas variáveis com sufixo `_2`. Aplique a **mesma política CORS**
do item 3.4 nesse bucket.

```env
S3_BUCKET_2=instablack-media-2
S3_ENDPOINT_URL_2=https://OUTRO_ACCOUNT_ID.r2.cloudflarestorage.com
S3_ACCESS_KEY_ID_2=sua_access_key_2
S3_SECRET_ACCESS_KEY_2=sua_secret_key_2
```

Com as quatro variáveis preenchidas, o app usa `DualS3Storage`: uploads novos
alternam ~50/50 entre os buckets; objetos no bucket 2 ficam com prefixo lógico
`b2/` (ex.: `b2/videos/...`). Download/delete/presign roteiam por esse prefixo.
Se `_2` estiver vazio, o comportamento continua só com o bucket principal.

### 3.6 Instagram API oficial (Meus Apps)

Cada usuário do instablack cadastra **seu próprio app** em **Meus Apps** no painel
(nome, Instagram App ID, App Secret). No [Meta for Developers](https://developers.facebook.com),
habilite **Instagram API with Instagram Login** e cole as três URLs geradas pelo painel
(por app, com ID interno):

- Redirect OAuth: `https://SEU-DOMINIO/accounts/meta/callback/{app_id}`
- Deauthorize: `https://SEU-DOMINIO/accounts/meta/deauthorize/{app_id}`
- Data deletion: `https://SEU-DOMINIO/accounts/meta/data-deletion/{app_id}`

Permissões: `instagram_business_basic`, `instagram_business_content_publish`,
`instagram_business_manage_insights`. App Review e verificação de empresa são
**por app de cada usuário**.

Configure no Railway apenas:

```env
META_INSTAGRAM_GRAPH_VERSION=v25.0
PUBLIC_BASE_URL=https://SEU-DOMINIO.up.railway.app
```

URLs públicas da plataforma (políticas — iguais para todos):

- Política de Privacidade: `https://SEU-DOMINIO.up.railway.app/privacy`
- Termos de Uso: `https://SEU-DOMINIO.up.railway.app/terms`
- Exclusão de dados (instruções): `https://SEU-DOMINIO.up.railway.app/data-deletion`

### 3b. Alternativa: Volume Railway

Se não usar R2:

1. Crie um **Volume** e monte em **`/data`** no **web** e **worker**
2. Use `STORAGE_BACKEND=local` e `LOCAL_STORAGE_PATH=/data/storage`

---

## 4. Variáveis de ambiente

Configure nos **3 services** (ou use Shared Variables):

```env
APP_ENV=production
SECRET_KEY=<gere: python -c "import secrets; print(secrets.token_urlsafe(48))">

ALLOW_REGISTRATION=false
INVITE_CODE=<codigo-secreto-unico>

DATABASE_URL=${{Postgres.DATABASE_URL}}
REDIS_URL=${{Redis.REDIS_URL}}

# Cloudflare R2 (recomendado)
STORAGE_BACKEND=s3
S3_BUCKET=instablack-media
S3_ENDPOINT_URL=https://SEU_ACCOUNT_ID.r2.cloudflarestorage.com
S3_ACCESS_KEY_ID=sua_access_key
S3_SECRET_ACCESS_KEY=sua_secret_key
S3_REGION=auto

# Segunda conta R2 (opcional) — ver seção 3.5
# S3_BUCKET_2=
# S3_ENDPOINT_URL_2=
# S3_ACCESS_KEY_ID_2=
# S3_SECRET_ACCESS_KEY_2=

# Instagram Graph (versão global; apps Meta ficam em Meus Apps por usuário)
META_INSTAGRAM_GRAPH_VERSION=v25.0
PUBLIC_BASE_URL=https://SEU-DOMINIO.up.railway.app

# Alternativa sem R2:
# STORAGE_BACKEND=local
# LOCAL_STORAGE_PATH=/data/storage

FFMPEG_BIN=ffmpeg
BEAT_TICK_SECONDS=60
TRUST_PROXY=true

# Pré-preenche o campo Proxy ao conectar contas (host:porta:user:senha)
DEFAULT_PROXY=host:porta:user:senha

BOOTSTRAP_ADMIN_USERNAME=admin
BOOTSTRAP_ADMIN_PASSWORD=<senha-forte>
BOOTSTRAP_ADMIN_IS_ADMIN=true

# Dono da plataforma (@ de login EXATO). Sem isso (ou errado) você vira só Admin no /admin.
OWNER_USERNAME=kauawqi

# Web Push — MESMAS keys no web E no worker (cooperative-dream)
# Sem isso no worker: sino funciona, celular não (ex.: "Conta fora do ar")
VAPID_PUBLIC_KEY=<mesma do web>
VAPID_PRIVATE_KEY=<mesma do web>
VAPID_SUBJECT=mailto:seu-email@dominio.com
```

O código converte `postgres://` para `postgresql+psycopg2://` automaticamente.

---

## Checklist

- [ ] Repo conectado: instablack
- [ ] 3 services (web, worker, beat)
- [ ] PostgreSQL + Redis
- [ ] **R2 configurado** (`STORAGE_BACKEND=s3` + 4 vars S3) **ou** Volume em `/data`
- [ ] CORS do R2 permite `PUT` vindo do domínio público do `web`
- [ ] `/readyz` → `storage: s3:seu-bucket` ok
- [ ] `SECRET_KEY` forte (32+ chars)
- [ ] `INVITE_CODE` definido (código único de cadastro)
- [ ] **VAPID no web e no worker** (push no celular com PC desligado)
- [ ] Domínio público no web
- [ ] `/healthz` retorna 200
