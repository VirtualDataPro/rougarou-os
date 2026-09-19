import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('publication',
    Path(__file__).resolve().parents[1] / 'scripts/check-publication.py')
publication = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publication)


class PublicationTests(unittest.TestCase):
    def test_private_endpoint_is_rejected_without_printing_it(self):
        endpoint = '.'.join(('192', '168', '44', '55'))
        result = publication.inspect_text('example.md', endpoint)
        self.assertEqual(result[0]['category'], 'private-network-address')
        self.assertNotIn(endpoint, str(result))

    def test_documentation_and_loopback_addresses_are_allowed(self):
        self.assertEqual(publication.inspect_text('example.md',
            '192.0.2.10 198.51.100.20 203.0.113.30 127.0.0.1'), [])

    def test_personal_home_is_rejected(self):
        home = '/home/' + 'private-owner' + '/work'
        result = publication.inspect_text('example.md', home)
        self.assertEqual(result[0]['category'], 'personal-home-path')
        self.assertNotIn(home, str(result))

    def test_private_key_block_is_rejected(self):
        marker = '-----BEGIN ' + 'OPENSSH PRIVATE KEY-----'
        result = publication.inspect_text('key', marker)
        self.assertEqual(result[0]['category'], 'private-key-block')


if __name__ == '__main__':
    unittest.main()
