import importlib.util
import io
import tarfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('cloud_image', ROOT / 'scripts/cloud-image.py')
cloud_image = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cloud_image)


class PublicCloudArchive(unittest.TestCase):
    def test_provenance_bytes_survive_without_host_identity(self):
        content = b'authenticated upstream metadata\n'
        member = tarfile.TarInfo('Packages.lz4')
        member.size = len(content)
        member.uid, member.gid = 1001, 1002
        member.uname, member.gname = 'example-builder', 'example-group'
        member.mode, member.mtime = 0o600, 123456789.25
        member.pax_headers = {'atime': '123456789', 'uname': 'example-builder'}
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode='w') as output:
            output.addfile(cloud_image.public_archive_member(member), io.BytesIO(content))
        archive.seek(0)
        with tarfile.open(fileobj=archive, mode='r') as result:
            actual = result.getmember('Packages.lz4')
            self.assertEqual(result.extractfile(actual).read(), content)
            self.assertEqual((actual.uid, actual.gid, actual.uname, actual.gname), (0, 0, 'root', 'root'))
            self.assertEqual((actual.mode, actual.mtime, actual.pax_headers), (0o644, 0, {}))
        self.assertNotIn(b'example-builder', archive.getvalue())
        self.assertNotIn(b'example-group', archive.getvalue())

    def test_unexpected_symlink_is_rejected(self):
        member = tarfile.TarInfo('evidence-link')
        member.type = tarfile.SYMTYPE
        member.linkname = '/outside/evidence'
        with self.assertRaises(ValueError):
            cloud_image.public_archive_member(member)


if __name__ == '__main__':
    unittest.main()
