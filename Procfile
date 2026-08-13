web: gunicorn app.main:app -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:$PORT --workers 4 --timeout 180
worker-publish: celery -A celery_app.config:celery_app worker -Q publish -l info --concurrency 10
worker-misc: celery -A celery_app.config:celery_app worker -Q beat,health,default -l info --concurrency 4
worker: celery -A celery_app.config:celery_app worker -Q default,publish,beat,health -l info --concurrency 4
beat: celery -A celery_app.config:celery_app beat -l info
