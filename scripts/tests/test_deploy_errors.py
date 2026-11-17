"""Run the installer's real Bash handlers/stages with isolated fake commands."""

import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "scripts/deploy.sh"
CANARY = "synthetic-sensitive-argument-not-for-diagnostics"


class DeploymentErrorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.marker = self.root / "user-data"
        self.marker.write_bytes(b"Existing user data must survive failures.\n")
        self.original = self.marker.read_bytes()
        self.lines = SOURCE.read_text().splitlines()
        # Copy only definitions, preserving their original line numbers. Never
        # execute root preflight, apt, systemctl, network or real Docker commands.
        self.definitions = [""] * len(self.lines)
        end = next(i for i, line in enumerate(self.lines) if line.startswith("root=$("))
        self.definitions[:end] = self.lines[:end]
        start = next(i for i, line in enumerate(self.lines) if line.startswith("run()"))
        end = self.lines.index(
            "# Check connectivity before apt; no preinstalled Python/Node/PostgreSQL needed."
        )
        self.definitions[start:end] = self.lines[start:end]
        self.environment = {
            "PATH": str(self.root) + os.pathsep + os.defpath,
        }

    def execute(self, lines):
        path = self.root / "deploy.sh"
        path.write_text("\n".join(lines) + "\n")
        return subprocess.run(
            ["bash", str(path)],
            capture_output=True,
            text=True,
            env=self.environment,
            timeout=10,
        )

    def assert_failure(self, result, stage, line, code, reason="command failed"):
        self.assertEqual(result.returncode, code)
        self.assertIn(
            f"Deployment failed at {stage} (deploy.sh:{line}, exit {code})", result.stderr
        )
        self.assertIn(reason, result.stderr)
        self.assertIn("data/volumes preserved", result.stderr)
        self.assertEqual(result.stderr.count("Deployment failed"), 1)
        self.assertNotIn("Ready:", result.stdout)
        self.assertNotIn("REACHED_AFTER_FAILURE", result.stdout)
        self.assertNotIn(CANARY, result.stdout + result.stderr)
        log = self.root / "deploy.log"
        if log.exists():
            self.assertNotIn(CANARY, log.read_text())
        self.assertEqual(self.marker.read_bytes(), self.original)

    def test_errors_in_real_stage_commands_report_their_call_sites(self):
        fake = self.root / "fake-compose"
        fake.write_text(
            f"#!{sys.executable}\n"
            "import os, sys\n"
            "target = os.environ['DEPLOY_TEST_TARGET']\n"
            "args = sys.argv[1:]\n"
            "failed = (target == 'build' and 'build' in args) or "
            "(target in ('migrations', 'storage-init', 'demo') and "
            "{'migrations': 'migrate', 'storage-init': 'storage-init', "
            "'demo': 'seed'}[target] in args) or "
            "(target == 'http-smoke' and any(arg.endswith('/check_http.py') for arg in args))\n"
            "sys.exit(int(os.environ['DEPLOY_TEST_EXIT']) if failed else 0)\n"
        )
        fake.chmod(0o700)
        (self.root / "docker").symlink_to(fake)
        start = self.lines.index("stage=build")
        lines = self.definitions.copy()
        lines[start - 4 : start] = [
            "log=" + shlex.quote(str(self.root / "deploy.log")),
            "root=" + shlex.quote(str(self.root)),
            "dc=(" + shlex.quote(str(fake)) + " " + shlex.quote(CANARY) + ")",
            "demo=true; python_image=synthetic-test-image; port=18080",
        ]
        lines[start:] = self.lines[start:]
        lines.append("printf 'REACHED_AFTER_FAILURE\\n'")
        cases = (
            ("build", 'run 1800 "${dc[@]}" build api web', 42),
            ("migrations", 'run 180 "${dc[@]}" run --rm --no-deps migrate', 73),
            ("storage-init", 'run 120 "${dc[@]}" run --rm --no-deps storage-init', 74),
            ("demo", 'run 900 "${dc[@]}" --profile demo run --rm --no-deps seed', 75),
            (
                "http-smoke",
                next(line for line in self.lines if line.startswith("run 120 docker run")),
                76,
            ),
        )
        for stage, command, code in cases:
            with self.subTest(stage=stage):
                self.environment.update(DEPLOY_TEST_TARGET=stage, DEPLOY_TEST_EXIT=str(code))
                line = next(i for i, value in enumerate(self.lines, 1) if value.strip() == command)
                self.assert_failure(self.execute(lines), stage, line, code)

    def body(self, commands):
        return self.definitions + ["log=" + shlex.quote(str(self.root / "deploy.log")), *commands]

    def test_nested_failure_reports_run_caller_without_sensitive_arguments(self):
        command = "    run 2 bash -c 'exit 73' -- " + shlex.quote(CANARY)
        lines = self.body(
            [
                "inner() {",
                command,
                "}",
                "outer() { inner; }",
                "stage=migrations",
                "outer",
                "printf 'REACHED_AFTER_FAILURE\\n'",
            ]
        )
        self.assert_failure(self.execute(lines), "migrations", lines.index(command) + 1, 73)

    def test_real_timeout_preserves_124_and_reports_safe_reason(self):
        command = "run 0.05 sleep 5"
        lines = self.body(["stage=demo", command, "printf 'REACHED_AFTER_FAILURE\\n'"])
        self.assert_failure(
            self.execute(lines), "demo", lines.index(command) + 1, 124, "time limit"
        )

    def test_missing_command_preserves_127_without_sensitive_arguments(self):
        command = "run 2 missing-deploy-regression-command " + shlex.quote(CANARY)
        lines = self.body(["stage=build", command, "printf 'REACHED_AFTER_FAILURE\\n'"])
        self.assert_failure(
            self.execute(lines), "build", lines.index(command) + 1, 127, "not found"
        )

    def test_expected_failure_still_allows_fallback(self):
        lines = self.body(
            ["stage=configuration", "run 2 false || run 2 true", "printf 'Ready: fixture\\n'"]
        )
        result = self.execute(lines)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertIn("Ready: fixture", result.stdout)
        self.assertEqual(self.marker.read_bytes(), self.original)

    def test_unwrapped_failure_reports_its_own_line_and_exit_code(self):
        command = "bash -c 'exit 61' -- " + shlex.quote(CANARY)
        lines = self.body(["stage=preflight", command, "printf 'REACHED_AFTER_FAILURE\\n'"])
        self.assert_failure(self.execute(lines), "preflight", lines.index(command) + 1, 61)
