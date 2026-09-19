"""Durable, single-operator job queue. No third-party Python dependencies."""
from __future__ import annotations

import argparse
import contextlib
import datetime
import fcntl
import json
import os
from pathlib import Path
import selectors
import signal
import sqlite3
import subprocess
import sys
import time
import uuid

import cli

UNIT = "rougarou-worker.service"
TERMINAL = {"succeeded", "failed", "cancelled", "timed_out", "interrupted"}
ATTENTION = {"failed", "timed_out", "interrupted"}
LOG_LIMIT = 16 * 1024 * 1024


def state_dir() -> Path:
    base = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state")))
    if not base.is_absolute():
        raise cli.OperatorError("XDG_STATE_HOME must be absolute.")
    return base / "rougarou"


@contextlib.contextmanager
def database():
    root = state_dir()
    cli.private_directory(root)
    path = root / "jobs.sqlite3"
    cli.check_regular(path, private=True)
    connection = sqlite3.connect(path, timeout=5, isolation_level=None)
    path.chmod(0o600)
    connection.row_factory = sqlite3.Row
    try:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1):
            raise cli.OperatorError("Queue schema is newer than this worker; refusing to modify it.")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, kind TEXT NOT NULL, spec TEXT NOT NULL,
                workspace TEXT NOT NULL, timeout INTEGER NOT NULL,
                state TEXT NOT NULL, created REAL NOT NULL, ready REAL NOT NULL,
                started REAL, finished REAL, exit_code INTEGER, reason TEXT,
                cancel_requested INTEGER NOT NULL DEFAULT 0, parent TEXT
            );
            CREATE INDEX IF NOT EXISTS queue ON jobs(state, ready, created);
            CREATE TABLE IF NOT EXISTS events (
                seq INTEGER PRIMARY KEY, job_id TEXT NOT NULL,
                at REAL NOT NULL, state TEXT NOT NULL, detail TEXT
            );
            CREATE TABLE IF NOT EXISTS worker (
                id INTEGER PRIMARY KEY CHECK(id=1), pid INTEGER,
                heartbeat REAL, state TEXT
            );
            CREATE TABLE IF NOT EXISTS job_acknowledgments (
                job_id TEXT PRIMARY KEY, state TEXT NOT NULL,
                acknowledged_at REAL NOT NULL
            );
            PRAGMA user_version=1;
        """)
        yield connection
    finally:
        connection.close()


@contextlib.contextmanager
def transaction(db):
    db.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        db.execute("ROLLBACK")
        raise
    else:
        db.execute("COMMIT")


def event(db, job_id, state, detail=None):
    db.execute("INSERT INTO events(job_id,at,state,detail) VALUES(?,?,?,?)",
               (job_id, time.time(), state, detail))


def get_job(db, job_id):
    row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if row is None:
        raise cli.OperatorError(f"Unknown job: {job_id}")
    return dict(row)


def submit(db, kind, spec, workspace, timeout=3600, delay=0, parent=None):
    workspace = Path(workspace).resolve(strict=True)
    if not workspace.is_dir():
        raise cli.OperatorError("Workspace must be a directory.")
    if not 1 <= timeout <= 604800 or not 0 <= delay <= 31536000:
        raise cli.OperatorError("Timeout must be 1–604800 seconds; delay 0–31536000 seconds.")
    job_id = uuid.uuid4().hex[:16]
    now = time.time()
    with transaction(db):
        db.execute("""INSERT INTO jobs
            (id,kind,spec,workspace,timeout,state,created,ready,parent)
            VALUES(?,?,?,?,?,'queued',?,?,?)""",
            (job_id, kind, json.dumps(spec), str(workspace), timeout, now, now + delay, parent))
        event(db, job_id, "queued", f"retry of {parent}" if parent else None)
    return job_id


def finish(db, job_id, state, reason=None, exit_code=None):
    with transaction(db):
        row = get_job(db, job_id)
        if row["state"] in TERMINAL:
            return
        if row["cancel_requested"]:
            state, reason = "cancelled", "Cancelled by operator"
        db.execute("UPDATE jobs SET state=?,finished=?,exit_code=?,reason=? WHERE id=?",
                   (state, time.time(), exit_code, reason, job_id))
        event(db, job_id, state, reason)


def cancel(db, job_id):
    with transaction(db):
        row = get_job(db, job_id)
        if row["state"] in TERMINAL:
            raise cli.OperatorError(f"Job is already {row['state']}.")
        if row["state"] == "queued":
            db.execute("UPDATE jobs SET state='cancelled',finished=?,reason=? WHERE id=?",
                       (time.time(), "Cancelled before execution", job_id))
            event(db, job_id, "cancelled", "Cancelled before execution")
        else:
            db.execute("UPDATE jobs SET cancel_requested=1 WHERE id=?", (job_id,))
            event(db, job_id, "cancel_requested")


def acknowledge(db, job_id=None):
    """Hide reviewed failures from attention, preserving outcomes and logs.

    This additive table leaves schema-1 jobs readable by an already-running
    worker. Acknowledgments belong to this job/outcome, never to future retries.
    """
    with transaction(db):
        if job_id is not None and get_job(db, job_id)["state"] not in ATTENTION:
            raise cli.OperatorError("Only failed, timed-out or interrupted jobs can be acknowledged.")
        rows = db.execute("""
            SELECT j.id,j.state FROM jobs j
            LEFT JOIN job_acknowledgments a ON a.job_id=j.id AND a.state=j.state
            WHERE j.state IN ('failed','timed_out','interrupted') AND a.job_id IS NULL
        """ + (" AND j.id=?" if job_id is not None else "") + " ORDER BY j.created,j.id",
            (job_id,) if job_id is not None else ()).fetchall()
        now = time.time()
        for row in rows:
            db.execute("""INSERT INTO job_acknowledgments(job_id,state,acknowledged_at)
                VALUES(?,?,?) ON CONFLICT(job_id) DO UPDATE SET
                state=excluded.state,acknowledged_at=excluded.acknowledged_at""",
                (row["id"], row["state"], now))
            event(db, row["id"], "acknowledged", f"Reviewed {row['state']} outcome; cleared login attention")
    return [row["id"] for row in rows]


def recover(db):
    # Called only while holding the worker lock. systemd clears the old cgroup
    # before restarting us. Never replay potentially non-idempotent agent work.
    rows = db.execute("SELECT id FROM jobs WHERE state='running'").fetchall()
    for row in rows:
        finish(db, row["id"], "interrupted", "Worker stopped before recording an outcome; inspect logs before retrying")


def claim(db):
    with transaction(db):
        row = db.execute("SELECT id FROM jobs WHERE state='queued' AND ready<=? ORDER BY ready,created LIMIT 1",
                         (time.time(),)).fetchone()
        if row is None:
            return None
        db.execute("UPDATE jobs SET state='running',started=? WHERE id=?", (time.time(), row["id"]))
        event(db, row["id"], "running")
        return get_job(db, row["id"])


def heartbeat(db, state="running"):
    db.execute("INSERT OR REPLACE INTO worker VALUES(1,?,?,?)", (os.getpid(), time.time(), state))


def log_path(job_id):
    if len(job_id) != 16 or any(c not in "0123456789abcdef" for c in job_id):
        raise cli.OperatorError("Invalid job ID.")
    root = state_dir() / "logs"
    cli.private_directory(root)
    return root / f"{job_id}.log"


def command_for(job):
    spec = json.loads(job["spec"])
    environment = os.environ.copy()
    environment.pop(cli.SECRET_ENV, None)
    if job["kind"] == "command":
        return spec["argv"], environment, None
    # Reuse provider configuration and credential handling, but never inherit
    # the interactive launcher's approval or terminal flags.
    argv, environment = cli.ai_command(spec["provider"], [], "", environment, batch=True)
    argv.extend(["--sandbox", spec["sandbox"], "--color", "never", "--json",
                 "--skip-git-repo-check", "-"])
    return argv, environment, spec["prompt"]


def kill_group(process, sig):
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, sig)


def execute(db, job, stopping):
    path = log_path(job["id"])
    process = None
    outcome, reason = "failed", "Unable to start job"
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "wb", buffering=0) as log:
            argv, environment, prompt = command_for(job)
            # A temporary regular file avoids deadlocks writing a long prompt to
            # a child that has not read stdin. It is unlinked before execution.
            import tempfile
            with tempfile.TemporaryFile(dir=state_dir()) as stdin:
                if prompt is not None:
                    stdin.write(prompt.encode())
                    stdin.seek(0)
                process = subprocess.Popen(argv, cwd=job["workspace"], env=environment,
                    stdin=stdin, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    start_new_session=True, close_fds=True)
            deadline = time.monotonic() + job["timeout"]
            written, truncated = 0, False
            term_at = None
            exited_at = None
            beat_at = 0
            with selectors.DefaultSelector() as selector:
                os.set_blocking(process.stdout.fileno(), False)
                selector.register(process.stdout, selectors.EVENT_READ)
                while True:
                    if time.monotonic() >= beat_at:
                        heartbeat(db)
                        beat_at = time.monotonic() + 2
                    requested = get_job(db, job["id"])["cancel_requested"]
                    if term_at is None:
                        if requested:
                            outcome, reason = "cancelled", "Cancelled by operator"
                        elif stopping():
                            outcome, reason = "interrupted", "Worker stopped; inspect logs before retrying"
                        elif time.monotonic() >= deadline:
                            outcome, reason = "timed_out", f"Exceeded {job['timeout']} seconds"
                        else:
                            outcome, reason = "succeeded", None
                        if outcome != "succeeded":
                            kill_group(process, signal.SIGTERM)
                            term_at = time.monotonic()
                    if term_at is not None and time.monotonic() - term_at >= 3:
                        kill_group(process, signal.SIGKILL)
                    for key, _ in selector.select(0.2):
                        chunk = os.read(key.fd, 65536)
                        if not chunk:
                            selector.unregister(key.fileobj)
                        else:
                            remaining = max(0, LOG_LIMIT - written)
                            if remaining:
                                log.write(chunk[:remaining])
                                written += min(len(chunk), remaining)
                            if len(chunk) > remaining and not truncated:
                                log.write(b"\n[rougarou: log limit reached; further output discarded]\n")
                                truncated = True
                    code = process.poll()
                    if code is not None:
                        if exited_at is None:
                            exited_at = time.monotonic()
                        # Reap descendants still in this process group. A child
                        # that deliberately changes session can outlive a job;
                        # stopping the service clears its whole cgroup.
                        kill_group(process, signal.SIGKILL)
                        if not selector.get_map() or time.monotonic() - exited_at > 1:
                            if term_at is None:
                                outcome = "succeeded" if code == 0 else "failed"
                                reason = None if code == 0 else f"Process exited with status {code}"
                            break
                log.flush()
                os.fsync(log.fileno())
    except Exception as error:
        # Error text may contain provider details; keep records generic.
        outcome, reason = "failed", f"Runner error: {type(error).__name__}; check workspace, executable and provider configuration"
    finally:
        if process is not None:
            kill_group(process, signal.SIGKILL)
            process.wait()
            if process.stdout:
                process.stdout.close()
    finish(db, job["id"], outcome, reason, process.returncode if process else None)


def worker():
    cli.require_operator()
    stopping = False

    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    with database() as db:
        lock_path = state_dir() / "worker.lock"
        cli.check_regular(lock_path, private=True)
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise cli.OperatorError("A worker already owns this queue.") from None
            recover(db)
            beat_at = 0
            try:
                while not stopping:
                    if time.monotonic() >= beat_at:
                        heartbeat(db)
                        beat_at = time.monotonic() + 2
                    job = claim(db)
                    if job:
                        execute(db, job, lambda: stopping)
                    else:
                        time.sleep(0.5)
            finally:
                heartbeat(db, "stopped")
    return 0


@contextlib.contextmanager
def read_database():
    """Inspect an existing queue without creating or migrating it at login."""
    path = state_dir() / "jobs.sqlite3"
    cli.check_regular(path, private=True)
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0.04)
    connection.row_factory = sqlite3.Row
    deadline = time.monotonic() + 0.08
    connection.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
    try:
        connection.execute("PRAGMA query_only=ON")
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version != 1:
            raise cli.OperatorError("Queue schema is unsupported by this reader.")
        yield connection
    finally:
        connection.close()


def snapshot():
    path = state_dir() / "jobs.sqlite3"
    cli.check_regular(path, private=True)
    if not path.exists():
        return {"counts": {}, "attention_counts": {}, "worker": None}
    with read_database() as db:
        db.execute("BEGIN")
        counts = {row["state"]: row["n"] for row in db.execute("SELECT state,COUNT(*) n FROM jobs GROUP BY state")}
        # Old queues are read as-is at login. Only an explicit queue command
        # creates the additive table; no migration or write occurs here.
        has_acknowledgments = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='job_acknowledgments'").fetchone()
        if has_acknowledgments:
            attention_counts = {row["state"]: row["n"] for row in db.execute("""
                SELECT j.state,COUNT(*) n FROM jobs j
                LEFT JOIN job_acknowledgments a ON a.job_id=j.id AND a.state=j.state
                WHERE j.state IN ('failed','timed_out','interrupted') AND a.job_id IS NULL
                GROUP BY j.state
            """)}
        else:
            attention_counts = {state: count for state, count in counts.items() if state in ATTENTION}
        row = db.execute("SELECT * FROM worker WHERE id=1").fetchone()
        worker_info = dict(row) if row else None
        if worker_info:
            worker_info["age_seconds"] = round(max(0, time.time() - worker_info["heartbeat"]), 1)
        return {"counts": counts, "attention_counts": attention_counts, "worker": worker_info}


def safe_text(text):
    # Do not render terminal escape sequences supplied by a job or filename.
    return "".join(c if c in "\n\t" or (c.isprintable() and ord(c) != 127) else f"\\x{ord(c):02x}" for c in text)


def main(argv):
    cli.require_operator()
    parser = argparse.ArgumentParser(prog="rougarou jobs", description="Durable jobs; one worker, persistent history, explicit retries.")
    commands = parser.add_subparsers(dest="action", required=True)
    for action in ("submit", "run"):
        p = commands.add_parser(action, help="submit an AI prompt" if action == "submit" else "run an explicit command without a shell")
        p.add_argument("--workspace", default=str(Path.cwd()))
        p.add_argument("--timeout", type=int, default=3600, help="maximum runtime in seconds")
        p.add_argument("--delay", type=int, default=0, help="delay execution in seconds")
        if action == "submit":
            p.add_argument("--sandbox", choices=("read-only", "workspace-write"), default="workspace-write")
            p.add_argument("prompt", help="prompt text, or - to read stdin")
        else:
            p.add_argument("argv", nargs=argparse.REMAINDER, help="-- executable arguments")
    p = commands.add_parser("list", aliases=["history"])
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--json", action="store_true")
    p = commands.add_parser("ack", help="acknowledge unsuccessful runs without deleting history or logs")
    p.add_argument("id", nargs="?", help="one failed, timed-out or interrupted job; defaults to all")
    p.add_argument("--all", action="store_true", help="acknowledge all currently unsuccessful runs (the default)")
    p.add_argument("--json", action="store_true")
    for action in ("show", "logs", "cancel", "retry"):
        p = commands.add_parser(action)
        p.add_argument("id")
        if action == "show":
            p.add_argument("--json", action="store_true")
        if action == "logs":
            p.add_argument("--tail", type=int, default=80)
    args = parser.parse_args(argv)
    if args.action == "ack" and args.id is not None and args.all:
        parser.error("ack accepts either a job ID or --all, not both")
    with database() as db:
        if args.action in {"submit", "run"}:
            if args.action == "submit":
                prompt = sys.stdin.read(262145) if args.prompt == "-" else args.prompt
                if not prompt.strip() or len(prompt) > 262144:
                    raise cli.OperatorError("Provide a prompt of 1–262144 characters.")
                provider = cli.load_config().get("provider")
                if not provider:
                    raise cli.OperatorError("Select an AI with rougarou provider first.")
                if provider["kind"] == "command":
                    raise cli.OperatorError("For a custom CLI, use rougarou jobs run -- followed by its unattended command.")
                spec = {"prompt": prompt, "provider": provider, "sandbox": args.sandbox}
                kind = "ai"
            else:
                command = args.argv[1:] if args.argv[:1] == ["--"] else args.argv
                if not command:
                    raise cli.OperatorError("Supply an executable after --.")
                # Resolve under the submitting operator's PATH, not systemd's.
                command[0] = cli.require_command(command[0])
                spec, kind = {"argv": command}, "command"
            print(submit(db, kind, spec, args.workspace, args.timeout, args.delay))
        elif args.action in {"list", "history"}:
            if not 1 <= args.limit <= 10000:
                raise cli.OperatorError("Limit must be 1–10000.")
            rows = [dict(row) for row in db.execute("""SELECT j.id,j.kind,j.state,j.created,j.started,j.finished,
                j.exit_code,j.workspace,j.parent,a.acknowledged_at FROM jobs j
                LEFT JOIN job_acknowledgments a ON a.job_id=j.id AND a.state=j.state
                ORDER BY j.created DESC LIMIT ?""", (args.limit,))]
            if args.json:
                print(json.dumps(rows, indent=2))
            else:
                print(f"{'ID':16}  {'STATE':12}  {'ACK':3}  {'CREATED (UTC)':19}  WORKSPACE")
                for row in rows:
                    when = datetime.datetime.fromtimestamp(row["created"], datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                    ack = "yes" if row["acknowledged_at"] is not None else "-"
                    print(safe_text(f"{row['id']}  {row['state']:12}  {ack:3}  {when}  {row['workspace']}"))
        elif args.action == "ack":
            acknowledged = acknowledge(db, args.id)
            if args.json:
                print(json.dumps({"acknowledged": acknowledged, "count": len(acknowledged)}, indent=2))
            elif acknowledged:
                print(f"Acknowledged {len(acknowledged)} unsuccessful run(s). History and logs are retained.")
            else:
                print("No unsuccessful runs need acknowledgment.")
        else:
            job = get_job(db, args.id)
            if args.action == "show":
                job["spec"] = json.loads(job["spec"])
                acknowledgment = db.execute("SELECT acknowledged_at FROM job_acknowledgments WHERE job_id=? AND state=?",
                                            (job["id"], job["state"])).fetchone()
                job["acknowledged_at"] = acknowledgment[0] if acknowledgment else None
                job["events"] = [dict(row) for row in db.execute("SELECT at,state,detail FROM events WHERE job_id=? ORDER BY seq", (args.id,))]
                output = json.dumps(job, indent=2, ensure_ascii=True)
                print(output)
            elif args.action == "cancel":
                cancel(db, args.id)
                print(f"Cancellation recorded for {args.id}.")
            elif args.action == "retry":
                if job["state"] not in TERMINAL - {"succeeded"}:
                    raise cli.OperatorError("Retry requires a failed, interrupted, timed-out or cancelled job.")
                print(submit(db, job["kind"], json.loads(job["spec"]), job["workspace"], job["timeout"], parent=job["id"]))
            elif args.action == "logs":
                if not 1 <= args.tail <= 10000:
                    raise cli.OperatorError("Tail must be 1–10000 lines.")
                path = log_path(args.id)
                cli.check_regular(path, private=True)
                if not path.exists():
                    print("No output recorded yet.")
                else:
                    from collections import deque
                    with path.open(errors="replace") as stream:
                        print(safe_text("".join(deque(stream, maxlen=args.tail))), end="")
    return 0
