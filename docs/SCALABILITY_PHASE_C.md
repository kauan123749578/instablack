# Fase C — Publish Meta assíncrono (submit + poll)

## Status

**Implementado.** O worker cria o container (`submit`), libera o slot Celery e
consulta o status em tasks curtas (`meta_poll_container`) até READY / timeout.

## Semáforo ffmpeg / camuflagem

Já implementado: `FFMPEG_MAX_CONCURRENT` (default 2) via Redis em
`core/capacity_metrics.ffmpeg_slot()`, usado no overlay de camuflagem em
`celery_app/tasks/publish.py`.

- `0` = sem limite
- Em produção com camuflagem frequente: mantenha `2`

## Submit + poll

Fluxo:

1. Claim slots (`META_GLOBAL_MAX_CONCURRENT` / `META_USER_MAX_CONCURRENT` + inflight)
2. `submit_media_container` (POST `/media`) — alguns segundos
3. Job Redis `meta:pending:{account_id}` + agenda poll
4. Poll com backoff 5 → 10 → 20 → 30s (cap 30)
5. READY → `finalize_media_publish` + PublishLog + cooldown
6. Timeout (`META_POLL_TIMEOUT_SEC`, default 600) → fail + libera slot

### Env

| Variável | Default | Efeito |
|----------|---------|--------|
| `META_ASYNC_SUBMIT_POLL` | `true` | `false` volta ao wait sync local |
| `META_POLL_TIMEOUT_SEC` | `600` | timeout submit→publish |
| `META_GLOBAL_MAX_CONCURRENT` | `18` | teto global de containers em voo |
| `META_USER_MAX_CONCURRENT` | `5` | teto por usuário Instablack |

Slots permanecem ocupados do submit até publish/fail (TTL ~720s + refresh a cada poll).

### Métricas (`/admin/metrics`)

- `meta_submit_seconds`
- `meta_process_seconds` (submit → READY)
- `meta_polls_per_publish`
- `meta_ready_total_seconds` (submit → publish done)
- `meta_poll_timeout_count`

### Rollback

```bash
META_ASYNC_SUBMIT_POLL=false
```

Redeploy worker-publish. Cooldown / anti-spam / beat não mudam.
