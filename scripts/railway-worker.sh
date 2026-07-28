#!/usr/bin/env bash
set -euo pipefail
exec celery -A celery_app.config:celery_app worker \
  -Q default,publish,beat,health \
  -l info \
  --concurrency "${CELERY_CONCURRENCY:-4}"
