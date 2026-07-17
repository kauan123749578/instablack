"""Abstração de storage de vídeos: local em disco ou S3."""
from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path
from typing import BinaryIO, Protocol

from app.config import settings


class StorageBackend(Protocol):
    def save(self, src_stream: BinaryIO, suggested_ext: str = ".mp4") -> str: ...
    def download_to(self, key: str, dest_path: Path) -> None: ...
    def delete(self, key: str) -> None: ...
    def presign_upload(self, key: str, content_type: str, expires_in: int = 3600) -> str: ...


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

    def ping(self) -> None:
        """Verifica credenciais e acesso ao bucket."""
        self.client.head_bucket(Bucket=self.bucket)


_storage_singleton: StorageBackend | None = None


def get_storage() -> StorageBackend:
    global _storage_singleton
    if _storage_singleton is not None:
        return _storage_singleton

    if settings.storage_backend == "s3":
        _storage_singleton = S3Storage(
            bucket=settings.s3_bucket,
            endpoint_url=settings.s3_endpoint_url or None,
            access_key_id=settings.s3_access_key_id,
            secret_access_key=settings.s3_secret_access_key,
            region=settings.s3_region,
        )
    else:
        path = settings.local_storage_path
        if not os.path.isabs(path):
            path = str((settings.base_dir / path).resolve())
        _storage_singleton = LocalStorage(path)

    return _storage_singleton
