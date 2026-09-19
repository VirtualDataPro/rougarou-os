"""Explicit, unprivileged installation of reviewed upstream agent releases."""
from __future__ import annotations

import hashlib
import fcntl
import json
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import stat
import subprocess
import tarfile
import tempfile

PINS = Path(__file__).resolve().parents[2] / "share/rougarou/agents"
AGENTS = {"claude", "opencode"}


class InstallError(Exception):
    """Download or local installation failed without changing an existing tool."""


def directory(path: Path) -> None:
    """Reject symlinked or shared installation paths, including existing parents."""
    home = Path.home()
    if not path.is_relative_to(home) or home.is_symlink():
        raise InstallError("Agent installation requires a real operator home directory.")
    info = home.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o022:
        raise InstallError("Your home directory must be owned by you and not writable by others.")
    current = home
    for part in path.relative_to(home).parts:
        current = current / part
        try:
            current.mkdir(mode=0o700)
        except FileExistsError:
            pass
        info = current.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o022:
            raise InstallError(f"Refusing an unsafe agent installation directory: {current}")


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_pin(agent: str) -> dict:
    if agent not in AGENTS:
        raise InstallError("Choose claude or opencode.")
    try:
        pin = json.loads((PINS / f"{agent}.json").read_text())
        valid = (isinstance(pin, dict)
                 and isinstance(pin.get("version"), str)
                 and re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", pin["version"])
                 and isinstance(pin.get("sha256"), str)
                 and re.fullmatch(r"[0-9a-f]{64}", pin["sha256"])
                 and type(pin.get("size")) is int and 0 < pin["size"] < 512 * 1024 * 1024
                 and pin.get("format") == ("raw" if agent == "claude" else "tar.gz")
                 and pin.get("binary") == agent
                 and isinstance(pin.get("name"), str) and 0 < len(pin["name"]) < 100
                 and all(32 <= ord(char) < 127 for char in pin["name"]))
        if agent == "opencode":
            valid = (valid and type(pin.get("binary_size")) is int
                     and 0 < pin["binary_size"] < 512 * 1024 * 1024
                     and isinstance(pin.get("binary_sha256"), str)
                     and re.fullmatch(r"[0-9a-f]{64}", pin["binary_sha256"]))
        if not valid:
            raise ValueError
    except (OSError, UnicodeError, ValueError, TypeError):
        raise InstallError("Invalid reviewed agent metadata; reinstall rougarou-base.") from None
    expected = (f"https://downloads.claude.ai/claude-code-releases/{pin['version']}/linux-x64/claude"
                if agent == "claude" else
                f"https://github.com/anomalyco/opencode/releases/download/v{pin['version']}/opencode-linux-x64-baseline.tar.gz")
    if pin.get("url") != expected:
        raise InstallError("Agent download URL does not match its reviewed release.")
    return pin


def download(pin: dict, destination: Path) -> None:
    result = subprocess.run([
        "/usr/bin/curl", "--disable", "--fail", "--location", "--proto", "=https",
        "--proto-redir", "=https", "--tlsv1.2", "--connect-timeout", "20",
        "--max-time", "600", "--max-filesize", str(pin["size"]),
        "--output", str(destination), pin["url"],
    ], check=False)
    if result.returncode:
        raise InstallError("Agent download failed. Your shell is ready; retry rougarou-agent-install later.")
    if destination.stat().st_size != pin["size"] or digest(destination) != pin["sha256"]:
        raise InstallError("Agent checksum did not match the reviewed release; nothing was installed.")


def unpack(pin: dict, artifact: Path, binary: Path) -> None:
    if pin["format"] == "raw":
        shutil.copyfile(artifact, binary)
    else:
        with tarfile.open(artifact, "r:gz") as archive:
            members = archive.getmembers()
            if len(members) != 1 or members[0].name != pin["binary"] or not members[0].isfile():
                raise InstallError("Unexpected agent archive layout; nothing was installed.")
            if members[0].size != pin["binary_size"]:
                raise InstallError("Unexpected agent executable size; nothing was installed.")
            with archive.extractfile(members[0]) as source, binary.open("xb") as target:
                shutil.copyfileobj(source, target)
    if digest(binary) != pin.get("binary_sha256", pin["sha256"]):
        raise InstallError("Extracted executable did not match its reviewed checksum.")
    binary.chmod(0o700)


def install(agent: str) -> Path:
    try:
        return install_reviewed(agent)
    except InstallError:
        raise
    except (OSError, ValueError, tarfile.TarError) as error:
        raise InstallError(f"Agent installation failed: {error}. No existing tool was replaced.") from None


def install_reviewed(agent: str) -> Path:
    if os.geteuid() == 0:
        raise InstallError("Install agents as your regular operator account, without sudo.")
    if platform.machine() not in {"x86_64", "amd64"}:
        raise InstallError("This reviewed agent release is for amd64 hosts.")
    pin = read_pin(agent)
    store = Path.home() / ".local/share/rougarou/agents" / agent
    bin_dir = Path.home() / ".local/bin"
    directory(store)
    directory(bin_dir)
    # An advisory directory lock needs no persistent lock file. It prevents two
    # Rougarou installers from publishing the same agent concurrently.
    descriptor = os.open(store, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise InstallError(f"Another {agent} installation is already running; retry after it finishes.") from None
        return install_locked(agent, pin, store, bin_dir)
    finally:
        os.close(descriptor)


def install_locked(agent: str, pin: dict, store: Path, bin_dir: Path) -> Path:
    target = store / pin["version"]
    launcher = bin_dir / agent
    binary = target / agent
    environment = "DISABLE_UPDATES=1" if agent == "claude" else "OPENCODE_DISABLE_AUTOUPDATE=1"
    upgrade_guard = ('if [ "${1-}" = upgrade ]; then echo "Use a reviewed Rougarou agent release to upgrade OpenCode." >&2; exit 2; fi\n'
                     if agent == "opencode" else "")
    text = ("#!/bin/sh\n# Managed by Rougarou's reviewed agent installer.\n"
            f"export {environment}\n{upgrade_guard}exec {shlex.quote(str(binary))} \"$@\"\n")
    if os.path.lexists(launcher):
        info = launcher.lstat()
        if (stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid()
                and not info.st_mode & 0o022 and info.st_size == len(text.encode())
                and launcher.read_text() == text
                and not target.is_symlink() and binary.is_file() and not binary.is_symlink()
                and target.stat().st_uid == os.geteuid() and not target.stat().st_mode & 0o022
                and binary.stat().st_uid == os.geteuid() and not binary.stat().st_mode & 0o022
                and digest(binary) == pin.get("binary_sha256", pin["sha256"])):
            return launcher
        raise InstallError(f"Keeping the existing {launcher}. Use that CLI, or move it aside before installing.")
    if os.path.lexists(target):
        raise InstallError(f"Keeping the existing {target}. Inspect it before retrying installation.")
    with tempfile.TemporaryDirectory(prefix=".download-", dir=store) as temporary:
        stage = Path(temporary)
        artifact = stage / "artifact"
        print(f"Downloading reviewed {pin['name']} {pin['version']} from its publisher.")
        download(pin, artifact)
        payload = stage / "payload"
        payload.mkdir(mode=0o700)
        unpack(pin, artifact, payload / agent)
        # Reserve the version exclusively, including if something appeared while
        # downloading. Never rename over an operator-created empty directory.
        target.mkdir(mode=0o700)
        published = False
        try:
            os.replace(payload / agent, binary)
            descriptor, temporary_launcher = tempfile.mkstemp(prefix=f".{agent}-", dir=bin_dir)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    os.fchmod(stream.fileno(), 0o700)
                    stream.write(text)
                    stream.flush()
                    os.fsync(stream.fileno())
                # A hard link exposes only a complete launcher and refuses an
                # existing filename atomically; no overwrite window is needed.
                os.link(temporary_launcher, launcher)
                published = True
            finally:
                os.unlink(temporary_launcher)
        except BaseException:
            if not published:
                shutil.rmtree(target)
            raise
    print(f"Installed {pin['name']} {pin['version']}. Authenticate through its own CLI.")
    return launcher


def main(arguments: list[str]) -> int:
    if len(arguments) != 1 or arguments[0] not in AGENTS:
        print("Usage: rougarou-agent-install claude|opencode")
        return 2
    try:
        print(install(arguments[0]))
        return 0
    except (InstallError, OSError, ValueError, tarfile.TarError) as error:
        print(f"Agent installation: {error}")
        return 1
