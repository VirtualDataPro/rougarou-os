"""Explicit encrypted operator backups; restore only into a new private directory."""
from __future__ import annotations

import argparse
import contextlib
import datetime
import fcntl
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import stat
import subprocess
import tempfile
import time
import uuid

import cli
import jobs

TAG = "rougarou-operator-v1"
PENDING = "rougarou-pending-v1"
CAPSULE = "rougarou-backup-capsule"
RESTORE_RESERVED = {"prepared", "RECOVERY.json", "RESTORE_INCOMPLETE"}
MAX_MANIFEST = 1024 * 1024
SETTINGS = (".bashrc", ".profile", ".inputrc", ".gitconfig", ".config/git/config",
            ".config/starship.toml", ".config/herdr/config.toml")


def utc():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def config_path():
    return cli.config_dir() / "backup.json"


def staging_root():
    # Kept outside HOME so an explicit whole-home source cannot ingest its own
    # growing snapshot. Selected sources must be disjoint from this directory.
    return Path("/var/tmp") / f"rougarou-backup-{os.geteuid()}"


def recovery_root():
    return Path.home() / "Rougarou-Recovery"


def absolute(value):
    if not isinstance(value, str) or not value or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise cli.OperatorError("Backup paths must be nonempty absolute paths without control characters.")
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise cli.OperatorError("Use an absolute local or mounted path; backend URLs are not supported here.")
    return path


def components(path):
    """Do not silently resolve operator-selected symlinks into another location."""
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise cli.OperatorError("A backup path contains a symlink; select its real location explicitly.")


def restore_parents(path):
    components(path)
    for parent in path.parents:
        if not parent.exists():
            continue
        info = parent.stat()
        trusted_owner = info.st_uid in {0, os.geteuid()}
        sticky = bool(info.st_mode & stat.S_ISVTX)
        if not trusted_owner or (info.st_mode & 0o022 and not sticky):
            raise cli.OperatorError("Restore ancestors must be controlled by this operator or root, without shared write access.")


def private(path):
    components(path)
    cli.private_directory(path)


def password_descriptor(path):
    components(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    info = os.fstat(fd)
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o600 or not 0 < info.st_size <= 65536):
        os.close(fd)
        raise cli.OperatorError("Restic password file must be nonempty, regular, owned by you, and mode 0600.")
    # An empty first line is an empty Restic password. Never print its contents.
    if not os.read(fd, min(info.st_size, 65536)).partition(b"\n")[0].strip():
        os.close(fd)
        raise cli.OperatorError("Restic password file cannot contain an empty password.")
    os.lseek(fd, 0, os.SEEK_SET)
    return fd


def restic(config, arguments, *, cwd=None, json_output=False, timeout=3600):
    binary = shutil.which("restic", path="/usr/local/bin:/usr/bin:/bin")
    if not binary:
        raise cli.OperatorError("Restic is required; install the signed Debian restic package.")
    fd = password_descriptor(absolute(config["password_file"]))
    try:
        command = [binary, "--repo", config["repository"], "--password-file", f"/proc/self/fd/{fd}", "--no-cache"]
        if json_output:
            command.append("--json")
        # Never inherit RESTIC_PASSWORD_COMMAND, repository overrides, backend
        # credentials, debug files, or arbitrary executable search paths.
        environment = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(Path.home()),
                       "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}
        result = subprocess.run(command + arguments, cwd=cwd, env=environment,
                                pass_fds=(fd,), stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                check=False, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise cli.OperatorError("Restic timed out; no successful operation was recorded.") from None
    finally:
        os.close(fd)
    if result.returncode:
        # Restic diagnostics can include backend URLs and sensitive filenames.
        # Exit 3 (partial backup) is a failure, never a successful snapshot.
        raise cli.OperatorError(f"Restic failed (exit {result.returncode}); no successful operation was recorded.")
    return result.stdout


def validate_config(value):
    if (not isinstance(value, dict) or set(value) != {"schema_version", "repository", "password_file", "includes", "restores"}
            or value["schema_version"] != 1 or not isinstance(value["includes"], list)
            or not isinstance(value["restores"], list)):
        raise cli.OperatorError("Unsupported backup configuration; existing files were preserved.")
    for key in ("repository", "password_file"):
        components(absolute(value[key]))
    for path in value["includes"] + value["restores"]:
        absolute(path)
    repository = absolute(value["repository"])
    if absolute(value["password_file"]).is_relative_to(repository):
        raise cli.OperatorError("Keep the repository password outside the encrypted repository directory.")
    for root in (cli.config_dir(), jobs.state_dir(), staging_root(), recovery_root()):
        if repository.is_relative_to(root) or root.is_relative_to(repository):
            raise cli.OperatorError("Repository must be separate from settings, queue, staging and recovery directories.")
    forbidden = (Path("/"), Path("/proc"), Path("/sys"), Path("/dev"), Path("/run"))
    for path in map(absolute, value["includes"]):
        if path in forbidden or any(path.is_relative_to(p) for p in forbidden[1:]):
            raise cli.OperatorError("Select application data paths, not the filesystem root or kernel/runtime trees.")
        if repository.is_relative_to(path) or path.is_relative_to(repository):
            raise cli.OperatorError("The backup repository must be outside all selected source trees.")
        if (path.is_relative_to(staging_root()) or staging_root().is_relative_to(path)
                or path.is_relative_to(recovery_root())):
            raise cli.OperatorError("Backup staging and recovery directories cannot be backup sources.")
        if path.parts[1] in {CAPSULE, *RESTORE_RESERVED}:
            raise cli.OperatorError("Selected source conflicts with reserved recovery metadata.")
    return value


def load_config():
    value = cli.read_json(config_path())
    if not value:
        raise cli.OperatorError("Configure a destination and private password file with rougarou backup init first.")
    return validate_config(value)


def save_config(value):
    cli.atomic_write(config_path(), json.dumps(validate_config(value), indent=2) + "\n")


@contextlib.contextmanager
def operation():
    private(cli.config_dir())
    path = cli.config_dir() / ".backup.lock"
    cli.check_regular(path, private=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise cli.OperatorError("Another Rougarou backup operation is already running.") from None
        yield
    finally:
        os.close(fd)


def initialize(repository, password_file, includes=(), *, existing=False):
    config = validate_config({"schema_version": 1, "repository": str(absolute(repository)),
                              "password_file": str(absolute(password_file)),
                              "includes": list(dict.fromkeys(str(absolute(p)) for p in includes)), "restores": []})
    if config_path().exists():
        raise cli.OperatorError("Backup is already configured; existing configuration was preserved.")
    os.close(password_descriptor(absolute(password_file)))
    destination = absolute(repository)
    if not existing:
        if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
            raise cli.OperatorError("New repository must be absent or empty; use --existing to attach one.")
        private(destination)
        restic(config, ["init"])
    else:
        if not destination.is_dir():
            raise cli.OperatorError("The existing local repository directory is unavailable.")
        restic(config, ["snapshots"], json_output=True)
    save_config(config)
    return {"configured": True, "encrypted": True, "includes": config["includes"],
            "password_backup_required": True}


def excluded(config):
    return [absolute(config["password_file"]), staging_root(), recovery_root(),
            cli.config_dir(), jobs.state_dir(), *map(absolute, config["restores"])]


def literal_pattern(path):
    return "".join("\\" + c if c in "\\*?[]" else c for c in str(path))


def copy_private(source, destination, *, omit=()):
    """Settings/log staging never follows symlinks or reads special files."""
    if source in omit or not source.exists() and not source.is_symlink():
        return
    info = source.lstat()
    if info.st_uid != os.geteuid():
        raise cli.OperatorError("An operator setting or log is not owned by this account.")
    if stat.S_ISDIR(info.st_mode):
        destination.mkdir(mode=0o700, parents=True, exist_ok=True)
        for child in source.iterdir():
            copy_private(child, destination / child.name, omit=omit)
    elif stat.S_ISREG(info.st_mode):
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            opened = os.fstat(descriptor)
            if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                raise cli.OperatorError("A backup source changed while opening it; retry when settings are stable.")
            with os.fdopen(descriptor, "rb", closefd=False) as incoming, destination.open("xb") as outgoing:
                os.chmod(destination, 0o600)
                shutil.copyfileobj(incoming, outgoing)
        finally:
            os.close(descriptor)
    else:
        raise cli.OperatorError("Operator settings/logs contain a symlink or special file; back up its real data explicitly.")


def queue_copy(target, *, timeout=15):
    source = jobs.state_dir() / "jobs.sqlite3"
    cli.check_regular(source, private=True)
    if not source.exists():
        return {"present": False, "states": {}}
    components(source)
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    with contextlib.closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=.2)) as incoming:
        incoming.execute("PRAGMA query_only=ON")
        if incoming.execute("PRAGMA user_version").fetchone()[0] != 1:
            raise cli.OperatorError("Queue schema is unsupported; no backup was created.")
        with contextlib.closing(sqlite3.connect(target)) as outgoing:
            os.chmod(target, 0o600)
            def progress(_status, _remaining, _total):
                if time.monotonic() > deadline:
                    raise cli.OperatorError("Queue backup was busy for too long; retry later.")
            incoming.backup(outgoing, pages=128, progress=progress, sleep=.05)
            if outgoing.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise cli.OperatorError("Queue snapshot integrity check failed.")
            states = dict(outgoing.execute("SELECT state,COUNT(*) FROM jobs GROUP BY state"))
    return {"present": True, "states": states}


def create(config):
    config = validate_config(config)
    for path in map(absolute, config["includes"]):
        components(path)
        if not path.exists() or not os.access(path, os.R_OK):
            raise cli.OperatorError("A selected service path is missing or unreadable; nothing is silently skipped.")
    private(staging_root())
    with tempfile.TemporaryDirectory(prefix="stage-", dir=staging_root()) as work:
        capsule = Path(work) / CAPSULE
        capsule.mkdir(mode=0o700)
        omit = {config_path(), cli.config_dir() / ".backup.lock", absolute(config["password_file"])}
        omit.update(cli.config_dir().glob("backup.json.backup-*"))
        copy_private(cli.config_dir(), capsule / "config", omit=omit)
        for relative in SETTINGS:
            source = Path.home() / relative
            if source != absolute(config["password_file"]):
                copy_private(source, capsule / "home" / relative)
        queue = queue_copy(capsule / "state/jobs.sqlite3")
        copy_private(jobs.state_dir() / "logs", capsule / "state/logs", omit=omit)
        manifest = {"schema_version": 1, "created_utc": utc(), "home": str(Path.home()),
                    "config_root": str(cli.config_dir()), "state_root": str(jobs.state_dir()),
                    "includes": config["includes"], "excluded_roots": list(map(str, excluded(config))),
                    "queue": queue, "queue_consistency": "SQLite online backup; worker not stopped",
                    "logs_consistency": "Running job logs and selected service files may change during capture",
                    "os_version": (cli.SHARE / "version").read_text().strip() if (cli.SHARE / "version").exists() else "unknown"}
        (capsule / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        token = "rougarou-run-" + uuid.uuid4().hex
        arguments = ["backup", "--tag", PENDING, "--tag", token]
        for path in excluded(config):
            if path == staging_root():
                continue  # Explicit capsule only; other roots cannot overlap staging.
            arguments += ["--exclude", literal_pattern(path)]
        arguments += ["--", CAPSULE, *config["includes"]]
        output = restic(config, arguments, cwd=work, json_output=True)
        records = [json.loads(line) for line in output.splitlines()]
        summary = next((v for v in records if v.get("message_type") == "summary"), None)
        if not summary or not re.fullmatch(r"[0-9a-f]{64}", summary.get("snapshot_id", "")):
            raise cli.OperatorError("Restic did not report a complete snapshot.")
        read_manifest(config, summary["snapshot_id"])  # Never publish a snapshot missing its recovery capsule.
        restic(config, ["tag", "--add", TAG, "--remove", PENDING, summary["snapshot_id"]])
        snapshots = json.loads(restic(config, ["snapshots", "--tag", token], json_output=True))
        complete = [s for s in snapshots if TAG in s.get("tags", []) and PENDING not in s.get("tags", [])]
        if len(complete) != 1:
            raise cli.OperatorError("Cannot identify the completed backup; inspect the repository before retrying.")
        return {"snapshot": complete[0]["id"], "created_utc": manifest["created_utc"],
                "files": summary["total_files_processed"], "bytes": summary["total_bytes_processed"],
                "queue": queue, "worker_stopped": False}


def snapshots(config):
    values = json.loads(restic(config, ["snapshots", "--tag", TAG], json_output=True))
    return [{"id": s["id"], "time": s["time"], "hostname": s.get("hostname", ""),
             "bytes": s.get("summary", {}).get("total_bytes_processed")}
            for s in values if PENDING not in s.get("tags", [])]


def snapshot(config, selector):
    values = snapshots(config)
    if selector == "latest":
        matches = sorted(values, key=lambda s: (s["time"], s["id"]))[-1:]
    elif re.fullmatch(r"[0-9a-f]{8,64}", selector):
        matches = [s for s in values if s["id"].startswith(selector)]
    else:
        raise cli.OperatorError("Choose a completed snapshot ID or latest; paths and arbitrary selectors are not accepted.")
    if len(matches) != 1:
        raise cli.OperatorError("Snapshot is missing or ambiguous; use backup list to choose an exact ID.")
    return matches[0]


def read_manifest(config, identifier):
    data = restic(config, ["dump", identifier, f"/{CAPSULE}/manifest.json"])
    if len(data) > MAX_MANIFEST:
        raise cli.OperatorError("Backup manifest is too large.")
    value = json.loads(data)
    if (not isinstance(value, dict) or value.get("schema_version") != 1
            or not isinstance(value.get("includes"), list) or not isinstance(value.get("queue"), dict)):
        raise cli.OperatorError("Snapshot has no supported Rougarou recovery manifest.")
    for key in ("home", "config_root", "state_root"):
        absolute(value[key])
    for path in value["includes"]:
        absolute(path)
    return value


def validate_tree(config, identifier, manifest):
    roots = [PurePosixPath("/") / CAPSULE, *map(PurePosixPath, manifest["includes"])]
    paths, links, link_directories = set(), [], {}
    for line in restic(config, ["ls", identifier], json_output=True).splitlines():
        node = json.loads(line)
        if node.get("message_type") == "snapshot":
            continue
        raw = node.get("path", "")
        path = PurePosixPath(raw)
        if not path.is_absolute() or ".." in path.parts or str(path) != raw:
            raise cli.OperatorError("Snapshot contains an unsafe restore path.")
        if len(path.parts) > 1 and path.parts[1] in RESTORE_RESERVED:
            raise cli.OperatorError("Snapshot conflicts with reserved recovery metadata.")
        if path in paths:
            raise cli.OperatorError("Snapshot contains conflicting restore paths.")
        paths.add(path)
        contained = any(path.is_relative_to(root) for root in roots)
        parent = any(root.is_relative_to(path) for root in roots)
        kind = node.get("type")
        if not contained and not (parent and kind == "dir"):
            raise cli.OperatorError("Snapshot contains paths outside its declared backup roots.")
        if kind == "symlink":
            # Restic 0.18's ls --json intentionally omits symlink targets.
            # Read the authenticated parent tree once per directory instead.
            if path.parent not in link_directories:
                data = json.loads(restic(config, ["cat", "tree", f"{identifier}:{path.parent}"]))
                if not isinstance(data, dict) or not isinstance(data.get("nodes"), list):
                    raise cli.OperatorError("Snapshot directory metadata could not be validated.")
                link_directories[path.parent] = data["nodes"]
            matching = [item for item in link_directories[path.parent]
                        if isinstance(item, dict) and item.get("name") == path.name]
            if len(matching) != 1 or matching[0].get("type") != "symlink":
                raise cli.OperatorError("Snapshot symlink metadata could not be validated.")
            target = matching[0].get("linktarget", "")
            # Refuse upward traversal before normalization. A virtual-root
            # normpath can clamp excess '..', and links traversed inside the
            # target can make even a lexically contained path escape it.
            if (not isinstance(target, str) or not target or "\x00" in target
                    or PurePosixPath(target).is_absolute() or ".." in PurePosixPath(target).parts):
                raise cli.OperatorError("Snapshot has an absolute or upward symlink; inspect with native Restic before manual recovery.")
            resolved = path.parent / target
            if not any(resolved.is_relative_to(root) for root in roots):
                raise cli.OperatorError("A snapshot symlink leaves the staged recovery tree.")
            links.append(path)
        elif kind not in {"file", "dir"}:
            raise cli.OperatorError("Snapshot contains a special file unsupported by staged recovery.")
    if any(path != link and path.is_relative_to(link) for path in paths for link in links):
        raise cli.OperatorError("Snapshot attempts to restore a file through a symlink.")


def prepare_queue(target):
    original = target / CAPSULE / "state/jobs.sqlite3"
    if not original.exists():
        return 0
    prepared = target / "prepared/rougarou-state"
    prepared.mkdir(mode=0o700, parents=True)
    copy_private(original.parent, prepared)
    path = prepared / "jobs.sqlite3"
    with contextlib.closing(sqlite3.connect(path)) as db:
        db.execute("PRAGMA trusted_schema=OFF")
        if db.execute("PRAGMA user_version").fetchone()[0] != 1 or db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise cli.OperatorError("Restored queue has an unsupported schema or failed its integrity check.")
        pending = db.execute("SELECT id,state FROM jobs WHERE state IN ('queued','running')").fetchall()
        now = time.time()
        for identifier, previous in pending:
            reason = f"Restored {previous} job; review workspace and credentials, then explicitly retry"
            db.execute("UPDATE jobs SET state='interrupted',finished=?,reason=?,cancel_requested=0 WHERE id=?", (now, reason, identifier))
            db.execute("INSERT INTO events(job_id,at,state,detail) VALUES(?,?,'interrupted',?)", (identifier, now, reason))
        db.execute("DELETE FROM worker")
        db.commit()
    return len(pending)


def recovery_note(path, text):
    """Never follow or overwrite a restored entry when writing our metadata."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())


def restore(config, selector, target=None, *, apply=False):
    chosen = snapshot(config, selector)
    if apply and selector != chosen["id"]:
        raise cli.OperatorError("Preview first, then pass the full snapshot ID with --apply.")
    manifest = read_manifest(config, chosen["id"])
    destination = absolute(str(target)) if target else recovery_root() / (time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + chosen["id"][:8])
    restore_parents(destination)
    if destination.exists():
        raise cli.OperatorError("Restore target must be a new directory; existing files are never overwritten.")
    protected = [Path.home(), cli.config_dir(), jobs.state_dir(), absolute(config["repository"]),
                 absolute(config["password_file"]), staging_root()]
    if any(destination == p or p.is_relative_to(destination) for p in protected):
        raise cli.OperatorError("Restore target overlaps a live home, configuration, queue or repository.")
    if destination.is_relative_to(absolute(config["repository"])) or destination.is_relative_to(staging_root()):
        raise cli.OperatorError("Restore target cannot be inside the repository or backup staging.")
    for source in (cli.config_dir(), jobs.state_dir(), *map(absolute, config["includes"])):
        if destination.is_relative_to(source) and not destination.is_relative_to(recovery_root()):
            raise cli.OperatorError("Restore outside selected live data, or use the isolated ~/Rougarou-Recovery directory.")
    validate_tree(config, chosen["id"], manifest)
    plan = {"snapshot": chosen["id"], "target": str(destination), "applied": False,
            "original_home": manifest["home"], "selected_service_paths": manifest["includes"],
            "queue": manifest["queue"], "services_started": False,
            "note": "Private staged restore only; review before manually copying settings or service data."}
    if not apply:
        return plan
    # Creating a private leaf must not chmod an existing home or mountpoint.
    missing = []
    parent = destination.parent
    while not parent.exists():
        missing.append(parent)
        parent = parent.parent
    for parent in reversed(missing):
        parent.mkdir(mode=0o700)
    destination.mkdir(mode=0o700)
    updated = dict(config, restores=list(dict.fromkeys(config["restores"] + [str(destination)])))
    save_config(updated)  # Exclude successful AND incomplete restore trees from future backups.
    try:
        restic(config, ["restore", chosen["id"], "--target", str(destination), "--overwrite", "never", "--verify"])
        plan["quarantined_jobs"] = prepare_queue(destination)
        plan["applied"] = True
        recovery_note(destination / "RECOVERY.json", json.dumps(plan, indent=2) + "\n")
        return plan
    except BaseException:
        # Keep private partial recovery for diagnosis, never delete operator data.
        # If a concurrently created entry blocks the marker, preserve the
        # original exception without touching that entry or its target.
        with contextlib.suppress(OSError):
            recovery_note(destination / "RESTORE_INCOMPLETE", "Restore failed; do not promote these files. Retry into a different new target.\n")
        raise


def main(argv):
    parser = argparse.ArgumentParser(prog="rougarou backup", description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    init = commands.add_parser("init", help="Configure a local/mounted encrypted Restic repository")
    init.add_argument("--repository", required=True)
    init.add_argument("--password-file", required=True)
    init.add_argument("--include", action="append", default=[], help="Additional readable application-data path")
    init.add_argument("--existing", action="store_true", help="Attach an existing repository without initializing it")
    for name in ("create", "list", "check", "restore"):
        command = commands.add_parser(name)
        command.add_argument("--json", action="store_true")
        if name == "check":
            command.add_argument("--read-data", action="store_true", help="Read and verify all encrypted data packs")
        if name == "restore":
            command.add_argument("snapshot")
            command.add_argument("--target", help="New isolated directory; defaults to ~/Rougarou-Recovery/...")
            command.add_argument("--apply", action="store_true", help="Materialize the reviewed full snapshot ID into the new directory")
    args = parser.parse_args(argv)
    cli.require_operator()
    try:
        with operation():
            if args.action == "init":
                result = initialize(args.repository, args.password_file, args.include, existing=args.existing)
            else:
                config = load_config()
                if args.action == "create":
                    result = create(config)
                elif args.action == "list":
                    result = snapshots(config)
                elif args.action == "check":
                    restic(config, ["check"] + (["--read-data"] if args.read_data else []))
                    result = {"checked": True, "all_data_read": args.read_data}
                else:
                    result = restore(config, args.snapshot, args.target, apply=args.apply)
        print(json.dumps(result, indent=2, ensure_ascii=True))
        if args.action == "init":
            print("Keep a separate private copy of the repository password: backups cannot recover it.")
        return 0
    except (ValueError, KeyError, TypeError) as error:
        raise cli.OperatorError("Backup metadata could not be validated; existing live data was preserved.") from None
