#!/usr/bin/env bash
set -euo pipefail
export APP_ROLE="${APP_ROLE:-web}"
exec gunicorn app.main:app \
  -k uvicorn.workers.UvicornWorker \
  --bind "0.0.0.0:${PORT:-8000}" \
  --workers "${WEB_CONCURRENCY:-4}" \
  --timeout 120 \
  --access-logfile - \
  --error-logfile -
