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

DATABASE_URL=${{Postgres.DATABASE_URL}}
REDIS_URL=${{Redis.REDIS_URL}}

# Cloudflare R2 (recomendado)
STORAGE_BACKEND=s3
S3_BUCKET=instablack-media
S3_ENDPOINT_URL=https://SEU_ACCOUNT_ID.r2.cloudflarestorage.com
S3_ACCESS_KEY_ID=sua_access_key
S3_SECRET_ACCESS_KEY=sua_secret_key
S3_REGION=auto

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
```

O código converte `postgres://` para `postgresql+psycopg2://` automaticamente.

---

## Checklist

- [ ] Repo conectado: instablack
- [ ] 3 services (web, worker, beat)
- [ ] PostgreSQL + Redis
- [ ] **R2 configurado** (`STORAGE_BACKEND=s3` + 4 vars S3) **ou** Volume em `/data`
- [ ] `/readyz` → `storage: s3:seu-bucket` ok
- [ ] `SECRET_KEY` forte (32+ chars)
- [ ] Domínio público no web
- [ ] `/healthz` retorna 200
