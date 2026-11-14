import json
import os
import re
from urllib.parse import quote, quote_plus

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app

HTTP_SECRETS = set()


def remember_secrets(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if any(word in key.lower() for word in ("password", "token", "secret")):
                if isinstance(child, str) and child:
                    HTTP_SECRETS.add(child)
            else:
                remember_secrets(child)
    elif isinstance(value, list):
        for child in value:
            remember_secrets(child)


def pytest_configure(config):
    if config.getoption("capture") in {"no", "tee-sys"} or config.getini("log_cli"):
        raise pytest.UsageError("Use captured test output to protect credentials")


@pytest.fixture(autouse=True)
def protect_http_diagnostics(monkeypatch):
    original = TestClient.send

    def send(self, *args, **kwargs):
        response = original(self, *args, **kwargs)
        HTTP_SECRETS.update(value for value in response.cookies.values() if value)
        for content in (response.content, response.request.content):
            try:
                remember_secrets(json.loads(content))
            except (ValueError, UnicodeDecodeError):
                pass
        return response

    monkeypatch.setattr(TestClient, "send", send)


def redact(text):
    """Defense in depth: pytest must not publish credentials even with --tb=long/-l."""
    values = [
        value
        for key, value in os.environ.items()
        if value
        and any(word in key.upper() for word in ("PASSWORD", "SECRET", "TOKEN", "ACCESS_KEY"))
    ]
    for value in sorted(set(values) | HTTP_SECRETS, key=len, reverse=True):
        for encoded in {value, quote(value, safe=""), quote_plus(value)}:
            text = text.replace(encoded, "[REDACTED]")
    text = re.sub(r"(postgres(?:ql)?(?:\+\w+)?://)[^\s\"']+", r"\1[REDACTED]", text)
    text = re.sub(
        r"(?i)((?:password(?:_hash)?|csrf_token|session_id|secret_key|access_key)"
        r"[\"']?\s*[:=]\s*[\"']?)[^\s,;\"'}]+",
        r"\1[REDACTED]",
        text,
    )
    return text


def redact_report(report):
    if report.longrepr:
        report.longrepr = redact(str(report.longrepr))
    report.sections = [(name, redact(content)) for name, content in report.sections]


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    result = yield
    redact_report(result.get_result())


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_make_collect_report(collector):
    # Resume after other wrappers have attached captured stdout/stderr to the report.
    result = yield
    redact_report(result.get_result())


@pytest.fixture(scope="session", autouse=True)
def isolated_integration_environment():
    if os.getenv("RUN_INTEGRATION") == "1":
        configuration = Settings()
        if not (
            configuration.postgres_host == "test-db"
            and configuration.postgres_db == "video_service_test"
            and configuration.postgres_user == "video_service_test"
            and configuration.s3_endpoint_url == "http://test-storage:8333"
            and configuration.s3_bucket == "video-test-media"
        ):
            pytest.fail("Integration tests require scripts/run_checks.py isolation", pytrace=False)


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        postgres_password="unit-test-value-never-used-in-a-running-database",
        s3_access_key="unit-test-access-key",
        s3_secret_key="unit-test-value-never-used-in-a-running-storage",
    )


@pytest.fixture
def client(settings: Settings):
    with TestClient(create_app(settings)) as test_client:
        yield test_client
