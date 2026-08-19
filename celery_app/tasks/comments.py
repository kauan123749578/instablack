"""Poll de comentários em Reels/fotos Meta + resposta automática (automações)."""
from __future__ import annotations

import datetime as dt
import logging
import time

from sqlalchemy import select

from app.security import decrypt_secret
from app.utils.comment_auto_reply import AUTO_REPLY_CONTENT_TYPES, pick_reply_message
from celery_app.config import celery_app
from core.database import session_scope
from core.meta_instagram import MetaInstagramError, list_media_comments, reply_to_comment
from models.models import Automation, CommentAutoReply, InstagramAccount, PublishLog

log = logging.getLogger(__name__)

LOOKBACK_DAYS = 14
MAX_LOGS_PER_TICK = 40


def _parse_comment_ts(raw: str | None) -> dt.datetime | None:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = dt.datetime.fromisoformat(text)
        if parsed.tzinfo is not None:
            return parsed.astimezone(dt.timezone.utc).replace(tzinfo=None)
        return parsed
    except ValueError:
        return None


def _username_norm(value: str | None) -> str:
    return (value or "").strip().lower().lstrip("@")


@celery_app.task(name="celery_app.tasks.comments.poll_auto_replies", bind=True, expires=25)
def poll_auto_replies(self) -> dict:
    """Varre posts Meta publicados por automações com auto-reply e responde comentários novos."""
    client = None
    try:
        from redis import Redis

        from app.config import settings

        client = Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2)
        if not client.set("celery:comment_auto_reply:lock", "1", nx=True, ex=18):
            return {"skipped": True, "reason": "lock"}
    except Exception as exc:
        log.warning("comment auto-reply lock falhou — seguindo: %s", exc)

    since = dt.datetime.utcnow() - dt.timedelta(days=LOOKBACK_DAYS)
    replied = 0
    skipped = 0
    errors = 0

    try:
        with session_scope() as db:
            rows = db.execute(
                select(PublishLog, Automation, InstagramAccount)
                .join(Automation, PublishLog.automation_id == Automation.id)
                .join(InstagramAccount, PublishLog.account_id == InstagramAccount.id)
                .where(
                    PublishLog.status == "success",
                    PublishLog.content_type.in_(AUTO_REPLY_CONTENT_TYPES),
                    PublishLog.media_id.isnot(None),
                    PublishLog.created_at >= since,
                    Automation.comment_auto_reply_enabled.is_(True),
                    Automation.status == "active",
                    InstagramAccount.provider == "meta",
                    InstagramAccount.status.in_(("active", "paused")),
                )
                .order_by(PublishLog.created_at.desc())
                .limit(MAX_LOGS_PER_TICK)
            ).all()

            now = dt.datetime.utcnow()

            for publish_log, automation, account in rows:
                message = pick_reply_message(automation)
                if not message:
                    continue
                media_id = (publish_log.media_id or "").strip()
                if not media_id:
                    continue
                token = decrypt_secret(account.encrypted_meta_access_token or "")
                if not token:
                    continue
                delay_sec = max(3, min(120, int(automation.comment_auto_reply_delay_seconds or 5)))
                owner = _username_norm(account.username)

                try:
                    payload = list_media_comments(
                        token,
                        media_id,
                        limit=50,
                        proxy=account.proxy,
                    )
                except MetaInstagramError as exc:
                    errors += 1
                    log.warning(
                        "auto-reply list comments media=%s @%s: %s",
                        media_id,
                        account.username,
                        exc,
                    )
                    continue
                except Exception as exc:
                    errors += 1
                    log.warning(
                        "auto-reply list comments media=%s @%s: %s",
                        media_id,
                        account.username,
                        exc,
                    )
                    continue

                for comment in payload.get("items") or []:
                    cid = str(comment.get("id") or "").strip()
                    if not cid:
                        continue
                    if db.scalar(
                        select(CommentAutoReply.id).where(
                            CommentAutoReply.ig_comment_id == cid
                        )
                    ):
                        skipped += 1
                        continue

                    comment_user = _username_norm(comment.get("username"))
                    if comment_user and owner and comment_user == owner:
                        skipped += 1
                        continue

                    existing_replies = comment.get("replies") or []
                    if isinstance(existing_replies, list):
                        already = any(
                            _username_norm(r.get("username")) == owner
                            for r in existing_replies
                            if isinstance(r, dict)
                        )
                        if already:
                            db.add(
                                CommentAutoReply(
                                    ig_comment_id=cid,
                                    publish_log_id=publish_log.id,
                                    automation_id=automation.id,
                                    account_id=account.id,
                                    reply_text="(já respondido)",
                                )
                            )
                            skipped += 1
                            continue

                    comment_ts = _parse_comment_ts(comment.get("timestamp"))
                    if comment_ts and (now - comment_ts).total_seconds() < delay_sec:
                        skipped += 1
                        continue

                    try:
                        result = reply_to_comment(
                            token,
                            cid,
                            message,
                            proxy=account.proxy,
                        )
                        db.add(
                            CommentAutoReply(
                                ig_comment_id=cid,
                                publish_log_id=publish_log.id,
                                automation_id=automation.id,
                                account_id=account.id,
                                reply_text=str(result.get("message") or message)[:500],
                            )
                        )
                        db.commit()
                        replied += 1
                        log.info(
                            "auto-reply ok @%s media=%s comment=%s automation=%s",
                            account.username,
                            media_id,
                            cid,
                            automation.id,
                        )
                        time.sleep(0.4)
                    except MetaInstagramError as exc:
                        errors += 1
                        log.warning(
                            "auto-reply fail @%s comment=%s: %s",
                            account.username,
                            cid,
                            exc,
                        )
                    except Exception as exc:
                        errors += 1
                        db.rollback()
                        log.warning(
                            "auto-reply fail @%s comment=%s: %s",
                            account.username,
                            cid,
                            exc,
                        )
    finally:
        if client is not None:
            try:
                client.delete("celery:comment_auto_reply:lock")
            except Exception:
                pass

    if replied or errors:
        log.info(
            "comment auto-reply tick: replied=%s skipped=%s errors=%s",
            replied,
            skipped,
            errors,
        )
    return {"replied": replied, "skipped": skipped, "errors": errors}
