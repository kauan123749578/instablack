"""Tasks de publicação: orquestração por automação + uma task por conta."""
from __future__ import annotations

import datetime as dt
import logging
import random
import tempfile
from pathlib import Path

from sqlalchemy import select

from app.security import decrypt_secret
from app.utils.automation_videos import playlist_items, resolve_video_key
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
from core.notifications import create_notification
from core.storage import get_storage
from models.models import Automation, InstagramAccount, PublishLog

log = logging.getLogger(__name__)


def _advance_playlist_after_success(automation_id: int, posted_index: int) -> None:
    """Safety net: se o claim no execute não avançou, avança após sucesso."""
    from sqlalchemy import select

    notify = None
    with session_scope() as db:
        automation = db.execute(
            select(Automation).where(Automation.id == automation_id).with_for_update()
        ).scalar_one_or_none()
        if not automation:
            return
        items = playlist_items(automation)
        if len(items) <= 1:
            return
        cur = int(automation.current_index or 0)
        # Só avança se ainda estamos no vídeo que acabou de sair (evita double-advance)
        if cur != posted_index:
            return
        automation.current_index = posted_index + 1
        if automation.current_index >= len(items):
            automation.status = "completed"
            automation.next_run_at = None
            notify = (automation.user_id, automation.name, len(items))
            log.info(
                "Playlist concluída automation=%s (%s/%s)",
                automation_id,
                len(items),
                len(items),
            )
        else:
            log.info(
                "Playlist automation=%s avançou para %s/%s",
                automation_id,
                automation.current_index + 1,
                len(items),
            )
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
        from sqlalchemy import select

        automation = db.execute(
            select(Automation).where(Automation.id == automation_id).with_for_update()
        ).scalar_one_or_none()
        if not automation:
            return {"error": "automation_not_found", "id": automation_id}
        if automation.status != "active":
            return {"skipped": True, "reason": "not_active"}

        items = playlist_items(automation)
        idx = int(automation.current_index or 0)
        total_videos = len(items) if items else 1

        # Playlist multi-vídeo já esgotada
        if len(items) > 1 and idx >= len(items):
            automation.status = "completed"
            automation.next_run_at = None
            done_notify = (automation.user_id, automation.name, len(items))
            # commit on exit; notify after
        else:
            account_ids = [
                acc.id for acc in automation.accounts
                if acc.status not in ("banned", "proxy_down", "paused", "needs_login")
            ]
            if not items:
                return {"error": "no_videos", "id": automation_id}

            video_key = items[idx]["video_key"] if idx < len(items) else items[-1]["video_key"]
            queue_index = idx
            playlist_done = False

            # Claim do slot agora (impede o próximo tick republicar o mesmo vídeo)
            if len(items) > 1:
                automation.current_index = idx + 1
                if automation.current_index >= len(items):
                    automation.next_run_at = None
                    automation.status = "completed"
                    playlist_done = True
            else:
                automation.current_index = 0

            owner_id = automation.user_id
            auto_name = automation.name
            log.info(
                "execute_automation id=%s claim video %s/%s key=%s → next_index=%s accounts=%s",
                automation_id,
                idx + 1,
                total_videos,
                video_key,
                automation.current_index,
                len(account_ids),
            )

    if done_notify:
        uid, name, total = done_notify
        create_notification(
            uid,
            "Automação concluída",
            f"“{name}”: todos os {total} vídeos foram publicados.",
            kind="publish",
            link="/automations",
        )
        return {"skipped": True, "reason": "playlist_done"}

    for i, account_id in enumerate(account_ids):
        countdown = random.randint(0, 40) + i * random.randint(2, 8)
        publish_to_account.apply_async(
            args=[automation_id, account_id, video_key, queue_index],
            countdown=countdown,
        )

    if playlist_done:
        create_notification(
            owner_id,
            "Automação concluída",
            f"“{auto_name}”: último vídeo da playlist ({total_videos}/{total_videos}) enfileirado.",
            kind="publish",
            link="/automations",
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
    story_sticker_text: str | None = None,
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
        story_sticker_text=story_sticker_text,
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
            story_sticker_text=getattr(automation, "story_sticker_text", None),
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
    story_sticker_text: str | None = None,
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
    stickered_path: Path | None = None
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
        sticker_label: str | None = None
        # Sticker desenhado no story (pill branco + texto) + link clicável alinhado
        if content_type == "story" and (story_sticker_text or story_link):
            from core.story_sticker_draw import apply_story_link_sticker
            from urllib.parse import urlparse

            label = (story_sticker_text or "").strip()
            if not label and story_link:
                try:
                    host = urlparse(
                        story_link if "://" in story_link else f"https://{story_link}"
                    ).hostname or ""
                    label = host.replace("www.", "") or "Link"
                except Exception:
                    label = "Link"
            if not label:
                label = "Link"
            sticker_label = label
            stickered_path = tmp_dir / f"sticker{clean_path.suffix or ext}"
            try:
                apply_story_link_sticker(clean_path, stickered_path, label)
                publish_path = stickered_path
                log.info("Sticker desenhado no story: %r", label)
            except Exception as exc:
                log.exception("Falha ao desenhar sticker — publica sem overlay")
                create_notification(
                    owner_user_id,
                    "Sticker não aplicado",
                    f"@{username}: {exc}",
                    kind="warning",
                    link="/logs",
                    send_push=False,
                )

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
                # Desenho na mídia + StoryLink do instagrapi na mesma posição (tap funciona)
                result = publish_story(
                    cl,
                    publish_path,
                    link_url=story_link,
                    sticker_text=sticker_label,
                )
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
            )
            db.add(plog)
            db.flush()
            publish_log_id = plog.id
            notify_user_id = acc.user_id if acc else owner_user_id
            notify_username = acc.username if acc else username

            # Notificação in-app NA MESMA transação do log (garante aparecer no sino)
            uid = notify_user_id or owner_user_id
            if uid:
                from core.notifications import content_label
                from models.models import AppNotification

                label = content_label(content_type)
                db.add(
                    AppNotification(
                        user_id=uid,
                        title=f"{label} publicado",
                        body=f"@{notify_username}",
                        kind="publish",
                        link="/logs",
                        is_read=False,
                    )
                )

        # Safety net: se o claim no execute falhou, avança aqui (estilo postagemIG)
        if automation_id is not None and playlist_index is not None:
            try:
                _advance_playlist_after_success(automation_id, playlist_index)
            except Exception:
                log.exception(
                    "Falha ao avançar playlist automation=%s index=%s",
                    automation_id,
                    playlist_index,
                )

        # Push no celular (fora da TX — não pode bloquear o commit do log)
        uid = notify_user_id or owner_user_id
        if uid:
            try:
                from core.webpush import notify_user_publish_success

                notify_user_publish_success(uid, notify_username, content_type=content_type)
            except Exception:
                log.exception("Falha ao enviar push de publicação user=%s", uid)

        if content_type == "reel" and publish_log_id:
            try:
                from celery_app.tasks.insights import sync_all_views

                sync_all_views.apply_async(countdown=90)
            except Exception:
                log.debug("Não foi possível agendar sync de views", exc_info=True)

        return {"ok": True, **result}

    finally:
        for p in (raw_path, clean_path, stickered_path, thumb_path, clean_thumb_path):
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
