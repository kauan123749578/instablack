#!/usr/bin/env bash
set -euo pipefail
export APP_ROLE="${APP_ROLE:-beat}"
exec celery -A celery_app.config:celery_app beat -l info
