"""Exercise the shipped CLI entry point without APT, network, or service actions."""
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest


ENTRY = Path(__file__).resolve().parents[1] / "rootfs/usr/local/bin/rougarou"


class PublicCommandTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="rougarou-public-cli-")
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.config = self.home / ".config/rougarou"
        self.config.mkdir(parents=True, mode=0o700)
        self.sentinel = self.config / "existing-operator-setting.txt"
        self.sentinel.write_text("preserve this unrelated setting\n")
        self.sentinel.chmod(0o600)
        self.environment = {
            "PATH": os.defpath,
            "HOME": str(self.home),
            "XDG_CONFIG_HOME": str(self.home / ".config"),
            "XDG_STATE_HOME": str(self.home / ".local/state"),
            "LC_ALL": "C",
            "PYTHONDONTWRITEBYTECODE": "1",
        }

    def command(self, *arguments):
        return subprocess.run(
            [sys.executable, "-B", str(ENTRY), *arguments],
            env=self.environment, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=10, check=False,
        )

    def assert_existing_config_preserved(self):
        self.assertEqual(self.sentinel.read_text(), "preserve this unrelated setting\n")
        self.assertEqual(stat.S_IMODE(self.sentinel.stat().st_mode), 0o600)
        # Backup's operation lock may be created before the missing-config error.
        # It must never invent a configured destination, provider or webhook.
        self.assertEqual(list(self.config.glob("*.json")), [])
        self.assertFalse((self.home / ".local/state").exists())

    def test_public_help_discovers_each_new_command(self):
        result = self.command("help")
        self.assertEqual(result.returncode, 0, result.stderr)
        for command in ("menu", "update", "backup", "notify"):
            self.assertIn(command, result.stdout)
        self.assert_existing_config_preserved()

    def test_subcommand_help_and_menu_usage_route_to_real_modules(self):
        for command, expected in (
            ("update", "plan"), ("backup", "restore"), ("notify", "configure"),
        ):
            with self.subTest(command=command):
                result = self.command(command, "--help")
                if command == "notify" and os.geteuid() == 0:
                    self.assertEqual(result.returncode, 1)
                    self.assertIn("regular operator account", result.stderr)
                else:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn("rougarou " + command, result.stdout)
                    self.assertIn(expected, result.stdout)
                self.assertNotIn("Traceback", result.stderr)
        # The deliberately small menu accepts no arguments, including --help.
        result = self.command("menu", "--help")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Usage: rougarou menu", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assert_existing_config_preserved()

    def test_update_recovery_json_is_readable_without_privilege_or_apt(self):
        before = sorted(str(path.relative_to(self.home)) for path in self.home.rglob("*"))
        result = self.command("update", "recovery", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertIs(report["automatic_rollback"], False)
        self.assertTrue(report["steps"])
        self.assertTrue(all(isinstance(step, str) for step in report["steps"]))
        self.assertTrue(any("snapshot" in step for step in report["steps"]))
        self.assertEqual(before, sorted(str(path.relative_to(self.home)) for path in self.home.rglob("*")))
        self.assert_existing_config_preserved()

    def test_unconfigured_backup_and_notifications_fail_without_creating_configuration(self):
        for arguments, expected in (
            (("backup", "list", "--json"), "rougarou backup init"),
            (("notify", "test"), "rougarou notify configure"),
        ):
            with self.subTest(command=arguments):
                result = self.command(*arguments)
                self.assertEqual(result.returncode, 1)
                self.assertIn("regular operator account" if os.geteuid() == 0 else expected, result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                self.assert_existing_config_preserved()

    def test_headless_menu_returns_operator_guidance_without_running_actions(self):
        result = self.command("menu")
        self.assertEqual(result.returncode, 1)
        self.assertIn("regular operator account" if os.geteuid() == 0 else "interactive terminal", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assert_existing_config_preserved()


if __name__ == "__main__":
    unittest.main()
