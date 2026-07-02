"""Entrypoint do FastAPI."""
from __future__ import annotations

import shutil
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask
from starlette.middleware.sessions import SessionMiddleware
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.config import settings
from app.routes import accounts, admin, auth, automations, dashboard, profile
from core.database import init_db
from core.health import check_database, check_redis
from core.storage import get_storage


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="OnlyGram",
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
    app.include_router(accounts.router)
    app.include_router(automations.router)
    app.include_router(profile.router)
    app.include_router(admin.router)

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
            filename=Path(file_key).name,
            background=BackgroundTask(lambda: shutil.rmtree(tmp_dir, ignore_errors=True)),
        )

    @app.get("/healthz", include_in_schema=False)
    def healthz():
        db_ok, db_msg = check_database()
        redis_ok, redis_msg = check_redis()
        healthy = db_ok and redis_ok
        body = {
            "status": "ok" if healthy else "degraded",
            "database": db_msg,
            "redis": redis_msg,
            "env": settings.app_env,
        }
        return JSONResponse(body, status_code=200 if healthy else 503)

    return app


app = create_app()
