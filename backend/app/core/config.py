from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore", hide_input_in_errors=True)

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
