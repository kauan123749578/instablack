# Deploy no Railway — OnlyGram

Guia para subir o OnlyGram em produção **sem Cloudflare**.

## Arquitetura

| Componente | Função |
|---|---|
| **web** | FastAPI (painel) |
| **worker** | Celery worker (publicações) |
| **beat** | Celery beat (agendador) |
| **PostgreSQL** | Banco de dados |
| **Redis** | Broker do Celery |
| **Volume Railway** | Mídia compartilhada entre web e worker |

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

## 3. Volume para mídia (obrigatório)

O disco do Railway é efêmero. Web e worker precisam do **mesmo volume** para vídeos e imagens.

1. Crie um **Volume** no projeto Railway.
2. Monte em **`/data`** nos services **web** e **worker** (mesmo caminho nos dois).
3. Configure:

```env
STORAGE_BACKEND=local
LOCAL_STORAGE_PATH=/data/storage
```

---

## 4. Variáveis de ambiente

Configure nos **3 services** (ou use Shared Variables):

```env
APP_ENV=production
SECRET_KEY=<gere: python -c "import secrets; print(secrets.token_urlsafe(48))">

ALLOW_REGISTRATION=false

DATABASE_URL=${{Postgres.DATABASE_URL}}
REDIS_URL=${{Redis.REDIS_URL}}

STORAGE_BACKEND=local
LOCAL_STORAGE_PATH=/data/storage

FFMPEG_BIN=ffmpeg
BEAT_TICK_SECONDS=60
TRUST_PROXY=true

BOOTSTRAP_ADMIN_USERNAME=admin
BOOTSTRAP_ADMIN_PASSWORD=<senha-forte>
BOOTSTRAP_ADMIN_IS_ADMIN=true
```

O código converte `postgres://` para `postgresql+psycopg2://` automaticamente.

### S3 opcional

Se preferir S3 em vez de volume:

```env
STORAGE_BACKEND=s3
S3_BUCKET=seu-bucket
S3_ACCESS_KEY_ID=...
S3_SECRET_ACCESS_KEY=...
S3_REGION=us-east-1
# S3_ENDPOINT_URL vazio = AWS S3 padrão
```

---

## 5. Verificar deploy

1. `https://seu-dominio.railway.app/healthz` → `{"status":"ok"}` (liveness — não depende de DB/Redis)
2. `https://seu-dominio.railway.app/readyz` → `{"status":"ok","database":"ok","redis":"ok"}` (checa Postgres + Redis)
2. Login com usuário bootstrap
3. Conecte conta IG em `/accounts`
4. Crie automação em `/automations/new`

---

## Checklist

- [ ] Repo conectado: instablack
- [ ] 3 services (web, worker, beat)
- [ ] PostgreSQL + Redis
- [ ] Volume montado em `/data` no web e worker
- [ ] `LOCAL_STORAGE_PATH=/data/storage`
- [ ] `SECRET_KEY` forte (32+ chars)
- [ ] Domínio público no web
- [ ] `/healthz` retorna 200
