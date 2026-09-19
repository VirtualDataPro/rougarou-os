import hashlib
import importlib.util
import io
from pathlib import Path
import tarfile
import tempfile
import unittest


spec = importlib.util.spec_from_file_location('build_herdr', Path(__file__).resolve().parents[1] / 'image/build-herdr.py')
herdr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(herdr)


class ExternalPackageTests(unittest.TestCase):
    def test_corrupted_cached_artifact_is_rejected_without_network(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / 'binary'
            path.write_bytes(b'changed')
            with self.assertRaisesRegex(RuntimeError, 'checksum mismatch'):
                herdr.fetch('https://invalid.example/never-contact', path, hashlib.sha256(b'original').hexdigest())

    def test_matching_cached_artifact_needs_no_network(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / 'binary'
            path.write_bytes(b'pinned')
            self.assertEqual(herdr.fetch('https://invalid.example/never-contact', path, hashlib.sha256(b'pinned').hexdigest()), path)

    def test_notice_archive_path_escape_is_rejected(self):
        with tempfile.TemporaryDirectory() as name:
            data = io.BytesIO()
            with tarfile.open(fileobj=data, mode='w') as archive:
                member = tarfile.TarInfo('crate/../../LICENSE')
                member.size = 1
                archive.addfile(member, io.BytesIO(b'x'))
            data.seek(0)
            with tarfile.open(fileobj=data) as archive, self.assertRaisesRegex(RuntimeError, 'Unsafe'):
                herdr.copy_notices(archive, Path(name) / 'notices', 'crate')
            self.assertFalse((Path(name) / 'LICENSE').exists())


if __name__ == '__main__':
    unittest.main()
