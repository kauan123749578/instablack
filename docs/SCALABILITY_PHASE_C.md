# Fase C — opcional (só se schedule_lag p95 continuar alto após Fase A+B)

## Semáforo ffmpeg / camuflagem

Já implementado: `FFMPEG_MAX_CONCURRENT` (default 2) via Redis em
`core/capacity_metrics.ffmpeg_slot()`, usado no overlay de camuflagem em
`celery_app/tasks/publish.py`.

- `0` = sem limite
- Em produção com camuflagem frequente: mantenha `2`

## Publish Meta assíncrono (submit + poll)

**Não implementar agora.** Com `META_GLOBAL_MAX_CONCURRENT=18` e workers
separados, o ganho de split submit/poll é marginal para 50–70 users.

Reavaliar se, após 1–2 semanas com `/admin/metrics`:

- `schedule_lag_seconds.p95` > 120s de forma sustentada
- `meta_inflight_global` saturado no teto E workers idle
- camuflagem dominante no tempo de hold

Critério para abrir Fase C async: dados reais de lag, não hipótese.
