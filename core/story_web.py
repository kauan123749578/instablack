"""Publicação de Story com link nativo via API web (fluxo INSSIST / Opalite).

Diferença das outras vias do InstaBlack:
- Meta Graph API: Story oficial, sem sticker de link customizado.
- instagrapi mobile (tap_models): costuma gerar sticker genérico.
- Esta via: rupload_igphoto + web/create/configure_to_story + story_link_stickers
  (mesmo padrão validado no teste local / extensão Opalite).
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
from PIL import Image, ImageDraw, ImageFont, ImageOps

log = logging.getLogger(__name__)

IG_APP_ID = "936619743392459"
IG_WEB_ORIGIN = "https://www.instagram.com"
UPLOAD_PHOTO_URL = "https://i.instagram.com/rupload_igphoto/{entity_name}"
CONFIGURE_STORY_URL = (
    "https://www.instagram.com/api/v1/web/create/configure_to_story/"
)
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
WEB_ASBD_ID = "359341"
STORY_CANVAS = (1080, 1920)
DEFAULT_STICKER = {
    "x": 0.5,
    "y": 0.8,
    "width": 0.6,
    "height": 0.068625,
    "rotation": 0.0,
}
STICKER_STYLES = {
    "default": {"bg": (255, 255, 255, 245), "icon": (0, 151, 253, 255), "text": (0, 0, 0, 255)},
    "white": {"bg": (255, 255, 255, 128), "icon": (255, 255, 255, 255), "text": (255, 255, 255, 255)},
    "rainbow": {"bg": (255, 255, 255, 245), "icon": (255, 0, 0, 255), "text": (0, 0, 0, 255)},
    "solid": {"bg": (255, 255, 255, 245), "icon": (0, 151, 253, 255), "text": (0, 151, 253, 255)},
    "brand": {"bg": (255, 255, 255, 245), "icon": (207, 40, 118, 255), "text": (0, 0, 0, 255)},
    "black-text": {"bg": None, "icon": (0, 0, 0, 255), "text": (0, 0, 0, 255)},
    "white-text": {"bg": None, "icon": (255, 255, 255, 255), "text": (255, 255, 255, 255)},
}


def normalize_story_url(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    if not value.startswith(("http://", "https://")):
        value = f"https://{value}"
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"URL inválida: {value!r}")
    return value


def default_sticker_text(url: str) -> str:
    host = urlparse(url).hostname or ""
    if host.lower().startswith("www."):
        host = host[4:]
    text = host.upper()
    return text if len(text) <= 60 else text[:57] + "..."


def _paste_chain_link_icon(
    overlay: Image.Image,
    x: int,
    y: int,
    size: int,
    *,
    color: tuple[int, int, int, int],
) -> None:
    """Ícone de corrente azul (dois elos diagonais), estilo Instagram/Opalite."""
    stroke = max(2, round(size * 0.16))
    icon = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ink = ImageDraw.Draw(icon)
    ink.ellipse(
        (int(size * 0.05), int(size * 0.28), int(size * 0.55), int(size * 0.78)),
        outline=color,
        width=stroke,
    )
    ink.ellipse(
        (int(size * 0.45), int(size * 0.22), int(size * 0.95), int(size * 0.72)),
        outline=color,
        width=stroke,
    )
    rotated = icon.rotate(-35, resample=Image.Resampling.BICUBIC, expand=False)
    overlay.alpha_composite(rotated, (max(0, x), max(0, y)))


def cookies_from_client(cl: Any, *, require_csrf: bool = True) -> dict[str, str]:
    cookies: dict[str, str] = {}
    raw = getattr(cl, "cookie_dict", None) or {}
    if isinstance(raw, dict):
        cookies.update({str(k): str(v) for k, v in raw.items() if v is not None})

    sid = getattr(cl, "sessionid", None)
    if sid:
        cookies["sessionid"] = str(sid)

    try:
        settings = cl.get_settings() or {}
    except Exception:
        settings = {}
    auth = settings.get("authorization_data") or {}
    if isinstance(auth, dict) and auth.get("sessionid"):
        cookies.setdefault("sessionid", str(auth["sessionid"]))
    settings_cookies = settings.get("cookies") or {}
    if isinstance(settings_cookies, dict):
        for key, value in settings_cookies.items():
            if value is not None:
                cookies.setdefault(str(key), str(value))

    required = ["sessionid", "csrftoken"] if require_csrf else ["sessionid"]
    missing = [name for name in required if not cookies.get(name)]
    if missing:
        raise RuntimeError(
            "Sessão incompleta para Story web (faltam: "
            + ", ".join(missing)
            + "). Em Contas conectadas, importe o JSON completo do Cookie-Editor "
            "(sessionid + csrftoken + mid + ig_did…)."
        )
    return cookies


DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
# Header típico do Instagram web (Opalite/INSSIST no navegador).
WEB_ASBD_ID = "359341"


def merge_web_cookies(
    cl: Any,
    web_cookies: dict[str, str] | None = None,
) -> dict[str, str]:
    """Monta o jar para a API web.

    Se houver cookies importados do Cookie-Editor, eles têm prioridade total
    (não misturar com UA/sessão mobile do instagrapi).
    """
    if web_cookies:
        cookies = {
            str(k): str(v)
            for k, v in web_cookies.items()
            if k and v is not None and str(v).strip()
        }
    else:
        cookies = cookies_from_client(cl, require_csrf=False)

    missing = [name for name in ("sessionid", "csrftoken") if not cookies.get(name)]
    if missing:
        raise RuntimeError(
            "Sessão incompleta para Story web (faltam: "
            + ", ".join(missing)
            + "). Importe o JSON completo do Cookie-Editor na conta."
        )
    return cookies


def _web_user_agent(cl: Any) -> str:
    """Sempre desktop Chrome na API web — UA Android do instagrapi causa HTTP 400."""
    _ = cl
    return DEFAULT_UA


def build_web_session(
    cl: Any,
    web_cookies: dict[str, str] | None = None,
) -> requests.Session:
    cookies = merge_web_cookies(cl, web_cookies)
    session = requests.Session()
    session.headers.update(
        {
            "accept": "*/*",
            "accept-language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
            "user-agent": _web_user_agent(cl),
            "x-ig-app-id": IG_APP_ID,
            "x-asbd-id": WEB_ASBD_ID,
            "x-requested-with": "XMLHttpRequest",
            "origin": IG_WEB_ORIGIN,
            "referer": f"{IG_WEB_ORIGIN}/",
        }
    )
    for name, value in cookies.items():
        # path=/ domain=.instagram.com — igual ao Cookie-Editor
        session.cookies.set(name, value, domain=".instagram.com", path="/")
    csrf = cookies.get("csrftoken")
    if csrf:
        session.headers["x-csrftoken"] = csrf
    proxy = _client_proxy(cl)
    if proxy:
        session.proxies.update({"http": proxy, "https": proxy})
    return session


def _warmup_web_session(session: requests.Session) -> None:
    """Abre o Instagram web para alinhar claim/csrf antes do configure."""
    try:
        response = session.get(
            f"{IG_WEB_ORIGIN}/",
            timeout=30,
            allow_redirects=True,
        )
        # Atualiza csrftoken se o Instagram rotacionar no warm-up
        csrf = session.cookies.get("csrftoken", domain=".instagram.com") or session.cookies.get(
            "csrftoken"
        )
        if csrf:
            session.headers["x-csrftoken"] = csrf
        log.info(
            "Story web warmup: status=%s csrf=%s",
            response.status_code,
            "ok" if csrf else "missing",
        )
    except requests.RequestException as exc:
        log.warning("Story web warmup falhou (seguindo mesmo assim): %s", exc)


def _client_proxy(cl: Any) -> str | None:
    proxy = getattr(cl, "proxy", None)
    if isinstance(proxy, str) and proxy.strip():
        return proxy.strip()
    try:
        proxies = getattr(getattr(cl, "private", None), "proxies", None) or {}
        return proxies.get("https") or proxies.get("http")
    except Exception:
        return None


def _format_ig_error(response: requests.Response) -> str:
    body = (response.text or "")[:800]
    try:
        payload = response.json()
        message = (
            payload.get("message")
            or payload.get("error_type")
            or payload.get("status")
            or payload
        )
        return f"HTTP {response.status_code}: {message} | body={body}"
    except ValueError:
        return f"HTTP {response.status_code}: {body}"


def _request_with_retry(
    session: requests.Session,
    method: str,
    url: str,
    *,
    attempts: int = 3,
    **kwargs,
) -> requests.Response:
    last_error: Exception | None = None
    last_response: requests.Response | None = None
    for attempt in range(1, attempts + 1):
        try:
            response = session.request(method, url, timeout=90, **kwargs)
            last_response = response
            if response.status_code == 200:
                return response
            body = response.text[:400]
            retryable = "Transcode not finished" in body or response.status_code >= 500
            if not retryable or attempt == attempts:
                raise RuntimeError(_format_ig_error(response))
        except requests.RequestException as exc:
            last_error = exc
            if attempt == attempts:
                raise
        time.sleep(2)
    if last_response is not None:
        raise RuntimeError(_format_ig_error(last_response))
    raise RuntimeError(f"Falha na requisição web story: {last_error}")


def _load_font(size: int) -> ImageFont.ImageFont:
    candidates = [
        "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def compose_story_canvas(source: Path, *, cover: bool = False) -> Image.Image:
    with Image.open(source) as original:
        image = ImageOps.exif_transpose(original).convert("RGB")
    canvas = Image.new("RGB", STORY_CANVAS, (0, 0, 0))
    if cover:
        fitted = ImageOps.fit(
            image, STORY_CANVAS, method=Image.Resampling.LANCZOS, centering=(0.5, 0.5)
        )
        canvas.paste(fitted, (0, 0))
    else:
        contained = ImageOps.contain(image, STORY_CANVAS, method=Image.Resampling.LANCZOS)
        paste_x = (STORY_CANVAS[0] - contained.width) // 2
        paste_y = (STORY_CANVAS[1] - contained.height) // 2
        canvas.paste(contained, (paste_x, paste_y))
    return canvas


def draw_link_sticker(
    canvas: Image.Image,
    text: str,
    *,
    x: float,
    y: float,
    width: float,
    height: float,
    variant: str = "default",
) -> Image.Image:
    """Desenha o botão visual (a área clicável vem do story_link_stickers)."""
    style = STICKER_STYLES.get(variant) or STICKER_STYLES["default"]
    image = canvas.convert("RGBA")
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    box_w = max(40, int(width * image.width))
    box_h = max(28, int(height * image.height))
    left = int((x - width / 2) * image.width)
    top = int((y - height / 2) * image.height)
    right = left + box_w
    bottom = top + box_h
    radius = max(12, int(min(box_w, box_h) * 0.45))

    draw.rounded_rectangle(
        (left + 2, top + 3, right + 2, bottom + 3),
        radius=radius,
        fill=(0, 0, 0, 40),
    )
    if style["bg"] is not None:
        draw.rounded_rectangle(
            (left, top, right, bottom),
            radius=radius,
            fill=style["bg"],
        )

    label = (text or "LINK").replace("\n", " ").strip()
    if len(label) > 50:
        label = label[:50] + "..."
    font = _load_font(max(18, int(box_h * 0.42)))
    text_bbox = draw.textbbox((0, 0), label, font=font)
    text_w = text_bbox[2] - text_bbox[0]
    text_h = text_bbox[3] - text_bbox[1]
    icon_size = max(14, int(box_h * 0.38))
    gap = max(8, int(box_w * 0.03))
    content_w = icon_size + gap + text_w
    start_x = left + max(12, (box_w - content_w) // 2)
    icon_y = top + (box_h - icon_size) // 2
    _paste_chain_link_icon(
        overlay,
        start_x,
        icon_y,
        icon_size,
        color=style["icon"],
    )
    text_x = start_x + icon_size + gap
    text_y = top + (box_h - text_h) // 2 - 1
    if variant == "rainbow":
        # Aproximação do gradiente: faixas coloridas no texto
        colors = [
            (255, 69, 0, 255),
            (255, 215, 0, 255),
            (50, 205, 50, 255),
            (30, 144, 255, 255),
            (173, 59, 255, 255),
        ]
        char_x = text_x
        for index, char in enumerate(label):
            color = colors[index % len(colors)]
            draw.text((char_x, text_y), char, font=font, fill=color)
            char_x += draw.textbbox((0, 0), char, font=font)[2]
    else:
        draw.text((text_x, text_y), label, font=font, fill=style["text"])

    return Image.alpha_composite(image, overlay).convert("RGB")


def prepare_story_image_with_link(
    source: Path,
    output: Path,
    *,
    url: str,
    sticker_text: str | None = None,
    x: float = DEFAULT_STICKER["x"],
    y: float = DEFAULT_STICKER["y"],
    width: float = DEFAULT_STICKER["width"],
    height: float = DEFAULT_STICKER["height"],
    cover: bool = False,
    variant: str = "default",
    draw_sticker: bool = False,
) -> tuple[int, int]:
    """Prepara a foto 9:16.

    draw_sticker=False (padrão): Instagram desenha o sticker nativo
    \"Acessar link >\" via story_link_stickers (fluxo INSSIST).

    draw_sticker=True: desenha o botão customizado na imagem (estilo Opalite).
    """
    canvas = compose_story_canvas(source, cover=cover)
    if draw_sticker:
        text = (sticker_text or "").strip() or default_sticker_text(url)
        canvas = draw_link_sticker(
            canvas,
            text,
            x=x,
            y=y,
            width=width,
            height=height,
            variant=variant,
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output, format="JPEG", quality=95, subsampling=0)
    return canvas.size


def upload_story_photo_web(
    session: requests.Session,
    image_path: Path,
    *,
    width: int,
    height: int,
    upload_id: str,
) -> None:
    entity_name = f"story_{upload_id}"
    image_bytes = image_path.read_bytes()
    headers = {
        "offset": "0",
        "x-entity-name": entity_name,
        "x-entity-length": str(len(image_bytes)),
        "x-ig-app-id": IG_APP_ID,
        "x-instagram-rupload-params": json.dumps(
            {
                "upload_id": upload_id,
                "media_type": 1,
                "upload_media_width": width,
                "upload_media_height": height,
            },
            separators=(",", ":"),
        ),
        "content-type": "image/jpeg",
    }
    response = _request_with_retry(
        session,
        "POST",
        UPLOAD_PHOTO_URL.format(entity_name=entity_name),
        attempts=2,
        headers=headers,
        data=image_bytes,
    )
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if payload.get("status") == "fail":
        raise RuntimeError(f"Upload web rejeitado: {payload}")


def configure_story_link_web(
    session: requests.Session,
    *,
    upload_id: str,
    url: str,
    x: float,
    y: float,
    width: float,
    height: float,
    rotation: float = 0.0,
) -> dict:
    link_sticker = {
        "x": x,
        "y": y,
        "width": width,
        "height": height,
        "rotation": rotation,
        "link_type": "web",
        "url": url,
    }
    data = {
        "upload_id": upload_id,
        "story_link_stickers": json.dumps([link_sticker], separators=(",", ":")),
    }
    csrf = (
        session.cookies.get("csrftoken", domain=".instagram.com")
        or session.cookies.get("csrftoken")
        or session.headers.get("x-csrftoken")
    )
    headers = {
        "accept": "*/*",
        "content-type": "application/x-www-form-urlencoded",
        "x-csrftoken": csrf or "",
        "x-ig-app-id": IG_APP_ID,
        "x-asbd-id": WEB_ASBD_ID,
        "x-requested-with": "XMLHttpRequest",
        "origin": IG_WEB_ORIGIN,
        "referer": f"{IG_WEB_ORIGIN}/",
    }
    response = _request_with_retry(
        session,
        "POST",
        CONFIGURE_STORY_URL,
        attempts=5,
        headers=headers,
        data=data,
    )
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError(
            f"Configure web inválido: {response.text[:500]}"
        ) from exc
    if payload.get("status") == "fail":
        raise RuntimeError(f"Configure web rejeitado: {payload}")
    return payload


def publish_photo_story_web_link(
    cl: Any,
    media_path: Path,
    *,
    link_url: str,
    sticker_text: str | None = None,
    x: float = DEFAULT_STICKER["x"],
    y: float = DEFAULT_STICKER["y"],
    width: float = DEFAULT_STICKER["width"],
    height: float = DEFAULT_STICKER["height"],
    rotation: float = DEFAULT_STICKER["rotation"],
    cover: bool = False,
    variant: str = "default",
    draw_sticker: bool = False,
    web_cookies: dict[str, str] | None = None,
    work_dir: Path | None = None,
) -> dict:
    """Publica Story de foto com link via API web (NÃO usa instagrapi upload).

    Usa cookies da sessão (sessionid/csrftoken) + rupload_igphoto +
    web/create/configure_to_story + story_link_stickers — mesmo padrão
    da extensão INSSIST/Opalite. O instagrapi mobile deixa o sticker invisível.
    """
    url = normalize_story_url(link_url)
    if not url:
        raise ValueError("URL do link é obrigatória para Story web.")

    base = work_dir or media_path.parent
    prepared = base / f"story-web-{int(time.time() * 1000)}.jpg"
    width_px, height_px = prepare_story_image_with_link(
        media_path,
        prepared,
        url=url,
        sticker_text=sticker_text,
        x=x,
        y=y,
        width=width,
        height=height,
        cover=cover,
        variant=variant,
        draw_sticker=draw_sticker,
    )

    session = build_web_session(cl, web_cookies)
    _warmup_web_session(session)
    upload_id = str(int(time.time() * 1000))
    log.info(
        "Story web+link (INSSIST/Opalite): upload_id=%s url=%s text=%r ua=desktop",
        upload_id,
        url,
        sticker_text or default_sticker_text(url),
    )
    try:
        upload_story_photo_web(
            session,
            prepared,
            width=width_px,
            height=height_px,
            upload_id=upload_id,
        )
        result = configure_story_link_web(
            session,
            upload_id=upload_id,
            url=url,
            x=x,
            y=y,
            width=width,
            height=height,
            rotation=rotation,
        )
    finally:
        try:
            prepared.unlink(missing_ok=True)
        except OSError:
            pass

    media = result.get("media") or {}
    media_id = media.get("pk") or media.get("id") or upload_id
    code = media.get("code")
    return {"id": str(media_id), "code": code, "url": None, "provider": "web_story_link"}
