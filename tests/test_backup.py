"""Isolated encrypted backups, online queue snapshots, and recovery boundaries."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

LIB = Path(os.environ.get("ROUGAROU_TEST_LIB", Path(__file__).resolve().parents[1] / "rootfs/usr/lib/rougarou"))
sys.path.insert(0, str(LIB))
import cli
import jobs
import backup


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        self.home.mkdir(mode=0o700)
        self.env = patch.dict(os.environ, {"HOME": str(self.home),
            "XDG_CONFIG_HOME": str(self.home / ".config"),
            "XDG_STATE_HOME": str(self.home / ".local/state"),
            "XDG_CACHE_HOME": str(self.home / ".cache")})
        self.env.start()
        self.addCleanup(self.env.stop)
        cli.atomic_write(cli.config_dir() / "config.json", '{"schema_version": 1}\n')
        cli.atomic_write(cli.config_dir() / "credentials.json", '{"fixture_secret": "private-provider-fixture"}\n')
        self.password = cli.config_dir() / "restic-password"
        self.password.write_text("a-private-recovery-password-never-print\n")
        self.password.chmod(0o600)
        self.service = self.home / "Work/service"
        self.service.mkdir(mode=0o700, parents=True)
        (self.service / "data").write_bytes(b"\x00service-byte-proof\xff\n")
        self.config = {"schema_version": 1, "repository": str(self.root / "repository"),
            "password_file": str(self.password), "includes": [str(self.home)], "restores": []}

    def queue(self):
        with jobs.database() as db:
            completed = jobs.submit(db, "command", {"argv": ["/bin/true"]}, self.service)
            jobs.finish(db, completed, "succeeded", exit_code=0)
            queued = jobs.submit(db, "command", {"argv": ["/bin/true"]}, self.service, delay=3600)
            running = jobs.submit(db, "command", {"argv": ["/bin/true"]}, self.service)
            db.execute("UPDATE jobs SET state='running' WHERE id=?", (running,))
            jobs.heartbeat(db)
        jobs.log_path(completed).write_text("completed-log-byte-proof\n")
        return completed, queued, running


class BackupTests(Fixture):
    def test_password_never_enters_argv_environment_or_errors(self):
        secret = self.password.read_text().strip()
        def child(argv, **options):
            self.assertNotIn(secret, str(argv))
            self.assertNotIn(secret, str(options["env"]))
            self.assertFalse(any(name.startswith("RESTIC_") for name in options["env"]))
            self.assertNotIn("AWS_SECRET_ACCESS_KEY", options["env"])
            descriptor = options["pass_fds"][0]
            self.assertEqual(os.read(descriptor, 65536).decode().strip(), secret)
            return subprocess.CompletedProcess(argv, 1, b"", b"https://user:password@example.invalid/?token=secret")
        with patch.dict(os.environ, {"RESTIC_PASSWORD_COMMAND": "touch /tmp/should-not-run",
                "RESTIC_PASSWORD": secret, "AWS_SECRET_ACCESS_KEY": "never-forward"}), \
                patch.object(shutil, "which", return_value="/usr/bin/restic"), \
                patch.object(subprocess, "run", side_effect=child):
            with self.assertRaises(cli.OperatorError) as caught:
                backup.restic(self.config, ["snapshots"])
        self.assertNotIn(secret, str(caught.exception))
        self.assertNotIn("token", str(caught.exception))
        self.assertNotIn("example.invalid", str(caught.exception))

    def test_password_permissions_symlinks_and_repository_recursion_are_refused(self):
        self.password.chmod(0o644)
        with self.assertRaises(cli.OperatorError):
            backup.password_descriptor(self.password)
        self.password.chmod(0o600)
        link = self.home / "password-link"
        link.symlink_to(self.password)
        with self.assertRaises(cli.OperatorError):
            backup.password_descriptor(link)
        for path in (self.home / "repo", cli.config_dir() / "repo", jobs.state_dir() / "repo"):
            with self.subTest(path=path), self.assertRaises(cli.OperatorError):
                backup.validate_config(dict(self.config, repository=str(path)))
        with self.assertRaises(cli.OperatorError) as caught:
            backup.absolute("rest:https://user:secret@example.invalid/repo?token=secret")
        self.assertNotIn("secret", str(caught.exception))
        with patch.object(backup, "restic") as command, self.assertRaisesRegex(cli.OperatorError, "password outside"):
            backup.initialize(self.config["repository"], str(Path(self.config["repository"]) / "password"), existing=True)
        command.assert_not_called()

    def test_online_wal_backup_stays_consistent_while_another_writer_runs(self):
        self.queue()
        path = jobs.state_dir() / "jobs.sqlite3"
        with jobs.database() as db:
            db.executescript("CREATE TABLE counter(value INTEGER); INSERT INTO counter VALUES(0); CREATE TABLE items(value INTEGER);")
        stop, started = threading.Event(), threading.Event()
        errors = []
        def writer():
            try:
                with contextlib.closing(sqlite3.connect(path, timeout=3, isolation_level=None)) as db:
                    for value in range(1, 3000):
                        if stop.is_set():
                            break
                        db.execute("BEGIN IMMEDIATE")
                        db.execute("INSERT INTO items VALUES(?)", (value,))
                        db.execute("UPDATE counter SET value=?", (value,))
                        db.execute("COMMIT")
                        started.set()
                        time.sleep(.001)
            except BaseException as error:
                errors.append(error)
        thread = threading.Thread(target=writer)
        thread.start()
        try:
            self.assertTrue(started.wait(3))
            destination = self.root / "copied.sqlite3"
            result = backup.queue_copy(destination)
            self.assertTrue(result["present"])
            self.assertTrue(thread.is_alive())
            with contextlib.closing(sqlite3.connect(destination)) as db:
                self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(db.execute("SELECT value FROM counter").fetchone()[0],
                                 db.execute("SELECT COUNT(*) FROM items").fetchone()[0])
        finally:
            stop.set()
            thread.join(4)
        self.assertFalse(errors)

    def test_only_prepared_restored_database_quarantines_pending_work(self):
        completed, queued, running = self.queue()
        destination = self.root / "recovery"
        original = destination / backup.CAPSULE / "state/jobs.sqlite3"
        backup.queue_copy(original)
        before = original.read_bytes()
        self.assertEqual(backup.prepare_queue(destination), 2)
        self.assertEqual(original.read_bytes(), before)
        with jobs.database() as db:
            self.assertEqual(jobs.get_job(db, queued)["state"], "queued")
            self.assertEqual(jobs.get_job(db, running)["state"], "running")
            self.assertEqual(db.execute("SELECT COUNT(*) FROM worker").fetchone()[0], 1)
        with contextlib.closing(sqlite3.connect(destination / "prepared/rougarou-state/jobs.sqlite3")) as db:
            states = dict(db.execute("SELECT id,state FROM jobs"))
            self.assertEqual(states, {completed: "succeeded", queued: "interrupted", running: "interrupted"})
            self.assertEqual(db.execute("SELECT COUNT(*) FROM worker").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM events WHERE state='interrupted'").fetchone()[0], 2)

    def test_restore_preflight_rejects_traversal_and_escaping_symlinks(self):
        manifest = {"includes": [str(self.home)]}
        for node in (
                {"path": "/../outside", "type": "file"},
                {"path": f"/{backup.CAPSULE}/link", "type": "symlink", "linktarget": "/etc/passwd"},
                {"path": f"/{backup.CAPSULE}/link", "type": "symlink", "linktarget": "../../outside"},
                {"path": f"/{backup.CAPSULE}/link", "type": "symlink", "linktarget": f"../../{backup.CAPSULE}/file"},
                {"path": f"/{backup.CAPSULE}/pipe", "type": "fifo"}):
            def metadata(config, arguments, **options):
                if arguments[0] == 'ls':
                    return json.dumps({key: value for key, value in node.items() if key != 'linktarget'}).encode()
                return json.dumps({'nodes': [dict(node, name=Path(node['path']).name)]}).encode()
            with self.subTest(node=node), patch.object(backup, "restic", side_effect=metadata):
                with self.assertRaises(cli.OperatorError):
                    backup.validate_tree(self.config, "f" * 64, manifest)
        self.assertFalse((self.root / "outside").exists())

    def test_link_chain_cannot_escape_after_another_link_changes_its_base(self):
        manifest = {"includes": ["/home/user"]}
        nodes = [
            {"path": "/home/user/safe/a", "type": "symlink", "linktarget": "../../../" + backup.CAPSULE},
            {"path": "/home/user/safe/b", "type": "symlink", "linktarget": "a/../../home/user/safe/file"},
        ]
        def metadata(config, arguments, **options):
            if arguments[0] == 'ls':
                return b"\n".join(json.dumps({key: value for key, value in node.items() if key != 'linktarget'}).encode() for node in nodes)
            return json.dumps({'nodes': [dict(node, name=Path(node['path']).name) for node in nodes]}).encode()
        with patch.object(backup, "restic", side_effect=metadata), self.assertRaises(cli.OperatorError):
            backup.validate_tree(self.config, "f" * 64, manifest)

    def test_reserved_recovery_metadata_is_rejected_and_notes_never_follow_links(self):
        for name in backup.RESTORE_RESERVED:
            with self.subTest(name=name):
                with self.assertRaises(cli.OperatorError):
                    backup.validate_config(dict(self.config, includes=["/" + name]))
                manifest = {"includes": ["/" + name]}
                node = {"path": "/" + name, "type": "file"}
                with patch.object(backup, "restic", return_value=json.dumps(node).encode()):
                    with self.assertRaises(cli.OperatorError):
                        backup.validate_tree(self.config, "f" * 64, manifest)
                outside = self.root / (name + "-outside")
                outside.write_text("preserve these bytes")
                link = self.root / name
                link.symlink_to(outside)
                with self.assertRaises(OSError):
                    backup.recovery_note(link, "must not replace")
                self.assertEqual(outside.read_text(), "preserve these bytes")
                self.assertTrue(link.is_symlink())
                link.unlink()
        note = self.root / "new-note"
        backup.recovery_note(note, "private recovery note")
        self.assertEqual(note.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(FileExistsError):
            backup.recovery_note(note, "must not replace")
        self.assertEqual(note.read_text(), "private recovery note")

    def test_root_cli_is_refused_before_repository_access(self):
        with patch.object(os, "geteuid", return_value=0), patch.object(backup, "restic") as command:
            with self.assertRaises(cli.OperatorError):
                backup.main(["list"])
            command.assert_not_called()

    def test_restore_rejects_existing_live_symlink_and_shared_targets_before_writes(self):
        identifier = 'a' * 64
        manifest = {'home': str(self.home), 'includes': [str(self.service)], 'queue': {}}
        shared = self.root/'shared'; shared.mkdir(); shared.chmod(0o777)
        alias = self.root/'alias'; alias.symlink_to(self.home, target_is_directory=True)
        sentinel = self.service/'data'
        before = sentinel.read_bytes()
        targets = [self.home, self.service/'new', shared/'new', alias/'new']
        with patch.object(backup, 'snapshot', return_value={'id': identifier}), \
                patch.object(backup, 'read_manifest', return_value=manifest), \
                patch.object(backup, 'restic') as command:
            for target in targets:
                with self.subTest(target=target), self.assertRaises(cli.OperatorError):
                    backup.restore(dict(self.config, includes=[str(self.service)]), identifier, target, apply=True)
            command.assert_not_called()
        self.assertEqual(sentinel.read_bytes(), before)


@unittest.skipUnless(shutil.which("restic", path="/usr/local/bin:/usr/bin:/bin"), "Native Restic required")
class NativeResticTests(Fixture):
    def initialize(self):
        backup.initialize(self.config["repository"], self.config["password_file"], self.config["includes"])
        return backup.load_config()

    def test_create_check_preview_restore_bytes_and_recover_on_fresh_operator_home(self):
        completed, queued, running = self.queue()
        (self.service / "data-link").symlink_to("data")
        config = self.initialize()
        recovery = backup.recovery_root() / "older-recovery"
        recovery.mkdir(mode=0o700, parents=True)
        (recovery / "must-not-recurse").write_text("old recovery marker")
        with patch.dict(os.environ, {"RESTIC_PASSWORD_COMMAND": "false", "RESTIC_REPOSITORY": "/nonexistent-override"}):
            created = backup.create(config)
        self.assertFalse(created["worker_stopped"])
        self.assertEqual(len(backup.snapshots(config)), 1)
        backup.restic(config, ["check", "--read-data"])
        identifier = created["snapshot"]
        records = backup.restic(config, ["ls", identifier], json_output=True).splitlines()
        tree = "\n".join(json.loads(line).get("path", "") for line in records)
        self.assertNotIn("restic-password", tree)
        self.assertNotIn("must-not-recurse", tree)
        self.assertNotIn("stage-", tree)
        before = {p: p.read_bytes() for p in (cli.config_dir()/"config.json", cli.config_dir()/"credentials.json", self.service/"data")}
        target = backup.recovery_root() / "reviewed"
        preview = backup.restore(config, identifier, target)
        self.assertFalse(preview["applied"])
        self.assertFalse(target.exists())
        result = backup.restore(config, identifier, target, apply=True)
        self.assertTrue(result["applied"])
        self.assertEqual(result["quarantined_jobs"], 2)
        self.assertEqual(target.stat().st_mode & 0o777, 0o700)
        self.assertEqual((target/str(self.service).lstrip('/')/'data').read_bytes(), before[self.service/'data'])
        restored_link = target/str(self.service).lstrip('/')/'data-link'
        self.assertTrue(restored_link.is_symlink())
        self.assertEqual(restored_link.read_bytes(), before[self.service/'data'])
        self.assertEqual((target/backup.CAPSULE/'config/credentials.json').read_bytes(), before[cli.config_dir()/'credentials.json'])
        self.assertEqual((target/backup.CAPSULE/'state/logs'/f'{completed}.log').read_text(), 'completed-log-byte-proof\n')
        for path, data in before.items():
            self.assertEqual(path.read_bytes(), data)
        with self.assertRaises(cli.OperatorError):
            backup.restore(backup.load_config(), identifier, target, apply=True)
        with self.assertRaises(cli.OperatorError):
            backup.restore(config, 'latest', backup.recovery_root()/'unreviewed', apply=True)
        # A replacement installation can attach the same encrypted repository
        # with only its independently retained password file.
        fresh = self.root/'fresh-home'
        fresh.mkdir(mode=0o700)
        key = fresh/'retained-key'
        key.write_bytes(self.password.read_bytes()); key.chmod(0o600)
        with patch.dict(os.environ, {'HOME':str(fresh), 'XDG_CONFIG_HOME':str(fresh/'.config'),
                'XDG_STATE_HOME':str(fresh/'.local/state'), 'XDG_CACHE_HOME':str(fresh/'.cache')}):
            backup.initialize(config['repository'], str(key), existing=True)
            fresh_config=backup.load_config()
            self.assertEqual(backup.snapshots(fresh_config)[0]['id'], identifier)
            fresh_target=fresh/'Rougarou-Recovery/recovered'
            backup.restore(fresh_config, identifier, fresh_target, apply=True)
            self.assertEqual((fresh_target/backup.CAPSULE/'config/credentials.json').read_bytes(), before[next(p for p in before if p.name=='credentials.json')])
            with patch.object(jobs, 'state_dir', return_value=fresh_target/'prepared/rougarou-state'):
                recovered=jobs.snapshot()
            self.assertEqual(recovered['counts'], {'interrupted':2,'succeeded':1})
            self.assertIsNone(recovered['worker'])

    def test_wrong_password_and_corrupted_pack_fail_instead_of_claiming_recovery(self):
        self.queue()
        config = self.initialize()
        created = backup.create(config)
        original = self.password.read_bytes()
        self.password.write_text('wrong-password\n')
        with self.assertRaises(cli.OperatorError):
            backup.snapshots(config)
        self.password.write_bytes(original)
        pack = next(p for p in (Path(config['repository'])/'data').glob('*/*') if p.is_file())
        # Damage encrypted blob data, not merely the pack's trailing header
        # (which a valid index can make unnecessary during a verified restore).
        data = bytearray(pack.read_bytes()); data[0] ^= 1; pack.chmod(0o600); pack.write_bytes(data)
        with self.assertRaises(cli.OperatorError):
            backup.restic(config, ['check','--read-data'])
        with self.assertRaises(cli.OperatorError):
            backup.restore(config, created['snapshot'], backup.recovery_root()/'damaged', apply=True)

    def test_unreadable_selected_file_is_not_published_as_complete(self):
        if os.geteuid() == 0:
            self.skipTest('Root bypasses permission checks; native nonroot fixture exercises this')
        config = self.initialize()
        hidden = self.service/'unreadable'
        hidden.write_text('cannot-read'); hidden.chmod(0)
        try:
            with self.assertRaises(cli.OperatorError):
                backup.create(config)
        finally:
            hidden.chmod(0o600)
        self.assertEqual(backup.snapshots(config), [])

    def test_native_snapshot_with_upward_symlink_is_refused_before_materialization(self):
        config = self.initialize()
        (self.service / "upward").symlink_to("../../../../" + backup.CAPSULE + "/file")
        created = backup.create(config)
        target = backup.recovery_root() / "unsafe-links"
        with self.assertRaisesRegex(cli.OperatorError, "symlink"):
            backup.restore(config, created["snapshot"], target, apply=True)
        self.assertFalse(target.exists())


if __name__ == '__main__':
    unittest.main()
