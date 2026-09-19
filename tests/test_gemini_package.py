"""Verify optional Gemini packaging boundaries without downloads or installation."""
import base64
import hashlib
import importlib.util
import io
from pathlib import Path
import stat
import tarfile
import tempfile
import unittest
import zipfile

spec = importlib.util.spec_from_file_location('build_gemini', Path(__file__).resolve().parents[1] / 'image/build-gemini.py')
gemini = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gemini)


class GeminiPackageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_cached_sha256_and_npm_sha512_are_checked_without_network(self):
        artifact = self.root / 'artifact'
        artifact.write_bytes(b'pinned')
        digest = hashlib.sha256(b'pinned').hexdigest()
        self.assertEqual(gemini.fetch('https://invalid.example/never-contact', artifact, digest), artifact)
        sri = 'sha512-' + base64.b64encode(hashlib.sha512(b'pinned').digest()).decode()
        gemini.verify(artifact, sri)
        artifact.write_bytes(b'tampered')
        with self.assertRaisesRegex(RuntimeError, 'checksum mismatch'):
            gemini.fetch('https://invalid.example/never-contact', artifact, digest)
        with self.assertRaisesRegex(RuntimeError, 'checksum mismatch'):
            gemini.verify(artifact, sri)

    def test_bundle_path_escape_is_refused(self):
        archive = self.root / 'bundle.zip'
        with zipfile.ZipFile(archive, 'w') as stream:
            stream.writestr('../escape', 'no')
        with self.assertRaisesRegex(RuntimeError, 'Unsafe archive path'):
            gemini.unpack_bundle(archive, self.root / 'runtime')
        self.assertFalse((self.root / 'escape').exists())

    def test_bundle_symlink_is_refused(self):
        archive = self.root / 'bundle.zip'
        with zipfile.ZipFile(archive, 'w') as stream:
            info = zipfile.ZipInfo('gemini.js')
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            stream.writestr(info, '/tmp/elsewhere')
        with self.assertRaisesRegex(RuntimeError, 'non-regular bundle entry'):
            gemini.unpack_bundle(archive, self.root / 'runtime')

    def test_source_notice_escape_is_refused(self):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode='w') as stream:
            info = tarfile.TarInfo('package/../../LICENSE')
            info.size = 1
            stream.addfile(info, io.BytesIO(b'x'))
        buffer.seek(0)
        with tarfile.open(fileobj=buffer) as stream, self.assertRaisesRegex(RuntimeError, 'Unsafe archive path'):
            gemini.copy_notices(stream, self.root / 'notices')

    def test_every_locked_version_of_bundled_dependency_is_retained(self):
        (self.root / 'gemini.js').write_text('// node_modules/example/index.js\n')
        records = {
            'node_modules/example': {'version': '1', 'resolved': 'https://registry.npmjs.org/example/one.tgz', 'integrity': 'one'},
            'packages/core/node_modules/example': {'version': '2', 'resolved': 'https://registry.npmjs.org/example/two.tgz', 'integrity': 'two'},
            'node_modules/not-bundled': {'version': '1', 'resolved': 'https://registry.npmjs.org/not-bundled/one.tgz', 'integrity': 'other'},
        }
        self.assertEqual({row['version'] for row in gemini.bundle_dependencies(self.root, records)}, {'1', '2'})

    def test_unmapped_bundle_dependency_fails_closed(self):
        (self.root / 'gemini.js').write_text('// node_modules/unreviewed/index.js\n')
        with self.assertRaisesRegex(RuntimeError, 'provenance requires review'):
            gemini.bundle_dependencies(self.root, {})


if __name__ == '__main__':
    unittest.main()
