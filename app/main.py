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

from sqlalchemy.exc import OperationalError, TimeoutError as SATimeoutError

from app.config import settings
from app.routes import (
    account_notes,
    accounts,
    accounts_profile,
    admin,
    aquecimento,
    auth,
    automations,
    camuflagem,
    dashboard,
    extension_api,
    logs,
    meta_apps,
    notifications,
    profile,
    reels_editor,
)
from app.templating import templates
from core.database import SessionLocal
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
    # Sobe o HTTP na hora. Migração roda em background — se travar no Postgres,
    # o painel NÃO fica 60s em "Waiting for application startup".
    import asyncio

    import anyio

    from core.database import init_db_background

    # Sync deps (get_db) rodam no threadpool. Default ~40 threads → 40 sessões
    # competindo por QueuePool(5+5) → TimeoutError em cascata ("Painel ocupado").
    try:
        anyio.to_thread.current_default_thread_limiter().total_tokens = 8
    except Exception:
        log.exception("Falha ao limitar threadpool anyio")

    asyncio.create_task(asyncio.to_thread(init_db_background))
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
    # Último @middleware registrado executa primeiro no request.
    @app.middleware("http")
    async def security_csrf_and_headers(request: Request, call_next):
        """CSRF + headers (roda dentro do SessionMiddleware)."""
        from app.config import settings as _settings
        from app.security_http import (
            csrf_forbidden_response,
            ensure_csrf_token,
            extract_csrf_token,
            security_headers_for,
            validate_csrf,
            _is_exempt,
        )

        try:
            ensure_csrf_token(request)
        except Exception:
            pass

        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            path = request.url.path or "/"
            if not _is_exempt(path):
                submitted = await extract_csrf_token(request)
                # Multipart/JSON: exige header (app.js injeta). Form urlencoded: header ou campo.
                ctype = (request.headers.get("content-type") or "").lower()
                if "multipart/form-data" in ctype or "application/json" in ctype:
                    if not validate_csrf(request, submitted):
                        return csrf_forbidden_response(request)
                else:
                    if not validate_csrf(request, submitted):
                        return csrf_forbidden_response(request)

        response = await call_next(request)
        security_headers_for(response, production=_settings.is_production)
        # Expõe token para JS (não é secreto além da sessão HttpOnly cookie)
        try:
            token = request.session.get("csrf_token")
            if token:
                response.headers["X-CSRF-Token"] = str(token)
        except Exception:
            pass
        return response

    @app.middleware("http")
    async def view_as_readonly_middleware(request: Request, call_next):
        view_as_id = request.session.get("view_as_user_id")
        auth_id = request.session.get("user_id")
        request.state.auth_user = None
        request.state.view_as_user = None
        request.state.view_as_username = None
        request.state.view_as_active = False

        # Só abre conexão extra no middleware quando há "Ver como".
        # Antes: 1 query em TODA request autenticada → somava com get_db e
        # estourava QueuePool (TimeoutError → Internal Server Error no painel).
        if auth_id and view_as_id:
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
                if auth_user is not None and getattr(auth_user, "is_admin", False):
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
                csrf = ""
                try:
                    csrf = str(request.session.get("csrf_token") or "")
                except Exception:
                    csrf = ""
                return HTMLResponse(
                    f"""<!doctype html><html lang="pt-br"><head><meta charset="utf-8">
                    <title>Somente leitura</title>
                    <meta name="viewport" content="width=device-width, initial-scale=1">
                    <meta name="csrf-token" content="{csrf}">
                    <style>
                      body{{font-family:system-ui,sans-serif;background:#0b0d12;color:#e8eaed;
                      display:grid;place-items:center;min-height:100vh;margin:0;padding:24px}}
                      .box{{max-width:420px;background:#141824;border:1px solid #2a3142;border-radius:12px;padding:24px}}
                      a{{color:#E8D48B}}
                    </style></head><body><div class="box">
                    <h1 style="font-size:1.2rem;margin:0 0 8px">Somente leitura</h1>
                    <p>{msg}</p>
                    <p><form action="/admin/stop-view-as" method="post" style="display:inline">
                    <input type="hidden" name="csrf_token" value="{csrf}">
                    <button type="submit" style="background:none;border:none;color:#E8D48B;cursor:pointer;text-decoration:underline;font:inherit;padding:0">Sair da visão</button>
                    </form> · <a href="/">Voltar</a></p>
                    </div></body></html>""",
                    status_code=403,
                )

        return await call_next(request)

    @app.middleware("http")
    async def pg_app_name_middleware(request: Request, call_next):
        """Marca application_name da request (visível em /admin/db-health)."""
        from core.database import clear_pg_app_context, set_pg_app_context

        path = (request.url.path or "/")[:48]
        set_pg_app_context(f"http:{request.method} {path}")
        try:
            return await call_next(request)
        finally:
            clear_pg_app_context()

    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        same_site="lax",
        https_only=settings.app_env == "production",
        max_age=60 * 60 * 24 * 14,  # 14 dias
    )
    if settings.trust_proxy:
        app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")

    @app.exception_handler(SATimeoutError)
    async def _db_pool_timeout(_request: Request, exc: SATimeoutError):
        """Pool esgotado → 503 rápido (evita hang longo virar upstream/502)."""
        log.warning("Postgres pool esgotado: %s", exc)
        accept = _request.headers.get("accept", "")
        if "text/html" in accept:
            return HTMLResponse(
                "<!doctype html><title>Instablack</title>"
                "<body style='font-family:system-ui;background:#050505;color:#eee;"
                "display:grid;place-items:center;min-height:100vh'>"
                "<div><h1>Painel ocupado</h1>"
                "<p>Banco temporariamente sem conexão. Atualize em alguns segundos.</p>"
                "<p><a href='/' style='color:#E8D48B'>Tentar de novo</a></p></div>"
                "</body>",
                status_code=503,
                headers={"Retry-After": "2"},
            )
        return JSONResponse(
            {"detail": "database_busy", "error": "QueuePool timeout"},
            status_code=503,
            headers={"Retry-After": "2"},
        )

    @app.exception_handler(OperationalError)
    async def _db_operational(_request: Request, exc: OperationalError):
        """SSL/PgBouncer caiu → 503 rápido em vez de hang/500 no login e painel."""
        log.warning("Postgres OperationalError: %s", exc)
        accept = _request.headers.get("accept", "")
        if "text/html" in accept:
            return HTMLResponse(
                "<!doctype html><title>Instablack</title>"
                "<body style='font-family:system-ui;background:#050505;color:#eee;"
                "display:grid;place-items:center;min-height:100vh'>"
                "<div><h1>Banco reiniciando</h1>"
                "<p>Conexão com o Postgres caiu. Atualize em 2–3 segundos.</p>"
                "<p><a href='/login' style='color:#E8D48B'>Voltar ao login</a></p></div>"
                "</body>",
                status_code=503,
                headers={"Retry-After": "2"},
            )
        return JSONResponse(
            {"detail": "database_unavailable", "error": "operational_error"},
            status_code=503,
            headers={"Retry-After": "2"},
        )

    static_dir = Path(__file__).resolve().parent / "static"
    static_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    app.include_router(auth.router)
    app.include_router(dashboard.router)
    app.include_router(logs.router)
    app.include_router(accounts.router)
    app.include_router(accounts_profile.router)
    app.include_router(account_notes.router)
    app.include_router(extension_api.router)
    app.include_router(extension_api.panel_router)
    app.include_router(meta_apps.router)
    app.include_router(automations.router)
    app.include_router(aquecimento.router)
    app.include_router(camuflagem.router)
    app.include_router(reels_editor.router)
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
                "theme_color": "#C9A227",
                "orientation": "portrait-primary",
                "icons": [
                    {
                        "src": "/static/pwa-icon-192.png?v=5",
                        "sizes": "192x192",
                        "type": "image/png",
                        "purpose": "any",
                    },
                    {
                        "src": "/static/pwa-icon-512.png?v=5",
                        "sizes": "512x512",
                        "type": "image/png",
                        "purpose": "any",
                    },
                    {
                        "src": "/static/pwa-icon-512-maskable.png?v=5",
                        "sizes": "512x512",
                        "type": "image/png",
                        "purpose": "maskable",
                    },
                ],
            }
        )

    @app.exception_handler(RequestValidationError)
    async def form_validation_error(request: Request, exc: RequestValidationError):
        """Evita JSON cru / 500 opaco em formulários multipart de automações."""
        path = request.url.path or "/"
        if request.method == "POST" and path in ("/register", "/login"):
            log.warning("Validação auth falhou path=%s errors=%s", path, exc.errors())
            dest = "/register" if path == "/register" else "/login"
            invite = request.query_params.get("invite") or ""
            if path == "/register" and invite:
                dest = f"/register?invite={invite}"
            # Mantém o código do convite se veio no Referer
            return RedirectResponse(dest, status_code=303)
        if request.method == "POST" and "/automations" in path:
            missing_video = any(
                ("video" in e.get("loc", ()) or "videos" in e.get("loc", ()))
                for e in exc.errors()
            )
            if missing_video:
                dest = "/automations/new/story" if "/story" in (request.url.path or "") else "/automations/new"
                return RedirectResponse(
                    f"{dest}?error=video",
                    status_code=303,
                )
            log.warning("Validação automação falhou path=%s errors=%s", request.url.path, exc.errors())
            detail = "; ".join(
                f"{'.'.join(str(x) for x in e.get('loc', ()))}: {e.get('msg')}"
                for e in exc.errors()[:4]
            )
            friendly = (
                "Formulário incompleto ou desatualizado. Recarregue a página "
                "(Ctrl+F5) e tente criar de novo."
            )
            if any("name" in e.get("loc", ()) for e in exc.errors()):
                friendly = (
                    "Não deu para ler o formulário (cache antigo ou envio incompleto). "
                    "Recarregue com Ctrl+F5 e tente de novo."
                )
            # Fetch (reel-draft etc.) precisa de JSON — HTML quebrava o fluxo
            # e alguns usuários só viam "body.name: Field required".
            xrw = (request.headers.get("x-requested-with") or "").lower()
            accept = (request.headers.get("accept") or "").lower()
            if "fetch" in xrw or "application/json" in accept:
                return JSONResponse(
                    {"error": friendly, "detail": detail},
                    status_code=400,
                )
            back = "/automations/new/story" if "/story" in (request.url.path or "") else "/automations/new"
            return RedirectResponse(f"{back}?error=form", status_code=303)
        return JSONResponse(status_code=422, content={"detail": exc.errors()})

    @app.api_route(
        "/media/{file_key:path}",
        methods=["GET", "HEAD"],
        include_in_schema=False,
    )
    def serve_media(
        file_key: str,
        request: Request,
        exp: int | None = None,
        sig: str | None = None,
    ):
        if ".." in file_key or file_key.startswith("/"):
            raise HTTPException(status_code=400, detail="Chave inválida")

        from app.deps import maybe_current_user
        from app.media_access import user_owns_media, verify_media_signature
        from core.database import SessionLocal

        allowed = verify_media_signature(file_key, exp, sig)
        if not allowed:
            db = SessionLocal()
            try:
                user = maybe_current_user(request, db)
                if user is not None and user_owns_media(db, user, file_key):
                    allowed = True
            finally:
                db.close()
        if not allowed:
            raise HTTPException(status_code=403, detail="Acesso negado à mídia")

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
            # public: a Meta precisa baixar a mídia pela URL assinada sem cookie.
            "Cache-Control": "public, max-age=3600",
        }
        if obj.get("ContentLength") is not None:
            headers["Content-Length"] = str(obj["ContentLength"])
        if obj.get("ContentRange"):
            headers["Content-Range"] = str(obj["ContentRange"])
        if obj.get("ETag"):
            headers["ETag"] = str(obj["ETag"])
        media_type = obj.get("ContentType") or "application/octet-stream"
        # R2 às vezes grava octet-stream; a Meta rejeita Story sem image/* ou video/*.
        if not media_type or media_type == "application/octet-stream":
            ext = Path(file_key).suffix.lower()
            media_type = {
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".png": "image/png",
                ".webp": "image/webp",
                ".mp4": "video/mp4",
                ".mov": "video/quicktime",
                ".webm": "video/webm",
                ".m4v": "video/mp4",
            }.get(ext, media_type)

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
    async def healthz():
        return {"status": "ok", "env": settings.app_env}

    @app.get("/readyz", include_in_schema=False)
    async def readyz():
        import asyncio

        async def _run(fn, label: str, timeout: float = 3.0):
            try:
                return await asyncio.wait_for(asyncio.to_thread(fn), timeout=timeout)
            except asyncio.TimeoutError:
                return False, f"{label}: timeout"
            except Exception as exc:
                return False, f"{label}: {exc}"

        db_ok, db_msg = await _run(check_database, "database")
        redis_ok, redis_msg = await _run(check_redis, "redis")
        storage_ok, storage_msg = await _run(check_storage, "storage")
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
