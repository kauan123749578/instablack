"""Configurações da aplicação (carregadas de variáveis de ambiente / .env)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

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

    database_url: str = "sqlite:///./app.db"
    redis_url: str = "redis://localhost:6379/0"

    storage_backend: Literal["local", "s3"] = "local"
    local_storage_path: str = "./storage"

    s3_bucket: str = ""
    s3_endpoint_url: str = ""
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    s3_region: str = "auto"

    ffmpeg_bin: str = "ffmpeg"
    beat_tick_seconds: int = 60

    bootstrap_admin_username: str = ""
    bootstrap_admin_password: str = ""
    bootstrap_admin_is_admin: bool = True

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
    def validate_production(self) -> Self:
        if self.app_env != "production":
            return self

        if self.secret_key.strip().lower() in _INSECURE_SECRET_KEYS or len(self.secret_key) < 32:
            raise ValueError(
                "SECRET_KEY inválida para produção: use uma chave aleatória com pelo menos 32 caracteres."
            )

        if self.storage_backend == "s3":
            missing = [
                name
                for name, val in (
                    ("S3_BUCKET", self.s3_bucket),
                    ("S3_ACCESS_KEY_ID", self.s3_access_key_id),
                    ("S3_SECRET_ACCESS_KEY", self.s3_secret_access_key),
                )
                if not val
            ]
            if missing:
                raise ValueError(f"Variáveis S3 obrigatórias: {', '.join(missing)}")
        elif self.storage_backend == "local":
            if not self.local_storage_path.startswith("/"):
                raise ValueError(
                    "Em produção com storage local, monte um Railway Volume nos services web e worker "
                    "e defina LOCAL_STORAGE_PATH com caminho absoluto (ex: /data/storage)."
                )
        else:
            raise ValueError("STORAGE_BACKEND inválido.")

        return self

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


settings = get_settings()
