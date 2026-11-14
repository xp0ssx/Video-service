"""Regression tests use the real Compose parser and synthetic secrets only."""

import contextlib
import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("prepare_env", ROOT / "scripts/prepare_env.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class PreparationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        for name in ("compose.yaml", ".env.example"):
            (self.root / name).write_bytes((ROOT / name).read_bytes())
        self.environment = patch.dict(os.environ, {}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        # Keep Docker discovery while removing credential/interpolation overrides.
        os.environ["PATH"] = os.defpath + ":/usr/local/bin"

    def configure(self, secret, quote=""):
        values = {
            "POSTGRES_PASSWORD": "test-postgres-password-only",
            "S3_ACCESS_KEY": "test-access-key-only",
            "S3_SECRET_KEY": secret,
            "S3_ADMIN_ACCESS_KEY": "test-admin-access-key",
            "S3_ADMIN_SECRET_KEY": "test-admin-secret-key-only-123456789",
        }
        (self.root / ".env").write_text(
            "".join(f"{key}={quote}{value}{quote}\n" for key, value in values.items())
        )

    def prepare(self):
        with contextlib.redirect_stdout(io.StringIO()):
            module.prepare_environment(self.root)

    def test_quotes_and_idempotence(self):
        for quote in ("", "'", '"'):
            with self.subTest(quote=quote):
                secret = "test-secret-with-spaces-and-#-symbol-12345"
                self.configure(secret, quote)
                before = (self.root / ".env").read_bytes()
                self.prepare()
                path = self.root / ".local/seaweedfs/s3.json"
                configuration = path.read_bytes()
                value = json.loads(configuration)["identities"][1]["credentials"][0]
                self.assertEqual(value["secretKey"], secret)
                self.prepare()
                self.assertEqual(path.read_bytes(), configuration)
                self.assertEqual((self.root / ".env").read_bytes(), before)

    def test_lengths_before_any_config_write(self):
        for length in (16, 31, 32, 33):
            with self.subTest(length=length), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                (root / "compose.yaml").write_bytes((ROOT / "compose.yaml").read_bytes())
                self.configure("x" * length)
                (root / ".env").write_bytes((self.root / ".env").read_bytes())
                with contextlib.redirect_stdout(io.StringIO()):
                    if length < 32:
                        with self.assertRaises(SystemExit) as error:
                            module.prepare_environment(root)
                        self.assertIn("S3_SECRET_KEY", str(error.exception))
                        self.assertNotIn("x" * length, str(error.exception))
                        self.assertFalse((root / ".local").exists())
                    else:
                        module.prepare_environment(root)
                        path = root / ".local/seaweedfs/s3.json"
                        before = path.read_bytes()
                        self.configure("x" * 16)
                        (root / ".env").write_bytes((self.root / ".env").read_bytes())
                        with self.assertRaises(SystemExit):
                            module.prepare_environment(root)
                        self.assertEqual(path.read_bytes(), before)

    def test_unresolved_interpolation_does_not_change_config(self):
        self.configure("x" * 32)
        self.prepare()
        path = self.root / ".local/seaweedfs/s3.json"
        before = path.read_bytes()
        self.configure("x" * 32 + "$UNSET_SECRET_FOR_REGRESSION")
        with self.assertRaises(SystemExit) as error:
            self.prepare()
        self.assertNotIn("x" * 32, str(error.exception))
        self.assertEqual(path.read_bytes(), before)

    def test_generated_environment(self):
        self.prepare()
        self.assertEqual((self.root / ".env").stat().st_mode & 0o777, 0o600)
        self.assertTrue((self.root / ".local/seaweedfs/s3.json").is_file())

    def test_literal_dollar_and_shell_override(self):
        self.configure("literal-$SOME_UNDEFINED_SECRET-" + "x" * 32, "'")
        self.prepare()
        path = self.root / ".local/seaweedfs/s3.json"
        self.assertIn(
            "$SOME_UNDEFINED_SECRET",
            json.loads(path.read_text())["identities"][1]["credentials"][0]["secretKey"],
        )
        os.environ["S3_SECRET_KEY"] = "override-" + "y" * 32
        self.prepare()
        self.assertEqual(
            json.loads(path.read_text())["identities"][1]["credentials"][0]["secretKey"],
            os.environ["S3_SECRET_KEY"],
        )
