#!/usr/bin/env python3
"""Create signed, immutable APT snapshots and explicitly promote tested builds."""
from __future__ import annotations

import argparse
import datetime as dt
import email.utils
import fcntl
import gzip
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile
import uuid


class RepositoryError(ValueError):
    pass


_signing_password: bytes | None = None


def signing_password() -> bytes | None:
    """Read a supplied secret once; never write it to disk or command arguments."""
    global _signing_password
    descriptor = os.environ.get("ROUGAROU_SIGNING_PASSPHRASE_FD")
    if descriptor is None:
        return None
    if not descriptor.isdigit():
        raise RepositoryError("ROUGAROU_SIGNING_PASSPHRASE_FD must be an integer")
    if _signing_password is None:
        chunks = bytearray()
        while len(chunks) <= 4096:
            chunk = os.read(int(descriptor), 1)
            if not chunk or chunk == b"\n":
                break
            chunks.extend(chunk)
        if not chunks or len(chunks) > 4096:
            raise RepositoryError("Signing passphrase is empty or exceeds 4096 bytes")
        _signing_password = bytes(chunks) + b"\n"
    return _signing_password


def sha256(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_bytes(data)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def records(path: Path) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    entry: dict[str, str] = {}
    key = ""
    for line in path.read_text(encoding="utf-8").splitlines() + [""]:
        if not line:
            if entry:
                result.append(entry)
                entry = {}
                key = ""
        elif line[0].isspace():
            if not key:
                raise RepositoryError("Malformed continuation in Packages")
            entry[key] += "\n" + line
        else:
            key, separator, value = line.partition(":")
            if not separator or key in entry:
                raise RepositoryError("Malformed or duplicate Packages field")
            entry[key] = value.lstrip()
    if not result:
        raise RepositoryError("Packages is empty")
    return result


def encode_records(entries: list[dict[str, str]]) -> bytes:
    return ("\n\n".join("\n".join(f"{key}: {value}" for key, value in entry.items())
                         for entry in entries) + "\n\n").encode()


def package_path(root: Path, name: str) -> Path:
    relative = PurePosixPath(name)
    if relative.is_absolute() or ".." in relative.parts or "\\" in name:
        raise RepositoryError(f"Unsafe package path: {name}")
    path = root / relative
    if not path.resolve().is_relative_to(root.resolve()) or path.is_symlink():
        raise RepositoryError(f"Package escapes repository: {name}")
    if path.suffix != ".deb" or not path.is_file():
        raise RepositoryError(f"Missing .deb package: {name}")
    return path


def validate_packages(root: Path) -> list[dict[str, str]]:
    entries = records(root / "Packages")
    seen = set()
    for entry in entries:
        for key in ("Package", "Version", "Architecture", "Filename", "Size", "SHA256"):
            if key not in entry or "\n" in entry[key]:
                raise RepositoryError(f"Missing/invalid package field: {key}")
        identity = (entry["Package"], entry["Version"], entry["Architecture"])
        if identity in seen:
            raise RepositoryError(f"Duplicate package version: {identity}")
        seen.add(identity)
        path = package_path(root, entry["Filename"])
        if not re.fullmatch(r"[0-9a-f]{64}", entry["SHA256"]):
            raise RepositoryError("Invalid SHA256 in Packages")
        if not entry["Size"].isdigit() or path.stat().st_size != int(entry["Size"]):
            raise RepositoryError(f"Size mismatch: {entry['Filename']}")
        if sha256(path) != entry["SHA256"]:
            raise RepositoryError(f"SHA256 mismatch: {entry['Filename']}")
    return entries


def identifier(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}", value):
        raise RepositoryError("Use a 1-96 character snapshot ID: letters, digits, . _ -")
    return value


def check_signer(home: Path, key: str, forbidden_root: Path) -> None:
    if not re.fullmatch(r"[0-9A-Fa-f]{40}|[0-9A-Fa-f]{64}", key):
        raise RepositoryError("Use the full signing-key fingerprint")
    if not home.is_dir() or home.resolve().is_relative_to(forbidden_root.resolve()):
        raise RepositoryError("GNUPGHOME must exist outside the repository/output tree")
    if home.stat().st_mode & 0o077:
        raise RepositoryError("GNUPGHOME must not be accessible to group/other (use 0700)")


def sign_flat(root: Path, home: Path, key: str, suite: str, valid_days: int = 30) -> None:
    if suite not in ("snapshot", "testing", "stable"):
        raise RepositoryError("Invalid suite")
    if not 1 <= valid_days <= 3650:
        raise RepositoryError("Metadata validity must be 1-3650 days")
    check_signer(home, key, root)
    entries = validate_packages(root)
    atomic_write(root / "Packages.gz", gzip.compress((root / "Packages").read_bytes(), mtime=0))
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    lines = ["Origin: Rougarou", "Label: Rougarou", f"Suite: {suite}",
             "Codename: rougarou", f"Date: {email.utils.format_datetime(now, usegmt=True)}",
             f"Valid-Until: {email.utils.format_datetime(now + dt.timedelta(days=valid_days), usegmt=True)}",
             "Architectures: " + " ".join(sorted({entry["Architecture"] for entry in entries})),
             "Description: Rougarou curated package snapshot", "Acquire-By-Hash: yes", "SHA256:"]
    metadata = [root / "Packages", root / "Packages.gz"]
    for name in ("snapshot.json", "promotion.json"):
        if (root / name).is_file():
            metadata.append(root / name)
    for path in metadata:
        digest = sha256(path)
        lines.append(f" {digest} {path.stat().st_size:16d} {path.name}")
        if path.name.startswith("Packages"):
            hashed = root / "by-hash" / "SHA256" / digest
            if hashed.exists() and sha256(hashed) != digest:
                raise RepositoryError("Existing by-hash metadata was modified")
            if not hashed.exists():
                atomic_write(hashed, path.read_bytes())
    release = ("\n".join(lines) + "\n").encode()
    # Publish the three metadata files only after both signatures succeed.
    with tempfile.TemporaryDirectory(prefix="rougarou-sign-") as temporary:
        stage = Path(temporary)
        (stage / "Release").write_bytes(release)
        common = ["gpg", "--homedir", str(home), "--batch", "--yes", "--local-user", key,
                  "--digest-algo", "SHA256"]
        password = signing_password()
        if password is not None:
            common += ["--pinentry-mode", "loopback", "--passphrase-fd", "0"]
        subprocess.run(common + ["--output", str(stage / "InRelease"), "--clearsign", str(stage / "Release")], input=password, check=True)
        subprocess.run(common + ["--armor", "--output", str(stage / "Release.gpg"), "--detach-sign", str(stage / "Release")], input=password, check=True)
        for name in ("Release", "Release.gpg", "InRelease"):
            atomic_write(root / name, (stage / name).read_bytes())


def verify(root: Path, keyring: Path) -> dict:
    if not keyring.is_file():
        raise RepositoryError("Public keyring is missing")
    with tempfile.TemporaryDirectory(prefix="rougarou-verify-") as temporary:
        cleartext = Path(temporary) / "Release"
        subprocess.run(["gpgv", "--keyring", str(keyring.resolve()), "--output", str(cleartext), str(root / "InRelease")], check=True)
        release_bytes = (root / "Release").read_bytes()
        # GnuPG 2.4.7 can emit one extra terminal LF when extracting a
        # clearsignature produced by 2.4.9. Accept only that exact difference;
        # the detached signature below must still authenticate Release bytes.
        if cleartext.read_bytes() not in (release_bytes, release_bytes + b"\n"):
            raise RepositoryError("InRelease does not authenticate this Release")
        subprocess.run(["gpgv", "--keyring", str(keyring.resolve()), str(root / "Release.gpg"), str(root / "Release")], check=True)
    release_entries = records(root / "Release")
    if len(release_entries) != 1:
        raise RepositoryError("Invalid Release")
    release = release_entries[0]
    if release.get("Origin") != "Rougarou" or release.get("Codename") != "rougarou":
        raise RepositoryError("Unexpected repository identity")
    expiry = email.utils.parsedate_to_datetime(release["Valid-Until"])
    if expiry <= dt.datetime.now(dt.timezone.utc):
        raise RepositoryError("Repository metadata has expired")
    checked = set()
    for line in release.get("SHA256", "").splitlines():
        if not line.strip():
            continue
        digest, size, name = line.split()
        if name not in ("Packages", "Packages.gz", "snapshot.json", "promotion.json") or name in checked:
            raise RepositoryError("Unexpected Release index")
        checked.add(name)
        path = root / name
        if not path.is_file() or path.is_symlink() or path.stat().st_size != int(size) or sha256(path) != digest:
            raise RepositoryError(f"Signed metadata mismatch: {name}")
    if not {"Packages", "Packages.gz"}.issubset(checked):
        raise RepositoryError("Release does not authenticate package indexes")
    if gzip.decompress((root / "Packages.gz").read_bytes()) != (root / "Packages").read_bytes():
        raise RepositoryError("Compressed index mismatch")
    validate_packages(root)
    snapshot = json.loads((root / "snapshot.json").read_text()) if "snapshot.json" in checked else {}
    return {"release": release, "snapshot": snapshot}


def snapshot(args: argparse.Namespace) -> None:
    identity = identifier(args.snapshot)
    destination = args.root / "snapshots" / identity
    if destination.exists():
        raise RepositoryError("Snapshot already exists; use a new ID")
    check_signer(args.gnupghome, args.key, args.root)
    entries = validate_packages(args.source)
    args.root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".snapshot-", dir=args.root) as temporary:
        stage = Path(temporary) / "snapshot"
        stage.mkdir()
        for entry in entries:
            source = package_path(args.source, entry["Filename"])
            name = f"pool/{entry['SHA256']}/{source.name}"
            target = stage / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            entry["Filename"] = name
        (stage / "Packages").write_bytes(encode_records(entries))
        manifest = {"schema": 1, "snapshot": identity, "source_packages_sha256": sha256(args.source / "Packages"),
                    "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                    "provenance": args.provenance, "package_count": len(entries)}
        (stage / "snapshot.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        sign_flat(stage, args.gnupghome, args.key, "snapshot", args.valid_days)
        destination.parent.mkdir(parents=True, exist_ok=True)
        stage.rename(destination)
    print(destination)


def add_immutable(source: Path, destination: Path) -> None:
    if destination.exists():
        if not destination.is_file() or sha256(source) != sha256(destination):
            raise RepositoryError(f"Immutable content was modified: {destination}")
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)


def promote(args: argparse.Namespace) -> None:
    identity = identifier(args.snapshot)
    check_signer(args.gnupghome, args.key, args.root)
    source = args.root / "snapshots" / identity
    verification = verify(source, args.keyring)
    if verification["snapshot"].get("snapshot") != identity:
        raise RepositoryError("Signed snapshot identity mismatch")
    evidence = None
    if args.channel == "stable":
        testing = args.root / "channels" / "testing"
        tested = verify(testing, args.keyring)
        if tested["release"].get("Suite") != "testing" or tested["snapshot"].get("snapshot") != identity:
            raise RepositoryError("Stable must promote the current signed testing snapshot")
        if not args.approve_stable or args.evidence is None:
            raise RepositoryError("Stable requires --approve-stable and --evidence")
        evidence = json.loads(args.evidence.read_text())
        if (evidence.get("snapshot") != identity or evidence.get("result") != "pass"
                or not isinstance(evidence.get("tests"), list) or not evidence["tests"]
                or any(not isinstance(test, str) or not test.strip() for test in evidence["tests"])
                or not isinstance(evidence.get("approved_by"), str) or not evidence["approved_by"].strip()):
            raise RepositoryError("Evidence needs matching snapshot, result=pass, nonempty tests and approved_by")
        # Store only the public approval schema, never arbitrary source fields.
        evidence = {field: evidence[field] for field in ("snapshot", "result", "tests", "approved_by")}
    publication_id = f"{identity}-{uuid.uuid4().hex[:12]}"
    audit = {"snapshot": identity, "channel": args.channel, "publication": publication_id,
             "published_at": dt.datetime.now(dt.timezone.utc).isoformat(),
             "evidence": evidence, "source_release_sha256": sha256(source / "Release")}
    # Global stores retain every package and index used by earlier clients.
    for path in (source / "pool").rglob("*.deb"):
        add_immutable(path, args.root / path.relative_to(source))
    for path in (source / "by-hash" / "SHA256").iterdir():
        add_immutable(path, args.root / "index-by-hash" / "SHA256" / path.name)
    with tempfile.TemporaryDirectory(prefix=".publication-", dir=args.root) as temporary:
        stage = Path(temporary) / "publication"
        stage.mkdir()
        for name in ("Packages", "Packages.gz", "snapshot.json"):
            shutil.copyfile(source / name, stage / name)
        (stage / "promotion.json").write_text(json.dumps(audit, sort_keys=True, indent=2) + "\n")
        # Hardlinked historical content keeps package/index URLs usable across
        # an atomic channel switch without duplicating the package bytes.
        shutil.copytree(args.root / "pool", stage / "pool", copy_function=os.link)
        shutil.copytree(args.root / "index-by-hash", stage / "by-hash", copy_function=os.link)
        sign_flat(stage, args.gnupghome, args.key, args.channel, args.valid_days)
        destination = args.root / "publications" / args.channel / publication_id
        destination.parent.mkdir(parents=True, exist_ok=True)
        stage.rename(destination)
    audit_path = args.root / "audit" / f"{args.channel}-{publication_id}.json"
    atomic_write(audit_path, (json.dumps(audit, sort_keys=True, indent=2) + "\n").encode())
    channels = args.root / "channels"
    channels.mkdir(exist_ok=True)
    pointer = channels / f".{args.channel}-{uuid.uuid4().hex}"
    pointer.symlink_to(Path("..") / "publications" / args.channel / publication_id)
    pointer.replace(channels / args.channel)
    print(channels / args.channel)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    for name in ("sign-flat", "snapshot", "promote"):
        command = commands.add_parser(name)
        command.add_argument("--gnupghome", required=True, type=Path)
        command.add_argument("--key", required=True, help="Full fingerprint of external signing key")
        command.add_argument("--valid-days", type=int, default=30)
        if name == "sign-flat":
            command.add_argument("--repo", type=Path, required=True)
            command.add_argument("--suite", choices=("snapshot", "testing", "stable"), default="stable")
        else:
            command.add_argument("--root", type=Path, required=True)
            command.add_argument("--snapshot", required=True)
        if name == "snapshot":
            command.add_argument("--source", type=Path, required=True, help="Verified offline .deb repository with Packages")
            command.add_argument("--provenance", required=True, help="Build/verification record identifier, without secrets")
        if name == "promote":
            command.add_argument("--channel", required=True, choices=("testing", "stable"))
            command.add_argument("--keyring", required=True, type=Path)
            command.add_argument("--evidence", type=Path)
            command.add_argument("--approve-stable", action="store_true")
    command = commands.add_parser("verify")
    command.add_argument("--repo", required=True, type=Path)
    command.add_argument("--keyring", required=True, type=Path)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "verify":
            info = verify(args.repo, args.keyring)
            print(json.dumps({"suite": info["release"]["Suite"], "snapshot": info["snapshot"].get("snapshot")}))
        elif args.command == "sign-flat":
            sign_flat(args.repo, args.gnupghome, args.key, args.suite, args.valid_days)
        else:
            args.root.mkdir(parents=True, exist_ok=True)
            with (args.root / ".repository.lock").open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                snapshot(args) if args.command == "snapshot" else promote(args)
    except (RepositoryError, OSError, KeyError, ValueError, subprocess.CalledProcessError) as error:
        print(f"Repository operation refused: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
