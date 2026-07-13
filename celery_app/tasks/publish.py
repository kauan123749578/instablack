"""Tasks de publicação: orquestração por automação + uma task por conta.

Playlist (estilo postagemIG):
  1) lê current_index
  2) posta items[index]
  3) SÓ após sucesso: current_index += 1
"""
from __future__ import annotations

import datetime as dt
import logging
import random
import tempfile
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.orm import selectinload

from app.security import decrypt_secret
from app.utils.automation_videos import playlist_items, playlist_is_exhausted, resolve_video_key
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


def _advance_playlist_locked(
    db,
    automation: Automation,
    posted_index: int,
    video_key: str,
) -> tuple[int, int, str] | None:
    """Avança current_index na MESMA sessão do sucesso (igual postagemIG).

    Retorna (user_id, name, total) se a playlist acabou; senão None.
    """
    items = playlist_items(automation)
    if len(items) <= 1:
        log.info(
            "PLAYLIST skip advance automation=%s: só %s vídeo(s) na lista",
            automation.id,
            len(items),
        )
        return None

    cur = int(automation.current_index or 0)
    # Já avançou (outra conta do mesmo ciclo postou o mesmo vídeo)
    if cur != posted_index:
        log.info(
            "PLAYLIST skip advance automation=%s (cur=%s posted=%s key=%s)",
            automation.id,
            cur,
            posted_index,
            video_key,
        )
        return None

    new_idx = posted_index + 1
    name = (
        items[posted_index].get("video_original_name")
        if posted_index < len(items)
        else ""
    )
    if new_idx >= len(items):
        db.execute(
            text(
                "UPDATE automations SET current_index = :idx, status = 'completed', "
                "next_run_at = NULL WHERE id = :id"
            ),
            {"idx": new_idx, "id": automation.id},
        )
        automation.current_index = new_idx
        automation.status = "completed"
        automation.next_run_at = None
        log.info(
            "PLAYLIST DONE automation=%s após %s/%s key=%s name=%r",
            automation.id,
            posted_index + 1,
            len(items),
            video_key,
            name,
        )
        return (automation.user_id, automation.name, len(items))

    db.execute(
        text("UPDATE automations SET current_index = :idx WHERE id = :id"),
        {"idx": new_idx, "id": automation.id},
    )
    automation.current_index = new_idx
    log.info(
        "PLAYLIST ADVANCE automation=%s %s→%s/%s key=%s name=%r",
        automation.id,
        posted_index,
        new_idx,
        len(items),
        video_key,
        name,
    )
    return None


@celery_app.task(name="celery_app.tasks.publish.execute_automation", bind=True, max_retries=0)
def execute_automation(self, automation_id: int) -> dict:
    with session_scope() as db:
        automation = db.execute(
            select(Automation)
            .where(Automation.id == automation_id)
            .options(selectinload(Automation.accounts))
            .with_for_update()
        ).scalar_one_or_none()
        if not automation:
            return {"error": "automation_not_found", "id": automation_id}
        if automation.status != "active":
            return {"skipped": True, "reason": "not_active"}

        items = playlist_items(automation)
        if not items:
            return {"error": "no_videos", "id": automation_id}

        if playlist_is_exhausted(automation):
            automation.status = "completed"
            automation.next_run_at = None
            db.execute(
                text(
                    "UPDATE automations SET status = 'completed', next_run_at = NULL WHERE id = :id"
                ),
                {"id": automation.id},
            )
            done = (automation.user_id, automation.name, len(items))
        else:
            done = None
            idx = int(automation.current_index or 0)
            if idx < 0:
                idx = 0
            if idx >= len(items):
                idx = len(items) - 1
            entry = items[idx]
            video_key = entry["video_key"]
            video_name = entry.get("video_original_name") or video_key
            queue_index = idx
            total_videos = len(items)
            # NÃO avança aqui — só depois do sucesso (igual postagemIG)

            account_ids = [
                acc.id
                for acc in automation.accounts
                if acc.status not in ("banned", "proxy_down", "paused", "needs_login")
            ]
            log.info(
                "execute_automation id=%s POSTAR vídeo %s/%s name=%r key=%s accounts=%s",
                automation_id,
                idx + 1,
                total_videos,
                video_name,
                video_key,
                len(account_ids),
            )

    if done:
        uid, name, total = done
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
        # Primeira conta: posta logo; demais com jitter
        countdown = 0 if i == 0 else random.randint(5, 40) + i * random.randint(2, 8)
        publish_to_account.apply_async(
            args=[automation_id, account_id, video_key, queue_index],
            countdown=countdown,
        )

    return {
        "automation_id": automation_id,
        "accounts_dispatched": len(account_ids),
        "queue_index": queue_index,
        "playlist_size": total_videos,
        "video_key": video_key,
        "video_name": video_name,
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

        if automation.status == "paused":
            db.add(PublishLog(
                automation_id=automation.id,
                account_id=account.id,
                status="skipped",
                error="automation_paused",
            ))
            return {"skipped": True}
        # video_key explícito = ciclo já escolhido (último da playlist pode estar completed)
        if video_key is None and automation.status != "active":
            db.add(PublishLog(
                automation_id=automation.id,
                account_id=account.id,
                status="skipped",
                error="automation_not_active",
            ))
            return {"skipped": True}

        items = playlist_items(automation)
        posted_index = queue_index
        if posted_index is None:
            posted_index = int(automation.current_index or 0)

        # Índice da fila é a fonte da verdade (postagemIG) — não confiar só no video_key passado
        if items:
            safe_idx = min(max(int(posted_index or 0), 0), len(items) - 1)
            posted_index = safe_idx
            video_key = items[safe_idx]["video_key"]
        vk = video_key or resolve_video_key(automation)

        log.info(
            "publish_to_account automation=%s account=%s idx=%s key=%s",
            automation_id,
            account.username,
            posted_index,
            vk,
        )

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
        owner_user_id = account.user_id
        username = account.username
        password = (
            decrypt_secret(account.encrypted_password)
            if account.encrypted_password
            else None
        )
        proxy = account.proxy
        settings_dict = deserialize_settings(account.session_json) if account.session_json else None

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
        log.info("Download mídia key=%s → %s", video_key, raw_path.name)
        storage.download_to(video_key, raw_path)

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
        playlist_done_notify: tuple[int, str, int] | None = None

        # Sucesso + avanço da playlist na MESMA transação (postagemIG)
        with session_scope() as db:
            acc = db.get(InstagramAccount, account_id)
            if acc:
                acc.session_json = serialize_settings(cl.get_settings())
                acc.last_login_at = dt.datetime.utcnow()
                acc.status = "active"
                acc.last_error = None

            if automation_id is not None:
                auto = db.execute(
                    select(Automation)
                    .where(Automation.id == automation_id)
                    .with_for_update()
                ).scalar_one_or_none()
                if auto:
                    auto.last_run_at = dt.datetime.utcnow()
                    auto.total_runs = (auto.total_runs or 0) + 1
                    if playlist_index is not None:
                        playlist_done_notify = _advance_playlist_locked(
                            db, auto, int(playlist_index), video_key
                        )

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

        if playlist_done_notify:
            uid_done, name_done, total_done = playlist_done_notify
            create_notification(
                uid_done,
                "Automação concluída",
                f"“{name_done}”: todos os {total_done} vídeos foram publicados.",
                kind="publish",
                link="/automations",
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

        return {
            "ok": True,
            "playlist_index": playlist_index,
            "video_key": video_key,
            **result,
        }

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
