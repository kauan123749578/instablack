# Reels Scheduler

SaaS privado para publicação automática e recorrente de Reels no Instagram em **múltiplas contas** simultaneamente. Construído com FastAPI + Celery + Redis + Postgres + instagrapi.

> Casos de uso: você sobe **um** vídeo, define um intervalo (ex.: a cada 30 min) e escolhe quais contas vão receber. O sistema republica continuamente até você pausar — limpando os metadados do vídeo a **cada** envio.

---

## Funcionalidades

- Autenticação por **usuário + senha** (login/registro, sessão por cookie).
- Conexão de múltiplas contas Instagram com **proxy obrigatório** por conta + health-check anti-vazamento de IP.
- Publicação em massa (1 vídeo + capa opcional + N contas) com jitter.
- **Agendamento recorrente** (a cada 30/60/120/240/360/720/1440 min) sem calendário — só intervalo.
- **Limpeza de metadados via ffmpeg em cada publicação** (não só no upload). O mesmo arquivo gera assinaturas diferentes a cada envio.
- Painel dark (tema OnlyChat): sidebar colapsável, glow cards, gráficos SVG, gauge, SPA parcial.
- Horários exibidos em **BRT** (America/Sao_Paulo).
- Logs de publicação por automação (sucesso, erro, link do post).
- Storage plugável: disco local (dev / Railway Volume) ou S3 opcional.
- Pronto para deploy no Railway com 3 services (web, worker, beat).

---

## Arquitetura

```
app/                # FastAPI (HTTP + Jinja templates + auth)
  ├ config.py      # Settings (.env via pydantic-settings)
  ├ security.py    # bcrypt + cifra simétrica das senhas IG
  ├ deps.py        # get_current_user
  ├ main.py        # cria a app + middlewares + routers
  ├ routes/        # auth, dashboard, accounts, automations
  ├ templates/     # Jinja2 (UI HTML)
  └ static/        # CSS

core/
  ├ database.py    # SQLAlchemy engine + sessão + init_db
  ├ instagram.py   # wrapper instagrapi (login, publicar, serializar sessão)
  ├ metadata.py    # ffmpeg -map_metadata -1 + creation_time aleatório
  └ storage.py     # Local / S3 (boto3)

models/
  └ models.py      # User, InstagramAccount, Automation, PublishLog (+ M2M)

celery_app/
  ├ config.py      # broker Redis, filas, beat_schedule (tick a cada N s)
  ├ beat.py        # tick() -> SELECT * FROM automations WHERE next_run_at <= now
  └ tasks/
      ├ publish.py    # execute_automation + publish_to_account (1 task por conta)
      └ scheduler.py  # helpers pause/resume/update_interval/start_now
```

Fluxo:

1. Usuário cria automação (upload do vídeo → salvo no storage com chave única).
2. `next_run_at` é setado para agora (sai no próximo tick).
3. **Celery Beat** dispara `tick()` a cada `BEAT_TICK_SECONDS` (padrão 60s).
4. `tick()` busca automações ativas vencidas e, para cada uma:
   - **Reagenda imediatamente** `next_run_at = now + interval` (evita disparo duplo).
   - Despacha `execute_automation(automation_id)`.
5. `execute_automation` dispara uma `publish_to_account` por conta da lista, com pequeno jitter.
6. `publish_to_account`:
   - Baixa o vídeo do storage para um `tempfile`.
   - Roda `ffmpeg -map_metadata -1 -c copy` (rápido, sem reencodar).
   - Faz login no IG reaproveitando a `session.json` salva (relogin automático se expirou).
   - Sobe o reel.
   - Atualiza `total_runs`, `last_run_at`, salva log e regrava a sessão atualizada.
7. Repete indefinidamente até o usuário **pausar**.

---

## Rodando local

### Pré-requisitos

- Python 3.11+
- ffmpeg no PATH (`ffmpeg -version` deve responder)
- Redis local (`docker run -p 6379:6379 redis:7-alpine` resolve)

### Setup

```bash
python -m venv .venv
.venv\Scripts\activate           # Windows
# source .venv/bin/activate      # Linux/macOS
pip install -r requirements.txt

copy .env.example .env
# edite .env e troque SECRET_KEY

# IMPORTANTE: se você já rodou uma versão anterior, apague app.db
# (o campo User.email virou User.username — não há migration automática)
del app.db
```

### Subir os 3 processos (em terminais separados)

```bash
# 1) API
uvicorn app.main:app --reload --port 8000

# 2) Worker Celery (publica)
celery -A celery_app.config:celery_app worker -Q default,publish,beat -l info --concurrency 4

# 3) Beat Celery (tick a cada 60s)
celery -A celery_app.config:celery_app beat -l info
```

Acesse `http://localhost:8000`, registre o primeiro usuário, conecte uma conta IG em `/accounts` e crie a primeira automação em `/automations/new`.

---

## Deploy no Railway

Guia completo em **[RAILWAY.md](RAILWAY.md)**.

Resumo: **3 services** (web, worker, beat) + **PostgreSQL** + **Redis** + **Volume Railway** para mídia.

| Service | Start command |
|---|---|
| `web`    | `bash scripts/railway-web.sh` |
| `worker` | `bash scripts/railway-worker.sh` |
| `beat`   | `bash scripts/railway-beat.sh` |

```env
APP_ENV=production
SECRET_KEY=<chave-aleatória-48+chars>
DATABASE_URL=${{Postgres.DATABASE_URL}}
REDIS_URL=${{Redis.REDIS_URL}}
STORAGE_BACKEND=local
LOCAL_STORAGE_PATH=/data/storage
BOOTSTRAP_ADMIN_USERNAME=admin
BOOTSTRAP_ADMIN_PASSWORD=...
```

Monte um **Volume** em `/data` no web e worker. Guia completo: **[RAILWAY.md](RAILWAY.md)**.

---

## Considerações importantes (LEIA)

- **Proxy é obrigatório** em toda conta. Antes de cada publicação o sistema valida o proxy (`check_proxy`). Se estiver fora ou vazando o IP do servidor, a publicação é **bloqueada** e a conta fica com status `proxy_down`.
  - Intervalos maiores nos primeiros dias de cada conta (warm-up).
  - Reaproveitar sessão (já fazemos) ao invés de logar a cada post.
- **Senhas das contas Instagram** ficam cifradas no banco com chave derivada do `SECRET_KEY`. Se você perder o `SECRET_KEY`, perde a capacidade de relogar automaticamente — só conseguirá reconectar via sessionid de novo.
- **Storage local em produção** exige Railway Volume montado em web e worker no mesmo caminho (`/data/storage`).
- O sistema **não tem rate-limiting global por conta** — se você criar 5 automações apontando pra mesma conta a cada 30 min, ela posta 5x a cada 30 min. Isso é intencional, mas cuidado.
