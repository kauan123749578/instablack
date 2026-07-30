#!/usr/bin/env bash
# LEGADO: um único worker com todas as filas.
# Em produção (50–70 users) use:
#   scripts/railway-worker-publish.sh  (réplicas dedicadas)
#   scripts/railway-worker-misc.sh     (tick/health/insights)
set -euo pipefail
export APP_ROLE="${APP_ROLE:-worker}"
exec celery -A celery_app.config:celery_app worker \
  -Q default,publish,beat,health \
  -l info \
  --concurrency "${CELERY_CONCURRENCY:-4}"
