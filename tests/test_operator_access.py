"""Isolated privilege-policy tests; never inspect or alter the host's sudoers."""
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock


SOURCE = Path(__file__).resolve().parents[1] / "rootfs/usr/lib/rougarou-system/operator-access"
loader = importlib.machinery.SourceFileLoader("operator_access", str(SOURCE))
spec = importlib.util.spec_from_loader(loader.name, loader)
access = importlib.util.module_from_spec(spec)
loader.exec_module(access)


class OperatorAccessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.etc = Path(self.temp.name)
        (self.etc / "sudoers.d").mkdir()
        (self.etc / "passwd").write_text("root:x:0:0:root:/root:/bin/bash\nowner:x:1000:1000:Owner:/home/owner:/bin/bash\nother:x:1001:1001:Other:/home/other:/bin/bash\nservice:x:999:999:Service:/srv/service:/bin/false\n")
        self.validate = mock.Mock()
        self.policy = access.Policy(self.etc, os.geteuid(), self.validate)

    def test_requires_explicit_acknowledgement_without_writes(self):
        with self.assertRaises(access.AccessError):
            self.policy.set("owner", "headless")
        self.assertFalse(self.policy.directory.exists())
        self.assertFalse(self.policy.grant.exists())

    def test_grants_only_named_owner_and_writes_public_state(self):
        result = self.policy.set("owner", "headless", acknowledged=True)
        self.assertEqual(result["mode"], "headless")
        self.assertIn("owner ALL=(ALL:ALL) NOPASSWD: ALL", self.policy.grant.read_text())
        self.assertNotIn("other", self.policy.grant.read_text())
        self.assertEqual(stat.S_IMODE(self.policy.grant.stat().st_mode), 0o440)
        self.assertEqual(stat.S_IMODE(self.policy.state.stat().st_mode), 0o644)
        self.assertEqual(json.loads(self.policy.state.read_text())["uid"], 1000)
        self.assertEqual(self.policy.status("other")["mode"], "unmanaged")
        self.assertEqual(self.validate.call_args_list[0].args, ())
        self.assertEqual(self.validate.call_args_list[-1].args, ())
        self.assertEqual(len(self.validate.call_args_list), 3)
        self.assertFalse(list(self.policy.grant.parent.glob(".rougarou-check-*")))

    def test_password_mode_revokes_only_managed_grant(self):
        custom = self.policy.grant.parent / "99-local-owner"
        contents = "owner ALL=(ALL) NOPASSWD: /usr/bin/true\n"
        custom.write_text(contents)
        self.policy.set("owner", "headless", acknowledged=True)
        self.policy.set("owner", "password")
        self.assertFalse(self.policy.grant.exists())
        self.assertEqual(self.policy.status("owner")["mode"], "password")
        self.assertEqual(custom.read_text(), contents)

    def test_same_mode_is_idempotent_and_owner_cannot_silently_change(self):
        self.policy.set("owner", "headless", acknowledged=True)
        previous = self.policy.grant.read_bytes()
        self.policy.set("owner", "headless", acknowledged=True)
        self.assertEqual(self.policy.grant.read_bytes(), previous)
        with self.assertRaises(access.AccessError):
            self.policy.set("other", "headless", acknowledged=True)
        self.assertEqual(self.policy.grant.read_bytes(), previous)

    def test_injected_root_service_and_missing_accounts_rejected(self):
        for username in ("root", "service", "unknown", "owner\nALL ALL=NOPASSWD:ALL", "owner #", "-owner", "../owner", "%sudo"):
            with self.subTest(username=username), self.assertRaises(access.AccessError):
                self.policy.set(username, "headless", acknowledged=True)
        self.assertFalse(self.policy.grant.exists())

    def test_unknown_policy_and_symlinks_preserved(self):
        original = "# custom policy\nother ALL=ALL\n"
        self.policy.grant.write_text(original)
        with self.assertRaises(access.AccessError):
            self.policy.set("owner", "headless", acknowledged=True)
        self.assertEqual(self.policy.grant.read_text(), original)
        self.policy.grant.unlink()
        victim = self.etc / "victim"
        victim.write_text("untouched")
        self.policy.grant.symlink_to(victim)
        with self.assertRaises(access.AccessError):
            self.policy.set("owner", "password")
        self.assertEqual(victim.read_text(), "untouched")

    def test_unsafe_directory_and_state_rejected(self):
        self.policy.directory.mkdir()
        self.policy.directory.chmod(0o777)
        with self.assertRaises(access.AccessError):
            self.policy.set("owner", "password")
        self.policy.directory.chmod(0o755)
        self.policy.state.write_text('{"schema_version":99}')
        with self.assertRaises(access.AccessError):
            self.policy.set("owner", "password")
        self.assertEqual(self.policy.state.read_text(), '{"schema_version":99}')

    def test_invalid_initial_policy_changes_nothing(self):
        self.validate.side_effect = access.AccessError("invalid baseline")
        with self.assertRaises(access.AccessError):
            self.policy.set("owner", "headless", acknowledged=True)
        self.assertFalse(self.policy.grant.exists())
        self.assertFalse(self.policy.state.exists())

    def test_rejected_candidate_never_replaces_grant(self):
        self.policy.set("owner", "password")
        previous = self.policy.state.read_bytes()
        self.validate.side_effect = [None, access.AccessError("candidate rejected")]
        with self.assertRaises(access.AccessError):
            self.policy.set("owner", "headless", acknowledged=True)
        self.assertFalse(self.policy.grant.exists())
        self.assertEqual(self.policy.state.read_bytes(), previous)

    def test_full_policy_failure_rolls_back(self):
        self.policy.set("owner", "headless", acknowledged=True)
        grant, state = self.policy.grant.read_bytes(), self.policy.state.read_bytes()
        self.validate.side_effect = [None, access.AccessError("whole policy rejected")]
        with self.assertRaises(access.AccessError):
            self.policy.set("owner", "password")
        self.assertEqual(self.policy.grant.read_bytes(), grant)
        self.assertEqual(self.policy.state.read_bytes(), state)

    def test_state_write_failure_rolls_back_grant(self):
        self.policy.set("owner", "password")
        state = self.policy.state.read_bytes()
        write = self.policy.write
        failed = False

        def fail_once(path, content, mode):
            nonlocal failed
            if path == self.policy.state and not failed:
                failed = True
                raise OSError("simulated disk failure")
            return write(path, content, mode)

        with mock.patch.object(self.policy, "write", side_effect=fail_once), self.assertRaises(OSError):
            self.policy.set("owner", "headless", acknowledged=True)
        self.assertFalse(self.policy.grant.exists())
        self.assertEqual(self.policy.state.read_bytes(), state)

    def test_changed_uid_or_modified_policy_requires_review(self):
        self.policy.set("owner", "headless", acknowledged=True)
        self.policy.grant.chmod(0o640)
        self.policy.grant.write_text(self.policy.grant.read_text() + "other ALL=ALL\n")
        with self.assertRaises(access.AccessError):
            self.policy.set("owner", "password")
        self.policy.grant.write_text(access.policy_text("owner"))
        passwd = self.etc / "passwd"
        passwd.write_text(passwd.read_text().replace("owner:x:1000", "owner:x:1002"))
        with self.assertRaises(access.AccessError):
            self.policy.status("owner")

    def test_status_never_creates_state(self):
        self.assertEqual(self.policy.status("owner")["mode"], "unmanaged")
        self.assertFalse(self.policy.directory.exists())
        self.validate.assert_not_called()

    def test_public_status_does_not_inspect_private_sudoers(self):
        self.policy.set("owner", "headless", acknowledged=True)
        check = self.policy.check

        def forbid_private(path, directory=False):
            if path == self.policy.grant or path == self.policy.grant.parent:
                self.fail("ordinary status must not traverse private sudoers")
            return check(path, directory)

        with mock.patch.object(self.policy, "check", side_effect=forbid_private):
            self.assertEqual(self.policy.status("owner", verify_policy=False)["mode"], "headless")

    def test_root_required_only_for_set(self):
        with mock.patch.object(access.os, "geteuid", return_value=1000), mock.patch.object(access, "Policy") as policy:
            self.assertEqual(access.main(["set", "owner", "headless", "--acknowledge-root-access"]), 1)
            policy.return_value.set.assert_not_called()


if __name__ == "__main__":
    unittest.main()
