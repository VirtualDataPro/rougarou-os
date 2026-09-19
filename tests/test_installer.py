"""Protect original installer payloads and explicit administrative choices."""
from pathlib import Path
import gzip
import importlib.util
import shutil
import stat
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("installer_overlay", ROOT / "image/installer/prepare-initrd.py")
OVERLAY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(OVERLAY)


def character_record(name, major, minor):
    """Independent newc fixture: a character device, without ever making one."""
    raw_name = name.encode() + b"\0"
    fields = [123, 0o020644, 0, 0, 1, 0, 0, 0, 0, major, minor, len(raw_name), 0]
    raw = b"070701" + b"".join(f"{value:08x}".encode() for value in fields) + raw_name
    return raw + b"\0" * (-len(raw) % 4)


@unittest.skipUnless(shutil.which("cpio"), "cpio is required for real initrd tests")
class InitrdTests(unittest.TestCase):
    def test_overlay_preserves_modules_modes_symlinks_and_original_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            original = work / "original"
            original.mkdir()
            files = {
                "usr/lib/cdebconf/frontend/newt.so": b"original frontend",
                "usr/lib/debian-installer.d/S70menu": b"native menu",
                "usr/lib/modules/kernel/drivers/virtio.ko": b"original module\x00\xff",
                "var/lib/dpkg/status": b"Package: cdebconf-udeb\nStatus: install ok installed\nVersion: 0.280\n\n",
                "init": b"#!/bin/sh\nexec /sbin/init\n",
            }
            for name, contents in files.items():
                path = original / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(contents)
            (original / "init").chmod(0o755)
            (original / "lib").symlink_to("usr/lib")
            (original / "var/lib/dpkg/info").mkdir()
            names = ["."] + sorted(str(path.relative_to(original)) for path in original.rglob("*"))
            archive = subprocess.run(
                ["cpio", "--quiet", "-o", "-H", "newc"], cwd=original,
                input=("\n".join(names) + "\n").encode(), capture_output=True, check=True,
            ).stdout
            # Put device records before the original archive. Linux accepts
            # consecutive newc records; the existing trailer terminates them.
            archive = character_record("dev/console", 5, 1) + character_record("dev/null", 1, 3) + archive
            source = work / "source.gz"
            source.write_bytes(gzip.compress(archive, mtime=0))
            frontend = work / "newt.so"
            frontend.write_bytes(b"reviewed replacement frontend")
            output = work / "output.gz"
            subprocess.run(["bash", str(ROOT / "image/installer/prepare-initrd.sh"),
                            str(source), str(frontend), str(output)], check=True, capture_output=True)
            self.assertTrue(output.read_bytes().startswith(source.read_bytes()))
            unpacked = OVERLAY.entries(gzip.decompress(output.read_bytes()))
            for name, contents in files.items():
                if name not in {"usr/lib/cdebconf/frontend/newt.so", "var/lib/dpkg/status"}:
                    self.assertEqual(unpacked[name][1], contents)
            self.assertEqual(unpacked["lib"], (stat.S_IFLNK | 0o777, b"usr/lib"))
            self.assertEqual(stat.S_IMODE(unpacked["init"][0]), 0o755)
            self.assertTrue(stat.S_ISCHR(unpacked["dev/console"][0]))
            self.assertTrue(stat.S_ISCHR(unpacked["dev/null"][0]))
            self.assertEqual(unpacked["usr/lib/cdebconf/frontend/newt.so"][1], frontend.read_bytes())
            metadata = unpacked["var/lib/dpkg/status"][1].decode()
            self.assertTrue(metadata.startswith(files["var/lib/dpkg/status"].decode()))
            self.assertIn("Depends: cdebconf-udeb, user-setup-udeb\n", metadata)
            self.assertEqual(metadata.count("Package: rougarou-access\n"), 1)
            # udpkg appends an incoming stanza verbatim during media loading.
            # Its parser must still see our final component as a separate block.
            appended = metadata + "Package: loaded-from-media\nStatus: install ok unpacked\n\n"
            access_stanza = next(block for block in appended.split("\n\n") if block.startswith("Package: rougarou-access\n"))
            self.assertNotIn("Package: loaded-from-media", access_stanza)
            changes = {name for name, entry in unpacked.items()
                       if entry != OVERLAY.entries(archive).get(name)}
            self.assertEqual(changes, {
                "usr/lib/cdebconf/frontend/newt.so", "var/lib/dpkg/status",
                "usr/lib/debian-installer.d/S66rougarou-theme",
                "var/lib/dpkg/info/rougarou-welcome.templates",
                "var/lib/dpkg/info/rougarou-access.templates",
                "var/lib/dpkg/info/rougarou-welcome.postinst",
                "var/lib/dpkg/info/rougarou-welcome.isinstallable",
                "var/lib/dpkg/info/rougarou-access.postinst",
                "var/lib/dpkg/info/rougarou-access.isinstallable",
                "var/lib/dpkg/info/rougarou-software.templates",
                "var/lib/dpkg/info/rougarou-software.postinst",
                "var/lib/dpkg/info/rougarou-software.isinstallable",
            })
            # Reapplying the overlay would duplicate installer components.
            rerun = subprocess.run(["bash", str(ROOT / "image/installer/prepare-initrd.sh"),
                                    str(output), str(frontend), str(work / "duplicate.gz")], capture_output=True)
            self.assertNotEqual(rerun.returncode, 0)


class PresentationSafetyTests(unittest.TestCase):
    def test_source_patch_refuses_unreviewed_upstream(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "newt.c"
            original = "/* a different upstream source */\n"
            source.write_text(original)
            result = subprocess.run(["python3", str(ROOT / "image/installer/patch-newt.py"), str(source)],
                                    capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(source.read_text(), original)

    def test_normal_media_does_not_preapprove_access_or_disk_writes(self):
        active = [line.strip() for line in (ROOT / "image/preseed.cfg").read_text().splitlines()
                  if line.strip() and not line.lstrip().startswith("#")]
        self.assertIn("d-i passwd/root-login boolean false", active)
        for line in active:
            self.assertFalse(line.startswith("d-i rougarou/operator-access "))
            for choice in ("agent", "docker", "podman", "herdr"):
                self.assertFalse(line.startswith(f"d-i rougarou/{choice} "))
            for setting in ("partman/confirm", "partman/confirm_nooverwrite", "partman-auto/disk"):
                self.assertFalse(line.startswith(f"d-i {setting} "))


if __name__ == "__main__":
    unittest.main()
