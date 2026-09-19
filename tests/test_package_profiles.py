import importlib.util
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('package_profiles', ROOT / 'image/package-profiles.py')
profiles = importlib.util.module_from_spec(spec)
spec.loader.exec_module(profiles)


class PackageProfileTests(unittest.TestCase):
    def test_core_keeps_worker_bus_but_optional_engines_and_agents_stay_optional(self):
        core = profiles.read_core(ROOT / 'packages.txt')
        selected = profiles.read_profiles(ROOT / 'image/package-profiles.txt')
        self.assertIn('dbus-user-session', core)
        self.assertTrue({'codex', 'herdr', 'docker-rootless', 'docker-system', 'podman'} <= set(selected))
        self.assertFalse({'docker.io', 'docker-cli', 'podman', 'rootlesskit', 'uidmap', 'slirp4netns', 'fuse-overlayfs'} & set(core))
        union = profiles.debian_roots(core, selected)
        self.assertTrue(set(core) <= set(union))
        self.assertTrue({'docker.io', 'podman', 'rootlesskit', 'uidmap'} <= set(union))
        self.assertFalse(any(item.startswith('rougarou-') for item in union))

    def test_no_profile_is_required_to_keep_core(self):
        self.assertEqual(profiles.debian_roots(['python3', 'apt'], {}), ['apt', 'python3'])

    def test_malformed_or_injected_profile_is_rejected(self):
        examples = ['bad|apt;touch /tmp/unwanted', 'bad|apt\nbad|git', 'bad|rougarou-unimplemented',
                    'bad|', 'bad|apt|git', '../bad|apt', 'bad|apt apt']
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / 'profiles'
            for text in examples:
                with self.subTest(text=text):
                    path.write_text(text)
                    with self.assertRaises(ValueError):
                        profiles.read_profiles(path)


if __name__ == '__main__':
    unittest.main()
