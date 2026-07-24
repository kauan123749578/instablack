"""Tasks de publicação: orquestração por automação + uma task por conta.

Playlist multi-vídeo (Celery async — NÃO dá para copiar postagemIG 1:1):
  1) execute_automation lê current_index e RESERVA o próximo (claim)
  2) enfileira publish com o video_key reservado
  3) o próximo tick já pega o vídeo seguinte

Assim o índice NÃO depende do worker lembrar de avançar depois do upload.
"""
from __future__ import annotations

import datetime as dt
import logging
import tempfile
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

import requests
from sqlalchemy import select, text

from app.config import settings
from app.security import decrypt_secret
from app.utils.anti_farm import account_publish_countdown, resolve_caption, resolve_stagger_config
from app.utils.auth_failures import (
    auth_status_reason,
    latest_auth_failure_reason,
    looks_auth_required,
)
from app.utils.automation_videos import playlist_items, playlist_is_exhausted, resolve_video_key
from app.utils.intervals import meta_min_interval_for_account
from celery_app.config import celery_app
from core.anti_farm_prefs import get_anti_farm_prefs_by_id
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
from core.media_prepare import (
    IMAGE_EXT,
    VIDEO_EXT,
    apply_camouflage_overlay,
    generate_video_thumbnail,
    prepare_clean_media,
    prepare_clean_thumb,
)
from core.meta_instagram import MetaInstagramError, publish_media as publish_meta_media
from core.metadata import MetadataStripError
from core.notifications import create_notification, notify_publish_success
from core.storage import get_storage
from models.models import Automation, InstagramAccount, PublishLog

log = logging.getLogger(__name__)

# Aparece nos logs do Railway — se não aparecer, o worker NÃO atualizou
PLAYLIST_CODE = "claim-v5-storage-fallback"


def _load_story_layout(automation: Automation) -> dict | None:
    raw = getattr(automation, "story_layout_json", None)
    if not raw:
        return None
    try:
        import json

        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _download_media(storage, key: str, dest_path: Path) -> None:
    """Baixa do storage do worker e usa o web como recuperação.

    Web e worker podem acabar com variáveis R2 diferentes no Railway. O fallback
    mantém a publicação funcionando porque o serviço web acessa o bucket usado
    no upload. Ele só é utilizado quando o download direto falha.
    """
    try:
        storage.download_to(key, dest_path)
        return
    except Exception as storage_exc:
        base_url = settings.public_base_url.strip().rstrip("/")
        if not base_url:
            raise RuntimeError(
                f"Worker não conseguiu baixar a mídia do storage: {storage_exc}"
            ) from storage_exc

        media_url = f"{base_url}/media/{quote(key, safe='/')}"
        log.warning(
            "Download direto do storage falhou para key=%s; tentando serviço web: %s",
            key,
            storage_exc,
        )
        try:
            with requests.get(media_url, stream=True, timeout=(15, 300)) as response:
                response.raise_for_status()
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                with dest_path.open("wb") as output:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            output.write(chunk)
            if not dest_path.exists() or dest_path.stat().st_size <= 0:
                raise RuntimeError("serviço web retornou um arquivo vazio")
            log.info(
                "Mídia recuperada pelo serviço web key=%s bytes=%s",
                key,
                dest_path.stat().st_size,
            )
        except Exception as web_exc:
            raise RuntimeError(
                "Não foi possível baixar a mídia. "
                f"Storage do worker: {storage_exc}; serviço web: {web_exc}"
            ) from web_exc


def _claim_next_slot(db, automation: Automation, items: list[dict]) -> tuple[int, str, str] | None:
    """Reserva o vídeo atual e avança current_index imediatamente.

    Retorna (queue_index, video_key, video_name) ou None se esgotou.
    """
    if len(items) <= 1:
        # Loop no mesmo vídeo (automação de 1 arquivo)
        entry = items[0]
        return (
            0,
            entry["video_key"],
            entry.get("video_original_name") or entry["video_key"],
        )

    idx = int(automation.current_index or 0)
    if idx < 0:
        idx = 0
    if idx >= len(items):
        return None

    entry = items[idx]
    video_key = entry["video_key"]
    video_name = entry.get("video_original_name") or video_key
    new_idx = idx + 1

    if new_idx >= len(items) and automation.content_type == "story":
        db.execute(
            text("UPDATE automations SET current_index = 0 WHERE id = :id"),
            {"id": automation.id},
        )
        automation.current_index = 0
        log.info(
            "PLAYLIST %s STORY LOOP automation=%s postar %s/%s e voltar ao primeiro key=%s name=%r",
            PLAYLIST_CODE,
            automation.id,
            idx + 1,
            len(items),
            video_key,
            video_name,
        )
    elif new_idx >= len(items):
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
            "PLAYLIST %s CLAIM+DONE automation=%s postar %s/%s key=%s name=%r",
            PLAYLIST_CODE,
            automation.id,
            idx + 1,
            len(items),
            video_key,
            video_name,
        )
    else:
        db.execute(
            text("UPDATE automations SET current_index = :idx WHERE id = :id"),
            {"idx": new_idx, "id": automation.id},
        )
        automation.current_index = new_idx
        log.info(
            "PLAYLIST %s CLAIM automation=%s postar %s/%s → próximo fica %s/%s key=%s name=%r",
            PLAYLIST_CODE,
            automation.id,
            idx + 1,
            len(items),
            new_idx + 1,
            len(items),
            video_key,
            video_name,
        )

    return idx, video_key, video_name


@celery_app.task(name="celery_app.tasks.publish.execute_automation", bind=True, max_retries=0)
def execute_automation(self, automation_id: int) -> dict:
    done = None
    account_ids: list[int] = []
    video_key = None
    video_name = None
    queue_index = None
    total_videos = 0
    rotate_keys: list[str] = []
    owner_user_id: int | None = None
    anti_prefs: dict = {}
    stagger_enabled = True
    stagger_min = 2
    stagger_max = 8
    auto_caption_by_account = True
    auto_caption_by_reel = False

    with session_scope() as db:
        automation = db.execute(
            select(Automation).where(Automation.id == automation_id).with_for_update()
        ).scalar_one_or_none()
        if not automation:
            return {"error": "automation_not_found", "id": automation_id}
        if automation.status != "active":
            return {"skipped": True, "reason": "not_active", "code": PLAYLIST_CODE}

        owner_user_id = automation.user_id
        anti_prefs = get_anti_farm_prefs_by_id(db, automation.user_id)
        stagger_enabled, stagger_min, stagger_max = resolve_stagger_config(
            automation, anti_prefs
        )
        auto_caption_by_account = bool(getattr(automation, "caption_rotate_by_account", True))
        auto_caption_by_reel = bool(getattr(automation, "caption_rotate_by_reel", False))

        # Dispara lazy-load das contas ainda com o row lock
        accounts = list(automation.accounts)
        account_ids = [
            acc.id
            for acc in accounts
            if acc.status not in ("banned", "proxy_down", "paused", "needs_login", "deleted")
        ]
        if not account_ids:
            # Não consome mídia se nenhuma conta pode publicar.
            automation.status = "paused"
            automation.next_run_at = None
            log.warning(
                "PLAYLIST %s PAUSED automation=%s sem conta elegível; índice preservado=%s",
                PLAYLIST_CODE,
                automation_id,
                automation.current_index,
            )
            return {
                "error": "no_eligible_accounts",
                "id": automation_id,
                "code": PLAYLIST_CODE,
            }

        items = playlist_items(automation)
        total_videos = len(items)
        log.info(
            "PLAYLIST %s execute id=%s status=%s index=%s items=%s names=%s",
            PLAYLIST_CODE,
            automation_id,
            automation.status,
            automation.current_index,
            total_videos,
            [it.get("video_original_name") for it in items],
        )

        if not items:
            return {"error": "no_videos", "id": automation_id, "code": PLAYLIST_CODE}

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
            claimed = _claim_next_slot(db, automation, items)
            if claimed is None:
                automation.status = "completed"
                automation.next_run_at = None
                db.execute(
                    text(
                        "UPDATE automations SET status = 'completed', next_run_at = NULL "
                        "WHERE id = :id"
                    ),
                    {"id": automation.id},
                )
                done = (automation.user_id, automation.name, len(items))
            else:
                queue_index, video_key, video_name = claimed
                rotate_keys = [it.get("video_key") or "" for it in items]

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
        return {"skipped": True, "reason": "playlist_done", "code": PLAYLIST_CODE}

    if not account_ids or not video_key:
        return {"error": "no_accounts_or_video", "id": automation_id, "code": PLAYLIST_CODE}

    n_accounts = len(account_ids)
    rotate = (
        bool(anti_prefs.get("media_rotate_enabled", True))
        and len(rotate_keys) >= 2
        and queue_index is not None
    )
    use_stagger = stagger_enabled
    use_caption_by_account = (
        bool(anti_prefs.get("caption_rotate_by_account", True)) and auto_caption_by_account
    )
    use_caption_by_reel = (
        bool(anti_prefs.get("caption_rotate_by_reel", False)) and auto_caption_by_reel
    )

    for i, account_id in enumerate(account_ids):
        countdown = (
            account_publish_countdown(
                i,
                n_accounts,
                min_minutes=stagger_min,
                max_minutes=stagger_max,
            )
            if use_stagger
            else 0
        )
        if rotate:
            acc_index = (int(queue_index) + i) % len(rotate_keys)
            acc_video_key = rotate_keys[acc_index] or video_key
            acc_queue_index = acc_index
        else:
            acc_video_key = video_key
            acc_queue_index = queue_index
        # Só account_slot nos kwargs — params extras quebram worker antigo (TypeError)
        publish_to_account.apply_async(
            args=[automation_id, account_id, acc_video_key, acc_queue_index],
            kwargs={"account_slot": i if use_caption_by_account else 0},
            countdown=countdown,
        )

    return {
        "automation_id": automation_id,
        "accounts_dispatched": len(account_ids),
        "queue_index": queue_index,
        "playlist_size": total_videos,
        "video_key": video_key,
        "video_name": video_name,
        "code": PLAYLIST_CODE,
        "anti_farm": {
            "stagger": use_stagger,
            "stagger_min": stagger_min,
            "stagger_max": stagger_max,
            "media_rotate": rotate,
            "caption_by_account": use_caption_by_account,
            "caption_by_reel": use_caption_by_reel,
        },
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
    story_layout: dict | None = None,
    camouflage_cover_key: str | None = None,
    camouflage_opacity: float = 0.10,
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
        story_layout=story_layout,
        camouflage_cover_key=camouflage_cover_key,
        camouflage_opacity=float(camouflage_opacity or 0.10),
    )


@celery_app.task(
    name="celery_app.tasks.publish.publish_to_account",
    bind=True,
    max_retries=2,
)
def publish_to_account(
    self,
    automation_id: int,
    account_id: int,
    video_key: str | None = None,
    queue_index: int | None = None,
    account_slot: int | None = None,
    **_compat_kwargs,
) -> dict:
    # _compat_kwargs: ignora caption_by_* de mensagens antigas na fila (não quebra o worker)
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
        # video_key explícito = ciclo já escolhido (claim pode ter marcado completed)
        if video_key is None and automation.status != "active":
            db.add(PublishLog(
                automation_id=automation.id,
                account_id=account.id,
                status="skipped",
                error="automation_not_active",
            ))
            return {"skipped": True}

        items = playlist_items(automation)
        # Confia no video_key reservado pelo claim — NÃO recalcular pelo current_index
        # (senão posta o próximo em vez do reservado)
        vk = (video_key or "").strip() or resolve_video_key(automation)
        posted_index = queue_index
        if posted_index is None and items:
            for i, it in enumerate(items):
                if it.get("video_key") == vk:
                    posted_index = i
                    break
        if posted_index is None:
            posted_index = 0

        try:
            prefs = get_anti_farm_prefs_by_id(db, automation.user_id)
        except Exception:
            log.exception("anti_farm prefs falhou user=%s — usando defaults", automation.user_id)
            prefs = {}

        by_acc = (
            bool(prefs.get("caption_rotate_by_account", True))
            and bool(getattr(automation, "caption_rotate_by_account", True))
        )
        by_reel = (
            bool(prefs.get("caption_rotate_by_reel", False))
            and bool(getattr(automation, "caption_rotate_by_reel", False))
        )

        slot = int(account_slot) if account_slot is not None else 0
        try:
            caption = resolve_caption(
                automation,
                account_slot=slot,
                reel_index=int(posted_index),
                by_account=by_acc,
                by_reel=by_reel,
            )
        except Exception:
            log.exception(
                "resolve_caption falhou automation=%s — usando caption principal",
                automation_id,
            )
            caption = automation.caption or ""

        # Garantia: nunca postar sem legenda se a principal existir
        if not (caption or "").strip():
            caption = automation.caption or ""

        log.info(
            "PLAYLIST %s publish automation=%s account=%s idx=%s slot=%s by_acc=%s by_reel=%s cap_len=%s key=%s",
            PLAYLIST_CODE,
            automation_id,
            account.username,
            posted_index,
            slot,
            by_acc,
            by_reel,
            len(caption or ""),
            vk,
        )

        try:
            return _execute_publish(
                automation_id=automation.id,
                account_id=account.id,
                video_key=vk,
                thumb_key=automation.thumb_key,
                caption=caption,
                content_type=automation.content_type or "reel",
                story_link=automation.story_link,
                story_sticker_text=automation.story_sticker_text,
                story_layout=_load_story_layout(automation),
                playlist_index=int(posted_index),
                camouflage_cover_key=getattr(automation, "camouflage_cover_key", None),
                camouflage_opacity=float(getattr(automation, "camouflage_opacity", 0.10) or 0.10),
            )
        except Exception as exc:
            if self.request.retries < self.max_retries:
                countdown = min(60 * (2 ** self.request.retries), 600)
                raise self.retry(exc=exc, countdown=countdown)
            _mark_now_automation_failed(automation.id)
            return {"error": "publish_failed", "detail": str(exc)[:500]}


def _execute_publish(
    automation_id: int | None,
    account_id: int,
    video_key: str,
    thumb_key: str | None,
    caption: str,
    content_type: str,
    story_link: str | None = None,
    story_sticker_text: str | None = None,
    story_layout: dict | None = None,
    playlist_index: int | None = None,
    camouflage_cover_key: str | None = None,
    camouflage_opacity: float = 0.10,
) -> dict:
    storage = get_storage()
    meta_warmup_skip_reason: str | None = None

    with session_scope() as db:
        account = db.get(InstagramAccount, account_id)
        if account is None:
            return {"error": "account_not_found"}
        provider = account.provider or "instagrapi"
        account_status = account.status
        recent_auth_failure = (
            latest_auth_failure_reason(db, account_id)
            if provider != "meta"
            else None
        )
        if recent_auth_failure and account_status not in ("deleted", "paused"):
            account.status = "needs_login"
            account.last_error = auth_status_reason(recent_auth_failure)
            account_status = account.status
        owner_user_id = account.user_id
        username = account.username
        password = (
            decrypt_secret(account.encrypted_password)
            if account.encrypted_password
            else None
        )
        proxy = account.proxy
        settings_dict = deserialize_settings(account.session_json) if account.session_json else None
        meta_access_token = decrypt_secret(account.encrypted_meta_access_token)
        meta_ig_user_id = account.meta_ig_user_id
        user_meta_app_id = account.user_meta_app_id
        account_created_at = account.created_at
        account_warmup_enabled = bool(getattr(account, "warmup_enabled", False))
        account_warmup_days = int(getattr(account, "warmup_days", 7) or 7)
        account_warmup_started_at = getattr(account, "warmup_started_at", None)
        from core.web_cookies import decrypt_web_cookies

        web_cookies = decrypt_web_cookies(account.encrypted_web_cookies)
        if (
            provider == "meta"
            and account_status == "needs_login"
            and "code=2" in (account.last_error or "").lower()
        ):
            # Versões anteriores confundiam OAuthException code=2 (temporário)
            # com token inválido. Repara automaticamente essas contas.
            account.status = "active"
            account.last_error = None
            account_status = "active"

        if provider == "meta" and account_status not in (
            "paused", "needs_login", "proxy_down", "banned", "deleted"
        ):
            anti = get_anti_farm_prefs_by_id(db, owner_user_id) if owner_user_id else {}
            warmup_on = bool(anti.get("meta_warmup_enabled", True))
            min_gap = meta_min_interval_for_account(
                SimpleNamespace(
                    provider="meta",
                    warmup_enabled=account_warmup_enabled,
                    warmup_days=account_warmup_days,
                    warmup_started_at=account_warmup_started_at,
                    created_at=account_created_at,
                )
            )
            if not warmup_on:
                from app.utils.intervals import META_MIN_INTERVAL as _META_FLOOR
                min_gap = _META_FLOOR
            if min_gap > 0:
                last_ok = db.scalars(
                    select(PublishLog)
                    .where(
                        PublishLog.account_id == account_id,
                        PublishLog.status == "success",
                    )
                    .order_by(PublishLog.created_at.desc())
                    .limit(1)
                ).first()
                if last_ok and last_ok.created_at:
                    last_at = last_ok.created_at
                    if last_at.tzinfo is not None:
                        last_at = last_at.astimezone(dt.timezone.utc).replace(tzinfo=None)
                    age_min = (dt.datetime.utcnow() - last_at).total_seconds() / 60.0
                    if age_min < min_gap:
                        wait_left = int(min_gap - age_min) + 1
                        meta_warmup_skip_reason = (
                            f"meta_min_interval:{min_gap}m "
                            f"(aguarde ~{wait_left} min; último post há {int(age_min)} min)"
                        )
                        db.add(
                            PublishLog(
                                automation_id=automation_id,
                                account_id=account_id,
                                status="skipped",
                                content_type=content_type,
                                error=meta_warmup_skip_reason[:2000],
                            )
                        )

    if account_status in ("paused", "needs_login", "proxy_down", "banned", "deleted"):
        return {"skipped": True, "reason": f"account_{account_status}"}

    if meta_warmup_skip_reason:
        log.info(
            "Meta interval skip account=%s reason=%s",
            account_id,
            meta_warmup_skip_reason,
        )
        return {"skipped": True, "reason": "meta_min_interval"}

    if provider == "meta":
        if not user_meta_app_id:
            _mark_account_needs_login(
                account_id,
                "Conta sem app Meta vinculado. Cadastre em Meus Apps e reconecte.",
            )
            return {"error": "meta_app_missing"}
        if not meta_access_token or not meta_ig_user_id:
            _mark_account_needs_login(account_id, "Token da API oficial ausente. Reconecte a conta.")
            return {"error": "meta_token_missing"}
        try:
            result = publish_meta_media(
                access_token=meta_access_token,
                ig_user_id=meta_ig_user_id,
                media_key=video_key,
                content_type=content_type,
                caption=caption,
                cover_key=thumb_key if content_type == "reel" else None,
            )
        except MetaInstagramError as exc:
            _log_failure(
                automation_id,
                account_id,
                f"API oficial: {exc}",
                content_type=content_type,
                owner_user_id=owner_user_id,
                username=username,
            )
            if exc.code in (102, 190):
                _mark_account_needs_login(account_id, str(exc))
                return {"error": "meta_auth"}
            # Conta IG restringida / checkpoint pela Meta (API code 25 / 2207050)
            if exc.code == 25 or exc.subcode == 2207050 or _meta_user_restricted(exc):
                _mark_account_meta_restricted(account_id, str(exc))
                return {"error": "meta_restricted"}
            raise

        cover_error = str(result.get("cover_error") or "")
        if cover_error:
            log.warning(
                "META REEL publicado sem capa account=%s key=%s erro=%s",
                username,
                thumb_key,
                cover_error,
            )
            create_notification(
                owner_user_id,
                "Reels publicado sem a capa personalizada",
                f"@{username}: a Meta recusou a capa, mas o Reels foi publicado. {cover_error[:140]}",
                kind="warning",
                link="/logs",
            )

        publish_log_id: int | None = None
        with session_scope() as db:
            acc = db.get(InstagramAccount, account_id)
            if acc:
                acc.last_login_at = dt.datetime.utcnow()
                acc.status = "active"
                acc.last_error = None
            auto = None
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
            if auto and (auto.start_mode or "") == "now":
                _complete_now_automation_if_ready(db, auto)

        notify_publish_success(
            owner_user_id,
            username,
            content_type=content_type or "reel",
            publish_log_id=publish_log_id,
        )
        return {
            "ok": True,
            "provider": "meta",
            "playlist_code": PLAYLIST_CODE,
            "playlist_index": playlist_index,
            "video_key": video_key,
            **result,
        }

    if not proxy or not proxy.strip():
        _log_failure(
            automation_id,
            account_id,
            "proxy não configurada",
            content_type=content_type,
            owner_user_id=owner_user_id,
            username=username,
        )
        _mark_account_proxy_down(account_id, "Proxy não configurada")
        return {"error": "proxy_missing"}

    if not check_proxy(proxy):
        _log_failure(
            automation_id,
            account_id,
            "proxy vazando IP do servidor",
            content_type=content_type,
            owner_user_id=owner_user_id,
            username=username,
        )
        _mark_account_proxy_down(account_id, "Proxy vazando IP do servidor")
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
    ext_lower = ext.lower()
    if ext_lower in VIDEO_EXT:
        clean_ext = ".mp4"
    elif ext_lower in IMAGE_EXT:
        # strip_image_metadata sempre produz JPEG.
        clean_ext = ".jpg"
    else:
        clean_ext = ext
    clean_path = tmp_dir / f"clean{clean_ext}"
    thumb_path: Path | None = None
    clean_thumb_path: Path | None = None
    meta_info: dict | None = None

    try:
        log.info("Download mídia key=%s → %s", video_key, raw_path.name)
        try:
            _download_media(storage, video_key, raw_path)
        except Exception as exc:
            _log_failure(
                automation_id,
                account_id,
                f"storage: {exc}",
                content_type=content_type,
                owner_user_id=owner_user_id,
                username=username,
            )
            raise

        # Limpeza de metadados é silenciosa no sino — só avisa se falhar.
        try:
            work_path = raw_path
            if (
                camouflage_cover_key
                and (content_type or "reel") == "reel"
                and raw_path.suffix.lower() in VIDEO_EXT
            ):
                cover_raw = tmp_dir / f"camu_cover{Path(camouflage_cover_key).suffix or '.jpg'}"
                camu_out = tmp_dir / "camu_overlay.mp4"
                try:
                    _download_media(storage, camouflage_cover_key, cover_raw)
                    apply_camouflage_overlay(
                        raw_path,
                        cover_raw,
                        camu_out,
                        opacity=camouflage_opacity,
                    )
                    work_path = camu_out
                    log.info(
                        "CAMOUFLAGE overlay automation=%s opacity=%.2f",
                        automation_id,
                        camouflage_opacity,
                    )
                except Exception as camu_exc:
                    log.warning(
                        "Camuflagem falhou automation=%s — seguindo sem overlay: %s",
                        automation_id,
                        camu_exc,
                    )

            clean_path, meta_info = prepare_clean_media(
                work_path,
                clean_path,
                content_type=content_type,
                account_hint=username,
            )
            fp = (meta_info or {}).get("fingerprint", "ok")
            raw_sha = (meta_info or {}).get("raw_sha256", "")
            clean_sha = (meta_info or {}).get("clean_sha256", "")
            log.info(
                "METADATA CLEAN automation=%s account=%s fp=%s raw_sha=%s clean_sha=%s size=%s",
                automation_id,
                username,
                fp,
                raw_sha[:12],
                clean_sha[:12],
                (meta_info or {}).get("clean_size"),
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
            try:
                _download_media(storage, thumb_key, raw_thumb)
                thumb_path = prepare_clean_thumb(raw_thumb, clean_thumb_path)
            except Exception as exc:
                _log_failure(
                    automation_id,
                    account_id,
                    f"capa: {exc}",
                    content_type=content_type,
                    owner_user_id=owner_user_id,
                    username=username,
                )
                return {"error": "thumb_prepare"}
        elif publish_path.suffix.lower() in (".mp4", ".mov", ".webm", ".mkv", ".avi"):
            # Instagrapi 2.16.x é mais estável quando o worker fornece um
            # thumbnail explícito (evita MoviePy/FFmpeg interno no upload).
            clean_thumb_path = tmp_dir / "generated_thumb.jpg"
            try:
                thumb_path = generate_video_thumbnail(publish_path, clean_thumb_path)
            except MetadataStripError as exc:
                log.warning(
                    "Thumbnail automático falhou automation=%s account=%s: %s",
                    automation_id,
                    username,
                    exc,
                )

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
                result = publish_story(
                    cl,
                    publish_path,
                    link_url=story_link,
                    thumbnail_path=thumb_path,
                    sticker_text=story_sticker_text,
                    story_layout=story_layout,
                    web_cookies=web_cookies,
                )
            elif content_type == "photo":
                result = publish_photo_feed(cl, clean_path, caption)
            else:
                result = publish_reel(
                    cl,
                    clean_path,
                    caption,
                    thumbnail_path=thumb_path,
                    web_cookies=web_cookies,
                )
        except Exception as exc:
            if looks_auth_required(exc):
                reason = f"Sessão expirada no upload: {exc}"
                _mark_account_needs_login(account_id, reason)
                _log_failure(
                    automation_id,
                    account_id,
                    reason,
                    content_type=content_type,
                    owner_user_id=owner_user_id,
                    username=username,
                )
                return {"error": "auth_upload"}
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
                metadata_fingerprint=(meta_info or {}).get("fingerprint"),
                raw_sha256=(meta_info or {}).get("raw_sha256"),
                clean_sha256=(meta_info or {}).get("clean_sha256"),
                clean_size=int((meta_info or {}).get("clean_size") or 0) or None,
            )
            db.add(plog)
            db.flush()
            publish_log_id = plog.id
            notify_user_id = acc.user_id if acc else owner_user_id
            notify_username = acc.username if acc else username

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
            "playlist_code": PLAYLIST_CODE,
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


def _complete_now_automation_if_ready(db, automation: Automation) -> None:
    entries = playlist_items(automation)
    expected = {
        (account.id, entry["video_key"])
        for account in automation.accounts
        for entry in entries
    }
    if not expected:
        return
    successful = set(
        db.execute(
            select(PublishLog.account_id, PublishLog.video_key).where(
                PublishLog.automation_id == automation.id,
                PublishLog.status == "success",
            )
        ).all()
    )
    if expected.issubset(successful):
        automation.status = "completed"
        automation.next_run_at = None
        automation.current_index = len(entries)


def _mark_now_automation_failed(automation_id: int) -> None:
    with session_scope() as db:
        automation = db.get(Automation, automation_id)
        if (
            automation
            and (automation.start_mode or "") == "now"
            and automation.status != "completed"
        ):
            automation.status = "paused"
            automation.next_run_at = None


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


def _meta_user_restricted(exc: MetaInstagramError) -> bool:
    text = str(exc).lower()
    return (
        "user access is restricted" in text
        or "user is restricted" in text
        or "instagram account is restricted" in text
        or "2207050" in text
    )


def _mark_account_meta_restricted(account_id: int, reason: str) -> None:
    """Pausa conta restringida pela Meta para não ficar tentando a cada hora."""
    from core.notifications import create_notification

    msg = (
        "Conta restringida pela Meta (API). Entre no Instagram pelo navegador (PC), "
        "resolva o aviso/checkpoint e depois reative a conta no painel."
    )
    with session_scope() as db:
        acc = db.get(InstagramAccount, account_id)
        if not acc or acc.status in ("deleted", "banned"):
            return
        prev = acc.status
        acc.status = "paused"
        acc.last_error = (reason or msg)[:1000]
        uid = acc.user_id
        uname = acc.username
    if prev != "paused":
        create_notification(
            uid,
            f"@{uname} restringida pela Meta",
            msg,
            kind="offline",
            link="/accounts/connected",
        )


def _mark_account_needs_login(account_id: int, reason: str) -> None:
    from core.notifications import create_notification

    with session_scope() as db:
        acc = db.get(InstagramAccount, account_id)
        if not acc or acc.status == "deleted":
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
