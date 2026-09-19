"""Offline regression tests for operator configuration and execution boundaries."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import tomllib
import types
import unittest
from unittest import mock


SOURCE = Path(__file__).resolve().parents[1] / "rootfs/usr/lib/rougarou/cli.py"
sys.path.insert(0, str(SOURCE.parent))
spec = importlib.util.spec_from_file_location("rougarou_cli", SOURCE)
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)
CODEX_HELP = "--approve-for-me\n--no-alt-screen\n--oss\n"


class CliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.environment = mock.patch.dict(os.environ, {
            "HOME": str(self.home), "XDG_CONFIG_HOME": str(self.home / ".config"),
            "CODEX_HOME": str(self.home / ".codex"),
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.root = mock.patch.object(cli, "require_operator")
        self.root.start()
        self.addCleanup(self.root.stop)
        software = mock.patch.object(cli, "INSTALL_SOFTWARE", self.home / "install-software")
        software.start()
        self.addCleanup(software.stop)

    def save(self, provider):
        cli.save_config({"schema_version": 1, "provider": provider})

    def test_atomic_write_permissions_and_backup(self):
        self.save({"kind": "codex"})
        path = cli.config_dir() / "config.json"
        previous = path.read_text()
        self.save({"kind": "command", "argv": ["agent", "--inline"]})
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
        backups = list(path.parent.glob("config.json.backup-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), previous)
        self.assertEqual(stat.S_IMODE(backups[0].stat().st_mode), 0o600)
        self.assertFalse(list(path.parent.glob(".rougarou-*")))

    def test_refuses_symlink_config_without_changing_target(self):
        cli.private_directory(cli.config_dir())
        target = self.home / "other"
        target.write_text("unchanged")
        (cli.config_dir() / "config.json").symlink_to(target)
        with self.assertRaises(cli.OperatorError):
            self.save({"kind": "codex"})
        self.assertEqual(target.read_text(), "unchanged")

    def test_refuses_public_credentials(self):
        path = cli.config_dir() / "credentials.json"
        cli.atomic_write(path, json.dumps({cli.SECRET_ENV: "never-print-this"}))
        path.chmod(0o644)
        with self.assertRaises(cli.OperatorError) as error:
            cli.read_json(path)
        self.assertNotIn("never-print-this", str(error.exception))

    def test_corrupt_config_is_not_overwritten(self):
        path = cli.config_dir() / "config.json"
        cli.atomic_write(path, "{corrupted")
        with self.assertRaises(cli.OperatorError):
            cli.load_config()
        self.assertEqual(path.read_text(), "{corrupted")

    def test_rejects_unknown_injected_provider_fields(self):
        self.save({"kind": "codex", "shell": "touch /tmp/unwanted"})
        with self.assertRaises(cli.OperatorError):
            cli.load_config()

    def test_custom_arguments_are_never_shell_evaluated(self):
        malicious = "$(touch /tmp/rougarou-should-not-exist); echo secret"
        argv, environment = cli.ai_command({"kind": "command", "argv": ["agent", malicious]}, ["x | y"], "", {cli.SECRET_ENV: "hidden"})
        self.assertEqual(argv, ["agent", malicious, "x | y"])
        self.assertNotIn(cli.SECRET_ENV, environment)
        self.assertNotIn("shell=True", SOURCE.read_text())
        self.save({"kind": "command", "argv": ["agent", malicious]})
        with mock.patch.object(cli, "require_command", return_value="/usr/bin/agent"), mock.patch.object(cli.os, "execvpe") as execute:
            cli.ai(["x | y"])
        self.assertEqual(execute.call_args.args[:2], ("/usr/bin/agent", argv))

    def test_default_matches_omarchy_auto_review_without_root_bypass(self):
        argv, _ = cli.ai_command({"kind": "codex"}, ["a prompt"], CODEX_HELP, {})
        self.assertEqual(argv, ["codex", "--approve-for-me", "--no-alt-screen", "a prompt"])
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", argv)
        self.assertNotIn("sudo", argv)

    def test_no_installer_selection_defaults_to_no_ai(self):
        with mock.patch.object(cli, "require_terminal"), mock.patch.object(cli, "field", return_value="5") as field, mock.patch.object(cli, "codex_login") as login:
            cli.provider()
        self.assertEqual(field.call_args.args, ("Provider", "5"))
        login.assert_not_called()
        self.assertNotIn("provider", cli.load_config())

    def test_installer_record_requires_all_four_valid_unique_fields(self):
        valid = "agent=opencode\ndocker=rootless\npodman=false\nherdr=true\n"
        cli.INSTALL_SOFTWARE.write_text(valid)
        self.assertEqual(cli.installer_software(), {"agent": "opencode", "docker": "rootless", "podman": "false", "herdr": "true"})
        for record in (valid + "agent=claude\n", valid.replace("opencode", "$(touch injected)"), "agent=codex\n", valid + "unexpected=true\n"):
            cli.INSTALL_SOFTWARE.write_text(record)
            self.assertEqual(cli.installer_software(), {})
        cli.INSTALL_SOFTWARE.unlink()
        target = self.home / "other-record"
        target.write_text(valid)
        cli.INSTALL_SOFTWARE.symlink_to(target)
        self.assertEqual(cli.installer_software(), {})

    def test_installer_agent_hint_preserves_existing_provider_and_absent_package(self):
        with mock.patch.object(cli, "agent_executable", return_value=None):
            for name in ("none", "codex", "gemini"):
                self.assertEqual(cli.provider_default({}, {"agent": name}), "5")
            self.assertEqual(cli.provider_default({}, {"agent": "opencode"}), "6")
            self.assertEqual(cli.provider_default({}, {"agent": "claude"}), "8")
        with mock.patch.object(cli, "agent_executable", return_value="/usr/bin/codex"):
            self.assertEqual(cli.provider_default({}, {"agent": "codex"}), "1")
            self.assertEqual(cli.provider_default({}, {"agent": "gemini"}), "7")
        self.assertEqual(cli.provider_default({"provider": {"kind": "codex"}}, {"agent": "opencode"}), "5")

    def test_named_installed_agents_preserve_native_auth_and_argument_boundaries(self):
        for option, name in (("6", "opencode"), ("7", "gemini"), ("8", "claude")):
            with self.subTest(name=name), mock.patch.object(cli, "require_terminal"), mock.patch.object(cli, "field", return_value=option), mock.patch.object(cli, "agent_executable", return_value=f"/usr/bin/{name}"), mock.patch.object(cli, "run") as run, mock.patch.object(cli, "codex_login") as login:
                cli.provider()
                selected = cli.load_config()["provider"]
                self.assertEqual(selected, {"kind": "command", "argv": [f"/usr/bin/{name}"]})
                argv, _ = cli.ai_command(selected, ["$(touch unwanted); hello"], "", {})
                self.assertEqual(argv, [f"/usr/bin/{name}", "$(touch unwanted); hello"])
                run.assert_not_called()
                login.assert_not_called()

    def test_deferred_download_decline_preserves_provider_and_credentials(self):
        self.save({"kind": "codex"})
        credentials = cli.config_dir() / "credentials.json"
        cli.atomic_write(credentials, '{"preserve":"existing"}\n')
        before = (cli.config_dir() / "config.json").read_bytes()
        for name in ("opencode", "claude"):
            with mock.patch.object(cli, "agent_executable", return_value=None), mock.patch.object(cli, "confirm", return_value=False) as confirm:
                self.assertIsNone(cli.named_agent(name))
                self.assertFalse(confirm.call_args.kwargs["default"])
        self.assertEqual((cli.config_dir() / "config.json").read_bytes(), before)
        self.assertEqual(credentials.read_text(), '{"preserve":"existing"}\n')

    def test_deferred_download_requires_confirmation_then_uses_absolute_launcher(self):
        for name in ("opencode", "claude"):
            launcher = self.home / ".local/bin" / name
            installer = types.SimpleNamespace(InstallError=RuntimeError, install=mock.Mock(return_value=launcher))
            with mock.patch.dict(sys.modules, {"agent_install": installer}), mock.patch.object(cli, "agent_executable", return_value=None), mock.patch.object(cli, "confirm", return_value=True):
                self.assertEqual(cli.named_agent(name), {"kind": "command", "argv": [str(launcher)]})
            installer.install.assert_called_once_with(name)

    def test_deferred_download_failure_does_not_save_a_selection(self):
        installer = types.SimpleNamespace(InstallError=RuntimeError, install=mock.Mock(side_effect=RuntimeError("Checksum mismatch")))
        with mock.patch.dict(sys.modules, {"agent_install": installer}), mock.patch.object(cli, "require_terminal"), mock.patch.object(cli, "field", return_value="6"), mock.patch.object(cli, "agent_executable", return_value=None), mock.patch.object(cli, "confirm", return_value=True), self.assertRaisesRegex(cli.OperatorError, "Checksum mismatch"):
            cli.provider()
        self.assertNotIn("provider", cli.load_config())

    def test_setup_keeps_existing_provider_and_only_prints_rootless_hint(self):
        self.save({"kind": "command", "argv": ["my-agent", "--custom"]})
        cli.INSTALL_SOFTWARE.write_text("agent=claude\ndocker=rootless\npodman=true\nherdr=false\n")
        output = io.StringIO()
        with mock.patch.object(cli, "require_terminal"), mock.patch.object(cli, "welcome"), mock.patch.object(cli, "confirm", return_value=True), mock.patch.object(cli, "github"), mock.patch.object(cli, "provider") as provider, mock.patch.object(cli, "run") as run, mock.patch.object(cli.os.path, "isfile", return_value=False), contextlib.redirect_stdout(output):
            cli.setup()
        provider.assert_not_called()
        run.assert_not_called()
        self.assertEqual(cli.load_config()["provider"], {"kind": "command", "argv": ["my-agent", "--custom"]})
        self.assertIn("rougarou-docker setup", output.getvalue())
        self.assertIn("existing AI selection is unchanged", output.getvalue())

    def test_batch_keeps_native_sandbox_and_never_interactive_approval(self):
        argv, _ = cli.ai_command({"kind": "codex"}, ["--sandbox", "read-only", "task"], CODEX_HELP, {}, batch=True)
        self.assertEqual(argv, ["codex", "-a", "never", "exec", "--sandbox", "read-only", "task"])
        self.assertNotIn("--approve-for-me", argv)
        self.assertNotIn("sudo", argv)

    def test_access_status_ignores_cached_sudo_without_changing_policy(self):
        state = {"username": "owner", "uid": 1000, "mode": "password", "owner": "owner"}
        outputs = [subprocess.CompletedProcess([], 0, json.dumps(state), ""), subprocess.CompletedProcess([], 0, "", "")]
        with mock.patch.object(cli.pwd, "getpwuid", return_value=mock.Mock(pw_name="owner")), mock.patch.object(cli, "run", side_effect=outputs) as run:
            status = cli.access_status()
        self.assertTrue(status["passwordless_probe"])
        self.assertEqual(run.call_args_list[0].args[0], [cli.ACCESS_HELPER, "status", "owner", "--json"])
        self.assertEqual(run.call_args_list[1].args[0], ["sudo", "-n", "-k", "--", "/usr/bin/true"])

    def test_headless_access_requires_explicit_noninteractive_acknowledgement(self):
        with mock.patch.object(cli, "interactive", return_value=False), mock.patch.object(cli, "run") as run:
            with self.assertRaises(cli.OperatorError):
                cli.access(["headless"])
            run.assert_not_called()

    def test_headless_access_uses_real_account_and_fixed_argv(self):
        with mock.patch.object(cli, "interactive", return_value=False), mock.patch.object(cli.pwd, "getpwuid", return_value=mock.Mock(pw_name="owner")), mock.patch.dict(os.environ, {"USER": "attacker; command"}), mock.patch.object(cli, "run", return_value=subprocess.CompletedProcess([], 0)) as run, mock.patch.object(cli, "access_status", return_value={"mode": "headless", "passwordless_probe": True}):
            self.assertEqual(cli.access(["headless", "--acknowledge-root-access"]), 0)
        self.assertEqual(run.call_args.args[0], ["sudo", "-n", "--", cli.ACCESS_HELPER, "set", "owner", "headless", "--acknowledge-root-access"])

    def test_headless_access_decline_has_no_policy_side_effects(self):
        with mock.patch.object(cli, "interactive", return_value=True), mock.patch.object(cli, "confirm", return_value=False), mock.patch.object(cli, "run") as run:
            self.assertEqual(cli.access(["headless"]), 0)
            run.assert_not_called()

    def test_older_codex_uses_native_authority(self):
        output = io.StringIO()
        with contextlib.redirect_stderr(output):
            argv, _ = cli.ai_command({"kind": "codex"}, [], "--no-alt-screen", {})
        self.assertEqual(argv, ["codex", "--no-alt-screen"])
        self.assertIn("native approval", output.getvalue())

    def test_provider_values_are_toml_quoted_not_injected(self):
        model = 'model"; sandbox_mode="danger-full-access'
        endpoint = 'https://example.com/v1/"injection'
        provider = {"kind": "responses", "endpoint": endpoint, "model": model, "authenticated": False}
        argv, _ = cli.ai_command(provider, [], CODEX_HELP, {})
        settings = [argv[index + 1] for index, value in enumerate(argv) if value == "-c"]
        parsed = tomllib.loads("\n".join(settings))
        self.assertEqual(parsed["model"], model)
        self.assertEqual(parsed["model_providers"]["rougarou"]["base_url"], endpoint)
        self.assertFalse(parsed["model_providers"]["rougarou"]["requires_openai_auth"])
        self.assertNotIn("sandbox_mode", parsed)

    def test_secret_only_passed_in_process_environment(self):
        secret = "provider-secret-test"
        cli.atomic_write(cli.config_dir() / "credentials.json", json.dumps({"endpoint": "https://example.com/v1", cli.SECRET_ENV: secret}))
        provider = {"kind": "responses", "endpoint": "https://example.com/v1", "model": "model", "authenticated": True}
        argv, environment = cli.ai_command(provider, [], CODEX_HELP, {"OTHER": "yes"})
        self.assertNotIn(secret, " ".join(argv))
        self.assertEqual(environment[cli.SECRET_ENV], secret)
        self.assertEqual(environment["OTHER"], "yes")
        self.assertNotIn(secret, json.dumps(provider))

    def test_provider_key_cannot_be_reused_for_changed_endpoint(self):
        cli.atomic_write(cli.config_dir() / "credentials.json", json.dumps({"endpoint": "https://original.example/v1", cli.SECRET_ENV: "private-key"}))
        changed = {"kind": "responses", "endpoint": "https://other.example/v1", "model": "model", "authenticated": True}
        with self.assertRaises(cli.OperatorError):
            cli.ai_command(changed, [], CODEX_HELP, {})

    def test_openai_key_is_hidden_and_sent_through_stdin(self):
        secret = "secret-openai-test"
        outputs = [subprocess.CompletedProcess([], 1, "", ""), subprocess.CompletedProcess([], 0, "", "")]
        output = io.StringIO()
        with mock.patch.object(cli, "require_command"), mock.patch.object(cli, "run", side_effect=outputs) as run, mock.patch.object(cli, "field", return_value="2"), mock.patch.object(cli.getpass, "getpass", return_value=secret), contextlib.redirect_stdout(output):
            cli.codex_login()
        call = run.call_args
        self.assertEqual(call.args[0], ["codex", "login", "--with-api-key"])
        self.assertEqual(call.kwargs["input"], secret + "\n")
        self.assertTrue(call.kwargs["capture_output"])
        self.assertNotIn(secret, output.getvalue())
        self.assertFalse((cli.config_dir() / "credentials.json").exists())

    def test_remote_http_and_embedded_credentials_rejected(self):
        for url in ["http://api.example.com/v1", "https://user:pass@example.com/v1", "https://example.com/v1?key=abc", "file:///tmp/key"]:
            with self.subTest(url=url), self.assertRaises(cli.OperatorError):
                cli.validate_url(url)
        self.assertEqual(cli.validate_url("http://127.0.0.1:11434/v1"), "http://127.0.0.1:11434/v1")
        self.assertEqual(cli.validate_url("http://[::1]:11434/v1"), "http://[::1]:11434/v1")

    def test_first_login_non_tty_has_no_side_effects(self):
        with mock.patch.object(cli, "interactive", return_value=False), mock.patch("builtins.input", side_effect=AssertionError("must not prompt")):
            self.assertEqual(cli.setup(first_login=True), 0)
        self.assertFalse(cli.config_dir().exists())

    def test_manual_setup_non_tty_has_actionable_error(self):
        with mock.patch.object(cli, "interactive", return_value=False), self.assertRaises(cli.OperatorError):
            cli.setup()

    def test_first_login_can_be_dismissed_and_manual_setup_resumed(self):
        with mock.patch.object(cli, "interactive", return_value=True), mock.patch.object(cli.os, "geteuid", return_value=os.geteuid()), mock.patch.object(cli, "confirm", return_value=False), mock.patch.object(cli, "welcome") as welcome:
            # The test process may be root, so simulate a non-root initial gate only.
            with mock.patch.object(cli.os, "geteuid", side_effect=[1000] + [os.geteuid()] * 30):
                cli.setup(first_login=True)
            welcome.assert_not_called()
            saved = cli.load_config()
            self.assertTrue(saved["first_login_seen"])
            self.assertNotIn("setup_complete", saved)
            cli.setup()
            welcome.assert_called_once_with()
            self.assertTrue(cli.load_config()["setup_complete"])
            self.assertTrue((self.home / "Work").is_dir())

    def test_no_color_in_piped_output_and_no_color_override(self):
        for tty, env in [(False, {}), (True, {"NO_COLOR": "1"}), (True, {"TERM": "dumb"})]:
            output = io.StringIO()
            with mock.patch.dict(os.environ, env), contextlib.redirect_stdout(output), mock.patch.object(output, "isatty", return_value=tty):
                cli.welcome()
            self.assertNotIn("\033[", output.getvalue())
            self.assertIn("ROUGAROU OS", output.getvalue())

    def test_truecolor_uses_current_theme_accent(self):
        output = io.StringIO()
        with mock.patch.dict(os.environ, {"TERM": "xterm-256color"}), contextlib.redirect_stdout(output), mock.patch.object(output, "isatty", return_value=True):
            os.environ.pop("NO_COLOR", None)
            cli.banner()
        self.assertIn("\033[38;2;121;197;142m", output.getvalue())

    def test_root_ai_refused(self):
        self.root.stop()
        with mock.patch.object(cli.os, "geteuid", return_value=0), self.assertRaises(cli.OperatorError):
            cli.ai([])

    def test_github_auth_uses_device_flow_and_no_shell(self):
        outputs = [subprocess.CompletedProcess([], 1, "", ""), subprocess.CompletedProcess([], 0), subprocess.CompletedProcess([], 0)]
        with mock.patch.object(cli, "require_terminal"), mock.patch.object(cli, "configure_git"), mock.patch.object(cli, "require_command"), mock.patch.object(cli, "confirm", return_value=True), mock.patch.object(cli, "run", side_effect=outputs) as run:
            cli.github()
        login_call = run.call_args_list[1]
        self.assertEqual(login_call.args[0], ["gh", "auth", "login", "--hostname", "github.com", "--git-protocol", "https", "--web"])
        self.assertEqual(login_call.kwargs["env"]["BROWSER"], "/bin/true")


if __name__ == "__main__":
    unittest.main()
