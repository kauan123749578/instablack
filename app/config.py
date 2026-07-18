"""Configurações da aplicação (carregadas de variáveis de ambiente / .env)."""
from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

log = logging.getLogger("app.config")

_INSECURE_SECRET_KEYS = frozenset({
    "change-me",
    "troque-isto-por-uma-chave-aleatoria-bem-longa",
    "secret",
    "dev",
})


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_env: Literal["development", "production"] = "development"
    secret_key: str = "change-me"
    allow_registration: bool = True
    trust_proxy: bool = True
    # Código único de convite (env). Se vazio, cadastro exige código inválido.
    invite_code: str = ""

    database_url: str = "sqlite:///./app.db"
    redis_url: str = "redis://localhost:6379/0"

    storage_backend: Literal["local", "s3"] = "local"
    local_storage_path: str = "./storage"

    s3_bucket: str = ""
    s3_endpoint_url: str = ""
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    s3_region: str = "auto"

    # Segunda conta R2 (opcional) — DualS3Storage distribui mídia ~50/50
    s3_bucket_2: str = ""
    s3_endpoint_url_2: str = ""
    s3_access_key_id_2: str = ""
    s3_secret_access_key_2: str = ""

    # Instagram API oficial (Business Login for Instagram)
    meta_instagram_app_id: str = ""
    meta_instagram_app_secret: str = ""
    meta_instagram_redirect_uri: str = ""
    meta_instagram_graph_version: str = "v25.0"
    public_base_url: str = ""

    ffmpeg_bin: str = "ffmpeg"
    beat_tick_seconds: int = 60

    bootstrap_admin_username: str = ""
    bootstrap_admin_password: str = ""
    bootstrap_admin_is_admin: bool = True
    # Se true, reseta a senha do usuário bootstrap já existente (recuperação de acesso).
    bootstrap_admin_reset: bool = False
    # Username do dono da plataforma (único que vê/gerencia usuários no /admin).
    owner_username: str = "kauan"
    default_account_limit: int = 0
    default_proxy: str = ""

    # Web Push (VAPID) — gere em https://vapidkeys.com/
    vapid_public_key: str = ""
    vapid_private_key: str = ""
    vapid_subject: str = "mailto:kauawqii@gmail.com"

    @field_validator("database_url", mode="before")
    @classmethod
    def normalize_database_url(cls, value: str) -> str:
        if not isinstance(value, str):
            return value
        if value.startswith("postgres://"):
            return value.replace("postgres://", "postgresql+psycopg2://", 1)
        if value.startswith("postgresql://") and "+psycopg2" not in value:
            return value.replace("postgresql://", "postgresql+psycopg2://", 1)
        return value

    @model_validator(mode="after")
    def warn_production(self) -> Self:
        """Avisa (sem derrubar o app) sobre configs frágeis em produção."""
        if self.app_env != "production":
            return self

        if self.secret_key.strip().lower() in _INSECURE_SECRET_KEYS or len(self.secret_key) < 32:
            log.warning(
                "SECRET_KEY fraca em produção: use uma chave aleatória com 32+ caracteres."
            )

        if self.storage_backend == "s3":
            missing = [
                name
                for name, val in (
                    ("S3_BUCKET", self.s3_bucket),
                    ("S3_ENDPOINT_URL", self.s3_endpoint_url),
                    ("S3_ACCESS_KEY_ID", self.s3_access_key_id),
                    ("S3_SECRET_ACCESS_KEY", self.s3_secret_access_key),
                )
                if not val
            ]
            if missing:
                log.warning("Variáveis R2/S3 faltando: %s", ", ".join(missing))
        elif self.storage_backend == "local" and not self.local_storage_path.startswith("/"):
            log.warning(
                "LOCAL_STORAGE_PATH relativo (%s): em produção use um Railway Volume "
                "com caminho absoluto (ex: /data/storage) para não perder as mídias.",
                self.local_storage_path,
            )

        return self

    @property
    def production_issues(self) -> list[str]:
        """Lista de problemas de config para exibir em /readyz."""
        issues: list[str] = []
        if not self.is_production:
            return issues
        if self.secret_key.strip().lower() in _INSECURE_SECRET_KEYS or len(self.secret_key) < 32:
            issues.append("SECRET_KEY fraca (use 32+ caracteres aleatórios)")
        if self.is_sqlite:
            issues.append("Usando SQLite (efêmero no Railway) — configure DATABASE_URL do Postgres")
        if self.storage_backend == "local" and not self.local_storage_path.startswith("/"):
            issues.append("LOCAL_STORAGE_PATH não é absoluto (use /data/storage com Volume ou STORAGE_BACKEND=s3 com R2)")
        elif self.storage_backend == "s3":
            for name, val in (
                ("S3_BUCKET", self.s3_bucket),
                ("S3_ENDPOINT_URL", self.s3_endpoint_url),
                ("S3_ACCESS_KEY_ID", self.s3_access_key_id),
                ("S3_SECRET_ACCESS_KEY", self.s3_secret_access_key),
            ):
                if not val:
                    issues.append(f"{name} não configurado (obrigatório para R2)")
        return issues

    @property
    def base_dir(self) -> Path:
        return Path(__file__).resolve().parent.parent

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()


try:
    settings = get_settings()
except Exception:  # nunca deixa a config derrubar o boot do processo
    log.exception("Falha ao carregar Settings; usando defaults.")
    get_settings.cache_clear()
    settings = Settings.model_construct()
