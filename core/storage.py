"""Abstração de storage de vídeos: local em disco ou S3."""
from __future__ import annotations

import itertools
import os
import shutil
import threading
import uuid
from pathlib import Path
from typing import BinaryIO, Protocol

from app.config import settings

B2_KEY_PREFIX = "b2/"


class StorageBackend(Protocol):
    def save(self, src_stream: BinaryIO, suggested_ext: str = ".mp4") -> str: ...
    def download_to(self, key: str, dest_path: Path) -> None: ...
    def open_download(self, key: str, byte_range: str | None = None) -> dict: ...
    def delete(self, key: str) -> None: ...
    def presign_upload(self, key: str, content_type: str, expires_in: int = 3600) -> str: ...
    def presign_download(self, key: str, expires_in: int = 3600) -> str: ...
    def allocate_key(self, key: str) -> str: ...


# ------------------------------------------------------------------
# Local
# ------------------------------------------------------------------
class LocalStorage:
    def __init__(self, base_path: str) -> None:
        self.base = Path(base_path).resolve()
        self.base.mkdir(parents=True, exist_ok=True)

    def _abs(self, key: str) -> Path:
        return (self.base / key).resolve()

    def save(self, src_stream: BinaryIO, suggested_ext: str = ".mp4") -> str:
        ext = suggested_ext if suggested_ext.startswith(".") else f".{suggested_ext}"
        key = f"videos/{uuid.uuid4().hex}{ext}"
        dest = self._abs(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            src_stream.seek(0)
        except Exception:
            pass
        with dest.open("wb") as out:
            shutil.copyfileobj(src_stream, out)
        return key

    def download_to(self, key: str, dest_path: Path) -> None:
        src = self._abs(key)
        if not src.exists():
            raise FileNotFoundError(f"Arquivo n\u00e3o encontrado no storage local: {key}")
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest_path)

    def delete(self, key: str) -> None:
        path = self._abs(key)
        if path.exists():
            try:
                path.unlink()
            except OSError:
                pass

    def presign_upload(self, key: str, content_type: str, expires_in: int = 3600) -> str:
        raise NotImplementedError("Upload direto requer STORAGE_BACKEND=s3")

    def presign_download(self, key: str, expires_in: int = 3600) -> str:
        raise NotImplementedError("URL assinada requer STORAGE_BACKEND=s3")

    def allocate_key(self, key: str) -> str:
        return key


# ------------------------------------------------------------------
# S3 (AWS ou compatível)
# ------------------------------------------------------------------
class S3Storage:
    def __init__(
        self,
        bucket: str,
        endpoint_url: str | None,
        access_key_id: str,
        secret_access_key: str,
        region: str = "auto",
    ) -> None:
        import boto3
        from botocore.config import Config

        if not bucket:
            raise ValueError("S3_BUCKET não configurado")
        if not access_key_id or not secret_access_key:
            raise ValueError("S3_ACCESS_KEY_ID e S3_SECRET_ACCESS_KEY são obrigatórios")

        self.bucket = bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint_url or None,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name=region or "auto",
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                retries={"max_attempts": 3, "mode": "standard"},
            ),
        )

    def _guess_content_type(self, ext: str) -> str:
        ext = ext.lower()
        return {
            ".mp4": "video/mp4",
            ".mov": "video/quicktime",
            ".webm": "video/webm",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".webp": "image/webp",
        }.get(ext, "application/octet-stream")

    def save(self, src_stream: BinaryIO, suggested_ext: str = ".mp4") -> str:
        ext = suggested_ext if suggested_ext.startswith(".") else f".{suggested_ext}"
        key = f"videos/{uuid.uuid4().hex}{ext}"
        extra = {"ContentType": self._guess_content_type(ext)}
        try:
            src_stream.seek(0)
        except Exception:
            pass
        self.client.upload_fileobj(
            src_stream,
            self.bucket,
            key,
            ExtraArgs=extra,
        )
        return key

    def download_to(self, key: str, dest_path: Path) -> None:
        from botocore.exceptions import ClientError

        dest_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with dest_path.open("wb") as fh:
                self.client.download_fileobj(self.bucket, key, fh)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in ("404", "NoSuchKey", "NotFound"):
                raise FileNotFoundError(f"Arquivo não encontrado no R2/S3: {key}") from exc
            raise

    def open_download(self, key: str, byte_range: str | None = None) -> dict:
        """Abre o objeto para streaming sem copiá-lo antes ao disco."""
        params = {"Bucket": self.bucket, "Key": key}
        if byte_range:
            params["Range"] = byte_range
        return self.client.get_object(**params)

    def delete(self, key: str) -> None:
        try:
            self.client.delete_object(Bucket=self.bucket, Key=key)
        except Exception:
            pass

    def presign_upload(self, key: str, content_type: str, expires_in: int = 3600) -> str:
        """Gera PUT temporário para o navegador enviar direto ao R2/S3."""
        return self.client.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": self.bucket,
                "Key": key,
                "ContentType": content_type,
            },
            ExpiresIn=expires_in,
        )

    def presign_download(self, key: str, expires_in: int = 3600) -> str:
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=expires_in,
        )

    def allocate_key(self, key: str) -> str:
        return key

    def ping(self) -> None:
        """Verifica credenciais e acesso ao bucket."""
        self.client.head_bucket(Bucket=self.bucket)


# ------------------------------------------------------------------
# Dual S3 — dois buckets R2 (~50/50), bucket 2 com prefixo b2/
# ------------------------------------------------------------------
class DualS3Storage:
    """Envolve dois S3Storage e roteia pelo prefixo lógico `b2/`."""

    def __init__(self, primary: S3Storage, secondary: S3Storage) -> None:
        self.primary = primary
        self.secondary = secondary
        self._lock = threading.Lock()
        self._counter = itertools.count(0)

    def _backend_for(self, key: str) -> S3Storage:
        return self.secondary if key.startswith(B2_KEY_PREFIX) else self.primary

    def allocate_key(self, key: str) -> str:
        """Alterna buckets: metade das keys recebe prefixo b2/ (bucket 2)."""
        if key.startswith(B2_KEY_PREFIX):
            return key
        with self._lock:
            use_secondary = next(self._counter) % 2 == 1
        return f"{B2_KEY_PREFIX}{key}" if use_secondary else key

    def save(self, src_stream: BinaryIO, suggested_ext: str = ".mp4") -> str:
        ext = suggested_ext if suggested_ext.startswith(".") else f".{suggested_ext}"
        base_key = f"videos/{uuid.uuid4().hex}{ext}"
        key = self.allocate_key(base_key)
        backend = self._backend_for(key)
        extra = {"ContentType": backend._guess_content_type(ext)}
        try:
            src_stream.seek(0)
        except Exception:
            pass
        backend.client.upload_fileobj(
            src_stream,
            backend.bucket,
            key,
            ExtraArgs=extra,
        )
        return key

    def download_to(self, key: str, dest_path: Path) -> None:
        self._backend_for(key).download_to(key, dest_path)

    def open_download(self, key: str, byte_range: str | None = None) -> dict:
        return self._backend_for(key).open_download(key, byte_range)

    def delete(self, key: str) -> None:
        self._backend_for(key).delete(key)

    def presign_upload(self, key: str, content_type: str, expires_in: int = 3600) -> str:
        return self._backend_for(key).presign_upload(key, content_type, expires_in)

    def presign_download(self, key: str, expires_in: int = 3600) -> str:
        return self._backend_for(key).presign_download(key, expires_in)

    def ping(self) -> None:
        self.primary.ping()
        self.secondary.ping()


def _secondary_s3_configured() -> bool:
    return bool(
        settings.s3_bucket_2
        and settings.s3_access_key_id_2
        and settings.s3_secret_access_key_2
    )


_storage_singleton: StorageBackend | None = None


def get_storage() -> StorageBackend:
    global _storage_singleton
    if _storage_singleton is not None:
        return _storage_singleton

    if settings.storage_backend == "s3":
        primary = S3Storage(
            bucket=settings.s3_bucket,
            endpoint_url=settings.s3_endpoint_url or None,
            access_key_id=settings.s3_access_key_id,
            secret_access_key=settings.s3_secret_access_key,
            region=settings.s3_region,
        )
        if _secondary_s3_configured():
            secondary = S3Storage(
                bucket=settings.s3_bucket_2,
                endpoint_url=(settings.s3_endpoint_url_2 or settings.s3_endpoint_url) or None,
                access_key_id=settings.s3_access_key_id_2,
                secret_access_key=settings.s3_secret_access_key_2,
                region=settings.s3_region,
            )
            _storage_singleton = DualS3Storage(primary, secondary)
        else:
            _storage_singleton = primary
    else:
        path = settings.local_storage_path
        if not os.path.isabs(path):
            path = str((settings.base_dir / path).resolve())
        _storage_singleton = LocalStorage(path)

    return _storage_singleton
