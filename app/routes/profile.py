"""Perfil do usuário do painel (SaaS)."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile, status
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.deps import get_current_user
from app.security import hash_password, verify_password
from app.templating import templates
from core.database import get_db
from core.notification_prefs import get_notification_prefs, prefs_from_form, save_notification_prefs
from core.storage import get_storage
from core.webpush import vapid_configured
from models.models import PushSubscription, User

router = APIRouter(prefix="/perfil", tags=["perfil"])

_AVATAR_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def _profile_context(
    request: Request,
    user: User,
    db: Session,
    *,
    error: str | None = None,
    ok: str | None = None,
) -> dict:
    push_subscribed = (
        db.scalar(
            select(func.count())
            .select_from(PushSubscription)
            .where(PushSubscription.user_id == user.id)
        )
        or 0
    ) > 0
    return {
        "request": request,
        "user": user,
        "error": error,
        "ok": ok,
        "vapid_ready": vapid_configured(),
        "push_subscribed": push_subscribed,
        "notification_prefs": get_notification_prefs(user),
    }


@router.get("")
def profile_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    ok_key = request.query_params.get("ok")
    ok_msg = {
        "notificacoes": "Preferências de notificação salvas!",
        "perfil": "Perfil atualizado com sucesso!",
        "senha": "Senha alterada. Outras sessões foram desconectadas.",
    }.get(ok_key or "")
    return templates.TemplateResponse(
        "profile.html",
        _profile_context(request, user, db, ok=ok_msg or None),
    )


@router.post("")
async def profile_update(
    request: Request,
    display_name: str = Form(""),
    avatar: UploadFile | None = File(None),
    current_password: str = Form(""),
    new_password: str = Form(""),
    new_password_confirm: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    error: str | None = None
    password_changed = False
    if new_password or new_password_confirm:
        if not current_password:
            error = "Informe a senha atual para definir uma nova senha."
        elif not verify_password(current_password, user.password_hash):
            error = "Senha atual incorreta."
        elif len(new_password) < 8:
            error = "A nova senha precisa ter pelo menos 8 caracteres."
        elif new_password != new_password_confirm:
            error = "As senhas não conferem."

    avatar_error: str | None = None
    new_avatar_key: str | None = None
    if not error and avatar and avatar.filename:
        ext = Path(avatar.filename).suffix.lower() or ".jpg"
        if ext not in _AVATAR_EXTS:
            avatar_error = "Use foto .jpg, .png ou .webp."
        else:
            try:
                try:
                    avatar.file.seek(0)
                except Exception:
                    pass
                storage = get_storage()
                key = f"avatars/user/{user.id}{ext}"
                # R2/local: sobrescreve a chave estável do usuário
                if hasattr(storage, "save_at_key"):
                    new_avatar_key = storage.save_at_key(
                        key,
                        avatar.file,
                        content_type={
                            ".jpg": "image/jpeg",
                            ".jpeg": "image/jpeg",
                            ".png": "image/png",
                            ".webp": "image/webp",
                        }.get(ext, "image/jpeg"),
                    )
                else:
                    new_avatar_key = storage.save(avatar.file, suggested_ext=ext)
            except Exception:
                avatar_error = "Não deu para salvar a foto. Tente outra imagem."

    if error or avatar_error:
        return templates.TemplateResponse(
            "profile.html",
            _profile_context(request, user, db, error=error or avatar_error),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    user.display_name = display_name.strip() or None

    if new_password:
        user.password_hash = hash_password(new_password)
        user.session_version = int(getattr(user, "session_version", 0) or 0) + 1
        password_changed = True

    if new_avatar_key:
        old_key = user.avatar_key
        user.avatar_key = new_avatar_key
        if old_key and old_key != new_avatar_key:
            try:
                get_storage().delete(old_key)
            except Exception:
                pass

    db.commit()
    db.refresh(user)

    if password_changed:
        # Invalida outros cookies; mantém esta sessão atualizada.
        request.session["session_version"] = int(user.session_version or 0)
        request.session["user_id"] = user.id
        return RedirectResponse("/perfil?ok=senha", status_code=303)

    return RedirectResponse("/perfil?ok=perfil", status_code=303)


@router.post("/notificacoes")
def profile_notifications_save(
    request: Request,
    enabled: str = Form(""),
    publish: str = Form(""),
    account_offline: str = Form(""),
    warmup: str = Form(""),
    errors: str = Form(""),
    desktop: str = Form(""),
    publish_title: str = Form("{label} publicado"),
    publish_body: str = Form(""),
    publish_show_username: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    save_notification_prefs(
        db,
        user,
        prefs_from_form(
            enabled=enabled,
            publish=publish,
            account_offline=account_offline,
            warmup=warmup,
            errors=errors,
            desktop=desktop,
            publish_show_username=publish_show_username,
            publish_title=publish_title,
            publish_body=publish_body,
        ),
    )
    return RedirectResponse("/perfil?ok=notificacoes", status_code=303)
