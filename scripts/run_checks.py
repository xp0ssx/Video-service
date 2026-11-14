"""Generate private test-only credentials and run isolated Compose checks.

Does not read production credentials. Docker Compose reads .env only to validate
inactive production services; none are passed to the test containers/network.
"""

import json
import os
import secrets
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / ".local" / "tests"


def prepare():
    DIRECTORY.mkdir(parents=True, exist_ok=True, mode=0o700)
    env_path = DIRECTORY / "environment"
    if not env_path.exists():
        values = {
            name: secrets.token_hex(32)
            for name in (
                "TEST_POSTGRES_PASSWORD",
                "TEST_S3_ACCESS_KEY",
                "TEST_S3_SECRET_KEY",
                "TEST_S3_ADMIN_ACCESS_KEY",
                "TEST_S3_ADMIN_SECRET_KEY",
            )
        }
        with open(env_path, "x", opener=lambda path, flags: os.open(path, flags, 0o600)) as f:
            f.write("".join(f"{name}={value}\n" for name, value in values.items()))
    else:
        values = dict(line.split("=", 1) for line in env_path.read_text().splitlines())
    config = {
        "identities": [
            {
                "name": "test-bootstrap",
                "credentials": [
                    {
                        "accessKey": values["TEST_S3_ADMIN_ACCESS_KEY"],
                        "secretKey": values["TEST_S3_ADMIN_SECRET_KEY"],
                    }
                ],
                "actions": ["Admin", "Read", "Write", "List", "Tagging"],
            },
            {
                "name": "test-app",
                "credentials": [
                    {
                        "accessKey": values["TEST_S3_ACCESS_KEY"],
                        "secretKey": values["TEST_S3_SECRET_KEY"],
                    }
                ],
                "actions": [
                    "Read:video-test-media",
                    "Write:video-test-media",
                    "List:video-test-media",
                ],
            },
        ]
    }
    path = DIRECTORY / "s3.json"
    with open(path, "w", opener=lambda path, flags: os.open(path, flags, 0o600)) as f:
        json.dump(config, f)
    return env_path


def main():
    env_path = prepare()
    command = [
        "docker",
        "compose",
        "--env-file",
        str(ROOT / ".env"),
        "--env-file",
        str(env_path),
        "--profile",
        "checks",
    ]
    # Remove shell overrides so isolated credentials have one source of truth.
    environment = {key: value for key, value in os.environ.items() if not key.startswith("TEST_")}
    args = sys.argv[1:] or ["run", "--build", "--rm", "checks"]
    return subprocess.run(command + args, cwd=ROOT, env=environment, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
