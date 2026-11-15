from functools import lru_cache
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore", hide_input_in_errors=True)

    app_origin: str = "http://localhost:8080"

    @model_validator(mode="after")
    def validate_origin(self):
        url = urlsplit(self.app_origin)
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or url.path
            or url.query
            or url.fragment
            or url.username
            or url.password
            or (url.scheme == "http" and url.hostname not in {"localhost", "127.0.0.1", "::1"})
        ):
            raise ValueError("APP_ORIGIN must be an HTTPS origin or local HTTP origin")
        return self

    @property
    def cookie_secure(self) -> bool:
        return urlsplit(self.app_origin).scheme == "https"

    max_upload_bytes: int = Field(default=500 * 1024 * 1024, ge=1, le=500 * 1024 * 1024)
    scratch_dir: str = "/tmp/video-service"
    scratch_min_free_bytes: int = Field(default=2 * 1024**3, ge=0)
    worker_required: bool = False

    postgres_host: str = "db"
    postgres_port: int = Field(default=5432, ge=1, le=65535)
    postgres_db: str = "video_service"
    postgres_user: str = "video_service"
    postgres_password: SecretStr = Field(min_length=16)
    s3_endpoint_url: str = "http://storage:8333"
    s3_bucket: str = "video-media"
    s3_region: str = "us-east-1"
    s3_access_key: SecretStr = Field(min_length=16)
    s3_secret_key: SecretStr = Field(min_length=32)

    @property
    def database_url(self) -> URL:
        return URL.create(
            "postgresql+psycopg",
            username=self.postgres_user,
            password=self.postgres_password.get_secret_value(),
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
