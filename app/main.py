"""Entrypoint do FastAPI."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.config import settings
from app.routes import (
    accounts,
    admin,
    aquecimento,
    auth,
    automations,
    dashboard,
    logs,
    meta_apps,
    notifications,
    profile,
)
from app.templating import templates
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
    app.include_router(meta_apps.router)
    app.include_router(automations.router)
    app.include_router(aquecimento.router)
    app.include_router(profile.router)
    app.include_router(admin.router)
    app.include_router(notifications.router)

    @app.get("/privacy", include_in_schema=False)
    def privacy_policy(request: Request):
        return templates.TemplateResponse("privacy.html", {"request": request})

    @app.get("/terms", include_in_schema=False)
    def terms_of_service(request: Request):
        return templates.TemplateResponse("terms.html", {"request": request})

    @app.get("/data-deletion", include_in_schema=False)
    def data_deletion_instructions(request: Request, code: str = ""):
        status_label = "Concluída" if code else "Aguardando solicitação"
        return templates.TemplateResponse(
            "data_deletion.html",
            {
                "request": request,
                "confirmation_code": code.strip() or None,
                "status_label": status_label,
            },
        )

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

    @app.api_route(
        "/media/{file_key:path}",
        methods=["GET", "HEAD"],
        include_in_schema=False,
    )
    def serve_media(file_key: str, request: Request):
        if ".." in file_key or file_key.startswith("/"):
            raise HTTPException(status_code=400, detail="Chave inválida")

        if settings.storage_backend == "local":
            base = (settings.base_dir / settings.local_storage_path).resolve()
            path = (base / file_key).resolve()
            if not str(path).startswith(str(base)) or not path.is_file():
                raise HTTPException(status_code=404, detail="Arquivo não encontrado")
            return FileResponse(
                path,
                headers={
                    "Accept-Ranges": "bytes",
                    "Cache-Control": "public, max-age=3600",
                },
            )

        storage = get_storage()
        try:
            if request.method == "HEAD":
                obj = storage.head_download(file_key)
            else:
                obj = storage.open_download(
                    file_key,
                    request.headers.get("range"),
                )
        except Exception:
            raise HTTPException(status_code=404, detail="Arquivo não encontrado")

        headers = {
            "Accept-Ranges": "bytes",
            "Cache-Control": "public, max-age=3600",
        }
        if obj.get("ContentLength") is not None:
            headers["Content-Length"] = str(obj["ContentLength"])
        if obj.get("ContentRange"):
            headers["Content-Range"] = str(obj["ContentRange"])
        if obj.get("ETag"):
            headers["ETag"] = str(obj["ETag"])
        media_type = obj.get("ContentType") or "application/octet-stream"

        if request.method == "HEAD":
            return Response(
                status_code=200,
                media_type=media_type,
                headers=headers,
            )

        try:
            body = obj["Body"]
        except KeyError:
            raise HTTPException(status_code=404, detail="Arquivo não encontrado")

        def stream_body():
            try:
                while chunk := body.read(1024 * 1024):
                    yield chunk
            finally:
                body.close()

        return StreamingResponse(
            stream_body(),
            status_code=206 if obj.get("ContentRange") else 200,
            media_type=media_type,
            headers=headers,
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
