#!/usr/bin/env bash
# Worker para tick/health/insights/warmup — NÃO compete com publish.
# Railway: 1 réplica, CELERY_CONCURRENCY=2–4.
set -euo pipefail
export APP_ROLE="${APP_ROLE:-worker-misc}"
exec celery -A celery_app.config:celery_app worker \
  -Q beat,health,default \
  -l info \
  --concurrency "${CELERY_CONCURRENCY:-4}"
