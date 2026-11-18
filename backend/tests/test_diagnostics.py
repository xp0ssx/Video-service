import os
import re
import secrets
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

import pytest


@pytest.mark.parametrize("target", ["unreachable", "test_db"])
def test_connection_failure_does_not_disclose_credentials(tmp_path, target):
    if target == "test_db" and os.getenv("RUN_INTEGRATION") != "1":
        pytest.skip("Real test PostgreSQL required")
    # Exercise the actual pytest reporter with a real psycopg connection failure.
    # Deliberately include raw, URL-encoded credentials and captured output/locals.
    source = Path(__file__).with_name("conftest.py")
    (tmp_path / "conftest.py").write_text(source.read_text())
    password = "diagnostic-sentinel-/@+never-real"
    token = "diagnostic-sentinel-token-never-real"
    host, port = ("test-db", "5432") if target == "test_db" else ("127.0.0.1", "1")
    dsn = "postgresql://test:" + quote(password, safe="") + f"@{host}:{port}/test"
    (tmp_path / "test_failure.py").write_text("""import os
import psycopg
from fastapi import FastAPI
from fastapi.testclient import TestClient

def test_failed_connection():
    app = FastAPI()
    @app.get("/secret")
    def secret():
        return {"csrf_token": os.environ["TEST_VALUE"]}
    with TestClient(app) as client:
        print(client.get("/secret").json())
    print(os.environ["TEST_SESSION_TOKEN"])
    print(os.environ["TEST_DSN"])
    psycopg.connect(host=os.environ["DIAGNOSTIC_HOST"], port=int(os.environ["DIAGNOSTIC_PORT"]),
                    user="video_service_test", dbname="video_service_test",
                    password=os.environ["POSTGRES_PASSWORD"], connect_timeout=1)
""")
    environment = {
        **os.environ,
        "RUN_INTEGRATION": "0",
        "POSTGRES_PASSWORD": password,
        "TEST_SESSION_TOKEN": token,
        "TEST_DSN": dsn,
        "TEST_VALUE": "response-sentinel-never-real",
        "DIAGNOSTIC_HOST": host,
        "DIAGNOSTIC_PORT": port,
        "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
    }
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            "--tb=long",
            "-l",
            str(tmp_path),
        ],
        env=environment,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 1
    assert "OperationalError" in output
    if target == "test_db":
        assert "password authentication failed" in output
    assert "[REDACTED]" in output
    assert password not in output
    assert quote(password, safe="") not in output
    assert token not in output
    assert dsn not in output
    assert environment["TEST_VALUE"] not in output


def test_collection_failure_does_not_disclose_credentials(tmp_path):
    source = Path(__file__).with_name("conftest.py")
    (tmp_path / "conftest.py").write_text(source.read_text())
    password = secrets.token_hex(24) + "/@+"
    token = secrets.token_hex(24)
    dsn = "postgresql://test:" + quote(password, safe="") + "@127.0.0.1:1/test"
    (tmp_path / "test_import_failure.py").write_text("""import os
import sys
import psycopg

database_password = os.environ["POSTGRES_PASSWORD"]
print("Collection stdout context", os.environ["TEST_DSN"])
print("Collection stderr context", os.environ["TEST_SESSION_TOKEN"], file=sys.stderr)
psycopg.connect(host="127.0.0.1", port=1, user="test", dbname="test",
                password=database_password, connect_timeout=1)
""")
    # Only synthetic credentials reach this subprocess, including during collection.
    environment = {
        "PATH": os.environ["PATH"],
        "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
        "RUN_INTEGRATION": "0",
        "POSTGRES_PASSWORD": password,
        "TEST_SESSION_TOKEN": token,
        "TEST_DSN": dsn,
    }
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            "--tb=long",
            "-l",
            str(tmp_path),
        ],
        env=environment,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 2
    assert "ERROR collecting test_import_failure.py" in output
    assert "OperationalError" in output
    assert "Connection refused" in output
    assert re.search(r"database_password\s*=\s*['\"]\[REDACTED\]['\"]", output)
    assert "Captured stdout" in output
    assert "Captured stderr" in output
    assert "Collection stdout context" in output
    assert "Collection stderr context" in output
    assert password not in output
    assert quote(password, safe="") not in output
    assert token not in output
    assert dsn not in output
    assert "[REDACTED]" in output


@pytest.mark.parametrize(
    "source",
    [
        'def test_skip():\n    pytest.skip(os.environ["TEST_SESSION_TOKEN"])\n',
        "@pytest.fixture(autouse=True)\ndef gate():\n"
        '    pytest.skip(os.environ["TEST_SESSION_TOKEN"])\n'
        "def test_skip():\n    pass\n",
        '@pytest.mark.skip(reason=os.environ["TEST_SESSION_TOKEN"])\ndef test_skip():\n    pass\n',
        'pytest.skip(os.environ["TEST_SESSION_TOKEN"], allow_module_level=True)\n',
    ],
    ids=["call", "fixture-setup", "marker", "collection"],
)
def test_skipped_reports_keep_structure_and_redact_credentials(tmp_path, source):
    (tmp_path / "conftest.py").write_text(Path(__file__).with_name("conftest.py").read_text())
    (tmp_path / "test_skip.py").write_text("import os\nimport pytest\n" + source)
    # Collection-only skips otherwise exit 5 (no runnable tests).
    (tmp_path / "test_pass.py").write_text("def test_pass():\n    pass\n")
    token = "synthetic-skipped-reason-never-real"
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-ra", str(tmp_path)],
        env={
            **os.environ,
            "RUN_INTEGRATION": "0",
            "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
            "TEST_SESSION_TOKEN": token,
        },
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "1 passed, 1 skipped" in output
    assert "SKIPPED" in output
    assert "[REDACTED]" in output
    assert token not in output
