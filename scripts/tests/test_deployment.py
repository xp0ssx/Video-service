"""Deployment configuration regressions with real Compose parsing and temporary files."""

import importlib.util
import json
import os
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location(
    "deployment_config", ROOT / "scripts/deployment_config.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        for name in (".env.example", "compose.yaml"):
            (self.root / name).write_bytes((ROOT / name).read_bytes())

    def config(self):
        result = subprocess.run(
            [
                "docker",
                "compose",
                "--env-file",
                str(self.root / ".env"),
                "-f",
                str(self.root / "compose.yaml"),
                "--profile",
                "demo",
                "config",
                "--format",
                "json",
            ],
            env={"PATH": os.environ["PATH"]},
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            result.returncode, 0, "Compose rejected private fixture; diagnostics hidden"
        )
        return json.loads(result.stdout)

    def test_first_and_repeated_configuration_preserve_secrets(self):
        module.initialize(self.root, 18080, "deployment-test")
        before = (self.root / ".env").read_bytes()
        config = self.config()
        self.assertEqual(module.configure(self.root, config), ("deployment-test", 18080))
        storage = (self.root / ".local/seaweedfs/s3.json").read_bytes()
        module.initialize(self.root, 19090, "ignored-existing-secrets")
        self.assertEqual((self.root / ".env").read_bytes(), before)
        module.configure(self.root, self.config())
        self.assertEqual((self.root / ".local/seaweedfs/s3.json").read_bytes(), storage)
        for name in (".env", ".local/seaweedfs/s3.json"):
            self.assertEqual((self.root / name).stat().st_mode & 0o777, 0o600)

    def test_rejects_publication_origin_or_changed_existing_options_before_storage_write(self):
        module.initialize(self.root)
        for change in ("host", "internal", "origin", "port", "project"):
            config = self.config()
            kwargs = {}
            if change == "host":
                config["services"]["web"]["ports"][0]["host_ip"] = "0.0.0.0"
            elif change == "internal":
                config["services"]["db"]["ports"] = [{"published": "5432"}]
            elif change == "origin":
                config["services"]["api"]["environment"]["APP_ORIGIN"] = "http://public.example"
            elif change == "port":
                kwargs["port"] = 18080
            else:
                kwargs["project"] = "unexpected-project"
            with self.subTest(change=change), self.assertRaises(ValueError):
                module.configure(self.root, config, **kwargs)
            self.assertFalse((self.root / ".local/seaweedfs/s3.json").exists())

    def test_existing_storage_credentials_are_not_overwritten(self):
        module.initialize(self.root)
        config = self.config()
        module.configure(self.root, config)
        path = self.root / ".local/seaweedfs/s3.json"
        custom = json.loads(path.read_text())
        custom["identities"][1]["credentials"][0]["secretKey"] = (
            "synthetic-different-secret-" + "x" * 32
        )
        path.write_text(json.dumps(custom))
        before = path.read_bytes()
        with self.assertRaises(ValueError):
            module.configure(self.root, config)
        self.assertEqual(path.read_bytes(), before)

    def test_symlink_configuration_is_never_overwritten(self):
        target = self.root / "original"
        target.write_text("untouched")
        (self.root / ".env").symlink_to(target)
        with self.assertRaises(ValueError):
            module.initialize(self.root)
        self.assertEqual(target.read_text(), "untouched")

    def test_busy_port_fails_without_touching_data(self):
        with socket.socket() as server:
            server.bind(("127.0.0.1", 0))
            with self.assertRaises(OSError):
                module.check_port(server.getsockname()[1])

    def test_shell_help_syntax_and_unprivileged_rejection(self):
        script = ROOT / "scripts/deploy.sh"
        self.assertEqual(subprocess.run(["bash", "-n", script]).returncode, 0)
        help_result = subprocess.run(["bash", script, "--help"], capture_output=True, text=True)
        self.assertEqual(help_result.returncode, 0)
        if os.geteuid() != 0:
            result = subprocess.run(["bash", script, "--demo"], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("sudo", result.stderr)

    def test_no_success_output_after_configuration_failure(self):
        module.initialize(self.root)
        config = self.config()
        config["services"]["api"]["environment"]["S3_SECRET_KEY"] = "mismatch"
        with patch.object(
            sys, "argv", ["deployment_config", "configure", "--root", str(self.root)]
        ):
            from io import StringIO

            with patch.object(sys, "stdin", StringIO(json.dumps(config))):
                with self.assertRaises(SystemExit) as error:
                    module.main()
        self.assertNotIn("mismatch", str(error.exception))
        self.assertFalse((self.root / ".local/demo").exists())
