#!/usr/bin/env bash
# Worker dedicado à fila publish (publicações Meta/instagrapi).
# Railway: 1–2 réplicas, CELERY_CONCURRENCY=8–10, META_GLOBAL_MAX_CONCURRENT≈18.
set -euo pipefail
exec celery -A celery_app.config:celery_app worker \
  -Q publish \
  -l info \
  --concurrency "${CELERY_CONCURRENCY:-10}"
