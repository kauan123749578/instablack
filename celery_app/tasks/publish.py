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
import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

import requests
from sqlalchemy import select, text

from app.config import settings
from app.security import decrypt_secret
from app.utils.anti_farm import (
    account_publish_countdown,
    best_available_caption,
    normalize_caption_text,
    resolve_caption,
    resolve_stagger_config,
)
from app.utils.auth_failures import (
    auth_status_reason,
    latest_auth_failure_reason,
    looks_auth_required,
)
from app.utils.automation_videos import playlist_items, playlist_is_exhausted, resolve_video_key
from app.utils.intervals import meta_min_interval_for_account
from celery_app.config import celery_app
from core.anti_farm_prefs import get_anti_farm_prefs_by_id
from core.capacity_metrics import (
    ffmpeg_slot,
    record_meta_defer,
    record_meta_poll_sample,
    record_publish_sample,
)
from core.database import session_scope
from core import aiograpi_client as aio_ig
from core.instagram import (
    InstagramAuthError,
    InstagramTwoFactorRequired,
    check_proxy,
    deserialize_settings,
    extract_sessionid_from_settings,
    get_ready_client,
    publish_photo_feed,
    publish_reel,
    publish_story,
    serialize_settings,
    try_refresh_session,
)
from core.media_prepare import (
    IMAGE_EXT,
    VIDEO_EXT,
    apply_camouflage_overlay,
    generate_video_thumbnail,
    prepare_clean_media,
    prepare_clean_thumb,
)
from core.meta_instagram import (
    MetaInstagramError,
    finalize_media_publish,
    get_container_status,
    publish_media as publish_meta_media,
    submit_media_container,
    uniquify_caption_for_account,
)
from core.metadata import MetadataStripError
from core.notifications import create_notification, notify_publish_success
from core.storage import get_storage
from core.web_cookies import decrypt_web_cookies, merge_sessionid_into_web_cookies
from core.web_browser import decrypt_web_browser
from models.models import Automation, InstagramAccount, PublishLog, automation_accounts

log = logging.getLogger(__name__)

# Aparece nos logs do Railway — se não aparecer, o worker NÃO atualizou
PLAYLIST_CODE = "claim-v5-storage-fallback"

_redis_client = None


def _redis():
    """Cliente Redis lazy (anti-spam de publish). Falha aberta se Redis cair."""
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    try:
        import redis

        _redis_client = redis.from_url(
            settings.redis_url,
            socket_connect_timeout=2,
            socket_timeout=2,
            decode_responses=True,
        )
        return _redis_client
    except Exception as exc:
        log.warning("Redis indisponível para anti-spam publish: %s", exc)
        return None


def _meta_cooldown_key(account_id: int) -> str:
    return f"meta:cooldown:{int(account_id)}"


def _meta_inflight_key(account_id: int) -> str:
    return f"meta:inflight:{int(account_id)}"


def _meta_defer_sched_key(account_id: int) -> str:
    return f"meta:defer_sched:{int(account_id)}"


def _parse_scheduled_at(raw: str | dt.datetime | None) -> dt.datetime | None:
    if raw is None:
        return None
    if isinstance(raw, dt.datetime):
        value = raw
    else:
        try:
            value = dt.datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except Exception:
            return None
    if value.tzinfo is not None:
        return value.astimezone(dt.timezone.utc).replace(tzinfo=None)
    return value


def _publish_timing(
    scheduled_at: str | dt.datetime | None,
    started_at: str | dt.datetime | None,
    *,
    enqueued_at: str | dt.datetime | None = None,
    dispatch_countdown: int = 0,
) -> dict:
    """Campos de observabilidade para PublishLog + Redis metrics.

    Aceita datetime ou ISO string (pending Meta no Redis serializa com isoformat).
    """
    sched = _parse_scheduled_at(scheduled_at)
    started = _parse_scheduled_at(started_at) or dt.datetime.utcnow()
    lag: int | None = None
    if sched is not None:
        try:
            lag = max(0, int((started - sched).total_seconds()))
        except Exception:
            lag = None
    duration: int | None = None
    started_for_duration = _parse_scheduled_at(started_at)
    if started_for_duration is not None:
        try:
            duration = max(0, int((dt.datetime.utcnow() - started_for_duration).total_seconds()))
        except Exception:
            duration = None
    queue_wait: int | None = None
    enq = _parse_scheduled_at(enqueued_at)
    if enq is not None and started_for_duration is not None:
        try:
            raw_wait = int((started_for_duration - enq).total_seconds())
            queue_wait = max(0, raw_wait - max(0, int(dispatch_countdown or 0)))
        except Exception:
            queue_wait = None
    return {
        "scheduled_at": sched,
        "started_at": started,
        "schedule_lag_seconds": lag,
        "duration_seconds": duration,
        "queue_wait_seconds": queue_wait,
    }


def _record_timing_metrics(timing: dict, status: str) -> None:
    record_publish_sample(
        schedule_lag_seconds=timing.get("schedule_lag_seconds"),
        duration_seconds=timing.get("duration_seconds"),
        queue_wait_seconds=timing.get("queue_wait_seconds"),
        status=status,
    )


def _redis_ttl_seconds(client, key: str, fallback: int) -> int:
    try:
        ttl = int(client.ttl(key) or 0)
    except Exception:
        return max(60, fallback)
    if ttl > 0:
        return ttl
    return max(60, fallback)


def _bump_automation_run_counters(db, automation_id: int | None) -> Automation | None:
    """UPDATE atômico — 30 workers não fazem read-modify-write na mesma linha."""
    if not automation_id:
        return None
    now = dt.datetime.utcnow()
    db.execute(
        text(
            "UPDATE automations SET last_run_at = :now, "
            "total_runs = COALESCE(total_runs, 0) + 1 WHERE id = :id"
        ),
        {"now": now, "id": int(automation_id)},
    )
    return db.get(Automation, int(automation_id))


def _meta_global_active_key() -> str:
    """Slots Meta em andamento (ZSET com expiry por membro — auto-limpa crash)."""
    return "meta:global_active"


# Inflight por conta: do submit até publish/fail (poll pode levar até ~10 min).
# Refresh a cada poll renova o TTL — se o worker cair, a chave expira sozinha.
META_INFLIGHT_TTL_SEC = 720
META_GLOBAL_SLOT_TTL_SEC = 720
META_PENDING_TTL_SEC = 900
_META_POLL_BACKOFF = (5, 10, 20, 30)


def _meta_global_max_concurrent() -> int:
    """Limite de publishes Meta simultâneos (env META_GLOBAL_MAX_CONCURRENT)."""
    try:
        from app.config import get_settings

        n = int(getattr(get_settings(), "meta_global_max_concurrent", 5) or 5)
    except Exception:
        n = 5
    return max(1, min(n, 20))


def _meta_user_max_concurrent() -> int:
    """Máx. publishes Meta simultâneos por usuário Instablack."""
    try:
        from app.config import get_settings

        n = int(getattr(get_settings(), "meta_user_max_concurrent", 2) or 2)
    except Exception:
        n = 2
    return max(1, min(n, 10))


def _meta_user_active_key(user_id: int) -> str:
    return f"meta:user_active:{int(user_id)}"


def _claim_meta_global_slot(client, account_id: int) -> tuple[bool, int]:
    """Fila curta entre contas Meta; tokens diferentes podem publicar em paralelo."""
    import time as _time

    key = _meta_global_active_key()
    now = _time.time()
    limit = _meta_global_max_concurrent()
    try:
        client.zremrangebyscore(key, 0, now)
        active = int(client.zcard(key) or 0)
        if active >= limit:
            # Espera proporcional à fila + jitter (evita todos acordarem no mesmo segundo).
            wait = 15 + (active * 5) + (int(account_id) % 20)
            return False, wait
        client.zadd(key, {str(int(account_id)): now + float(META_GLOBAL_SLOT_TTL_SEC)})
        return True, 0
    except Exception as exc:
        log.warning("claim_meta_global_slot falhou account=%s: %s", account_id, exc)
        return True, 0


def _release_meta_global_slot(client, account_id: int) -> None:
    try:
        client.zrem(_meta_global_active_key(), str(int(account_id)))
    except Exception:
        pass


def _claim_meta_user_slot(client, user_id: int | None, account_id: int) -> tuple[bool, int]:
    """Limita quantas contas do MESMO user rodam juntos — libera fila pros outros."""
    import time as _time

    if not user_id:
        return True, 0
    key = _meta_user_active_key(int(user_id))
    now = _time.time()
    limit = _meta_user_max_concurrent()
    try:
        client.zremrangebyscore(key, 0, now)
        active = int(client.zcard(key) or 0)
        if active >= limit:
            wait = 20 + (active * 8) + (int(account_id) % 15)
            return False, wait
        client.zadd(key, {str(int(account_id)): now + float(META_GLOBAL_SLOT_TTL_SEC)})
        return True, 0
    except Exception as exc:
        log.warning(
            "claim_meta_user_slot falhou user=%s account=%s: %s",
            user_id,
            account_id,
            exc,
        )
        return True, 0


def _release_meta_user_slot(client, user_id: int | None, account_id: int) -> None:
    if not user_id:
        return
    try:
        client.zrem(_meta_user_active_key(int(user_id)), str(int(account_id)))
    except Exception:
        pass


def _refresh_meta_slots(account_id: int, user_id: int | None = None) -> None:
    """Renova TTL dos slots enquanto o poll async está em andamento."""
    import time as _time

    client = _redis()
    if client is None:
        return
    now = _time.time()
    expiry = now + float(META_GLOBAL_SLOT_TTL_SEC)
    try:
        pipe = client.pipeline()
        pipe.expire(_meta_inflight_key(account_id), META_INFLIGHT_TTL_SEC)
        pipe.zadd(_meta_global_active_key(), {str(int(account_id)): expiry})
        if user_id:
            pipe.zadd(
                _meta_user_active_key(int(user_id)),
                {str(int(account_id)): expiry},
            )
        pipe.execute()
    except Exception as exc:
        log.warning("refresh_meta_slots falhou account=%s: %s", account_id, exc)


def _meta_pending_key(account_id: int) -> str:
    return f"meta:pending:{int(account_id)}"


def _save_meta_pending(account_id: int, payload: dict) -> None:
    import json

    client = _redis()
    if client is None:
        raise RuntimeError("Redis indisponível para pending Meta")
    client.set(
        _meta_pending_key(account_id),
        json.dumps(payload, default=str),
        ex=META_PENDING_TTL_SEC,
    )


def _load_meta_pending(account_id: int) -> dict | None:
    import json

    client = _redis()
    if client is None:
        return None
    raw = client.get(_meta_pending_key(account_id))
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _clear_meta_pending(account_id: int) -> None:
    client = _redis()
    if client is None:
        return
    try:
        client.delete(_meta_pending_key(account_id))
    except Exception:
        pass


def _meta_poll_countdown(poll_count: int) -> float:
    if getattr(settings, "meta_http_mock", False):
        return 0.1
    idx = min(max(0, int(poll_count)), len(_META_POLL_BACKOFF) - 1)
    return float(_META_POLL_BACKOFF[idx])


def _meta_poll_timeout_sec() -> int:
    try:
        n = int(getattr(settings, "meta_poll_timeout_sec", 600) or 600)
    except Exception:
        n = 600
    return max(60, min(n, 1800))


def _cleanup_temp_camu_key(temp_camu_key: str | None) -> None:
    if not temp_camu_key:
        return
    try:
        get_storage().delete(temp_camu_key)
    except Exception:
        log.warning("Falha ao apagar temp camuflagem key=%s", temp_camu_key)


def _inflight_user_id(client, account_id: int) -> int | None:
    """user_id gravado no valor do key inflight (para liberar slot sem parâmetro)."""
    try:
        raw = client.get(_meta_inflight_key(account_id))
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="ignore")
        return int(raw) if str(raw).isdigit() else None
    except Exception:
        return None


def _claim_meta_publish_slot(
    account_id: int,
    cooldown_sec: int,
    user_id: int | None = None,
) -> tuple[bool, int, str]:
    """1 publish por conta + teto por user + teto global Meta.

    Retorna (pode_publicar, wait_seconds, motivo).
    """
    client = _redis()
    if client is None:
        return True, 0, ""

    cooldown_sec = max(60, int(cooldown_sec or 3600))
    cool_key = _meta_cooldown_key(account_id)
    fly_key = _meta_inflight_key(account_id)
    uid_val = str(int(user_id)) if user_id else "0"

    try:
        if client.exists(cool_key):
            # Cooldown pós-sucesso: não precisa esperar a hora toda na task —
            # reagenda no máximo 3 min e tenta de novo.
            wait = min(_redis_ttl_seconds(client, cool_key, cooldown_sec), 180)
            return False, max(60, wait), f"meta_cooldown:{wait}s"

        if not client.set(fly_key, uid_val, nx=True, ex=META_INFLIGHT_TTL_SEC):
            wait = min(_redis_ttl_seconds(client, fly_key, META_INFLIGHT_TTL_SEC), 90)
            return False, max(30, wait), f"meta_inflight:{wait}s"

        ok_user, wait_user = _claim_meta_user_slot(client, user_id, account_id)
        if not ok_user:
            try:
                client.delete(fly_key)
            except Exception:
                pass
            return False, wait_user, f"meta_user_queue:{wait_user}s"

        ok_global, wait_global = _claim_meta_global_slot(client, account_id)
        if not ok_global:
            try:
                client.delete(fly_key)
            except Exception:
                pass
            _release_meta_user_slot(client, user_id, account_id)
            return False, wait_global, f"meta_global_queue:{wait_global}s"

        return True, 0, ""
    except Exception as exc:
        log.warning("claim_meta_publish_slot falhou account=%s: %s", account_id, exc)
        return True, 0, ""


def _release_meta_inflight(account_id: int, user_id: int | None = None) -> None:
    client = _redis()
    if client is None:
        return
    try:
        uid = user_id if user_id else _inflight_user_id(client, account_id)
        client.delete(_meta_inflight_key(account_id))
        _release_meta_global_slot(client, account_id)
        _release_meta_user_slot(client, uid, account_id)
    except Exception:
        pass


def _trip_meta_caption_circuit(*, ttl_sec: int = 600) -> None:
    """Desativado: pausava TODAS as contas Meta por 10 min após 1 falha de caption."""
    _ = ttl_sec
    return


def _mark_meta_published(
    account_id: int,
    cooldown_sec: int,
    user_id: int | None = None,
) -> None:
    """Após sucesso: cooldown Redis + libera inflight (DB pode atrasar e liberar race)."""
    client = _redis()
    if client is None:
        return
    cooldown_sec = max(60, int(cooldown_sec or 3600))
    try:
        uid = user_id if user_id else _inflight_user_id(client, account_id)
        pipe = client.pipeline()
        pipe.set(_meta_cooldown_key(account_id), "1", ex=cooldown_sec)
        pipe.delete(_meta_inflight_key(account_id))
        pipe.execute()
        _release_meta_global_slot(client, account_id)
        _release_meta_user_slot(client, uid, account_id)
    except Exception as exc:
        log.warning("mark_meta_published falhou account=%s: %s", account_id, exc)
        _release_meta_inflight(account_id, user_id)


def _schedule_meta_defer_once(
    *,
    account_id: int,
    wait: int,
    schedule_fn,
) -> bool:
    """Agenda no máximo 1 retry deferido por conta (evita storm de apply_async)."""
    wait = max(60, min(int(wait), 6 * 3600))
    client = _redis()
    if client is None:
        schedule_fn(wait)
        return True
    key = _meta_defer_sched_key(account_id)
    try:
        if client.set(key, "1", nx=True, ex=wait):
            schedule_fn(wait)
            return True
        log.info(
            "skip duplicate meta defer schedule account=%s (já há retry em fila)",
            account_id,
        )
        return False
    except Exception as exc:
        log.warning("schedule_meta_defer_once falhou account=%s: %s", account_id, exc)
        schedule_fn(wait)
        return True


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


def _render_camouflage_reel(
    storage,
    *,
    video_key: str,
    camouflage_cover_key: str,
    camouflage_opacity: float,
    tmp_dir: Path,
    automation_id: int | None = None,
    video_path: Path | None = None,
) -> Path:
    """Baixa vídeo + capa e gera MP4 com overlay (camuflagem)."""
    if video_path is None:
        ext = Path(video_key).suffix or ".mp4"
        raw_path = tmp_dir / f"camu_raw{ext}"
        _download_media(storage, video_key, raw_path)
    else:
        raw_path = video_path
    cover_raw = tmp_dir / f"camu_cover{Path(camouflage_cover_key).suffix or '.jpg'}"
    camu_out = tmp_dir / "camu_overlay.mp4"
    log.info(
        "CAMOUFLAGE start automation=%s video=%s cover=%s opacity=%.2f",
        automation_id,
        video_key,
        camouflage_cover_key,
        camouflage_opacity,
    )
    _download_media(storage, camouflage_cover_key, cover_raw)
    apply_camouflage_overlay(
        raw_path,
        cover_raw,
        camu_out,
        opacity=camouflage_opacity,
    )
    log.info(
        "CAMOUFLAGE ok automation=%s size=%s",
        automation_id,
        camu_out.stat().st_size,
    )
    return camu_out


def _upload_temp_media(storage, path: Path, *, suggested_ext: str = ".mp4") -> str:
    with path.open("rb") as fh:
        return storage.save(fh, suggested_ext)


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

        from app.media_access import absolute_signed_media_url

        media_url = absolute_signed_media_url(key)
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


def _claim_next_slot(
    db,
    automation: Automation,
    items: list[dict],
    *,
    scheduled_at: dt.datetime | None = None,
) -> tuple[int, str, str] | None:
    """Reserva o vídeo atual e avança current_index imediatamente.

    Story calendário com calendar_time por item: escolhe a mídia do horário
    que disparou (BRT), não FIFO cego.

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

    is_story_cal = (
        (automation.content_type or "").lower() == "story"
        and (automation.schedule_type or "") == "calendar"
        and any(str(it.get("calendar_time") or "").strip() for it in items)
    )

    idx: int | None = None
    if is_story_cal and scheduled_at is not None:
        from app.utils.calendar_schedule import _normalize_hhmm, utc_to_brt_hhmm

        target = utc_to_brt_hhmm(scheduled_at)
        for i, entry in enumerate(items):
            norm = _normalize_hhmm(str(entry.get("calendar_time") or ""))
            if norm == target:
                idx = i
                break
        if idx is None:
            # Tolerância ±1 min (atraso do tick)
            for i, entry in enumerate(items):
                norm = _normalize_hhmm(str(entry.get("calendar_time") or ""))
                if not norm:
                    continue
                th, tm = map(int, norm.split(":"))
                sh, sm = map(int, target.split(":"))
                if abs((th * 60 + tm) - (sh * 60 + sm)) <= 1:
                    idx = i
                    break
        if idx is not None:
            log.info(
                "PLAYLIST %s STORY SLOT automation=%s slot=%s → idx=%s/%s key=%s",
                PLAYLIST_CODE,
                automation.id,
                target,
                idx + 1,
                len(items),
                items[idx]["video_key"],
            )

    if idx is None:
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
def execute_automation(self, automation_id: int, scheduled_at: str | None = None) -> dict:
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

    # Fase 1: leitura SEM lock — prefs/contas fora do FOR UPDATE.
    with session_scope() as db:
        automation = db.get(Automation, automation_id)
        if not automation:
            return {"error": "automation_not_found", "id": automation_id}
        if automation.status != "active":
            return {"skipped": True, "reason": "not_active", "code": PLAYLIST_CODE}

        owner_user_id = automation.user_id
        anti_prefs = get_anti_farm_prefs_by_id(db, automation.user_id)
        stagger_enabled, stagger_min, stagger_max = resolve_stagger_config(
            automation, anti_prefs
        )
        account_rows = db.execute(
            select(InstagramAccount.id, InstagramAccount.status)
            .join(
                automation_accounts,
                automation_accounts.c.account_id == InstagramAccount.id,
            )
            .where(automation_accounts.c.automation_id == automation_id)
        ).all()
        account_ids = [
            row.id
            for row in account_rows
            if row.status
            not in ("banned", "proxy_down", "paused", "needs_login", "deleted")
        ]
        if not account_ids:
            retry_at = dt.datetime.utcnow() + dt.timedelta(minutes=5)
            db.execute(
                text("UPDATE automations SET next_run_at = :nxt WHERE id = :id"),
                {"nxt": retry_at, "id": automation_id},
            )
            log.warning(
                "PLAYLIST %s DEFER automation=%s sem conta elegível; "
                "índice preservado=%s next_run=%s",
                PLAYLIST_CODE,
                automation_id,
                automation.current_index,
                retry_at.isoformat(),
            )
            return {
                "deferred": True,
                "reason": "no_eligible_accounts",
                "id": automation_id,
                "code": PLAYLIST_CODE,
                "next_run_at": retry_at.isoformat(),
            }

    # Fase 2: lock CURTO só pro claim. NOWAIT = nunca segura o painel esperando.
    with session_scope() as db:
        try:
            automation = db.execute(
                select(Automation)
                .where(Automation.id == automation_id)
                .with_for_update(nowait=True)
            ).scalar_one_or_none()
        except Exception as exc:
            # LockNotAvailable / could not obtain lock
            msg = str(exc).lower()
            if "lock" in msg or "could not obtain" in msg:
                log.warning(
                    "PLAYLIST %s lock busy automation=%s — reagenda sem bloquear painel",
                    PLAYLIST_CODE,
                    automation_id,
                )
                execute_automation.apply_async(
                    args=[automation_id, scheduled_at],
                    countdown=15,
                )
                return {
                    "deferred": True,
                    "reason": "row_locked",
                    "id": automation_id,
                    "code": PLAYLIST_CODE,
                }
            raise

        if not automation:
            return {"error": "automation_not_found", "id": automation_id}
        if automation.status != "active":
            return {"skipped": True, "reason": "not_active", "code": PLAYLIST_CODE}

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
            scheduled_dt: dt.datetime | None = None
            if scheduled_at:
                try:
                    scheduled_dt = dt.datetime.fromisoformat(
                        str(scheduled_at).replace("Z", "+00:00")
                    )
                    if scheduled_dt.tzinfo is not None:
                        scheduled_dt = scheduled_dt.astimezone(dt.timezone.utc).replace(
                            tzinfo=None
                        )
                except ValueError:
                    scheduled_dt = None
            claimed = _claim_next_slot(
                db, automation, items, scheduled_at=scheduled_dt
            )
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
        # account_slot só para fingerprint invisível da mesma legenda (anti-drop Meta)
        publish_to_account.apply_async(
            args=[automation_id, account_id, acc_video_key, acc_queue_index],
            kwargs={
                "account_slot": i,
                "scheduled_at": scheduled_at,
                "enqueued_at": dt.datetime.utcnow().isoformat(),
                "dispatch_countdown": countdown,
            },
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
            "caption_fixed": True,
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
    result = _execute_publish(
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
    if result.get("deferred"):
        wait = max(20, min(int(result.get("wait_seconds") or 3600), 6 * 3600))
        log.info(
            "Meta defer publish_once account=%s wait=%ss reason=%s",
            account_id,
            wait,
            result.get("reason"),
        )

        def _sched_once(countdown: int) -> None:
            publish_once.apply_async(
                kwargs={
                    "account_id": account_id,
                    "video_key": video_key,
                    "thumb_key": thumb_key,
                    "caption": caption or "",
                    "content_type": content_type or "reel",
                    "story_link": story_link,
                    "story_sticker_text": story_sticker_text,
                    "story_layout": story_layout,
                    "camouflage_cover_key": camouflage_cover_key,
                    "camouflage_opacity": float(camouflage_opacity or 0.10),
                },
                countdown=countdown,
            )

        scheduled = _schedule_meta_defer_once(
            account_id=account_id,
            wait=wait,
            schedule_fn=_sched_once,
        )
        return {**result, "countdown": wait, "defer_scheduled": scheduled}
    return result


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
    scheduled_at: str | None = None,
    enqueued_at: str | None = None,
    dispatch_countdown: int = 0,
    **_compat_kwargs,
) -> dict:
    # _compat_kwargs: ignora caption_by_* de mensagens antigas na fila (não quebra o worker)
    started_at = dt.datetime.utcnow()
    # Sessão CURTA: só lê/copia. Rede Meta/proxy fica FORA (evita idle in transaction).
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

        slot = int(account_slot) if account_slot is not None else 0
        try:
            caption = resolve_caption(automation)
        except Exception:
            log.exception(
                "resolve_caption falhou automation=%s — usando caption principal",
                automation_id,
            )
            caption = automation.caption or ""

        caption = normalize_caption_text(caption) or best_available_caption(automation)
        caption = normalize_caption_text(caption)

        content_type = automation.content_type or "reel"
        username = account.username
        owner_user_id = automation.user_id
        if content_type in ("reel", "photo") and not caption:
            log.error(
                "PLAYLIST %s ABORT EMPTY CAPTION automation=%s account=%s — NÃO publica Reel/Foto sem legenda",
                PLAYLIST_CODE,
                automation_id,
                username,
            )
            _log_failure(
                automation_id,
                account.id,
                "Abortado: automação sem legenda (não publica Reel/Foto vazio)",
                content_type=content_type,
                owner_user_id=owner_user_id,
                username=username,
            )
            return {"error": "empty_caption", "aborted": True}

        # Mesma legenda em N contas: fingerprint invisível por slot (Meta dropa spam idêntico)
        if caption and content_type in ("reel", "photo"):
            caption = uniquify_caption_for_account(caption, slot)

        thumb_key = automation.thumb_key
        story_link = automation.story_link
        story_sticker_text = automation.story_sticker_text
        story_layout = _load_story_layout(automation)
        camouflage_cover_key = getattr(automation, "camouflage_cover_key", None)
        camouflage_opacity = float(getattr(automation, "camouflage_opacity", 0.25) or 0.25)
        posted_index = int(posted_index)

        if not caption:
            log.warning(
                "PLAYLIST %s EMPTY CAPTION automation=%s account=%s type=%s — seguindo (story)",
                PLAYLIST_CODE,
                automation_id,
                username,
                content_type,
            )
        else:
            log.info(
                "PLAYLIST %s CAPTION READY automation=%s account=%s cap_len=%s preview=%r",
                PLAYLIST_CODE,
                automation_id,
                username,
                len(caption),
                caption[:48],
            )

        log.info(
            "PLAYLIST %s publish automation=%s account=%s idx=%s slot=%s caption_fixed=True cap_len=%s key=%s camu=%s opacity=%.2f",
            PLAYLIST_CODE,
            automation_id,
            username,
            posted_index,
            slot,
            len(caption or ""),
            vk,
            camouflage_cover_key or "-",
            camouflage_opacity,
        )

    try:
        result = _execute_publish(
            automation_id=automation_id,
            account_id=account_id,
            video_key=vk,
            thumb_key=thumb_key,
            caption=caption,
            content_type=content_type,
            story_link=story_link,
            story_sticker_text=story_sticker_text,
            story_layout=story_layout,
            playlist_index=posted_index,
            camouflage_cover_key=camouflage_cover_key,
            camouflage_opacity=camouflage_opacity,
            scheduled_at=scheduled_at,
            started_at=started_at,
            enqueued_at=enqueued_at,
            dispatch_countdown=dispatch_countdown,
        )
    except Exception as exc:
        # Abort de legenda: NÃO retentar a task — cria outro Reel no ar
        # (a API não deixa apagar Reel recém-publicado).
        msg = str(exc)
        if "SEM legenda" in msg or "caption_missing_abort" in msg:
            log.error(
                "publish_to_account abort caption account=%s automation=%s: %s",
                account_id,
                automation_id,
                msg[:300],
            )
            _trip_meta_caption_circuit(ttl_sec=600)
            _mark_now_automation_failed(automation_id)
            return {"error": "caption_missing_abort", "detail": msg[:500]}
        if self.request.retries < self.max_retries:
            countdown = min(60 * (2 ** self.request.retries), 600)
            raise self.retry(exc=exc, countdown=countdown)
        _mark_now_automation_failed(automation_id)
        _notify_publish_failure_once(
            account_id=account_id,
            owner_user_id=owner_user_id,
            username=username,
            content_type=content_type,
            error=msg,
        )
        return {"error": "publish_failed", "detail": msg[:500]}

    if result.get("deferred"):
        wait = max(20, min(int(result.get("wait_seconds") or 3600), 6 * 3600))
        reason = str(result.get("reason") or "meta_defer")
        record_meta_defer(reason)
        # Lag mede atraso do retry, não o slot original acumulado pelos defers.
        retry_scheduled_at = (
            dt.datetime.utcnow() + dt.timedelta(seconds=wait)
        ).isoformat()
        log.info(
            "Meta defer automation=%s account=%s wait=%ss reason=%s retry_at=%s",
            automation_id,
            account_id,
            wait,
            reason,
            retry_scheduled_at,
        )

        def _sched_acct(countdown: int) -> None:
            publish_to_account.apply_async(
                args=[automation_id, account_id, vk, posted_index],
                kwargs={
                    "account_slot": int(account_slot) if account_slot is not None else 0,
                    "scheduled_at": retry_scheduled_at,
                },
                countdown=countdown,
            )

        scheduled = _schedule_meta_defer_once(
            account_id=account_id,
            wait=wait,
            schedule_fn=_sched_acct,
        )
        return {**result, "countdown": wait, "defer_scheduled": scheduled}
    return result


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
    scheduled_at: str | dt.datetime | None = None,
    started_at: dt.datetime | None = None,
    enqueued_at: str | dt.datetime | None = None,
    dispatch_countdown: int = 0,
) -> dict:
    storage = get_storage()
    started_at = started_at or dt.datetime.utcnow()
    timing_base = _publish_timing(
        scheduled_at,
        started_at,
        enqueued_at=enqueued_at,
        dispatch_countdown=dispatch_countdown,
    )

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
        web_cookies = decrypt_web_cookies(account.encrypted_web_cookies)
        web_browser = decrypt_web_browser(
            getattr(account, "encrypted_web_browser", None)
        )
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

        # Instagrapi mobile sem liberação do dono: força sessão expirada e para o publish.
        if provider != "meta":
            from app.utils.instagrapi_access import (
                INSTAGRAPI_EXPIRED_MSG,
                can_use_instagrapi,
            )
            from models.models import User as _User

            has_web_csrf = bool(web_cookies and web_cookies.get("csrftoken"))
            if not has_web_csrf:
                owner = db.get(_User, owner_user_id)
                if not can_use_instagrapi(owner):
                    account.status = "needs_login"
                    account.last_error = INSTAGRAPI_EXPIRED_MSG
                    if account.session_json:
                        account.session_json = None
                    account_status = "needs_login"
                    log.info(
                        "publish blocked instagrapi_revoked account=%s @%s user=%s",
                        account_id,
                        username,
                        owner_user_id,
                    )

        meta_min_gap_min = 0
        if provider == "meta" and account_status not in (
            "paused", "needs_login", "proxy_down", "banned", "deleted"
        ):
            # "Postar agora" (start_mode=now): o usuário pediu imediato — não reagenda.
            # Só o agendamento recorrente (beat) respeita o piso de 60 min por conta.
            # Anti-spam Redis (cooldown/inflight) vale sempre — evita 2 posts de uma vez.
            force_now = False
            if automation_id is not None:
                auto_row = db.get(Automation, automation_id)
                force_now = bool(auto_row and (auto_row.start_mode or "") == "now")
            anti = get_anti_farm_prefs_by_id(db, owner_user_id) if owner_user_id else {}
            warmup_on = bool(anti.get("meta_warmup_enabled", True))
            meta_min_gap_min = meta_min_interval_for_account(
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
                meta_min_gap_min = _META_FLOOR
            if force_now:
                log.info(
                    "Meta interval bypass (Postar agora) automation=%s account=%s",
                    automation_id,
                    account_id,
                )
            elif meta_min_gap_min > 0:
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
                    if age_min < meta_min_gap_min:
                        wait_left = max(1, int(meta_min_gap_min - age_min) + 1)
                        # Não marca Ignorada — remarca o post para quando a conta puder.
                        return {
                            "deferred": True,
                            "wait_seconds": wait_left * 60,
                            "reason": (
                                f"meta_defer:{meta_min_gap_min}m "
                                f"(reagendado +{wait_left} min; "
                                f"último post há {int(age_min)} min)"
                            ),
                            "account_id": account_id,
                            "automation_id": automation_id,
                        }

    if account_status in ("paused", "needs_login", "proxy_down", "banned", "deleted"):
        log.info(
            "publish skipped provider=%s account=%s @%s status=%s",
            provider,
            account_id,
            username,
            account_status,
        )
        return {"skipped": True, "reason": f"account_{account_status}", "provider": provider}

    if provider == "meta":
        log.info(
            "PUBLISH provider=meta (API oficial) account=%s @%s automation=%s",
            account_id,
            username,
            automation_id,
        )
        if not user_meta_app_id:
            _mark_account_needs_login(
                account_id,
                "Conta sem app Meta vinculado. Cadastre em Meus Apps e reconecte.",
            )
            return {"error": "meta_app_missing"}
        if not meta_access_token or not meta_ig_user_id:
            _mark_account_needs_login(account_id, "Token da API oficial ausente. Reconecte a conta.")
            return {"error": "meta_token_missing"}

        meta_proxy = (proxy or "").strip() or None
        if (
            meta_proxy
            and not getattr(settings, "meta_http_mock", False)
            and not check_proxy(meta_proxy)
        ):
            log.warning(
                "META proxy inválida account=%s @%s — publicando sem proxy (IP do servidor)",
                account_id,
                username,
            )
            meta_proxy = None
        if meta_proxy:
            log.info(
                "META via proxy residencial account=%s @%s",
                account_id,
                username,
            )
        else:
            log.info(
                "META sem proxy account=%s @%s — Graph pelo IP do servidor",
                account_id,
                username,
            )

        cooldown_sec = max(60, int(meta_min_gap_min or 60) * 60)
        can_pub, wait_sec, claim_reason = _claim_meta_publish_slot(
            account_id, cooldown_sec, user_id=owner_user_id
        )
        if not can_pub:
            return {
                "deferred": True,
                "wait_seconds": wait_sec,
                "reason": (
                    f"meta_anti_spam:{claim_reason} "
                    f"(fila Meta; reagendado +{max(1, wait_sec // 60)} min)"
                ),
                "account_id": account_id,
                "automation_id": automation_id,
            }

        publish_key = video_key
        temp_camu_key: str | None = None
        tmp_dir: Path | None = None
        async_submitted = False
        try:
            if (
                camouflage_cover_key
                and (content_type or "reel") == "reel"
                and Path(video_key).suffix.lower() in VIDEO_EXT
            ):
                tmp_dir = Path(tempfile.mkdtemp(prefix="meta_camu_"))
                try:
                    with ffmpeg_slot():
                        camu_path = _render_camouflage_reel(
                            storage,
                            video_key=video_key,
                            camouflage_cover_key=camouflage_cover_key,
                            camouflage_opacity=float(camouflage_opacity or 0.25),
                            tmp_dir=tmp_dir,
                            automation_id=automation_id,
                        )
                        # Limpa metadados antes da Meta baixar o arquivo público.
                        clean_path = tmp_dir / "camu_clean.mp4"
                        try:
                            upload_path, _ = prepare_clean_media(
                                camu_path,
                                clean_path,
                                content_type="reel",
                                account_hint=username,
                            )
                        except MetadataStripError:
                            upload_path = camu_path
                    temp_camu_key = _upload_temp_media(storage, upload_path, suggested_ext=".mp4")
                    publish_key = temp_camu_key
                    log.info(
                        "CAMOUFLAGE meta uploaded automation=%s temp_key=%s",
                        automation_id,
                        temp_camu_key,
                    )
                except Exception as camu_exc:
                    log.exception(
                        "Camuflagem Meta falhou automation=%s key=%s",
                        automation_id,
                        camouflage_cover_key,
                    )
                    create_notification(
                        owner_user_id,
                        "Falha na camuflagem do Reel",
                        f"@{username}: {camu_exc}",
                        kind="warning",
                        link="/logs",
                    )
                    _log_failure(
                        automation_id,
                        account_id,
                        f"camuflagem: {camu_exc}",
                        content_type=content_type,
                        owner_user_id=owner_user_id,
                        username=username,
                    )
                    _release_meta_inflight(account_id, owner_user_id)
                    raise

            try:
                reel_cover = thumb_key if (content_type or "reel") == "reel" else None
                log.info(
                    "META publish start account=%s automation=%s thumb_key=%s cover_will_send=%s async=%s",
                    username,
                    automation_id,
                    reel_cover or "-",
                    bool(reel_cover),
                    bool(getattr(settings, "meta_async_submit_poll", True)),
                )
                use_async = bool(getattr(settings, "meta_async_submit_poll", True))
                if use_async:
                    import time as _time
                    import uuid

                    t0 = _time.perf_counter()
                    submitted = submit_media_container(
                        access_token=meta_access_token,
                        ig_user_id=meta_ig_user_id,
                        media_key=publish_key,
                        content_type=content_type,
                        caption=caption,
                        cover_key=reel_cover,
                        proxy=meta_proxy,
                    )
                    submit_sec = max(0.0, _time.perf_counter() - t0)
                    record_meta_poll_sample(submit_seconds=submit_sec)
                    job_token = uuid.uuid4().hex
                    submitted_at = str(
                        submitted.get("submitted_at")
                        or dt.datetime.utcnow().isoformat()
                    )
                    try:
                        _save_meta_pending(
                            account_id,
                            {
                                "job_token": job_token,
                                "container_id": str(submitted.get("container_id") or ""),
                                "automation_id": automation_id,
                                "account_id": account_id,
                                "owner_user_id": owner_user_id,
                                "username": username,
                                "video_key": video_key,
                                "publish_key": publish_key,
                                "temp_camu_key": temp_camu_key,
                                "thumb_key": thumb_key,
                                "content_type": content_type or "reel",
                                "caption": caption or "",
                                "meta_proxy": meta_proxy,
                                "cooldown_sec": cooldown_sec,
                                "playlist_index": playlist_index,
                                "camouflage_applied": bool(
                                    camouflage_cover_key
                                    and (content_type or "reel") == "reel"
                                ),
                                "cover_applied": bool(submitted.get("cover_applied")),
                                "cover_error": submitted.get("cover_error"),
                                "caption_sent_len": submitted.get("caption_sent_len"),
                                "caption_verified": submitted.get("caption_verified"),
                                "scheduled_at": (
                                    timing_base.get("scheduled_at").isoformat()
                                    if isinstance(
                                        timing_base.get("scheduled_at"), dt.datetime
                                    )
                                    else timing_base.get("scheduled_at")
                                ),
                                "started_at": (
                                    timing_base.get("started_at").isoformat()
                                    if isinstance(
                                        timing_base.get("started_at"), dt.datetime
                                    )
                                    else timing_base.get("started_at")
                                ),
                                "enqueued_at": enqueued_at,
                                "dispatch_countdown": dispatch_countdown,
                                "submitted_at": submitted_at,
                                "submit_seconds": submit_sec,
                                "poll_count": 0,
                            },
                        )
                    except Exception:
                        _release_meta_inflight(account_id, owner_user_id)
                        _cleanup_temp_camu_key(temp_camu_key)
                        raise
                    _refresh_meta_slots(account_id, owner_user_id)
                    meta_poll_container.apply_async(
                        args=[account_id, job_token],
                        countdown=_meta_poll_countdown(0),
                    )
                    async_submitted = True
                    log.info(
                        "META submit async account=%s container=%s wait_poll=%ss",
                        account_id,
                        submitted.get("container_id"),
                        _meta_poll_countdown(0),
                    )
                    return {
                        "ok": True,
                        "submitted": True,
                        "provider": "meta",
                        "container_id": submitted.get("container_id"),
                        "playlist_code": PLAYLIST_CODE,
                        "playlist_index": playlist_index,
                        "video_key": video_key,
                        "camouflage_applied": bool(
                            camouflage_cover_key and (content_type or "reel") == "reel"
                        ),
                    }

                result = publish_meta_media(
                    access_token=meta_access_token,
                    ig_user_id=meta_ig_user_id,
                    media_key=publish_key,
                    content_type=content_type,
                    caption=caption,
                    # Thumb da automação = capa do Reel (cover_url) + caption juntos.
                    # Sem delete/republicar: a API não apaga Reel novo (100/33).
                    cover_key=reel_cover,
                    proxy=meta_proxy,
                )
            except MetaInstagramError as exc:
                _release_meta_inflight(account_id, owner_user_id)
                # notify=False: evita spam no celular a cada retry do Celery.
                # A notificação final fica em publish_to_account / dedupe Redis.
                _log_failure(
                    automation_id,
                    account_id,
                    f"API oficial: {exc}",
                    content_type=content_type,
                    owner_user_id=owner_user_id,
                    username=username,
                    notify=False,
                )
                if "SEM legenda" in str(exc) or getattr(exc, "error_type", None) == "caption_missing_abort":
                    _notify_publish_failure_once(
                        account_id=account_id,
                        owner_user_id=owner_user_id,
                        username=username,
                        content_type=content_type,
                        error=str(exc),
                    )
                if exc.code in (102, 190):
                    _mark_account_needs_login(account_id, str(exc))
                    return {"error": "meta_auth"}
                # Conta IG restringida / checkpoint pela Meta (API code 25 / 2207050)
                if exc.code == 25 or exc.subcode == 2207050 or _meta_user_restricted(exc):
                    _mark_account_meta_restricted(account_id, str(exc))
                    return {"error": "meta_restricted"}
                # Story: permissão / tipo de mídia — deixa o motivo no log (já gravado)
                if (content_type or "") == "story":
                    log.warning(
                        "META story fail account=%s @%s code=%s sub=%s err=%s",
                        account_id,
                        username,
                        getattr(exc, "code", None),
                        getattr(exc, "subcode", None),
                        exc,
                    )
                    return {"error": "meta_story", "detail": str(exc)}
                raise
            except Exception:
                _release_meta_inflight(account_id, owner_user_id)
                raise
        finally:
            # Async: Meta ainda baixa o MP4 — só limpa temp no poll finalize/timeout.
            if temp_camu_key and not async_submitted:
                try:
                    storage.delete(temp_camu_key)
                except Exception:
                    log.warning("Falha ao apagar temp camuflagem key=%s", temp_camu_key)
            if tmp_dir is not None:
                shutil.rmtree(tmp_dir, ignore_errors=True)

        if async_submitted:
            return {"ok": True, "submitted": True, "provider": "meta"}

        cover_applied = bool(result.get("cover_applied"))
        cover_error = str(result.get("cover_error") or "")
        log.info(
            "META capa RESULT account=%s thumb=%s cover_applied=%s cover_error=%r caption_ok=%s url=%s",
            username,
            thumb_key or "-",
            cover_applied,
            cover_error or None,
            result.get("caption_verified"),
            result.get("url"),
        )
        # Só avisa se a automação TEM thumb e a capa não ficou no post.
        if (content_type or "reel") == "reel" and thumb_key and not cover_applied:
            log.warning(
                "META REEL sem capa final account=%s key=%s erro=%s",
                username,
                thumb_key,
                cover_error or "cover_applied=False",
            )
            create_notification(
                owner_user_id,
                "Reels publicado sem a capa personalizada",
                f"@{username}: {(cover_error or 'capa não aplicada')[:160]}",
                kind="warning",
                link="/logs",
            )

        # publish_meta_media: True = confirmado; None = Graph atrasou o campo
        # (caption já enviada no /media — aceitamos como sucesso com aviso).
        caption_verified = result.get("caption_verified")
        via_comment = caption_verified == "via_comment"
        if via_comment:
            caption_ok: bool | None = True
        elif caption_verified is True:
            caption_ok = True
        elif caption_verified is None and (content_type or "reel") in ("reel", "photo"):
            caption_ok = None
        else:
            caption_ok = False
        if via_comment:
            log.warning(
                "META caption via COMMENT account=%s media=%s — Graph dropou caption",
                username,
                result.get("id"),
            )
            create_notification(
                owner_user_id,
                "Reels: legenda foi no comentário",
                f"@{username}: a Meta publicou sem caption (bug da Graph). "
                "Postamos o texto como 1º comentário.",
                kind="warning",
                link=str(result.get("url") or "/logs"),
            )
        elif (content_type or "reel") in ("reel", "photo") and caption_verified is None:
            log.warning(
                "META caption não indexou a tempo account=%s media=%s — sucesso com aviso",
                username,
                result.get("id"),
            )
            create_notification(
                owner_user_id,
                "Reels: legenda ainda não apareceu na Graph",
                f"@{username}: publicamos com caption, mas a Meta demorou a indexar. "
                "Confira o post no Instagram.",
                kind="warning",
                link=str(result.get("url") or "/logs"),
            )
        elif (content_type or "reel") in ("reel", "photo") and caption_ok is not True:
            caption_ok = False
            log.error(
                "META REEL SEM LEGENDA CONFIRMADA account=%s media=%s",
                username,
                result.get("id"),
            )
            create_notification(
                owner_user_id,
                "Reels SEM legenda — abortado",
                f"@{username}: a Meta confirmou caption vazia. O post não foi aceito como sucesso.",
                kind="error",
                link=str(result.get("url") or "/logs"),
            )

        publish_log_id: int | None = None
        log_status = "success"
        log_error = None
        if (content_type or "reel") in ("reel", "photo") and caption_ok is False:
            log_status = "failed"
            log_error = "Abortado: Reel/Foto sem legenda confirmada"
            caption_ok = False

        with session_scope() as db:
            acc = db.get(InstagramAccount, account_id)
            if acc:
                acc.last_login_at = dt.datetime.utcnow()
                acc.status = "active"
                acc.last_error = None
            auto = _bump_automation_run_counters(db, automation_id)
            timing = _publish_timing(
                timing_base.get("scheduled_at"),
                timing_base.get("started_at"),
                enqueued_at=enqueued_at,
                dispatch_countdown=dispatch_countdown,
            )
            plog = PublishLog(
                automation_id=automation_id,
                account_id=account_id,
                status=log_status,
                content_type=content_type or "reel",
                media_id=result.get("id"),
                media_url=result.get("url"),
                video_key=video_key,
                caption_ok=caption_ok if (content_type or "reel") in ("reel", "photo") else None,
                error=log_error,
                scheduled_at=timing.get("scheduled_at"),
                started_at=timing.get("started_at"),
                schedule_lag_seconds=timing.get("schedule_lag_seconds"),
                duration_seconds=timing.get("duration_seconds"),
                queue_wait_seconds=timing.get("queue_wait_seconds"),
            )
            db.add(plog)
            db.flush()
            publish_log_id = plog.id
            if auto and (auto.start_mode or "") == "now":
                _complete_now_automation_if_ready(db, auto)
        _record_timing_metrics(timing, log_status)

        if log_status == "success":
            _mark_meta_published(account_id, cooldown_sec, owner_user_id)
            notify_publish_success(
                owner_user_id,
                username,
                content_type=content_type or "reel",
                publish_log_id=publish_log_id,
            )
        else:
            _release_meta_inflight(account_id, owner_user_id)
        return {
            "ok": log_status == "success",
            "provider": "meta",
            "playlist_code": PLAYLIST_CODE,
            "playlist_index": playlist_index,
            "video_key": video_key,
            "camouflage_applied": bool(camouflage_cover_key and (content_type or "reel") == "reel"),
            "caption_ok": caption_ok,
            **result,
        }

    log.info(
        "PUBLISH provider=%s account=%s @%s automation=%s",
        "aiograpi" if provider == "aiograpi" else "instagrapi",
        account_id,
        username,
        automation_id,
    )

    use_aiograpi = provider == "aiograpi"

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
        if password and username:
            try:
                if use_aiograpi:
                    settings_dict = aio_ig.try_refresh_session(
                        settings_dict=None,
                        proxy=proxy,
                        username=username,
                        password=password,
                    )
                else:
                    settings_dict = try_refresh_session(
                        settings_dict=None,
                        proxy=proxy,
                        username=username,
                        password=password,
                    )
                with session_scope() as db:
                    acc = db.get(InstagramAccount, account_id)
                    if acc:
                        acc.session_json = serialize_settings(settings_dict)
                        if not use_aiograpi:
                            new_sid = extract_sessionid_from_settings(settings_dict)
                            merged = merge_sessionid_into_web_cookies(
                                acc.encrypted_web_cookies, new_sid
                            )
                            if merged:
                                acc.encrypted_web_cookies = merged
                                web_cookies = decrypt_web_cookies(merged) or web_cookies
                        acc.status = "active"
                        acc.last_error = None
                        acc.last_login_at = dt.datetime.utcnow()
                log.info("publish auto-reconnect OK account=%s (sem session)", account_id)
            except (InstagramAuthError, InstagramTwoFactorRequired) as exc:
                _log_failure(
                    automation_id,
                    account_id,
                    f"sem sessão / re-login: {exc}",
                    content_type=content_type,
                    owner_user_id=owner_user_id,
                    username=username,
                )
                _mark_account_needs_login(account_id, str(exc))
                return {"error": "no_session"}
        else:
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
                try:
                    with ffmpeg_slot():
                        work_path = _render_camouflage_reel(
                            storage,
                            video_key=video_key,
                            camouflage_cover_key=camouflage_cover_key,
                            camouflage_opacity=float(camouflage_opacity or 0.25),
                            tmp_dir=tmp_dir,
                            automation_id=automation_id,
                            video_path=raw_path,
                        )
                except Exception as camu_exc:
                    log.exception(
                        "Camuflagem falhou automation=%s key=%s",
                        automation_id,
                        camouflage_cover_key,
                    )
                    create_notification(
                        owner_user_id,
                        "Falha na camuflagem do Reel",
                        f"@{username}: {camu_exc}",
                        kind="warning",
                        link="/logs",
                    )
                    # Com camuflagem pedida, não publica o original sem overlay
                    _log_failure(
                        automation_id,
                        account_id,
                        f"camuflagem: {camu_exc}",
                        content_type=content_type,
                        owner_user_id=owner_user_id,
                        username=username,
                    )
                    raise

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
            if use_aiograpi:
                settings_dict = aio_ig.try_refresh_session(
                    settings_dict=settings_dict,
                    proxy=proxy,
                    username=username,
                    password=password,
                )
                with session_scope() as db:
                    acc = db.get(InstagramAccount, account_id)
                    if acc:
                        acc.session_json = serialize_settings(settings_dict)
            else:
                settings_dict = try_refresh_session(
                    settings_dict=settings_dict,
                    proxy=proxy,
                    username=username,
                    password=password,
                )
                cl = get_ready_client(
                    settings_dict=settings_dict,
                    proxy=proxy,
                    username=username,
                    password=password,
                )
                with session_scope() as db:
                    acc = db.get(InstagramAccount, account_id)
                    if acc:
                        acc.session_json = serialize_settings(settings_dict)
                        new_sid = extract_sessionid_from_settings(settings_dict)
                        merged = merge_sessionid_into_web_cookies(
                            acc.encrypted_web_cookies, new_sid
                        )
                        if merged:
                            acc.encrypted_web_cookies = merged
                            web_cookies = decrypt_web_cookies(merged) or web_cookies
        except (InstagramAuthError, InstagramTwoFactorRequired) as exc:
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
            if use_aiograpi:
                if content_type == "story":
                    result = aio_ig.publish_story(
                        settings_dict,
                        proxy,
                        publish_path,
                        link_url=story_link,
                        thumbnail_path=thumb_path,
                        username=username,
                    )
                elif content_type == "photo":
                    result = aio_ig.publish_photo(
                        settings_dict, proxy, clean_path, caption, username=username
                    )
                else:
                    result = aio_ig.publish_reel(
                        settings_dict,
                        proxy,
                        clean_path,
                        caption,
                        thumbnail_path=thumb_path,
                        username=username,
                    )
            elif content_type == "story":
                result = publish_story(
                    cl,
                    publish_path,
                    link_url=story_link,
                    thumbnail_path=thumb_path,
                    sticker_text=story_sticker_text,
                    story_layout=story_layout,
                    web_cookies=web_cookies,
                    browser=web_browser,
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
                    browser=web_browser,
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
                if use_aiograpi:
                    acc.session_json = serialize_settings(settings_dict)
                else:
                    acc.session_json = serialize_settings(cl.get_settings())
                acc.last_login_at = dt.datetime.utcnow()
                acc.status = "active"
                acc.last_error = None

            if automation_id is not None:
                auto = _bump_automation_run_counters(db, automation_id)
            else:
                auto = None

            plog = PublishLog(
                automation_id=automation_id,
                account_id=account_id,
                status="success",
                content_type=content_type or "reel",
                media_id=result.get("id"),
                media_url=result.get("url"),
                video_key=video_key,
                # instagrapi: enviamos caption → assumimos OK (API não devolve verificação)
                caption_ok=True if (caption or "").strip() and (content_type or "reel") in ("reel", "photo") else None,
                metadata_fingerprint=(meta_info or {}).get("fingerprint"),
                raw_sha256=(meta_info or {}).get("raw_sha256"),
                clean_sha256=(meta_info or {}).get("clean_sha256"),
                clean_size=int((meta_info or {}).get("clean_size") or 0) or None,
                **{
                    k: v
                    for k, v in _publish_timing(
                        timing_base.get("scheduled_at"),
                        timing_base.get("started_at"),
                        enqueued_at=enqueued_at,
                        dispatch_countdown=dispatch_countdown,
                    ).items()
                },
            )
            db.add(plog)
            db.flush()
            publish_log_id = plog.id
            notify_user_id = acc.user_id if acc else owner_user_id
            notify_username = acc.username if acc else username
        _record_timing_metrics(
            _publish_timing(
                timing_base.get("scheduled_at"),
                timing_base.get("started_at"),
                enqueued_at=enqueued_at,
                dispatch_countdown=dispatch_countdown,
            ),
            "success",
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
    notify: bool = True,
    scheduled_at: str | dt.datetime | None = None,
    started_at: dt.datetime | None = None,
) -> None:
    from sqlalchemy.exc import IntegrityError

    uid = owner_user_id
    uname = username
    aid = automation_id
    timing = _publish_timing(scheduled_at, started_at)
    with session_scope() as db:
        if aid is not None and db.get(Automation, aid) is None:
            aid = None
        try:
            db.add(
                PublishLog(
                    automation_id=aid,
                    account_id=account_id,
                    status="failed",
                    content_type=content_type,
                    error=error[:2000],
                    scheduled_at=timing.get("scheduled_at"),
                    started_at=timing.get("started_at"),
                    schedule_lag_seconds=timing.get("schedule_lag_seconds"),
                    duration_seconds=timing.get("duration_seconds"),
                )
            )
            db.flush()
        except IntegrityError:
            db.rollback()
            log.warning(
                "publish_log FK falhou automation_id=%s — gravando sem automação",
                automation_id,
            )
            db.add(
                PublishLog(
                    automation_id=None,
                    account_id=account_id,
                    status="failed",
                    content_type=content_type,
                    error=error[:2000],
                    scheduled_at=timing.get("scheduled_at"),
                    started_at=timing.get("started_at"),
                    schedule_lag_seconds=timing.get("schedule_lag_seconds"),
                    duration_seconds=timing.get("duration_seconds"),
                )
            )
        if uid is None or uname is None:
            acc = db.get(InstagramAccount, account_id)
            if acc:
                uid = uid or acc.user_id
                uname = uname or acc.username

    _record_timing_metrics(timing, "failed")

    if uid and notify:
        _notify_publish_failure_once(
            account_id=account_id,
            owner_user_id=uid,
            username=uname,
            content_type=content_type,
            error=error,
        )


def _notify_publish_failure_once(
    *,
    account_id: int,
    owner_user_id: int | None,
    username: str | None,
    content_type: str | None,
    error: str,
    ttl_sec: int = 1800,
) -> None:
    """No máximo 1 push/notificação de erro por conta a cada 30 min."""
    from core.notifications import content_label, create_notification

    if not owner_user_id:
        return
    client = _redis()
    key = f"notif:publish_fail:{int(account_id)}"
    if client is not None:
        try:
            if not client.set(key, "1", nx=True, ex=max(60, int(ttl_sec))):
                log.info(
                    "skip duplicate publish-fail notification account=%s",
                    account_id,
                )
                return
        except Exception as exc:
            log.warning("notify dedupe falhou account=%s: %s", account_id, exc)
    label = content_label(content_type)
    create_notification(
        owner_user_id,
        f"Erro ao publicar {label}",
        f"@{username or '?'}: {error[:180]}",
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
            f"Sessão expirada: @{uname}",
            reason[:200] or "A conta precisa ser reconectada para voltar a publicar.",
            kind="offline",
            link="/accounts/connected",
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
            f"Proxy fora: @{uname}",
            reason[:200] or "Proxy inválido ou fora do ar — atualize em Contas conectadas.",
            kind="offline",
            link="/accounts/connected",
        )


def _fail_meta_pending_job(
    *,
    account_id: int,
    job: dict,
    error: str,
    timed_out: bool = False,
) -> dict:
    """Libera slots, limpa pending/temp e registra falha."""
    automation_id = job.get("automation_id")
    owner_user_id = job.get("owner_user_id")
    username = job.get("username")
    content_type = job.get("content_type") or "reel"
    _clear_meta_pending(account_id)
    _cleanup_temp_camu_key(job.get("temp_camu_key"))
    _release_meta_inflight(account_id, owner_user_id if owner_user_id else None)
    if timed_out:
        record_meta_poll_sample(timed_out=True)
    _log_failure(
        automation_id,
        account_id,
        error,
        content_type=content_type,
        owner_user_id=owner_user_id,
        username=username,
        notify=False,
    )
    _notify_publish_failure_once(
        account_id=account_id,
        owner_user_id=owner_user_id,
        username=username,
        content_type=content_type,
        error=error,
    )
    return {"error": "meta_poll_failed", "detail": error[:300], "timed_out": timed_out}


def _complete_meta_pending_success(*, account_id: int, job: dict, result: dict) -> dict:
    """Persistência + cooldown após media_publish async.

    Grava PublishLog ANTES de notificações/side-effects — o post já está no IG.
    """
    automation_id = job.get("automation_id")
    try:
        automation_id = int(automation_id) if automation_id is not None else None
    except (TypeError, ValueError):
        automation_id = None
    owner_user_id = job.get("owner_user_id")
    try:
        owner_user_id = int(owner_user_id) if owner_user_id is not None else None
    except (TypeError, ValueError):
        owner_user_id = None
    username = str(job.get("username") or "")
    content_type = job.get("content_type") or "reel"
    video_key = job.get("video_key")
    thumb_key = job.get("thumb_key")
    cooldown_sec = int(job.get("cooldown_sec") or 3600)
    playlist_index = job.get("playlist_index")
    cover_applied = bool(job.get("cover_applied") or result.get("cover_applied"))
    cover_error = str(job.get("cover_error") or "")
    result = {
        **result,
        "cover_applied": cover_applied,
        "cover_error": cover_error or None,
        "caption_sent_len": job.get("caption_sent_len"),
        "caption_verified": job.get("caption_verified"),
    }

    caption_verified = result.get("caption_verified")
    via_comment = caption_verified == "via_comment"
    if via_comment:
        caption_ok: bool | None = True
    elif caption_verified is True:
        caption_ok = True
    elif caption_verified is None and content_type in ("reel", "photo"):
        caption_ok = None
    else:
        caption_ok = False

    publish_log_id: int | None = None
    log_status = "success"
    log_error = None
    if content_type in ("reel", "photo") and caption_ok is False:
        log_status = "failed"
        log_error = "Abortado: Reel/Foto sem legenda confirmada"

    try:
        timing = _publish_timing(
            job.get("scheduled_at"),
            job.get("started_at"),
            enqueued_at=job.get("enqueued_at"),
            dispatch_countdown=int(job.get("dispatch_countdown") or 0),
        )
    except Exception:
        log.exception(
            "META async timing falhou account=%s — gravando PublishLog sem métricas",
            account_id,
        )
        timing = {
            "scheduled_at": None,
            "started_at": dt.datetime.utcnow(),
            "schedule_lag_seconds": None,
            "duration_seconds": None,
            "queue_wait_seconds": None,
        }

    # 1) Persistência do log — prioridade absoluta (post já está no Instagram).
    try:
        with session_scope() as db:
            acc = db.get(InstagramAccount, account_id)
            if acc:
                acc.last_login_at = dt.datetime.utcnow()
                acc.status = "active"
                acc.last_error = None
            auto = _bump_automation_run_counters(db, automation_id)
            plog = PublishLog(
                automation_id=automation_id,
                account_id=account_id,
                status=log_status,
                content_type=content_type,
                media_id=str(result.get("id") or "")[:64] or None,
                media_url=str(result.get("url") or "")[:512] or None,
                video_key=video_key,
                caption_ok=caption_ok if content_type in ("reel", "photo") else None,
                error=log_error,
                scheduled_at=timing.get("scheduled_at"),
                started_at=timing.get("started_at"),
                schedule_lag_seconds=timing.get("schedule_lag_seconds"),
                duration_seconds=timing.get("duration_seconds"),
                queue_wait_seconds=timing.get("queue_wait_seconds"),
            )
            db.add(plog)
            db.flush()
            publish_log_id = plog.id
            if auto and (auto.start_mode or "") == "now":
                try:
                    _complete_now_automation_if_ready(db, auto)
                except Exception:
                    log.exception(
                        "complete_now_automation falhou automation=%s", automation_id
                    )
    except Exception:
        log.exception(
            "CRITICAL: PublishLog async falhou após media_publish account=%s media=%s",
            account_id,
            result.get("id"),
        )
        # Ainda libera slots / limpa pending para não travar a conta.
        _clear_meta_pending(account_id)
        _cleanup_temp_camu_key(job.get("temp_camu_key"))
        _release_meta_inflight(account_id, owner_user_id)
        raise

    try:
        _record_timing_metrics(timing, log_status)
    except Exception:
        pass

    _clear_meta_pending(account_id)
    _cleanup_temp_camu_key(job.get("temp_camu_key"))

    log.info(
        "META capa RESULT (async) account=%s thumb=%s cover_applied=%s cover_error=%r url=%s log_id=%s",
        username,
        thumb_key or "-",
        cover_applied,
        cover_error or None,
        result.get("url"),
        publish_log_id,
    )

    # 2) Notificações — nunca podem impedir o log (já gravado).
    try:
        if content_type == "reel" and thumb_key and not cover_applied:
            create_notification(
                owner_user_id,
                "Reels publicado sem a capa personalizada",
                f"@{username}: {(cover_error or 'capa não aplicada')[:160]}",
                kind="warning",
                link="/logs",
            )
        if content_type in ("reel", "photo") and caption_verified is None:
            create_notification(
                owner_user_id,
                "Reels: legenda ainda não apareceu na Graph",
                f"@{username}: publicamos com caption, mas a Meta demorou a indexar. "
                "Confira o post no Instagram.",
                kind="warning",
                link=str(result.get("url") or "/logs"),
            )
    except Exception:
        log.exception("notificação pós-publish async falhou account=%s", account_id)

    if log_status == "success":
        _mark_meta_published(account_id, cooldown_sec, owner_user_id)
        try:
            notify_publish_success(
                owner_user_id,
                username,
                content_type=content_type,
                publish_log_id=publish_log_id,
            )
        except Exception:
            log.exception("notify_publish_success async falhou account=%s", account_id)
    else:
        _release_meta_inflight(account_id, owner_user_id)

    return {
        "ok": log_status == "success",
        "provider": "meta",
        "async": True,
        "playlist_code": PLAYLIST_CODE,
        "playlist_index": playlist_index,
        "video_key": video_key,
        "camouflage_applied": bool(job.get("camouflage_applied")),
        "caption_ok": caption_ok,
        "publish_log_id": publish_log_id,
        **result,
    }


@celery_app.task(
    name="celery_app.tasks.publish.meta_poll_container",
    bind=True,
    max_retries=0,
)
def meta_poll_container(self, account_id: int, job_token: str) -> dict:
    """Consulta status do container Meta e finaliza ou re-agenda (backoff)."""
    _ = self
    job = _load_meta_pending(account_id)
    if not job or str(job.get("job_token") or "") != str(job_token or ""):
        log.info(
            "meta_poll skip account=%s token=%s (pending ausente ou token antigo)",
            account_id,
            (job_token or "")[:8],
        )
        return {"skipped": True, "reason": "no_pending"}

    container_id = str(job.get("container_id") or "")
    if not container_id:
        return _fail_meta_pending_job(
            account_id=account_id, job=job, error="pending sem container_id"
        )

    submitted_at = _parse_scheduled_at(job.get("submitted_at")) or dt.datetime.utcnow()
    age = (dt.datetime.utcnow() - submitted_at).total_seconds()
    timeout = _meta_poll_timeout_sec()
    if age > timeout:
        return _fail_meta_pending_job(
            account_id=account_id,
            job=job,
            error=f"Meta poll timeout após {int(age)}s (limite {timeout}s)",
            timed_out=True,
        )

    owner_user_id = job.get("owner_user_id")
    meta_proxy = job.get("meta_proxy") or None
    _refresh_meta_slots(account_id, owner_user_id)

    with session_scope() as db:
        acc = db.get(InstagramAccount, account_id)
        if acc is None:
            return _fail_meta_pending_job(
                account_id=account_id, job=job, error="account_not_found"
            )
        token = decrypt_secret(acc.encrypted_meta_access_token or "")
        ig_user_id = (acc.meta_ig_user_id or "").strip()
        if not token or not ig_user_id:
            return _fail_meta_pending_job(
                account_id=account_id, job=job, error="meta_token_missing"
            )

    try:
        status, status_detail = get_container_status(
            container_id, token, proxy=meta_proxy if meta_proxy else None
        )
    except MetaInstagramError as exc:
        if exc.code in (102, 190):
            _mark_account_needs_login(account_id, str(exc))
        return _fail_meta_pending_job(
            account_id=account_id, job=job, error=f"poll status: {exc}"
        )

    poll_count = int(job.get("poll_count") or 0) + 1
    job["poll_count"] = poll_count

    if status in ("ERROR", "EXPIRED"):
        # A Meta às vezes marca ERROR cedo e depois FINISHED — tolera alguns polls.
        if status == "ERROR" and poll_count < 4:
            _save_meta_pending(account_id, job)
            countdown = _meta_poll_countdown(poll_count)
            meta_poll_container.apply_async(
                args=[account_id, job_token],
                countdown=countdown,
            )
            log.warning(
                "META poll ERROR early account=%s container=%s poll=%s detail=%r next=%ss",
                account_id,
                container_id,
                poll_count,
                status_detail,
                countdown,
            )
            return {
                "ok": True,
                "processing": True,
                "status": status,
                "status_detail": status_detail,
                "poll_count": poll_count,
                "countdown": countdown,
                "error_retry": True,
            }
        err = f"Container Meta status={status}"
        if status_detail:
            err = f"{err}: {status_detail}"
        return _fail_meta_pending_job(
            account_id=account_id,
            job=job,
            error=err,
        )

    if status not in ("FINISHED", "PUBLISHED"):
        _save_meta_pending(account_id, job)
        countdown = _meta_poll_countdown(poll_count)
        meta_poll_container.apply_async(
            args=[account_id, job_token],
            countdown=countdown,
        )
        log.info(
            "META poll PROCESSING account=%s container=%s poll=%s next=%ss age=%.0fs",
            account_id,
            container_id,
            poll_count,
            countdown,
            age,
        )
        return {
            "ok": True,
            "processing": True,
            "status": status,
            "poll_count": poll_count,
            "countdown": countdown,
        }

    process_sec = max(0.0, age)
    try:
        finalized = finalize_media_publish(
            access_token=token,
            ig_user_id=ig_user_id,
            container_id=container_id,
            proxy=meta_proxy if meta_proxy else None,
            settle_seconds=2.0,
            allow_not_ready=True,
        )
    except MetaInstagramError as exc:
        if exc.code in (102, 190):
            _mark_account_needs_login(account_id, str(exc))
        if exc.code == 25 or exc.subcode == 2207050 or _meta_user_restricted(exc):
            _mark_account_meta_restricted(account_id, str(exc))
        return _fail_meta_pending_job(
            account_id=account_id, job=job, error=f"finalize: {exc}"
        )

    if finalized.get("not_ready"):
        _save_meta_pending(account_id, job)
        countdown = _meta_poll_countdown(poll_count)
        meta_poll_container.apply_async(
            args=[account_id, job_token],
            countdown=countdown,
        )
        return {
            "ok": True,
            "processing": True,
            "status": "NOT_READY_9007",
            "poll_count": poll_count,
            "countdown": countdown,
        }

    ready_total = max(0.0, (dt.datetime.utcnow() - submitted_at).total_seconds())
    record_meta_poll_sample(
        process_seconds=process_sec,
        polls=poll_count,
        ready_total_seconds=ready_total,
    )
    try:
        return _complete_meta_pending_success(
            account_id=account_id, job=job, result=finalized
        )
    except Exception as exc:
        # Post já está no Instagram — último recurso para não sumir do painel.
        log.exception(
            "META complete após publish falhou account=%s media=%s: %s",
            account_id,
            finalized.get("id"),
            exc,
        )
        try:
            with session_scope() as db:
                plog = PublishLog(
                    automation_id=job.get("automation_id"),
                    account_id=account_id,
                    status="success",
                    content_type=job.get("content_type") or "reel",
                    media_id=str(finalized.get("id") or "")[:64] or None,
                    media_url=str(finalized.get("url") or "")[:512] or None,
                    video_key=job.get("video_key"),
                    error=f"log_recover: {exc}"[:500],
                )
                db.add(plog)
                db.flush()
                recover_id = plog.id
            _clear_meta_pending(account_id)
            _cleanup_temp_camu_key(job.get("temp_camu_key"))
            _mark_meta_published(
                account_id,
                int(job.get("cooldown_sec") or 3600),
                job.get("owner_user_id"),
            )
            log.info(
                "META PublishLog recovered after complete failure log_id=%s account=%s",
                recover_id,
                account_id,
            )
            return {
                "ok": True,
                "provider": "meta",
                "async": True,
                "recovered_log": True,
                "publish_log_id": recover_id,
                **finalized,
            }
        except Exception:
            log.exception(
                "META recover PublishLog ALSO failed account=%s media=%s",
                account_id,
                finalized.get("id"),
            )
            _clear_meta_pending(account_id)
            _release_meta_inflight(account_id, job.get("owner_user_id"))
            raise
