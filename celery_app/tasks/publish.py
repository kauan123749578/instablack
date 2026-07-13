"""Tasks de publicação: orquestração por automação + uma task por conta."""
from __future__ import annotations

import datetime as dt
import logging
import random
import tempfile
from pathlib import Path

from sqlalchemy import select

from app.security import decrypt_secret
from app.utils.automation_videos import (
    compute_next_playlist_index,
    playlist_items,
    playlist_is_exhausted,
    resolve_video_key,
    sync_playlist_cursor,
)
from celery_app.config import celery_app
from core.database import session_scope
from core.instagram import (
    InstagramAuthError,
    check_proxy,
    deserialize_settings,
    get_ready_client,
    publish_photo_feed,
    publish_reel,
    publish_story,
    serialize_settings,
)
from core.media_prepare import prepare_clean_media, prepare_clean_thumb
from core.metadata import MetadataStripError
from core.notifications import create_notification, notify_publish_success
from core.storage import get_storage
from models.models import Automation, InstagramAccount, PublishLog

log = logging.getLogger(__name__)


def _advance_after_video_posted(automation_id: int, video_key: str) -> None:
    """Após sucesso: recalcula cursor pelos logs (nunca republica o mesmo video_key)."""
    notify = None
    with session_scope() as db:
        automation = db.get(Automation, automation_id)
        if not automation:
            return
        items = playlist_items(automation)
        if len(items) <= 1:
            return
        # Garante que o video_key deste sucesso já está visível nesta sessão
        db.flush()
        next_idx = sync_playlist_cursor(db, automation)
        log.info(
            "Playlist automation=%s após post key=%s → next_index=%s/%s",
            automation_id,
            video_key,
            next_idx,
            len(items),
        )
        if next_idx >= len(items) and automation.status == "completed":
            notify = (automation.user_id, automation.name, len(items))

    if notify:
        uid, name, total = notify
        create_notification(
            uid,
            "Automação concluída",
            f"“{name}”: todos os {total} vídeos foram publicados.",
            kind="publish",
            link="/automations",
        )


@celery_app.task(name="celery_app.tasks.publish.execute_automation", bind=True, max_retries=0)
def execute_automation(self, automation_id: int) -> dict:
    done_notify = None
    with session_scope() as db:
        from sqlalchemy import text

        automation = db.execute(
            select(Automation).where(Automation.id == automation_id).with_for_update()
        ).scalar_one_or_none()
        if not automation:
            return {"error": "automation_not_found", "id": automation_id}
        if automation.status != "active":
            return {"skipped": True, "reason": "not_active"}

        items = playlist_items(automation)
        if not items:
            return {"error": "no_videos", "id": automation_id}

        # Fonte da verdade = logs de sucesso, não o current_index quebrado
        idx = sync_playlist_cursor(db, automation)
        total_videos = len(items)

        if idx >= len(items):
            done_notify = (automation.user_id, automation.name, len(items))
            account_ids = []
            video_key = None
            queue_index = None
            playlist_done = True
        else:
            entry = items[idx]
            video_key = entry["video_key"]
            queue_index = idx
            playlist_done = False

            # Reserva: marca este video_key como "em andamento" avançando o cursor
            # (o sync pós-sucesso confirma; se falhar, o key ainda não está nos logs
            #  e o próximo sync volta neste idx — por isso gravamos um "claim" via index)
            claim_idx = idx + 1
            if len(items) > 1:
                db.execute(
                    text("UPDATE automations SET current_index = :idx WHERE id = :id"),
                    {"idx": claim_idx, "id": automation.id},
                )
                automation.current_index = claim_idx
                if claim_idx >= len(items):
                    db.execute(
                        text(
                            "UPDATE automations SET status = 'completed', next_run_at = NULL "
                            "WHERE id = :id"
                        ),
                        {"id": automation.id},
                    )
                    automation.status = "completed"
                    automation.next_run_at = None
                    playlist_done = True

            account_ids = [
                acc.id
                for acc in automation.accounts
                if acc.status not in ("banned", "proxy_down", "paused", "needs_login")
            ]
            log.info(
                "execute_automation id=%s POSTAR %s/%s key=%s claim_index=%s accounts=%s",
                automation_id,
                idx + 1,
                total_videos,
                video_key,
                automation.current_index,
                len(account_ids),
            )

    if done_notify:
        uid, name, total = done_notify
        if total > 1:
            create_notification(
                uid,
                "Automação concluída",
                f"“{name}”: todos os {total} vídeos foram publicados.",
                kind="publish",
                link="/automations",
            )
        return {"skipped": True, "reason": "playlist_done"}

    if not account_ids or not video_key:
        return {"error": "no_accounts_or_video", "id": automation_id}

    for i, account_id in enumerate(account_ids):
        countdown = random.randint(0, 40) + i * random.randint(2, 8)
        publish_to_account.apply_async(
            args=[automation_id, account_id, video_key, queue_index],
            countdown=countdown,
        )

    return {
        "automation_id": automation_id,
        "accounts_dispatched": len(account_ids),
        "queue_index": queue_index,
        "playlist_size": total_videos,
        "playlist_done": playlist_done,
    }


@celery_app.task(
    name="celery_app.tasks.publish.publish_once",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
    max_retries=2,
)
def publish_once(
    self,
    account_id: int,
    video_key: str,
    thumb_key: str | None,
    caption: str,
    content_type: str,
    story_link: str | None = None,
) -> dict:
    """Publicação única imediata (sem automação recorrente)."""
    return _execute_publish(
        automation_id=None,
        account_id=account_id,
        video_key=video_key,
        thumb_key=thumb_key,
        caption=caption or "",
        content_type=content_type or "reel",
        story_link=story_link,
    )


@celery_app.task(
    name="celery_app.tasks.publish.publish_to_account",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
    max_retries=2,
)
def publish_to_account(
    self,
    automation_id: int,
    account_id: int,
    video_key: str | None = None,
    queue_index: int | None = None,
) -> dict:
    with session_scope() as db:
        automation = db.get(Automation, automation_id)
        account = db.get(InstagramAccount, account_id)
        if automation is None or account is None:
            return {"error": "not_found"}

        # video_key explícito = já enfileirado (pode ser o último da playlist com status completed)
        if automation.status == "paused":
            db.add(PublishLog(
                automation_id=automation.id,
                account_id=account.id,
                status="skipped",
                error="automation_paused",
            ))
            return {"skipped": True}
        if video_key is None and automation.status != "active":
            db.add(PublishLog(
                automation_id=automation.id,
                account_id=account.id,
                status="skipped",
                error="automation_not_active",
            ))
            return {"skipped": True}

        vk = video_key or resolve_video_key(automation)
        posted_index = queue_index
        if posted_index is None:
            posted_index = int(automation.current_index or 0)

        return _execute_publish(
            automation_id=automation.id,
            account_id=account.id,
            video_key=vk,
            thumb_key=automation.thumb_key,
            caption=automation.caption or "",
            content_type=automation.content_type or "reel",
            story_link=automation.story_link,
            playlist_index=posted_index,
        )


def _execute_publish(
    automation_id: int | None,
    account_id: int,
    video_key: str,
    thumb_key: str | None,
    caption: str,
    content_type: str,
    story_link: str | None = None,
    playlist_index: int | None = None,
) -> dict:
    storage = get_storage()

    with session_scope() as db:
        account = db.get(InstagramAccount, account_id)
        if account is None:
            return {"error": "account_not_found"}
        if account.status == "paused":
            db.add(PublishLog(
                automation_id=automation_id,
                account_id=account_id,
                status="skipped",
                content_type=content_type or "reel",
                error="conta pausada",
            ))
            return {"skipped": True, "reason": "account_paused"}
        settings_dict = deserialize_settings(account.session_json)
        proxy = account.proxy
        password = decrypt_secret(account.encrypted_password)
        username = account.username
        owner_user_id = account.user_id

    if not proxy:
        _log_failure(
            automation_id,
            account_id,
            "sem proxy — publicação bloqueada",
            content_type=content_type,
            owner_user_id=owner_user_id,
            username=username,
        )
        _mark_account_proxy_down(account_id, "Conta sem proxy configurado")
        return {"error": "no_proxy"}

    if not check_proxy(proxy):
        _log_failure(
            automation_id,
            account_id,
            "proxy fora ou vazando IP do servidor",
            content_type=content_type,
            owner_user_id=owner_user_id,
            username=username,
        )
        _mark_account_proxy_down(account_id, "Proxy inválido ou fora do ar")
        return {"error": "proxy_down"}

    if not settings_dict:
        _log_failure(
            automation_id,
            account_id,
            "sem sessão salva (refaça o login)",
            content_type=content_type,
            owner_user_id=owner_user_id,
            username=username,
        )
        _mark_account_needs_login(account_id, "Sessão expirada — reconecte a conta")
        return {"error": "no_session"}

    tmp_dir = Path(tempfile.mkdtemp(prefix="pub_"))
    ext = Path(video_key).suffix or ".mp4"
    raw_path = tmp_dir / f"raw{ext}"
    clean_path = tmp_dir / f"clean{ext}"
    thumb_path: Path | None = None
    clean_thumb_path: Path | None = None

    try:
        storage.download_to(video_key, raw_path)

        from core.notifications import create_notification

        create_notification(
            owner_user_id,
            "Limpando metadados",
            f"Gerando metadados únicos para @{username} antes de publicar…",
            kind="metadata",
            link="/logs",
            send_push=False,
        )

        try:
            clean_path, meta_info = prepare_clean_media(
                raw_path,
                clean_path,
                content_type=content_type,
                account_hint=username,
            )
            fp = (meta_info or {}).get("fingerprint", "ok")
            create_notification(
                owner_user_id,
                "Metadados limpos",
                f"@{username}: fingerprint {fp} — pronto para postar.",
                kind="metadata",
                link="/logs",
                send_push=False,
            )
        except MetadataStripError as exc:
            create_notification(
                owner_user_id,
                "Falha ao limpar metadados",
                f"@{username}: {exc}",
                kind="warning",
                link="/logs",
            )
            _log_failure(
                automation_id,
                account_id,
                f"metadados: {exc}",
                content_type=content_type,
                owner_user_id=owner_user_id,
                username=username,
            )
            return {"error": "metadata_strip"}

        publish_path = clean_path

        if content_type == "reel" and thumb_key:
            raw_thumb = tmp_dir / "raw_thumb.jpg"
            clean_thumb_path = tmp_dir / "clean_thumb.jpg"
            storage.download_to(thumb_key, raw_thumb)
            try:
                thumb_path = prepare_clean_thumb(raw_thumb, clean_thumb_path)
            except Exception as exc:
                _log_failure(
                    automation_id,
                    account_id,
                    f"metadados capa: {exc}",
                    content_type=content_type,
                    owner_user_id=owner_user_id,
                    username=username,
                )
                return {"error": "metadata_strip_thumb"}

        try:
            cl = get_ready_client(
                settings_dict=settings_dict,
                proxy=proxy,
                username=username,
                password=password,
            )
        except InstagramAuthError as exc:
            _mark_account_needs_login(account_id, str(exc))
            _log_failure(
                automation_id,
                account_id,
                f"login: {exc}",
                content_type=content_type,
                owner_user_id=owner_user_id,
                username=username,
            )
            return {"error": "auth"}

        try:
            if content_type == "story":
                result = publish_story(cl, publish_path, link_url=story_link)
            elif content_type == "photo":
                result = publish_photo_feed(cl, clean_path, caption)
            else:
                result = publish_reel(cl, clean_path, caption, thumbnail_path=thumb_path)
        except Exception as exc:
            _log_failure(
                automation_id,
                account_id,
                f"upload: {exc}",
                content_type=content_type,
                owner_user_id=owner_user_id,
                username=username,
            )
            raise

        notify_user_id: int | None = None
        notify_username = username
        publish_log_id: int | None = None

        with session_scope() as db:
            acc = db.get(InstagramAccount, account_id)
            if acc:
                acc.session_json = serialize_settings(cl.get_settings())
                acc.last_login_at = dt.datetime.utcnow()
                acc.status = "active"
                acc.last_error = None

            if automation_id is not None:
                auto = db.get(Automation, automation_id)
                if auto:
                    auto.last_run_at = dt.datetime.utcnow()
                    auto.total_runs = (auto.total_runs or 0) + 1

            plog = PublishLog(
                automation_id=automation_id,
                account_id=account_id,
                status="success",
                content_type=content_type or "reel",
                media_id=result.get("id"),
                media_url=result.get("url"),
                video_key=video_key,
            )
            db.add(plog)
            db.flush()
            publish_log_id = plog.id
            notify_user_id = acc.user_id if acc else owner_user_id
            notify_username = acc.username if acc else username

        # Avança playlist DEPOIS do commit do log (recalcula pelos video_keys publicados)
        if automation_id is not None and video_key:
            try:
                _advance_after_video_posted(automation_id, video_key)
            except Exception:
                log.exception(
                    "Falha ao avançar playlist automation=%s key=%s",
                    automation_id,
                    video_key,
                )

        uid = notify_user_id or owner_user_id
        if uid:
            notify_publish_success(
                uid,
                notify_username,
                content_type=content_type or "reel",
                publish_log_id=publish_log_id,
            )

        if content_type == "reel" and publish_log_id:
            try:
                from celery_app.tasks.insights import sync_all_views

                sync_all_views.apply_async(countdown=90)
            except Exception:
                log.debug("Não foi possível agendar sync de views", exc_info=True)

        return {"ok": True, **result}

    finally:
        for p in (raw_path, clean_path, thumb_path, clean_thumb_path):
            if p is None:
                continue
            try:
                if p.exists():
                    p.unlink()
            except OSError:
                pass
        try:
            tmp_dir.rmdir()
        except OSError:
            pass


def _log_failure(
    automation_id: int | None,
    account_id: int,
    error: str,
    *,
    content_type: str | None = None,
    owner_user_id: int | None = None,
    username: str | None = None,
) -> None:
    from core.notifications import content_label, create_notification

    uid = owner_user_id
    uname = username
    with session_scope() as db:
        db.add(
            PublishLog(
                automation_id=automation_id,
                account_id=account_id,
                status="failed",
                content_type=content_type,
                error=error[:2000],
            )
        )
        if uid is None or uname is None:
            acc = db.get(InstagramAccount, account_id)
            if acc:
                uid = uid or acc.user_id
                uname = uname or acc.username

    if uid:
        label = content_label(content_type)
        create_notification(
            uid,
            f"Erro ao publicar {label}",
            f"@{uname or '?'}: {error[:180]}",
            kind="warning",
            link="/logs",
        )


def _mark_account_needs_login(account_id: int, reason: str) -> None:
    from core.notifications import create_notification

    with session_scope() as db:
        acc = db.get(InstagramAccount, account_id)
        if not acc:
            return
        prev = acc.status
        acc.status = "needs_login"
        acc.last_error = reason[:1000]
        uid = acc.user_id
        uname = acc.username
    if prev != "needs_login":
        create_notification(
            uid,
            f"Conta @{uname} fora do ar",
            reason[:200] or "Sessão expirada — reconecte a conta",
            kind="offline",
            link="/accounts",
        )


def _mark_account_proxy_down(account_id: int, reason: str) -> None:
    from core.notifications import create_notification

    with session_scope() as db:
        acc = db.get(InstagramAccount, account_id)
        if not acc:
            return
        prev = acc.status
        acc.status = "proxy_down"
        acc.last_error = reason[:1000]
        uid = acc.user_id
        uname = acc.username
    if prev != "proxy_down":
        create_notification(
            uid,
            f"Conta @{uname} fora do ar",
            reason[:200] or "Proxy inválido ou fora do ar",
            kind="offline",
            link="/accounts",
        )
