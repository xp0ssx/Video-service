"""Verify test environment isolation with synthetic credentials and real Compose parsing."""

import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("run_checks", ROOT / "scripts/run_checks.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class IsolatedChecksTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.directory = self.root / ".local/tests"
        override = patch.object(module, "DIRECTORY", self.directory)
        override.start()
        self.addCleanup(override.stop)

    def test_generated_credentials_are_private_and_stable(self):
        production = self.root / ".env"
        production.write_text("POSTGRES_PASSWORD=unrelated-working-placeholder\n")
        before = production.read_bytes()
        path = module.prepare()
        initial = path.read_bytes()
        storage = (self.directory / "s3.json").read_bytes()
        values = dict(line.split("=", 1) for line in path.read_text().splitlines())
        self.assertEqual(len(values), 5)
        self.assertTrue(all(len(value) >= 32 for value in values.values()))
        self.assertEqual(len(set(values.values())), 5)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.directory / "s3.json").stat().st_mode & 0o777, 0o600)
        module.prepare()
        self.assertEqual(path.read_bytes(), initial)
        self.assertEqual((self.directory / "s3.json").read_bytes(), storage)
        self.assertEqual(production.read_bytes(), before)

    def test_compose_checks_do_not_inherit_production_credentials(self):
        (self.root / "compose.yaml").write_bytes((ROOT / "compose.yaml").read_bytes())
        working = {
            key: "synthetic-working-placeholder-" + key
            for key in (
                "POSTGRES_PASSWORD",
                "S3_ACCESS_KEY",
                "S3_SECRET_KEY",
                "S3_ADMIN_ACCESS_KEY",
                "S3_ADMIN_SECRET_KEY",
            )
        }
        production = self.root / ".env"
        production.write_text("".join(f"{key}={value}\n" for key, value in working.items()))
        tests = module.prepare()
        result = subprocess.run(
            [
                "docker",
                "compose",
                "--env-file",
                str(production),
                "--env-file",
                str(tests),
                "--profile",
                "checks",
                "config",
                "--format",
                "json",
            ],
            cwd=self.root,
            env={"PATH": os.defpath + ":/usr/local/bin"},
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            result.returncode, 0, "Compose parse failed; private diagnostics suppressed"
        )
        services = json.loads(result.stdout)["services"]
        for name in ("checks", "test-migrate", "test-storage-init", "test-db"):
            config = services[name]
            self.assertEqual(set(config["networks"]), {"test-data"})
            encoded = json.dumps(config)
            self.assertTrue(all(value not in encoded for value in working.values()))
        self.assertEqual(services["checks"]["environment"]["POSTGRES_DB"], "video_service_test")
        self.assertNotIn("volumes", services["test-db"])
        self.assertTrue(services["test-db"]["tmpfs"])
