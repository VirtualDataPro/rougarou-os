"""Policy, expiry and review guards; native signed APT proof is update-native.py."""
import contextlib
import datetime as dt
import email.utils
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('updates', ROOT / 'rootfs/usr/lib/rougarou/updates.py')
updates = importlib.util.module_from_spec(spec)
spec.loader.exec_module(updates)


class UpdatePolicyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / 'sources.list.d').mkdir()
        self.file = self.root / 'sources.list.d/rougarou.sources'
        self.write_source()
        self.safety = patch.object(updates, 'safe_file')
        self.key_safety = patch.object(updates, 'safe_key')
        self.hash = patch.object(updates, 'file_hash', return_value='a' * 64)
        self.safety.start()
        self.key_safety.start()
        self.hash.start()

    def tearDown(self):
        self.hash.stop()
        self.key_safety.stop()
        self.safety.stop()
        self.temporary.cleanup()

    def write_source(self, uri='https://repo.example.invalid/channels/testing', extra='', key=updates.KEY):
        self.file.write_text(f'Types: deb\nURIs: {uri}\nSuites: ./\nSigned-By: {key}\n{extra}')

    def test_configured_channel_and_direct_security_are_distinct(self):
        (self.root / 'sources.list.d/security.sources').write_text('Types: deb\nURIs: https://security.debian.org/debian-security\nSuites: trixie-security\nComponents: main non-free-firmware\nSigned-By: ' + updates.DEBIAN_KEY + '\n')
        policy = updates.source_policy(self.root)
        self.assertEqual({s['channel'] for s in policy['sources']}, {'testing', 'debian-security'})
        self.assertIn('Signed-By:', updates.source_text(policy['sources']))
        self.assertNotIn('Trusted:', updates.source_text(policy['sources']))

    def test_only_exact_offline_bootstrap_path_is_accepted(self):
        self.write_source('file:/var/cache/rougarou/repo')
        self.assertEqual(updates.source_policy(self.root)['sources'][0]['channel'], 'bootstrap')
        self.write_source('file:/tmp/unreviewed-repository')
        with self.assertRaises(updates.UpdateError):
            updates.source_policy(self.root)

    def test_trust_overrides_vendor_bypasses_and_credentials_are_rejected(self):
        cases = [
            ('https://repo.invalid/channels/testing', 'Trusted: yes\n', updates.KEY),
            ('https://repo.invalid/channels/testing', 'Check-Valid-Until: no\n', updates.KEY),
            ('https://repo.invalid/channels/testing', 'Allow-Insecure: yes\n', updates.KEY),
            ('https://repo.invalid/snapshots/unpromoted', '', updates.KEY),
            ('https://user:secret@repo.invalid/channels/testing', '', updates.KEY),
            ('https://repo.invalid/channels/testing?token=secret', '', updates.KEY),
            ('https://deb.debian.org/debian', '', updates.DEBIAN_KEY),
            ('https://repo.invalid/channels/testing', '', '/tmp/my-key.gpg'),
        ]
        for uri, extra, key in cases:
            with self.subTest(uri=uri, extra=extra):
                self.write_source(uri, extra, key)
                with self.assertRaises(updates.UpdateError) as error:
                    updates.source_policy(self.root)
                self.assertEqual(error.exception.code, 'policy_error')
                self.assertNotIn('secret', str(error.exception))

    def test_disabled_vendor_is_ignored_but_configuration_is_bound(self):
        before = updates.source_policy(self.root)
        (self.root / 'sources.list.d/vendor.sources').write_text('Enabled: no\nTypes: deb\nURIs: https://vendor.invalid\nSuites: stable\n')
        after = updates.source_policy(self.root)
        self.assertEqual(before['sources'], after['sources'])
        self.assertNotEqual(before['inputs'], after['inputs'])

    def test_legacy_signed_source_and_duplicate_refusal(self):
        self.file.unlink()
        (self.root / 'sources.list').write_text(f'deb [signed-by={updates.KEY}] https://repo.invalid/channels/stable ./\n')
        self.assertEqual(updates.source_policy(self.root)['sources'][0]['channel'], 'stable')
        self.write_source('https://repo.invalid/channels/stable')
        with self.assertRaisesRegex(updates.UpdateError, 'Duplicate'):
            updates.source_policy(self.root)

    def test_custom_preferences_are_not_silently_ignored(self):
        (self.root / 'preferences').write_text('Package: *\nPin: release a=stable\nPin-Priority: 1001\n')
        with self.assertRaisesRegex(updates.UpdateError, 'preferences'):
            updates.source_policy(self.root)


class MetadataAndTransactionTests(unittest.TestCase):
    def release(self):
        now = dt.datetime.now(dt.timezone.utc)
        return {'Origin': 'Rougarou', 'Label': 'Rougarou', 'Codename': 'rougarou', 'Suite': 'testing',
                'Date': email.utils.format_datetime(now - dt.timedelta(minutes=1)),
                'Valid-Until': email.utils.format_datetime(now + dt.timedelta(days=1))}

    def test_expired_future_and_wrong_channel_metadata_rejected(self):
        cases = [('Valid-Until', email.utils.format_datetime(dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=5))),
                 ('Date', email.utils.format_datetime(dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=1))),
                 ('Suite', 'stable'), ('Origin', 'Vendor')]
        for field, value in cases:
            fields = self.release()
            fields[field] = value
            with self.subTest(field=field), self.assertRaises(updates.UpdateError):
                updates.release_check(fields, {'channel': 'testing'})
        self.assertGreater(updates.release_check(self.release(), {'channel': 'testing'}), 0)

    def test_signed_hashes_and_continuations(self):
        text = 'Origin: Rougarou\nSHA256:\n ' + 'a' * 64 + ' 123 Packages\n\n'
        fields = updates.paragraphs(text)[0]
        self.assertEqual(updates.signed_hashes(fields), {'Packages': ('a' * 64, 123)})
        with self.assertRaises(updates.UpdateError):
            updates.paragraphs('Origin: one\nOrigin: two\n')
        with self.assertRaises(updates.UpdateError):
            updates.signed_hashes({'SHA256': 'not-a-hash 12 Packages'})

    def test_native_operation_parser_rejects_removal_and_unrelated_configuration(self):
        self.assertEqual(updates.operations('Inst example [1] (2 Rougarou:testing [all])\nConf example (2 Rougarou:testing [all])\n'), {'example': '2'})
        for text in ('Remv package [1]\n', 'Purg package [1]\n', 'Conf unrelated (1 [all])\n', 'Inst invalid:foreign (1 [arm64])\n'):
            with self.subTest(text=text), self.assertRaises(updates.UpdateError):
                updates.operations(text)

    def test_apply_requires_explicit_review_and_recovery_attestation_before_state_changes(self):
        engine = updates.Updates(Path('/does/not/exist'))
        for plan, reference, confirmation in [('a' * 64, 'snapshot-123', False), ('bad', 'snapshot', True),
                                               ('a' * 64, '', True), ('a' * 64, 'snapshot\ncommand', True)]:
            with self.subTest(plan=plan, reference=reference), self.assertRaises(updates.UpdateError):
                engine.apply(plan, reference, confirmation)

    def test_stale_plan_rejected_before_creating_transaction(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = updates.Updates(Path(directory))
            with patch.object(engine, 'lock', return_value=contextlib.nullcontext()), patch.object(engine, 'plan', return_value={'plan_id': 'b' * 64}):
                with self.assertRaises(updates.UpdateError) as error:
                    engine.apply('a' * 64, 'existing-snapshot', True)
            self.assertEqual(error.exception.code, 'stale')
            self.assertFalse(list(Path(directory).iterdir()))

    def test_sudo_entry_point_is_fixed_and_headless_is_noninteractive(self):
        with patch.object(updates.os, 'geteuid', return_value=1000), patch.object(updates.sys.stdin, 'isatty', return_value=False), patch.object(updates.subprocess, 'run') as run:
            run.return_value.returncode = 0
            self.assertEqual(updates.main(['refresh', '--json']), 0)
            self.assertEqual(run.call_args.args[0], ['/usr/bin/sudo', '-n', '/usr/lib/rougarou-system/update', 'refresh', '--json'])

    def test_reboot_recommendation_clears_on_new_boot_and_does_not_claim_required(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            boot = root / 'boot-id'
            boot.write_text('first-boot')
            required = root / 'reboot-required'
            real_path = Path
            def paths(value):
                return {'/proc/sys/kernel/random/boot_id': boot, '/run/reboot-required': required}.get(str(value), real_path(value))
            status = root / 'status.json'
            status.write_text(json.dumps({'status': 'complete', 'boot_id': 'first-boot', 'reboot_may_be_needed': True}))
            with patch.object(updates, 'STATE', root), patch.object(updates, 'Path', side_effect=paths), patch.object(updates, 'safe_file'):
                value = updates.reboot_status()
                self.assertFalse(value['required'])
                self.assertTrue(value['recommended'])
                self.assertEqual(value['reason'], 'core-packages-updated-this-boot')
                boot.write_text('second-boot')
                self.assertFalse(updates.reboot_status()['recommended'])
                required.touch()
                self.assertTrue(updates.reboot_status()['required'])
                self.assertEqual(updates.reboot_status()['reason'], 'system-marker')

    def test_timed_out_probe_keeps_private_partial_log(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / 'command.log'
            with self.assertRaises(updates.UpdateError) as error:
                updates.run([sys.executable, '-c', "import time; print('probe started', flush=True); time.sleep(2)"], timeout=0.1, log=log)
            self.assertEqual(error.exception.code, 'command_timeout')
            self.assertIn('probe started', log.read_text())
            self.assertIn('COMMAND TIMED OUT', log.read_text())
            self.assertEqual(log.stat().st_mode & 0o777, 0o600)

    def test_status_policy_failure_does_not_return_url_or_secret(self):
        with patch.object(updates, 'source_policy', side_effect=updates.UpdateError('https://user:secret@host', 'policy_error')):
            status = updates.repository_status()
        self.assertEqual(status['sources'][0]['status'], 'policy_error')
        self.assertNotIn('secret', json.dumps(status))


if __name__ == '__main__':
    unittest.main()
