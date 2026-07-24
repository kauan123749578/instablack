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
    camuflagem,
    dashboard,
    logs,
    meta_apps,
    notifications,
    profile,
)
from app.templating import templates
from core.database import SessionLocal, init_db
from core.health import check_database, check_redis, check_storage
from core.storage import get_storage
from models.models import User

log = logging.getLogger(__name__)

_VIEW_AS_MUTATION_ALLOW = {
    "/logout",
    "/admin/stop-view-as",
}


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

    # Ordem importa: @middleware http entra ANTES do SessionMiddleware.
    # Session/Proxy são add_middleware por último → ficam por fora e
    # request.session já existe quando o view-as roda.
    @app.middleware("http")
    async def view_as_readonly_middleware(request: Request, call_next):
        view_as_id = request.session.get("view_as_user_id")
        auth_id = request.session.get("user_id")
        request.state.auth_user = None
        request.state.view_as_user = None
        request.state.view_as_username = None
        request.state.view_as_active = False

        if auth_id:
            db = SessionLocal()
            try:
                auth_user = db.get(User, auth_id)
                if auth_user is not None:
                    _ = (
                        auth_user.username,
                        auth_user.is_admin,
                        getattr(auth_user, "is_owner", False),
                    )
                    db.expunge(auth_user)
                    request.state.auth_user = auth_user
                if (
                    view_as_id
                    and auth_user is not None
                    and getattr(auth_user, "is_admin", False)
                ):
                    try:
                        target = db.get(User, int(view_as_id))
                    except (TypeError, ValueError):
                        target = None
                    allowed = bool(target and target.is_active and target.id != auth_user.id)
                    if allowed and not getattr(auth_user, "is_owner", False):
                        if getattr(target, "is_owner", False) or getattr(
                            target, "owner_private", False
                        ):
                            allowed = False
                    if allowed:
                        request.state.view_as_username = target.username
                        request.state.view_as_active = True
                        db.expunge(target)
                        request.state.view_as_user = target
                    else:
                        request.session.pop("view_as_user_id", None)
            except Exception:
                log.exception("Falha ao resolver visão owner")
            finally:
                db.close()

        if request.state.view_as_active and request.method in ("POST", "PUT", "PATCH", "DELETE"):
            path = request.url.path.rstrip("/") or "/"
            allowed = {p.rstrip("/") or "/" for p in _VIEW_AS_MUTATION_ALLOW}
            if path not in allowed:
                accept = request.headers.get("accept", "")
                msg = (
                    "Modo somente leitura: você está vendo a conta de outro usuário. "
                    "Saia da visão para fazer alterações."
                )
                if "application/json" in accept or "fetch" in (
                    request.headers.get("x-requested-with") or ""
                ).lower():
                    return JSONResponse({"error": msg}, status_code=403)
                return HTMLResponse(
                    f"""<!doctype html><html lang="pt-br"><head><meta charset="utf-8">
                    <title>Somente leitura</title>
                    <meta name="viewport" content="width=device-width, initial-scale=1">
                    <style>
                      body{{font-family:system-ui,sans-serif;background:#0b0d12;color:#e8eaed;
                      display:grid;place-items:center;min-height:100vh;margin:0;padding:24px}}
                      .box{{max-width:420px;background:#141824;border:1px solid #2a3142;border-radius:12px;padding:24px}}
                      a{{color:#3d82ff}}
                    </style></head><body><div class="box">
                    <h1 style="font-size:1.2rem;margin:0 0 8px">Somente leitura</h1>
                    <p>{msg}</p>
                    <p><a href="/admin/stop-view-as">Sair da visão</a> · <a href="/">Voltar</a></p>
                    </div></body></html>""",
                    status_code=403,
                )

        return await call_next(request)

    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        same_site="lax",
        https_only=settings.app_env == "production",
        max_age=60 * 60 * 24 * 14,  # 14 dias
    )
    if settings.trust_proxy:
        app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")

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
    app.include_router(camuflagem.router)
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
