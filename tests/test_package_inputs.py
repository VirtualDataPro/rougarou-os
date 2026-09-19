import importlib.util
from pathlib import Path
import tempfile
import unittest


spec = importlib.util.spec_from_file_location('package_inputs', Path(__file__).resolve().parents[1] / 'image/package-input-manifest.py')
inputs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inputs)


class PackageInputTests(unittest.TestCase):
    def test_executable_mode_change_invalidates_tested_candidate(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            script = root / 'helper'
            script.write_text('#!/bin/sh\nexit 0\n')
            script.chmod(0o755)
            before = inputs.manifest(root, [script])
            script.chmod(0o644)
            after = inputs.manifest(root, [script])
            self.assertNotEqual(before, after)
            self.assertEqual(before['inputs']['helper']['sha256'], after['inputs']['helper']['sha256'])

    def test_equal_content_symlink_retargeting_is_detected_without_following(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            for target in ('a', 'b'):
                (root / target).write_text('identical bytes')
            link = root / 'link'
            link.symlink_to('a')
            before = inputs.manifest(root, [link])
            link.unlink()
            link.symlink_to('b')
            self.assertNotEqual(before, inputs.manifest(root, [link]))
            link.unlink()
            link.symlink_to('absent')
            self.assertEqual(inputs.fingerprint(link)['target'], 'absent')

    def test_directory_privacy_mode_is_an_input(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            directory.chmod(0o700)
            before = inputs.fingerprint(directory)
            directory.chmod(0o755)
            self.assertNotEqual(before, inputs.fingerprint(directory))


if __name__ == '__main__':
    unittest.main()
