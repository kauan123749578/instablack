#!/usr/bin/env bash
# Worker para tick/health/insights/warmup — NÃO compete com publish.
# Railway: 1 réplica, CELERY_CONCURRENCY=2–4.
# -B = Celery Beat embutido (agenda tick a cada N seg). Sem serviço beat separado, automações não rodam.
set -euo pipefail
export APP_ROLE="${APP_ROLE:-worker-misc}"
exec celery -A celery_app.config:celery_app worker \
  -B \
  -Q beat,health,default \
  -l info \
  --concurrency "${CELERY_CONCURRENCY:-4}"
