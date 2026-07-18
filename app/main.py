"""Entrypoint do FastAPI."""
from __future__ import annotations

import logging
import shutil
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask
from starlette.middleware.sessions import SessionMiddleware
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.config import settings
from app.routes import (
    accounts,
    admin,
    auth,
    automations,
    dashboard,
    logs,
    notifications,
    profile,
    proxy_store,
)
from core.database import init_db
from core.health import check_database, check_redis, check_storage
from core.storage import get_storage

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        init_db()
    except Exception:  # não derruba o processo se o banco estiver indisponível no boot
        log.exception("init_db falhou no startup; a aplicação seguirá e tentará novamente ao usar o banco.")
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="instablack",
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/docs" if settings.app_env == "development" else None,
        redoc_url=None,
    )

    if settings.trust_proxy:
        app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")

    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        same_site="lax",
        https_only=settings.app_env == "production",
        max_age=60 * 60 * 24 * 14,  # 14 dias
    )

    static_dir = Path(__file__).resolve().parent / "static"
    static_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    app.include_router(auth.router)
    app.include_router(dashboard.router)
    app.include_router(logs.router)
    app.include_router(accounts.router)
    app.include_router(automations.router)
    app.include_router(profile.router)
    app.include_router(admin.router)
    app.include_router(notifications.router)
    app.include_router(proxy_store.router)

    @app.get("/sw.js", include_in_schema=False)
    def service_worker():
        return FileResponse(
            static_dir / "sw.js",
            media_type="application/javascript",
            headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"},
        )

    @app.get("/manifest.webmanifest", include_in_schema=False)
    def web_manifest():
        return JSONResponse(
            {
                "name": "instablack",
                "short_name": "instablack",
                "description": "Painel de automação Instagram",
                "start_url": "/",
                "scope": "/",
                "display": "standalone",
                "background_color": "#030308",
                "theme_color": "#1161FE",
                "orientation": "portrait-primary",
                "icons": [
                    {
                        "src": "/static/favicon.svg",
                        "sizes": "any",
                        "type": "image/svg+xml",
                        "purpose": "any maskable",
                    }
                ],
            }
        )

    @app.exception_handler(RequestValidationError)
    async def form_validation_error(request: Request, exc: RequestValidationError):
        """Evita JSON cru quando falta arquivo em formulários multipart."""
        if request.method == "POST" and request.url.path.startswith("/automations"):
            missing_video = any(
                e.get("loc") == ("body", "video") for e in exc.errors()
            )
            if missing_video:
                return RedirectResponse(
                    "/automations/new?error=video",
                    status_code=303,
                )
        return JSONResponse(status_code=422, content={"detail": exc.errors()})

    @app.get("/media/{file_key:path}", include_in_schema=False)
    def serve_media(file_key: str):
        if ".." in file_key or file_key.startswith("/"):
            raise HTTPException(status_code=400, detail="Chave inválida")

        if settings.storage_backend == "local":
            base = (settings.base_dir / settings.local_storage_path).resolve()
            path = (base / file_key).resolve()
            if not str(path).startswith(str(base)) or not path.is_file():
                raise HTTPException(status_code=404, detail="Arquivo não encontrado")
            return FileResponse(path)

        storage = get_storage()
        suffix = Path(file_key).suffix or ""
        tmp_dir = Path(tempfile.mkdtemp(prefix="media_"))
        tmp_path = tmp_dir / f"file{suffix}"
        try:
            storage.download_to(file_key, tmp_path)
        except FileNotFoundError:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            raise HTTPException(status_code=404, detail="Arquivo não encontrado")
        except Exception:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            raise HTTPException(status_code=404, detail="Arquivo não encontrado")

        return FileResponse(
            tmp_path,
            background=BackgroundTask(lambda: shutil.rmtree(tmp_dir, ignore_errors=True)),
        )

    @app.get("/healthz", include_in_schema=False)
    def healthz():
        # Liveness: o processo web está de pé. Não depende de banco/redis
        # para não bloquear o deploy caso os plugins ainda não estejam prontos.
        return {"status": "ok", "env": settings.app_env}

    @app.get("/readyz", include_in_schema=False)
    def readyz():
        db_ok, db_msg = check_database()
        redis_ok, redis_msg = check_redis()
        storage_ok, storage_msg = check_storage()
        issues = settings.production_issues
        healthy = db_ok and redis_ok and storage_ok and not issues
        body = {
            "status": "ok" if healthy else "degraded",
            "database": db_msg,
            "redis": redis_msg,
            "storage": storage_msg,
            "storage_backend": settings.storage_backend,
            "config_issues": issues,
            "env": settings.app_env,
        }
        return JSONResponse(body, status_code=200 if healthy else 503)

    return app


app = create_app()
