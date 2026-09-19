"""Deferred agent installs preserve existing tools and verify before exposure."""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import subprocess
import tarfile
import tempfile
import unittest
from unittest import mock

SOURCE = Path(__file__).resolve().parents[1] / "rootfs/usr/lib/rougarou/agent_install.py"
spec = importlib.util.spec_from_file_location("agent_install", SOURCE)
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class AgentInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.data = b"verified executable test fixture\n"
        self.pin = {"name": "Claude Code", "version": "1.2.3", "format": "raw",
                    "binary": "claude", "size": len(self.data),
                    "sha256": hashlib.sha256(self.data).hexdigest()}
        home = mock.patch.object(installer.Path, "home", return_value=self.home)
        home.start()
        self.addCleanup(home.stop)
        pin = mock.patch.object(installer, "read_pin", return_value=self.pin)
        pin.start()
        self.addCleanup(pin.stop)
        # Tests run under ordinary host and root CI users. Preserve filesystem
        # ownership checks while bypassing only the root entrypoint guard.
        self.real_uid = os.geteuid()

    def invoke(self):
        calls = iter([1000])
        def uid():
            return next(calls, self.real_uid)
        with mock.patch.object(installer.os, "geteuid", side_effect=uid):
            return installer.install("claude")

    def write_download(self, pin, path):
        path.write_bytes(self.data)

    def test_verified_download_is_idempotent_and_disables_updates(self):
        with mock.patch.object(installer, "download", side_effect=self.write_download) as download:
            path = self.invoke()
            self.assertEqual(self.invoke(), path)
        download.assert_called_once()
        self.assertIn("export DISABLE_UPDATES=1", path.read_text())
        binary = self.home / ".local/share/rougarou/agents/claude/1.2.3/claude"
        self.assertEqual(binary.read_bytes(), self.data)
        self.assertEqual(binary.stat().st_mode & 0o777, 0o700)
        self.assertFalse(list(binary.parent.parent.glob(".download-*")))

    def test_existing_launcher_is_preserved_without_download(self):
        path = self.home / ".local/bin/claude"
        path.parent.mkdir(parents=True)
        path.write_text("operator's existing installation")
        with mock.patch.object(installer, "download") as download:
            with self.assertRaises(installer.InstallError):
                self.invoke()
        self.assertEqual(path.read_text(), "operator's existing installation")
        download.assert_not_called()

    def test_symlink_parent_is_rejected_without_writing_elsewhere(self):
        outside = self.home / "elsewhere"
        outside.mkdir()
        (self.home / ".local").symlink_to(outside)
        with mock.patch.object(installer, "download") as download:
            with self.assertRaises(installer.InstallError):
                self.invoke()
        self.assertEqual(list(outside.iterdir()), [])
        download.assert_not_called()

    def test_bad_payload_never_creates_launcher_or_version(self):
        with mock.patch.object(installer, "download", side_effect=lambda _, path: path.write_bytes(b"wrong")):
            with self.assertRaises(installer.InstallError):
                self.invoke()
        self.assertFalse((self.home / ".local/bin/claude").exists())
        self.assertEqual(list((self.home / ".local/share/rougarou/agents/claude").iterdir()), [])

    def test_failed_network_is_retryable_and_keeps_no_partial_version(self):
        with mock.patch.object(installer, "download", side_effect=installer.InstallError("network")):
            with self.assertRaises(installer.InstallError):
                self.invoke()
        with mock.patch.object(installer, "download", side_effect=self.write_download):
            self.assertTrue(self.invoke().is_file())

    def test_archive_path_and_link_entries_are_never_extracted(self):
        artifact = self.home / "archive.tar.gz"
        for name, kind in [("../opencode", tarfile.REGTYPE), ("opencode", tarfile.SYMTYPE)]:
            with self.subTest(name=name, kind=kind):
                with tarfile.open(artifact, "w:gz") as archive:
                    entry = tarfile.TarInfo(name)
                    entry.type = kind
                    entry.linkname = "/etc/passwd"
                    entry.size = 0
                    archive.addfile(entry, io.BytesIO(b""))
                with self.assertRaises(installer.InstallError):
                    installer.unpack({"format": "tar.gz", "binary": "opencode"}, artifact, self.home / "binary")
        self.assertFalse((self.home / "binary").exists())

    def test_download_hash_failure_is_rejected_after_successful_http(self):
        artifact = self.home / "payload"
        artifact.write_bytes(b"x" * len(self.data))
        pin = dict(self.pin, url="https://example.invalid/pinned")
        with mock.patch.object(installer.subprocess, "run", return_value=mock.Mock(returncode=0)) as run:
            with self.assertRaises(installer.InstallError):
                installer.download(pin, artifact)
        self.assertEqual(run.call_args.args[0][1], "--disable")
        self.assertIn("--proto-redir", run.call_args.args[0])

    def test_concurrent_install_is_rejected_before_second_download(self):
        def download(pin, path):
            with self.assertRaisesRegex(installer.InstallError, "already running"):
                self.invoke()
            self.write_download(pin, path)
        with mock.patch.object(installer, "download", side_effect=download) as fetch:
            self.assertTrue(self.invoke().is_file())
        fetch.assert_called_once()

    def test_version_created_during_download_is_preserved(self):
        target = self.home / ".local/share/rougarou/agents/claude/1.2.3"
        def download(pin, path):
            target.mkdir()
            self.write_download(pin, path)
        with mock.patch.object(installer, "download", side_effect=download):
            with self.assertRaises(installer.InstallError):
                self.invoke()
        self.assertTrue(target.is_dir())
        self.assertEqual(list(target.iterdir()), [])
        self.assertFalse((self.home / ".local/bin/claude").exists())

    def test_launcher_write_failure_is_clean_and_retryable(self):
        with mock.patch.object(installer, "download", side_effect=self.write_download), mock.patch.object(installer.os, "fsync", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(installer.InstallError, "disk full"):
                self.invoke()
        self.assertEqual(list((self.home / ".local/bin").iterdir()), [])
        self.assertFalse((self.home / ".local/share/rougarou/agents/claude/1.2.3").exists())
        with mock.patch.object(installer, "download", side_effect=self.write_download):
            self.assertTrue(self.invoke().is_file())

    def test_launcher_created_during_download_is_preserved(self):
        launcher = self.home / ".local/bin/claude"
        def download(pin, path):
            launcher.write_text("operator tool appeared during download")
            self.write_download(pin, path)
        with mock.patch.object(installer, "download", side_effect=download):
            with self.assertRaises(installer.InstallError):
                self.invoke()
        self.assertEqual(launcher.read_text(), "operator tool appeared during download")
        self.assertEqual(list(launcher.parent.iterdir()), [launcher])
        self.assertFalse((self.home / ".local/share/rougarou/agents/claude/1.2.3").exists())

    def test_malformed_archive_is_an_operator_error(self):
        self.pin.update(format="tar.gz", binary="opencode", binary_size=20,
                        binary_sha256=hashlib.sha256(b"something").hexdigest())
        with mock.patch.object(installer, "download", side_effect=self.write_download):
            with self.assertRaises(installer.InstallError):
                self.invoke()
        self.assertFalse((self.home / ".local/bin/claude").exists())

    def test_read_pin_rejects_bad_types_and_requires_extracted_digest(self):
        # Read the real validator, bypassing only this fixture's pin stub.
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for data in ([], {"version": 5}, dict(self.pin, name="bad\x1bname"),
                     dict(self.pin, binary="opencode", format="tar.gz")):
            with mock.patch.object(module.Path, "read_text", return_value=json.dumps(data)):
                with self.assertRaises(module.InstallError):
                    module.read_pin("opencode")
        for agent in ("claude", "opencode"):
            self.assertEqual(module.read_pin(agent)["binary"], agent)

    def test_opencode_wrapper_forces_update_flag_and_preserves_argv(self):
        binary = b'#!/usr/bin/python3\nimport json,os,sys\nprint(json.dumps([os.environ.get("OPENCODE_DISABLE_AUTOUPDATE"),sys.argv[1:]]))\n'
        self.pin.update(name="OpenCode", binary="opencode", format="tar.gz", binary_size=len(binary),
                        binary_sha256=hashlib.sha256(binary).hexdigest())
        def download(pin, path):
            with tarfile.open(path, "w:gz") as archive:
                member = tarfile.TarInfo("opencode")
                member.size = len(binary)
                archive.addfile(member, io.BytesIO(binary))
        calls = iter([1000])
        with mock.patch.object(installer.os, "geteuid", side_effect=lambda: next(calls, self.real_uid)), mock.patch.object(installer, "download", side_effect=download):
            launcher = installer.install("opencode")
        arguments = ["one space", "$(touch never); literal"]
        result = subprocess.run([str(launcher), *arguments], env=dict(os.environ, OPENCODE_DISABLE_AUTOUPDATE="0"), text=True, capture_output=True, check=True)
        self.assertEqual(json.loads(result.stdout), ["1", arguments])
        result = subprocess.run([str(launcher), "upgrade"], text=True, capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("reviewed Rougarou", result.stderr)


if __name__ == "__main__":
    unittest.main()
