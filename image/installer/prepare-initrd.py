#!/usr/bin/env python3
"""Append a deterministic newc/gzip overlay to an untouched Debian initrd.

Linux initramfs accepts concatenated compressed archives. Keeping the original
member intact preserves its device nodes and modules without root or mknod.
"""
from __future__ import annotations

import gzip
from pathlib import Path
import stat
import sys


def entries(archive: bytes) -> dict[str, tuple[int, bytes]]:
    """Read newc members, including zero padding and concatenated archives."""
    result = {}
    offset = 0
    while offset < len(archive):
        while offset < len(archive) and archive[offset] == 0:
            offset += 1
        if offset == len(archive):
            break
        if archive[offset:offset + 6] not in {b"070701", b"070702"}:
            raise ValueError("Unsupported original initrd archive format")
        header = archive[offset:offset + 110]
        if len(header) != 110:
            raise ValueError("Truncated initrd header")
        fields = [int(header[index:index + 8], 16) for index in range(6, 110, 8)]
        mode, size, name_size = fields[1], fields[6], fields[11]
        offset += 110
        raw_name = archive[offset:offset + name_size]
        if not raw_name or not raw_name.endswith(b"\0"):
            raise ValueError("Invalid initrd entry name")
        name = raw_name[:-1].decode("utf-8")
        offset = (offset + name_size + 3) & ~3
        payload = archive[offset:offset + size]
        if len(payload) != size:
            raise ValueError("Truncated initrd payload")
        offset = (offset + size + 3) & ~3
        if name != "TRAILER!!!":
            result[name.removeprefix("./")] = (mode, payload)
    return result


def record(name: str, payload: bytes, mode: int, inode: int) -> bytes:
    encoded = name.encode() + b"\0"
    fields = [inode, mode, 0, 0, 1, 0, len(payload), 0, 0, 0, 0, len(encoded), 0]
    header = b"070701" + b"".join(f"{value:08x}".encode() for value in fields)
    result = header + encoded
    result += b"\0" * (-len(result) % 4)
    result += payload
    return result + b"\0" * (-len(result) % 4)


def prepare(original: Path, frontend: Path, output: Path) -> None:
    source_dir = Path(__file__).resolve().parent
    if original.resolve() == output.resolve():
        raise ValueError("Original and output initrd must be different paths")
    original_bytes = original.read_bytes()
    originals = entries(gzip.decompress(original_bytes))
    status_path = "var/lib/dpkg/status"
    frontend_path = "usr/lib/cdebconf/frontend/newt.so"
    if frontend_path not in originals or status_path not in originals:
        raise ValueError("Expected Debian installer frontend and status database")
    status = originals[status_path][1].decode()
    if any(line.startswith("Package: rougarou-") for line in status.splitlines()):
        raise ValueError("Refusing to apply the installer overlay twice")
    status += """
Package: rougarou-welcome
Status: install ok unpacked
Version: 1
Architecture: all
Section: debian-installer
Priority: optional
Depends: cdebconf-udeb
Installer-Menu-Item: 900
Description: Begin Rougarou installation

Package: rougarou-access
Status: install ok unpacked
Version: 1
Architecture: all
Section: debian-installer
Priority: optional
Depends: cdebconf-udeb, user-setup-udeb
Installer-Menu-Item: 2450
Description: Choose operator access

Package: rougarou-software
Status: install ok unpacked
Version: 1
Architecture: all
Section: debian-installer
Priority: optional
Depends: cdebconf-udeb, rougarou-access
Installer-Menu-Item: 2460
Description: Choose agents and container tools

"""
    # udpkg appends newly unpacked component stanzas without an extra separator.
    # Missing this final blank line merges the last component with the next udeb.
    assert status.endswith("\n\n")
    templates = (source_dir / "rougarou.templates").read_text()
    welcome_templates, access_templates = templates.split("Template: debian-installer/rougarou-access/title", 1)
    access_templates = "Template: debian-installer/rougarou-access/title" + access_templates
    access_templates, software_templates = access_templates.split("Template: debian-installer/rougarou-software/title", 1)
    software_templates = "Template: debian-installer/rougarou-software/title" + software_templates
    overlay = {
        frontend_path: (0o644, frontend.read_bytes()),
        status_path: (0o644, status.encode()),
        "usr/lib/debian-installer.d/S66rougarou-theme": (0o644, (source_dir / "S66rougarou-theme").read_bytes()),
        "var/lib/dpkg/info/rougarou-welcome.templates": (0o644, welcome_templates.encode()),
        "var/lib/dpkg/info/rougarou-access.templates": (0o644, access_templates.encode()),
        "var/lib/dpkg/info/rougarou-software.templates": (0o644, software_templates.encode()),
    }
    for component in ("welcome", "access", "software"):
        overlay[f"var/lib/dpkg/info/rougarou-{component}.postinst"] = (0o755, (source_dir / f"{component}.postinst").read_bytes())
        overlay[f"var/lib/dpkg/info/rougarou-{component}.isinstallable"] = (0o755, (source_dir / "component.isinstallable").read_bytes())
    cpio = b"".join(record(name, data, stat.S_IFREG | mode, inode)
                    for inode, (name, (mode, data)) in enumerate(sorted(overlay.items()), 1))
    cpio += record("TRAILER!!!", b"", 0, len(overlay) + 1)
    cpio += b"\0" * (-len(cpio) % 512)
    output.write_bytes(original_bytes + gzip.compress(cpio, compresslevel=9, mtime=0))
    result = entries(gzip.decompress(output.read_bytes()))
    for name, (mode, data) in overlay.items():
        if result[name] != (stat.S_IFREG | mode, data):
            raise ValueError(f"Initrd overlay validation failed: {name}")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        raise SystemExit("Usage: prepare-initrd.py ORIGINAL_INITRD NEWT_SO OUTPUT_INITRD")
    try:
        prepare(*(Path(value) for value in sys.argv[1:]))
    except (OSError, ValueError) as error:
        raise SystemExit(str(error)) from error
