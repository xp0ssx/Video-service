"""Create local secrets and the authenticated S3 configuration without printing them."""

import json
import os
import secrets
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SECRET_LENGTHS = {
    "POSTGRES_PASSWORD": 32,
    "S3_ACCESS_KEY": 20,
    "S3_SECRET_KEY": 40,
    "S3_ADMIN_ACCESS_KEY": 20,
    "S3_ADMIN_SECRET_KEY": 40,
}


def write_private(path: Path, content: str, *, exclusive: bool = False) -> None:
    flags = os.O_WRONLY | os.O_CREAT | (os.O_EXCL if exclusive else os.O_TRUNC)
    flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(content)


def resolve_environment(root: Path, env_path: Path) -> dict[str, str]:
    """Use the exact same dotenv/interpolation rules as the running containers."""
    try:
        result = subprocess.run(
            [
                "docker",
                "compose",
                "--project-directory",
                str(root.resolve()),
                "--env-file",
                str(env_path.resolve()),
                "-f",
                str(root / "compose.yaml"),
                "config",
                "--format",
                "json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        # Warnings about unresolved variables must not silently modify credentials.
        if result.returncode or result.stderr.strip():
            raise ValueError("Compose configuration rejected")
        configuration = json.loads(result.stdout)
        values = dict(configuration["services"]["storage-init"]["environment"])
        api = configuration["services"]["api"]["environment"]
        if any(
            values.get(key) != api.get(key)
            for key in ("POSTGRES_PASSWORD", "S3_ACCESS_KEY", "S3_SECRET_KEY", "S3_BUCKET")
        ):
            raise ValueError("Inconsistent service credentials")
        return values
    except (OSError, ValueError, KeyError, TypeError):
        raise SystemExit(
            "Не удалось проверить .env через Docker Compose. "
            "Проверьте синтаксис и обязательные переменные; значения скрыты."
        ) from None


def prepare_environment(root: Path = ROOT) -> None:
    env_path = root / ".env"
    new_environment = not env_path.exists()
    if new_environment:
        template = (root / ".env.example").read_text()
        for key, length in SECRET_LENGTHS.items():
            template = template.replace(f"{key}=\n", f"{key}={secrets.token_hex(length)}\n")
        template = template.replace("LOCAL_UID=1000", f"LOCAL_UID={os.getuid()}")
        template = template.replace("LOCAL_GID=1000", f"LOCAL_GID={os.getgid()}")
        # Validate a private temporary candidate before publishing any configuration.
        with tempfile.TemporaryDirectory(prefix="video-service-env-") as temporary:
            candidate = Path(temporary) / ".env"
            write_private(candidate, template, exclusive=True)
            values = resolve_environment(root, candidate)
    else:
        values = resolve_environment(root, env_path)
    minimums = dict.fromkeys(SECRET_LENGTHS, 16)
    minimums["S3_SECRET_KEY"] = 32
    for key, minimum in minimums.items():
        if len(values.get(key, "")) < minimum:
            raise SystemExit(f"{key}: требуется минимум {minimum} символов; значение не показано")
    if new_environment:
        write_private(env_path, template, exclusive=True)

    bucket = values.get("S3_BUCKET", "video-media")
    identities = [
        {
            "name": "bootstrap",
            "credentials": [
                {
                    "accessKey": values["S3_ADMIN_ACCESS_KEY"],
                    "secretKey": values["S3_ADMIN_SECRET_KEY"],
                }
            ],
            "actions": ["Admin", "Read", "Write", "List", "Tagging"],
        },
        {
            "name": "application",
            "credentials": [
                {
                    "accessKey": values["S3_ACCESS_KEY"],
                    "secretKey": values["S3_SECRET_KEY"],
                }
            ],
            "actions": [f"Read:{bucket}", f"Write:{bucket}", f"List:{bucket}"],
        },
    ]
    config_dir = root / ".local" / "seaweedfs"
    config_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    (root / ".local" / "checks").mkdir(exist_ok=True, mode=0o700)
    write_private(config_dir / "s3.json", json.dumps({"identities": identities}, indent=2) + "\n")
    print("Local configuration ready; existing .env and its secrets preserved")


if __name__ == "__main__":
    prepare_environment()
