"""Repository policy/integrity tests; no release key is generated or accessed."""
import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location("rougarou_repository", Path(__file__).resolve().parents[1] / "scripts" / "repo.py")
repo = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(repo)


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.source = self.base / "input"
        self.source.mkdir()
        self.root = self.base / "published"
        self.home = self.base / "private-key-home"
        self.home.mkdir(mode=0o700)
        (self.home / "secret-key-never-copy").write_text("private material")
        self.keyring = self.base / "public.gpg"
        self.keyring.write_bytes(b"public fixture")
        self.key = "A" * 40
        self.package = self.source / "example_1_amd64.deb"
        self.package.write_bytes(b"package fixture version one")
        self.write_index()
        self.calls = []
        self.subprocess_patch = patch.object(repo.subprocess, "run", side_effect=self.crypto_fixture)
        self.subprocess_patch.start()
        self.addCleanup(self.subprocess_patch.stop)

    def write_index(self):
        self.entry = {"Package": "example", "Version": "1", "Architecture": "amd64",
                      "Filename": self.package.name, "Size": str(self.package.stat().st_size),
                      "SHA256": repo.sha256(self.package)}
        (self.source / "Packages").write_bytes(repo.encode_records([self.entry]))

    def crypto_fixture(self, command, **kwargs):
        """Only fake crypto I/O; package, metadata, policy and file operations run."""
        self.calls.append((command, kwargs))
        if command[0] == "gpg":
            output = Path(command[command.index("--output") + 1])
            payload = Path(command[-1]).read_bytes()
            output.write_bytes(payload if "--clearsign" in command else hashlib.sha256(payload).digest())
        elif "--output" in command:
            Path(command[command.index("--output") + 1]).write_bytes(Path(command[-1]).read_bytes())
        elif command[0] == "gpgv":
            expected = hashlib.sha256(Path(command[-1]).read_bytes()).digest()
            if Path(command[-2]).read_bytes() != expected:
                raise repo.subprocess.CalledProcessError(1, command)

    def arguments(self, **overrides):
        values = dict(root=self.root, source=self.source, snapshot="20260918T120000Z",
                      gnupghome=self.home, key=self.key, valid_days=30,
                      provenance="verified ISO build test", keyring=self.keyring,
                      channel="testing", evidence=None, approve_stable=False)
        values.update(overrides)
        return argparse.Namespace(**values)

    def create(self, **kwargs):
        args = self.arguments(**kwargs)
        with contextlib.redirect_stdout(io.StringIO()):
            repo.snapshot(args)
        return args

    def publish(self, args):
        with contextlib.redirect_stdout(io.StringIO()):
            repo.promote(args)

    def test_tampered_package_refuses_signature(self):
        self.package.write_bytes(b"tampered")
        with self.assertRaises(repo.RepositoryError):
            repo.sign_flat(self.source, self.home, self.key, "stable")
        self.assertEqual(self.calls, [])
        self.assertFalse((self.source / "InRelease").exists())

    def test_path_escape_refused(self):
        self.entry["Filename"] = "../outside.deb"
        (self.source / "Packages").write_bytes(repo.encode_records([self.entry]))
        with self.assertRaises(repo.RepositoryError):
            repo.validate_packages(self.source)

    def test_existing_snapshot_not_overwritten_and_key_not_copied(self):
        args = self.create()
        with self.assertRaises(repo.RepositoryError):
            repo.snapshot(args)
        self.assertFalse(list(self.root.rglob("secret-key-never-copy")))
        self.assertEqual(repo.verify(self.root / "snapshots" / args.snapshot, self.keyring)["snapshot"]["snapshot"], args.snapshot)

    def test_modified_signed_metadata_refused(self):
        args = self.create()
        target = self.root / "snapshots" / args.snapshot
        (target / "snapshot.json").write_text('{"snapshot":"wrong"}')
        with self.assertRaises(repo.RepositoryError):
            repo.verify(target, self.keyring)

    def test_one_extra_gpg_terminal_newline_is_accepted_with_detached_verification(self):
        args = self.create()
        target = self.root / "snapshots" / args.snapshot
        def extracted_extra_newline(command, **kwargs):
            self.crypto_fixture(command, **kwargs)
            if command[0] == "gpgv" and "--output" in command:
                path = Path(command[command.index("--output") + 1])
                path.write_bytes(path.read_bytes() + b"\n")
        self.calls.clear()
        with patch.object(repo.subprocess, "run", side_effect=extracted_extra_newline):
            self.assertEqual(repo.verify(target, self.keyring)["snapshot"]["snapshot"], args.snapshot)
        self.assertTrue(any(command[0] == "gpgv" and "--output" not in command
                            and command[-1] == str(target / "Release") for command, _ in self.calls))

    def test_other_cleartext_differences_are_rejected(self):
        args = self.create()
        target = self.root / "snapshots" / args.snapshot
        changes = [lambda data: data + b"\n\n",
                   lambda data: data.replace(b"Origin: Rougarou", b"Origin: other"),
                   lambda data: data.replace(b"\nDate:", b"\n\nDate:"),
                   lambda data: data.replace(b"\n", b"\r\n")]
        for change in changes:
            def altered_extraction(command, **kwargs):
                self.crypto_fixture(command, **kwargs)
                if command[0] == "gpgv" and "--output" in command:
                    path = Path(command[command.index("--output") + 1])
                    path.write_bytes(change(path.read_bytes()))
            with self.subTest(change=change), patch.object(repo.subprocess, "run", side_effect=altered_extraction):
                with self.assertRaisesRegex(repo.RepositoryError, "InRelease does not authenticate"):
                    repo.verify(target, self.keyring)

    def test_appended_release_newline_is_rejected_by_detached_signature(self):
        args = self.create()
        target = self.root / "snapshots" / args.snapshot
        release = target / "Release"
        release.write_bytes(release.read_bytes() + b"\n")
        def extracted_extra_newline(command, **kwargs):
            self.crypto_fixture(command, **kwargs)
            if command[0] == "gpgv" and "--output" in command:
                path = Path(command[command.index("--output") + 1])
                path.write_bytes(path.read_bytes() + b"\n")
        with patch.object(repo.subprocess, "run", side_effect=extracted_extra_newline):
            with self.assertRaises(repo.subprocess.CalledProcessError):
                repo.verify(target, self.keyring)

    def test_stable_requires_current_testing_and_explicit_approval(self):
        args = self.create()
        args.channel = "stable"
        with self.assertRaises((repo.RepositoryError, OSError)):
            self.publish(args)
        args.channel = "testing"
        self.publish(args)
        args.channel = "stable"
        with self.assertRaises(repo.RepositoryError):
            self.publish(args)
        args.evidence = self.base / "evidence.json"
        args.evidence.write_text(json.dumps({"snapshot": args.snapshot, "result": "pass",
                                             "tests": ["offline install", "reboot"],
                                             "approved_by": "operator", "token": "DO-NOT-COPY"}))
        with self.assertRaises(repo.RepositoryError):
            self.publish(args)
        args.approve_stable = True
        self.publish(args)
        current = self.root / "channels" / "stable"
        self.assertTrue(current.is_symlink())
        self.assertEqual(repo.verify(current, self.keyring)["release"]["Suite"], "stable")
        self.assertNotIn("DO-NOT-COPY", (current / "promotion.json").read_text())
        self.assertIn("promotion.json", (current / "Release").read_text())

    def test_stable_cannot_skip_current_testing_snapshot(self):
        old = self.create()
        self.publish(old)
        new = self.create(snapshot="20260919T120000Z")
        self.publish(new)
        old.channel = "stable"
        old.approve_stable = True
        with self.assertRaises(repo.RepositoryError):
            self.publish(old)

    def test_channel_switch_preserves_old_packages_and_by_hash(self):
        old = self.create()
        self.publish(old)
        old_target = (self.root / "channels" / "testing").resolve()
        old_index = (old_target / "Packages").read_bytes()
        old_filename = repo.records(old_target / "Packages")[0]["Filename"]
        self.package.write_bytes(b"different package fixture version two")
        self.write_index()
        new = self.create(snapshot="20260919T120000Z")
        self.publish(new)
        current = self.root / "channels" / "testing"
        self.assertNotEqual(current.resolve(), old_target)
        self.assertTrue(old_target.is_dir())
        self.assertTrue((current / old_filename).is_file())
        self.assertEqual((current / "by-hash" / "SHA256" / hashlib.sha256(old_index).hexdigest()).read_bytes(), old_index)

    def test_private_home_must_be_external_and_private(self):
        self.home.chmod(0o755)
        with self.assertRaises(repo.RepositoryError):
            repo.sign_flat(self.source, self.home, self.key, "stable")
        internal = self.source / "gnupg"
        internal.mkdir(mode=0o700)
        with self.assertRaises(repo.RepositoryError):
            repo.sign_flat(self.source, internal, self.key, "stable")

    def test_passphrase_only_passed_through_standard_input(self):
        read_fd, write_fd = os.pipe()
        self.addCleanup(os.close, read_fd)
        os.write(write_fd, b"secret fixture\n")
        os.close(write_fd)
        with patch.dict(os.environ, {"ROUGAROU_SIGNING_PASSPHRASE_FD": str(read_fd)}), patch.object(repo, "_signing_password", None):
            repo.sign_flat(self.source, self.home, self.key, "stable", 3650)
        signing_calls = [(command, options) for command, options in self.calls if command[0] == "gpg"]
        self.assertEqual(len(signing_calls), 2)
        for command, options in signing_calls:
            self.assertNotIn("secret fixture", " ".join(command))
            self.assertEqual(options["input"], b"secret fixture\n")
            self.assertIn("--passphrase-fd", command)
        for path in self.source.rglob("*"):
            if path.is_file():
                self.assertNotIn(b"secret fixture", path.read_bytes())


if __name__ == "__main__":
    unittest.main()
