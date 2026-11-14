import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_database_url_preserves_special_characters(settings):
    password = "unit-test-p@ss:/?#%with-characters"
    configured = settings.model_copy(
        update={"postgres_password": settings.postgres_password.__class__(password)}
    )
    assert configured.database_url.password == password
    assert password not in str(configured.database_url)


def test_missing_secrets_fail_closed(monkeypatch):
    for key in ("POSTGRES_PASSWORD", "S3_ACCESS_KEY", "S3_SECRET_KEY"):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_settings_repr_does_not_expose_secrets(settings):
    representation = repr(settings)
    assert settings.postgres_password.get_secret_value() not in representation
    assert settings.s3_secret_key.get_secret_value() not in representation


@pytest.mark.parametrize("length", [16, 31, 32, 33])
def test_s3_secret_minimum(settings, length):
    values = settings.model_dump()
    values["s3_secret_key"] = "x" * length
    if length < 32:
        with pytest.raises(ValidationError) as error:
            Settings(_env_file=None, **values)
        assert "x" * length not in str(error.value)
    else:
        assert len(Settings(_env_file=None, **values).s3_secret_key.get_secret_value()) == length
