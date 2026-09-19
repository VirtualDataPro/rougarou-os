"""Installer selection boundaries: omitted software stays omitted."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class SoftwareSelectionTests(unittest.TestCase):
    def resolve(self, choices, profiles=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'core').write_text('bash-completion\npython3\nrougarou-base\n')
            (root / 'profiles').write_text(profiles or (
                'codex|rougarou-codex\nherdr|rougarou-herdr\n'
                'opencode|rougarou-opencode\ngemini|rougarou-gemini nodejs\n'
                'claude|ca-certificates curl gnupg\n'
                'docker-rootless|docker.io docker-cli rootlesskit uidmap\n'
                'docker-system|docker.io docker-cli\npodman|podman uidmap\n'))
            (root / 'choices').write_text(choices)
            return subprocess.run(['sh', str(ROOT / 'image/select-packages.sh'),
                                   str(root / 'core'), str(root / 'profiles'), str(root / 'choices')],
                                  text=True, capture_output=True)

    def test_no_optional_selection_installs_only_core(self):
        result = self.resolve('agent=none\ndocker=none\npodman=false\nherdr=false\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(set(result.stdout.split()), {'bash-completion', 'python3', 'rougarou-base'})

    def test_selected_combinations_are_exact_and_deduplicated(self):
        result = self.resolve('agent=codex\ndocker=rootless\npodman=true\nherdr=true\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        actual = result.stdout.split()
        self.assertEqual(len(actual), len(set(actual)))
        self.assertEqual(set(actual), {'bash-completion', 'python3', 'rougarou-base', 'rougarou-codex',
                                      'docker.io', 'docker-cli', 'rootlesskit', 'uidmap', 'podman', 'rougarou-herdr'})

    def test_system_docker_does_not_select_rootless_or_podman(self):
        result = self.resolve('agent=opencode\ndocker=system\npodman=false\nherdr=false\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(set(result.stdout.split()), {'bash-completion', 'python3', 'rougarou-base',
                                                     'rougarou-opencode', 'docker.io', 'docker-cli'})

    def test_invalid_missing_or_duplicate_choices_are_rejected_without_packages(self):
        records = [
            'agent=codex\ndocker=none\npodman=false\n',
            'agent=codex\nagent=none\npodman=false\nherdr=false\n',
            'agent=codex;echo injected\ndocker=none\npodman=false\nherdr=false\n',
            'agent=none\ndocker=both\npodman=false\nherdr=false\n',
            'agent=none\ndocker=none\npodman=TRUE\nherdr=false\n',
        ]
        for record in records:
            with self.subTest(record=record):
                result = self.resolve(record)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(result.stdout)

    def test_unavailable_or_malformed_profile_never_installs_partial_selection(self):
        for profiles in ['herdr|rougarou-herdr\n', 'codex|rougarou-codex;echo\n',
                         'codex|rougarou-codex\ncodex|other\n']:
            with self.subTest(profiles=profiles):
                result = self.resolve('agent=codex\ndocker=none\npodman=false\nherdr=false\n', profiles)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(result.stdout)


if __name__ == '__main__':
    unittest.main()
