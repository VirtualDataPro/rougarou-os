"""Installed GRUB defaults and preservation boundaries; no host boot edits."""
import importlib.machinery
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / 'rootfs/usr/lib/rougarou-system/configure-grub'
TEMPLATE = ROOT / 'rootfs/usr/share/rougarou/grub-defaults.cfg'
GENERATOR = ROOT / 'rootfs/etc/grub.d/06_rougarou_theme'
loader = importlib.machinery.SourceFileLoader('rougarou_configure_grub', str(HELPER))
spec = importlib.util.spec_from_loader(loader.name, loader)
grub = importlib.util.module_from_spec(spec)
loader.exec_module(grub)


class GrubDefaultsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name) / 'grub.d'

    def seed(self):
        return grub.seed_defaults(self.directory, TEMPLATE, uid=os.geteuid())

    def test_missing_defaults_are_created_and_repeat_preserves_local_edit(self):
        self.assertTrue(self.seed())
        target = self.directory / grub.CONFIGURATION
        self.assertEqual(target.read_bytes(), TEMPLATE.read_bytes())
        self.assertEqual(target.stat().st_mode & 0o777, 0o644)
        target.write_text('GRUB_TIMEOUT=42\n# operator choice\n')
        target.chmod(0o600)
        self.assertFalse(self.seed())
        self.assertEqual(target.read_text(), 'GRUB_TIMEOUT=42\n# operator choice\n')
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        self.assertEqual(list(self.directory.glob('.rougarou-grub-*')), [])

    def test_symlink_and_writable_configuration_are_refused(self):
        self.directory.mkdir()
        other = Path(self.temporary.name) / 'operator.cfg'
        other.write_text('untouched\n')
        target = self.directory / grub.CONFIGURATION
        target.symlink_to(other)
        with self.assertRaises(grub.GrubError):
            self.seed()
        self.assertEqual(other.read_text(), 'untouched\n')
        target.unlink()
        target.write_text('untouched\n')
        target.chmod(0o666)
        with self.assertRaises(grub.GrubError):
            self.seed()
        self.assertEqual(target.read_text(), 'untouched\n')

    def test_symlink_directory_is_refused(self):
        self.directory.symlink_to(self.temporary.name)
        with self.assertRaises(grub.GrubError):
            self.seed()

    def shell_defaults(self, environment):
        command = '. "$1"; printf "%s\\n" "$GRUB_DISTRIBUTOR" "$GRUB_TIMEOUT_STYLE" "$GRUB_TIMEOUT" "$GRUB_TERMINAL_INPUT" "$GRUB_TERMINAL_OUTPUT" "$GRUB_CMDLINE_LINUX" "$GRUB_DISABLE_RECOVERY"'
        return subprocess.check_output(['/bin/sh', '-c', command, 'test', str(TEMPLATE)], env=environment, text=True).splitlines()

    def test_native_console_defaults_have_brand_and_visible_menu(self):
        values = self.shell_defaults({'PATH': '/usr/bin:/bin'})
        self.assertEqual(values[:5], ['RougarouOS', 'menu', '5', 'console', 'console'])

    def test_local_serial_timeout_kernel_and_recovery_settings_survive(self):
        values = self.shell_defaults({'PATH': '/usr/bin:/bin', 'GRUB_TIMEOUT': '17', 'GRUB_TIMEOUT_STYLE': 'countdown',
                                      'GRUB_TERMINAL': 'console serial', 'GRUB_CMDLINE_LINUX': 'cryptdevice=UUID=example console=ttyS0,115200',
                                      'GRUB_DISABLE_RECOVERY': 'true'})
        self.assertEqual(values[1:5], ['countdown', '17', 'console serial', 'console serial'])
        self.assertEqual(values[5:], ['cryptdevice=UUID=example console=ttyS0,115200', 'true'])

    def test_palette_requires_opt_in_and_respects_existing_visual_theme(self):
        def output(extra):
            return subprocess.check_output(['/bin/sh', str(GENERATOR)], env={'PATH': '/usr/bin:/bin', **extra}, text=True)
        self.assertEqual(output({}), '')
        self.assertEqual(output({'ROUGAROU_GRUB_PALETTE': 'no'}), '')
        self.assertEqual(output({'ROUGAROU_GRUB_PALETTE': 'yes', 'GRUB_THEME': '/boot/local.theme'}), '')
        self.assertEqual(output({'ROUGAROU_GRUB_PALETTE': 'yes', 'GRUB_BACKGROUND': '/boot/local.png'}), '')
        palette = output({'ROUGAROU_GRUB_PALETTE': 'yes'})
        self.assertIn('set menu_color_normal=light-gray/black\n', palette)
        self.assertIn('set menu_color_highlight=black/green\n', palette)
        self.assertNotIn('menuentry', palette)
        self.assertNotIn('linux ', palette)


if __name__ == '__main__':
    unittest.main()
