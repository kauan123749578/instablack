web: gunicorn app.main:app -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:$PORT --workers 2 --timeout 120
worker: celery -A celery_app.config:celery_app worker -Q default,publish,beat -l info --concurrency 4
beat: celery -A celery_app.config:celery_app beat -l info
