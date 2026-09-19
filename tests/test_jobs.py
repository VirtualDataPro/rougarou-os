import contextlib
import io
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

LIB = Path(__file__).resolve().parents[1] / "rootfs/usr/lib/rougarou"
sys.path.insert(0, str(LIB))
import cli
import jobs
import health


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.environment = patch.dict(os.environ, {
            "XDG_STATE_HOME": str(self.root / "state"),
            "XDG_CONFIG_HOME": str(self.root / "config"),
            "PYTHONPATH": str(LIB),
            "PYTHONDONTWRITEBYTECODE": "1",
        })
        self.environment.start()
        self.workers = []

    def tearDown(self):
        for process in self.workers:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=8)
        self.environment.stop()
        self.temp.cleanup()

    def enqueue(self, code="print('hello')", timeout=10, delay=0):
        with jobs.database() as db:
            return jobs.submit(db, "command", {"argv": [sys.executable, "-c", code]}, self.root, timeout, delay)

    def start(self):
        process = subprocess.Popen([sys.executable, "-c", "import os,jobs; os.umask(0o077); jobs.worker()"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.workers.append(process)
        return process

    def wait_state(self, job_id, states, seconds=8):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            with jobs.database() as db:
                job = jobs.get_job(db, job_id)
            if job["state"] in states:
                return job
            time.sleep(0.05)
        self.fail(f"Job remained {job['state']}, wanted {states}")

    def test_success_failure_history_and_permissions(self):
        good = self.enqueue()
        bad = self.enqueue("import sys; print('failure detail'); sys.exit(7)")
        self.start()
        self.assertEqual(self.wait_state(good, {"succeeded"})["exit_code"], 0)
        self.assertEqual(self.wait_state(bad, {"failed"})["exit_code"], 7)
        self.assertIn("hello", jobs.log_path(good).read_text())
        with jobs.database() as db:
            states = [r[0] for r in db.execute("SELECT state FROM events WHERE job_id=? ORDER BY seq", (good,))]
        self.assertEqual(states, ["queued", "running", "succeeded"])
        self.assertEqual(jobs.log_path(good).stat().st_mode & 0o777, 0o600)
        self.assertEqual((jobs.state_dir() / "jobs.sqlite3").stat().st_mode & 0o777, 0o600)

    def test_queued_cancel_and_delay(self):
        delayed = self.enqueue(delay=60)
        cancelled = self.enqueue()
        with jobs.database() as db:
            jobs.cancel(db, cancelled)
        self.start()
        time.sleep(0.7)
        with jobs.database() as db:
            self.assertEqual(jobs.get_job(db, delayed)["state"], "queued")
            self.assertEqual(jobs.get_job(db, cancelled)["state"], "cancelled")
        self.assertFalse(jobs.log_path(cancelled).exists())

    def test_running_cancel_and_timeout_kill_children(self):
        marker = self.root / "escaped"
        child = f"import time,pathlib; time.sleep(3); pathlib.Path({str(marker)!r}).touch()"
        code = f"import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',{child!r}]); time.sleep(30)"
        cancelled = self.enqueue(code)
        timed = self.enqueue("import time; time.sleep(30)", timeout=1)
        self.start()
        self.wait_state(cancelled, {"running"})
        time.sleep(0.2)
        with jobs.database() as db:
            jobs.cancel(db, cancelled)
        self.wait_state(cancelled, {"cancelled"})
        self.wait_state(timed, {"timed_out"})
        time.sleep(2)
        self.assertFalse(marker.exists())

    def test_shutdown_recovers_queue_without_replaying_active_job(self):
        active = self.enqueue("import time; time.sleep(30)")
        queued = self.enqueue()
        worker = self.start()
        self.wait_state(active, {"running"})
        worker.terminate()
        worker.wait(timeout=8)
        self.wait_state(active, {"interrupted"})
        self.start()
        self.wait_state(queued, {"succeeded"})
        with jobs.database() as db:
            self.assertEqual(jobs.get_job(db, active)["state"], "interrupted")

    def test_crash_recovery_and_single_worker_lock(self):
        job = self.enqueue(delay=60)
        first = self.start()
        time.sleep(0.7)
        second = self.start()
        self.assertNotEqual(second.wait(timeout=4), 0)
        first.kill()
        first.wait(timeout=4)
        # Simulate durable running state left by a killed worker. Live process
        # cleanup belongs to systemd and is also exercised in host smoke tests.
        with jobs.database() as db:
            db.execute("UPDATE jobs SET state='running' WHERE id=?", (job,))
        self.start()
        self.wait_state(job, {"interrupted"})

    def test_retry_creates_linked_run(self):
        job = self.enqueue()
        with jobs.database() as db:
            jobs.cancel(db, job)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            jobs.main(["retry", job])
        with jobs.database() as db:
            retry = jobs.get_job(db, output.getvalue().strip())
            self.assertEqual(retry["parent"], job)
            self.assertEqual(jobs.get_job(db, job)["state"], "cancelled")

    def test_acknowledgment_preserves_outcome_logs_and_history(self):
        job_id = self.enqueue("import sys; print('retain this failure'); sys.exit(7)")
        with jobs.database() as db:
            jobs.execute(db, jobs.claim(db), lambda: False)
            before = jobs.get_job(db, job_id)
            events = [tuple(row) for row in db.execute("SELECT * FROM events ORDER BY seq")]
        log = jobs.log_path(job_id).read_bytes()
        self.assertIn("1 need review; rougarou jobs ack", health.summary())
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(cli.main(["jobs", "ack", job_id, "--json"]), 0)
        self.assertEqual(json.loads(output.getvalue()), {"acknowledged": [job_id], "count": 1})
        with jobs.database() as db:
            self.assertEqual(jobs.get_job(db, job_id), before)
            after_events = [tuple(row) for row in db.execute("SELECT * FROM events ORDER BY seq")]
            self.assertEqual(after_events[:-1], events)
            self.assertEqual(after_events[-1][3], "acknowledged")
            self.assertEqual(jobs.acknowledge(db, job_id), [])
            self.assertEqual(db.execute("SELECT COUNT(*) FROM events").fetchone()[0], len(after_events))
        self.assertEqual(jobs.log_path(job_id).read_bytes(), log)
        self.assertEqual(jobs.snapshot()["counts"], {"failed": 1})
        self.assertEqual(jobs.snapshot()["attention_counts"], {})
        self.assertIn("0 need review", health.summary())
        self.assertNotIn("rougarou jobs ack", health.summary())
        for command in (["show", job_id, "--json"], ["list", "--json"]):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                jobs.main(command)
            record = json.loads(output.getvalue())
            if isinstance(record, list):
                record = record[0]
            self.assertEqual(record["state"], "failed")
            self.assertIsInstance(record["acknowledged_at"], float)

    def test_ack_all_only_clears_current_failures_and_retry_needs_review(self):
        job_ids = {}
        with jobs.database() as db:
            for state in ("failed", "timed_out", "interrupted", "succeeded", "cancelled", "queued", "running"):
                job_id = jobs.submit(db, "command", {"argv": ["/bin/true"]}, self.root)
                job_ids[state] = job_id
                if state in jobs.TERMINAL:
                    jobs.finish(db, job_id, state)
                elif state == "running":
                    db.execute("UPDATE jobs SET state='running' WHERE id=?", (job_id,))
        before = jobs.snapshot()["counts"]
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            jobs.main(["ack", "--all", "--json"])
        self.assertEqual(set(json.loads(output.getvalue())["acknowledged"]),
                         {job_ids[state] for state in jobs.ATTENTION})
        self.assertEqual(jobs.snapshot()["counts"], before)
        self.assertEqual(jobs.snapshot()["attention_counts"], {})
        with patch.object(health, "probe", return_value=(3, "disabled")), patch.object(cli, "load_config", return_value={}):
            checks = {row["check"]: row for row in health.checks()}
        self.assertEqual(checks["failed_runs"]["status"], "warning")
        self.assertEqual(checks["interrupted_runs"]["status"], "warning")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            jobs.main(["retry", job_ids["failed"]])
        retry = output.getvalue().strip()
        with jobs.database() as db:
            jobs.finish(db, retry, "failed", "new failure", 1)
        self.assertEqual(jobs.snapshot()["attention_counts"], {"failed": 1})
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            jobs.main(["ack", "--json"])
        self.assertEqual(json.loads(output.getvalue()), {"acknowledged": [retry], "count": 1})

    def test_ack_refuses_unknown_or_ineligible_jobs_and_conflicting_arguments(self):
        with jobs.database() as db:
            for state in ("queued", "running", "succeeded", "cancelled"):
                job_id = jobs.submit(db, "command", {"argv": ["/bin/true"]}, self.root)
                db.execute("UPDATE jobs SET state=? WHERE id=?", (state, job_id))
                with self.subTest(state=state), self.assertRaises(cli.OperatorError):
                    jobs.acknowledge(db, job_id)
            with self.assertRaisesRegex(cli.OperatorError, "Unknown job"):
                jobs.acknowledge(db, "0" * 16)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM job_acknowledgments").fetchone()[0], 0)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            jobs.main(["ack", job_id, "--all"])
        self.assertEqual(error.exception.code, 2)

    def test_ack_all_rolls_back_atomically_on_event_failure(self):
        with jobs.database() as db:
            for state in ("failed", "interrupted"):
                job_id = jobs.submit(db, "command", {"argv": ["/bin/true"]}, self.root)
                jobs.finish(db, job_id, state)
            before = db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            original_event = jobs.event
            calls = 0

            def fail_second_event(*arguments):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise sqlite3.OperationalError("test transaction failure")
                return original_event(*arguments)

            with patch.object(jobs, "event", side_effect=fail_second_event), self.assertRaises(sqlite3.OperationalError):
                jobs.acknowledge(db)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM job_acknowledgments").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM events").fetchone()[0], before)
        self.assertEqual(jobs.snapshot()["attention_counts"], {"failed": 1, "interrupted": 1})

    def test_old_schema_one_queue_stays_read_only_until_explicit_ack(self):
        with jobs.database() as existing_worker:
            job_id = jobs.submit(existing_worker, "command", {"argv": ["/bin/true"]}, self.root)
            jobs.finish(existing_worker, job_id, "interrupted", "old worker stopped")
            existing_worker.execute("DROP TABLE job_acknowledgments")
            columns = [row[1] for row in existing_worker.execute("PRAGMA table_info(jobs)")]
            self.assertEqual(jobs.snapshot()["attention_counts"], {"interrupted": 1})
            self.assertIn("rougarou jobs ack", health.summary())
            self.assertIsNone(existing_worker.execute("SELECT 1 FROM sqlite_master WHERE name='job_acknowledgments'").fetchone())
            with contextlib.redirect_stdout(io.StringIO()):
                jobs.main(["ack"])
            self.assertEqual(existing_worker.execute("PRAGMA user_version").fetchone()[0], 1)
            self.assertEqual([row[1] for row in existing_worker.execute("PRAGMA table_info(jobs)")], columns)
            self.assertEqual(jobs.get_job(existing_worker, job_id)["state"], "interrupted")
            # An already-open worker connection keeps using the same jobs/events
            # layout and can finish later work without inheriting an old ack.
            later = jobs.submit(existing_worker, "command", {"argv": ["/bin/true"]}, self.root)
            self.assertEqual(jobs.claim(existing_worker)["id"], later)
            jobs.finish(existing_worker, later, "failed", "later failure", 2)
        self.assertEqual(jobs.snapshot()["attention_counts"], {"failed": 1})

    def test_log_limit_and_terminal_escaping(self):
        job_id = self.enqueue("print('x' * 20000)")
        with jobs.database() as db, patch.object(jobs, "LOG_LIMIT", 1024):
            jobs.execute(db, jobs.claim(db), lambda: False)
        self.assertLess(jobs.log_path(job_id).stat().st_size, 1200)
        self.assertIn("log limit reached", jobs.log_path(job_id).read_text())
        self.assertNotIn("\x1b", jobs.safe_text("\x1b[2Jfoo"))

    def test_batch_arguments_and_secret_not_persisted(self):
        job = {"kind": "ai", "spec": json.dumps({"provider": {"kind": "codex"}, "sandbox": "workspace-write", "prompt": "hello"})}
        argv, env, stdin = jobs.command_for(job)
        self.assertEqual(argv[:4], ["codex", "-a", "never", "exec"])
        self.assertNotIn("--approve-for-me", argv)
        self.assertNotIn("--no-alt-screen", argv)
        self.assertEqual(stdin, "hello")
        self.assertEqual(argv[-1], "-")

    def test_missing_executable_and_unknown_job(self):
        with jobs.database() as db:
            job_id = jobs.submit(db, "command", {"argv": ["/does/not/exist"]}, self.root)
            jobs.execute(db, jobs.claim(db), lambda: False)
            self.assertEqual(jobs.get_job(db, job_id)["state"], "failed")
            with self.assertRaises(cli.OperatorError):
                jobs.cancel(db, "../bad")

    def test_future_schema_is_not_overwritten(self):
        with jobs.database() as db:
            db.execute("PRAGMA user_version=99")
        with self.assertRaises(cli.OperatorError):
            with jobs.database():
                pass

    def test_health_detects_stale_worker(self):
        with jobs.database() as db:
            jobs.heartbeat(db)
            db.execute("UPDATE worker SET heartbeat=?", (time.time() - 100,))
        with patch.object(health, "probe", return_value=(0, "active")):
            report = health.checks()
        check = next(row for row in report if row["check"] == "worker_heartbeat")
        self.assertEqual(check["status"], "error")

    def test_login_summary_does_not_create_state_or_probe_services(self):
        with patch.object(health, "probe", side_effect=AssertionError("Login must stay local")):
            self.assertIn("Worker not enabled", health.summary())
        self.assertFalse(jobs.state_dir().exists())

    def test_snapshot_is_read_only_and_rejects_future_schema(self):
        job_id = self.enqueue(delay=60)
        self.assertEqual(jobs.snapshot()["counts"], {"queued": 1})
        with jobs.read_database() as db:
            with self.assertRaises(sqlite3.OperationalError):
                db.execute("DELETE FROM jobs")
        with jobs.database() as db:
            self.assertEqual(jobs.get_job(db, job_id)["state"], "queued")
            db.execute("PRAGMA user_version=99")
        with self.assertRaises(cli.OperatorError):
            jobs.snapshot()

    def test_fresh_optional_worker_and_provider_are_not_errors(self):
        def probe(argv, timeout=3):
            if argv[:3] == ["systemctl", "--user", "is-active"]:
                return 3, "inactive"
            if argv[:3] == ["systemctl", "--user", "is-enabled"]:
                return 1, "disabled"
            return 0, "active"
        with patch.object(health, "probe", side_effect=probe), patch.object(cli, "load_config", return_value={}):
            report = health.checks()
        self.assertFalse([row for row in report if row["status"] == "error"])
        self.assertFalse(jobs.state_dir().exists())

    def test_enabled_but_stopped_worker_is_an_error(self):
        def probe(argv, timeout=3):
            if argv[:3] == ["systemctl", "--user", "is-active"]:
                return 3, "inactive"
            if argv[:3] == ["systemctl", "--user", "is-enabled"]:
                return 0, "enabled"
            return 0, "active"
        with patch.object(health, "probe", side_effect=probe), patch.object(cli, "load_config", return_value={}):
            report = health.checks()
        by_name = {row["check"]: row for row in report}
        self.assertEqual(by_name["worker_service"]["status"], "error")
        self.assertEqual(by_name["worker_heartbeat"]["status"], "error")

    def test_summary_handles_corrupt_queue_without_rendering_details(self):
        cli.private_directory(jobs.state_dir())
        path = jobs.state_dir() / "jobs.sqlite3"
        path.write_text("sensitive corrupt content")
        path.chmod(0o600)
        text = health.summary()
        self.assertIn("Queue unavailable", text)
        self.assertNotIn("sensitive", text)

    def test_hosted_update_channel_with_retained_local_baseline(self):
        sources = self.root / "sources.list.d"
        sources.mkdir()
        key = "Signed-By: /usr/share/keyrings/rougarou-archive-keyring.gpg\n"
        (sources / "rougarou.sources").write_text("Types: deb\nURIs: file:/var/cache/rougarou/repo\nSuites: ./\n" + key)
        self.assertEqual(health.update_channels(sources), {"local": True, "hosted": []})
        remote = "Types: deb\nURIs: https://private-user:private-password@example.invalid/testing\nSuites: ./\n" + key
        (sources / "rougarou-testing.sources").write_text(remote)
        result = health.update_channels(sources)
        self.assertEqual(result, {"local": True, "hosted": ["rougarou-testing.sources"]})
        self.assertNotIn("private-password", str(result))
        (sources / "rougarou-testing.sources").write_text(remote + "Enabled: no\n")
        self.assertEqual(health.update_channels(sources), {"local": True, "hosted": []})

    def test_debian_security_and_source_only_channels_are_not_rougarou_updates(self):
        sources = self.root / "sources.list.d"
        sources.mkdir()
        (sources / "debian.sources").write_text("Types: deb\nURIs: https://security.debian.org/debian-security\nSigned-By: /usr/share/keyrings/debian-archive-keyring.gpg\n")
        (sources / "source.sources").write_text("Types: deb-src\nURIs: https://example.invalid/source\nSigned-By: /usr/share/keyrings/rougarou-archive-keyring.gpg\n")
        self.assertEqual(health.update_channels(sources), {"local": False, "hosted": []})


if __name__ == "__main__":
    unittest.main()
