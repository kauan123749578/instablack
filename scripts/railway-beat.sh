#!/usr/bin/env bash
set -euo pipefail
exec celery -A celery_app.config:celery_app beat -l info
