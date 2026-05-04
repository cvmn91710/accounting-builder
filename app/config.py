"""Application settings from environment (cached)."""

from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _env_bool(v: object) -> bool:
    if isinstance(v, bool):
        return v
    if v is None:
        return False
    s = str(v).strip().lower()
    return s in ("1", "true", "yes", "on")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    gemini_api_key: str = Field(default="", validation_alias="GEMINI_API_KEY")
    gemini_model: str = Field(default="gemini-1.5-pro", validation_alias="GEMINI_MODEL")
    app_env: str = Field(default="development", validation_alias="APP_ENV")

    entra_tenant_id: str = Field(default="", validation_alias="ENTRA_TENANT_ID")
    entra_client_id: str = Field(default="", validation_alias="ENTRA_CLIENT_ID")
    entra_client_secret: str = Field(default="", validation_alias="ENTRA_CLIENT_SECRET")
    entra_redirect_uri: str = Field(
        default="http://localhost:8501", validation_alias="ENTRA_REDIRECT_URI"
    )
    entra_scopes: str = Field(default="User.Read", validation_alias="ENTRA_SCOPES")
    entra_admin_role: str = Field(
        default="Accounting.Admin", validation_alias="ENTRA_ADMIN_ROLE"
    )

    template_path_conservatorship: Path = Field(
        default=Path("templates/conservatorship_template.xlsx"),
        validation_alias="TEMPLATE_PATH_CONSERVATORSHIP",
    )
    template_path_probate: Path = Field(
        default=Path("templates/probate_template.xlsx"),
        validation_alias="TEMPLATE_PATH_PROBATE",
    )
    template_path_trust: Path = Field(
        default=Path("templates/trust_template.xlsx"),
        validation_alias="TEMPLATE_PATH_TRUST",
    )
    template_mapping_path: Path = Field(
        default=Path("templates/template_mapping.json"),
        validation_alias="TEMPLATE_MAPPING_PATH",
    )

    max_file_size_mb: int = Field(default=50, validation_alias="MAX_FILE_SIZE_MB")
    max_session_files: int = Field(default=50, validation_alias="MAX_SESSION_FILES")
    sqlite_db_path: Path = Field(
        default=Path("data/accounting.db"), validation_alias="SQLITE_DB_PATH"
    )
    skip_entra_auth: bool = Field(default=False, validation_alias="SKIP_ENTRA_AUTH")

    upload_dir: Path = Field(default=Path("data/uploads"))

    @field_validator("skip_entra_auth", mode="before")
    @classmethod
    def _bool_skip_entra(cls, v: object) -> bool:
        return _env_bool(v)

    @property
    def entra_scopes_list(self) -> list[str]:
        return [s.strip() for s in self.entra_scopes.split() if s.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


def matter_template_path(matter_type: str) -> Path:
    s = get_settings()
    key = (matter_type or "").lower().replace(" ", "_")
    if "conservator" in key:
        return s.template_path_conservatorship
    if "trust" in key:
        return s.template_path_trust
    return s.template_path_probate
