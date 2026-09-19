"""Bounded local checks; never send a billable model request at login."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import time
from urllib.parse import urlsplit

import cli
import jobs


def probe(argv, timeout=3):
    try:
        result = subprocess.run(argv, text=True, capture_output=True, timeout=timeout,
                                stdin=subprocess.DEVNULL)
        return result.returncode, result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return -1, "unavailable"


def summary():
    try:
        data = jobs.snapshot()
        worker = data["worker"]
        if worker is None and not data["counts"]:
            return "Worker not enabled — optional: rougarou service enable"
        alive = worker and worker["state"] == "running" and worker["age_seconds"] < 10
        counts = data["counts"]
        attention = sum(data["attention_counts"].values())
        review = f"{attention} need review"
        if attention:
            review += "; rougarou jobs ack"
        return (f"Worker {'ready' if alive else 'offline'}  |  "
                f"{counts.get('queued', 0)} queued  {counts.get('running', 0)} running  "
                f"{review}")
    except (OSError, cli.OperatorError, sqlite3.Error):
        return "Queue unavailable — run rougarou doctor"


def update_channels(directory=Path("/etc/apt/sources.list.d")):
    """Find enabled Rougarou deb822 channels without exposing URI credentials."""
    local = False
    hosted = []
    for path in sorted(directory.glob("*.sources")):
        for stanza in re.split(r"\n\s*\n", path.read_text()):
            fields = {}
            previous = None
            for line in stanza.splitlines():
                if not line.strip() or line.lstrip().startswith("#"):
                    continue
                if line[0].isspace() and previous:
                    fields[previous] += " " + line.strip()
                elif ":" in line:
                    key, value = line.split(":", 1)
                    previous = key.lower()
                    fields[previous] = value.strip()
            if fields.get("enabled", "yes").lower() == "no" or "deb" not in fields.get("types", "").split():
                continue
            if "/usr/share/keyrings/rougarou-archive-keyring.gpg" not in fields.get("signed-by", "").split():
                continue
            for uri in fields.get("uris", "").split():
                parsed = urlsplit(uri)
                local = local or parsed.scheme == "file"
                if parsed.scheme in {"http", "https"} and parsed.hostname:
                    hosted.append(path.name)
    return {"local": local, "hosted": sorted(set(hosted))}


def checks():
    result = []

    def add(name, level, detail):
        result.append({"check": name, "status": level, "detail": detail})

    active_code, active = probe(["systemctl", "--user", "is-active", jobs.UNIT])
    enabled_code, enabled = probe(["systemctl", "--user", "is-enabled", jobs.UNIT])
    worker_expected = active_code == 0 or enabled_code == 0
    add("worker_service", "ok" if active_code == 0 else "error" if worker_expected else "warning",
        active if active_code == 0 else active + "; optional: rougarou service start")
    add("worker_boot", "ok" if enabled_code == 0 else "warning",
        enabled if enabled_code == 0 else enabled + "; optional: rougarou service enable")
    code, linger = probe(["loginctl", "show-user", str(os.getuid()), "-p", "Linger", "--value"])
    add("login_persistence", "ok" if code == 0 and linger == "yes" else "warning",
        "enabled" if linger == "yes" else "Enable lingering to run at boot and after logout: loginctl enable-linger")
    try:
        data = jobs.snapshot()
        worker = data["worker"]
        alive = worker and worker["state"] == "running" and worker["age_seconds"] < 10
        add("worker_heartbeat", "ok" if alive else "error" if worker_expected else "warning",
            f"{worker['age_seconds']} seconds old; {worker['state']}" if worker else "No heartbeat recorded")
        if (jobs.state_dir() / "jobs.sqlite3").exists():
            with jobs.read_database() as db:
                integrity = db.execute("PRAGMA quick_check").fetchone()[0]
                add("queue_database", "ok" if integrity == "ok" else "error", integrity)
        else:
            add("queue_database", "warning", "No queue yet; created when the operator submits work")
        counts = data["counts"]
        add("queue", "ok", json.dumps(counts, sort_keys=True))
        unfinished = counts.get("interrupted", 0)
        add("interrupted_runs", "warning" if unfinished else "ok",
            f"{unfinished} interrupted runs retained; inspect with rougarou jobs list")
        failures = counts.get("failed", 0) + counts.get("timed_out", 0)
        add("failed_runs", "warning" if failures else "ok", f"{failures} failed/timed-out runs in history")
    except (OSError, cli.OperatorError, sqlite3.Error) as error:
        add("queue_database", "error", f"Cannot read queue: {type(error).__name__}")
    for label, path in (("state_storage", jobs.state_dir()), ("workspace_storage", Path.home() / "Work")):
        try:
            while not path.exists():
                path = path.parent
            usage = shutil.disk_usage(path)
            fraction = usage.free / usage.total
            level = "error" if usage.free < 256 * 1024**2 or fraction < 0.02 else "warning" if usage.free < 1024**3 or fraction < 0.1 else "ok"
            add(label, level, f"{usage.free / 1024**3:.1f} GiB free ({fraction:.0%})")
        except OSError:
            add(label, "error", "Cannot inspect filesystem capacity")
    try:
        memory = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
        available = int(memory["MemAvailable"].split()[0]) // 1024
        add("memory", "warning" if available < 256 else "ok", f"{available} MiB available")
    except (OSError, KeyError, ValueError):
        add("memory", "warning", "Cannot inspect available memory")
    try:
        selected = cli.load_config().get("provider")
        if not selected:
            add("provider", "warning", "No provider selected; optional: rougarou provider")
        else:
            executable = selected["argv"][0] if selected["kind"] == "command" else "codex"
            add("provider_cli", "ok" if shutil.which(executable) else "error", executable)
            if selected["kind"] == "codex":
                code, _ = probe(["codex", "login", "status"], timeout=5)
                add("provider_auth", "ok" if code == 0 else "error",
                    "CLI reports authenticated (local check)" if code == 0 else "Authentication unavailable; run rougarou provider")
            elif selected["kind"] == "responses":
                cli.ai_command(selected, [], "", os.environ, batch=True)
                add("provider_auth", "ok", "Provider configuration and credential-file checks passed; remote access not probed")
            elif selected["kind"] == "ollama":
                code, models = probe(["ollama", "list"], timeout=5)
                model = selected["model"]
                found = code == 0 and any(line.split() and line.split()[0] in {model, model + ":latest"} for line in models.splitlines()[1:])
                add("local_model", "ok" if found else "error", f"{model}: {'available' if found else 'runtime/model unavailable'}")
            else:
                add("provider_auth", "warning", "Custom CLI authentication must be checked with its own tools")
    except (cli.OperatorError, OSError):
        add("provider", "error", "Invalid provider configuration or credentials; run rougarou provider")
    for unit, label in (("ssh.service", "ssh"), ("apt-daily-upgrade.timer", "apt_upgrade_timer")):
        code, state = probe(["systemctl", "is-active", unit])
        add(label, "ok" if code == 0 else "warning", state)
    stamp = Path("/var/lib/apt/periodic/update-success-stamp")
    if stamp.exists():
        age = (time.time() - stamp.stat().st_mtime) / 86400
        add("package_index_age", "warning" if age > 7 else "ok", f"{age:.1f} days since successful index refresh")
    else:
        add("package_index_age", "warning", "No successful refresh timestamp available")
    add("reboot", "warning" if Path("/run/reboot-required").exists() else "ok",
        "Reboot requested by packages" if Path("/run/reboot-required").exists() else "No package reboot request")
    try:
        channels = update_channels()
        if channels["hosted"]:
            add("rougarou_updates", "ok", "Hosted channel configured: " + ", ".join(channels["hosted"]) + "; reachability not probed")
        elif channels["local"]:
            add("rougarou_updates", "warning", "Local package channel; no hosted Rougarou update channel configured")
        else:
            add("rougarou_updates", "warning", "No enabled Rougarou deb822 package channel found")
    except (OSError, ValueError, UnicodeError):
        add("rougarou_updates", "warning", "Could not inspect Rougarou package channels")
    return result


def main(command, argv):
    parser = argparse.ArgumentParser(prog=f"rougarou {command}")
    if command == "service":
        parser.add_argument("action", choices=("status", "start", "stop", "restart", "enable", "disable"))
        args = parser.parse_args(argv)
        cli.require_operator()
        systemctl = ["systemctl", "--user", args.action]
        if args.action in {"enable", "disable"}:
            systemctl.append("--now")
        systemctl.extend([jobs.UNIT, "--no-pager"])
        return subprocess.run(systemctl, check=False).returncode
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if command == "status":
        data = jobs.snapshot()
        worker = data["worker"]
        healthy = bool(worker and worker["state"] == "running" and worker["age_seconds"] < 10)
        code, _ = probe(["systemctl", "--user", "is-active", jobs.UNIT])
        data["healthy"] = healthy and code == 0
        print(json.dumps(data, indent=2) if args.json else summary())
        return 0 if data["healthy"] else 1
    report = checks()
    healthy = all(row["status"] != "error" for row in report)
    if args.json:
        print(json.dumps({"healthy": healthy, "checks": report}, indent=2))
    else:
        print("Rougarou health — local checks, no billable API requests")
        for row in report:
            print(jobs.safe_text(f"  {row['status'].upper():7} {row['check']:22} {row['detail']}"))
    return 0 if healthy else 1
