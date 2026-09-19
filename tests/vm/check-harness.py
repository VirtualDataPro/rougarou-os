#!/usr/bin/env python3
"""Exercise acceptance rendering without booting or modifying any VM."""
import ast
import importlib.util
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import validate

spec = importlib.util.spec_from_file_location('timing', Path(__file__).with_name('installer-timings.py'))
timing = importlib.util.module_from_spec(spec)
spec.loader.exec_module(timing)


class HarnessTests(unittest.TestCase):
    def test_rendered_scripts_preserve_all_three_explicit_selections(self):
        # Rendering can be checked before the independent Gemini pin review is
        # complete. The real run still requires versions.env's reviewed value.
        with patch.dict(validate.PACKAGE_VERSIONS, {'rougarou-gemini': '0.60.0+test'}):
            for case in validate.CASE_NAMES:
                with self.subTest(case=case):
                    script = validate.render_guest_script(case)
                    self.assertFalse(re.search(r'@[A-Z_]+@', script))
                    subprocess.run(['sh', '-n'], input=script, text=True, check=True)
                    for number, block in enumerate(re.findall(r"<<'PY'\n(.*?)\nPY", script, re.S)):
                        ast.parse(block, filename=f'{case}-embedded-{number}.py')
                    self.assertIn('EXACT_RECORDED_SELECTION_AND_UNSELECTED_PACKAGE_ABSENCE_PASSED', script)
        self.assertEqual(set(validate.selected_package_versions('bios')), {'rougarou-base'})
        self.assertEqual(set(validate.selected_package_versions('uefi')), {'rougarou-base', 'rougarou-codex', 'rougarou-herdr'})

    def test_system_case_uses_bios_and_selected_system_engine(self):
        self.assertEqual(validate.case_firmware('bios-system'), 'bios')
        self.assertEqual(validate.SOFTWARE_CHOICES['bios-system']['docker'], 'system')
        self.assertEqual(validate.SOFTWARE_CHOICES['bios-system']['podman'], 'false')

    def test_timing_extractor_requires_all_stages_and_omits_unrelated_log_content(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root / 'var/log/installer').mkdir(parents=True)
            (root / 'var/log/apt').mkdir()
            (root / 'var/log/installer/syslog').write_text("Sep 19 10:20:30 main-menu[15]: INFO: Menu item 'bootstrap-base' selected\nprivate fixture must not be exported\n")
            (root / 'var/log/apt/history.log').write_text('Start-Date: 2026-09-19  10:20:31\nCommandline: private fixture\nEnd-Date: 2026-09-19  10:20:35\n')
            lines = []
            for number, stage in enumerate(('verify', 'unpack', 'packages', 'configure', 'finish')):
                lines += [f'ROUGAROU_STAGE stage={stage} started={number * 10} utc=2026-09-19T10:20:30Z',
                          f'ROUGAROU_TIMING stage={stage} seconds=10']
            log = root / 'var/log/rougarou-install.log'
            log.write_text('\n'.join(lines) + '\nprivate fixture\n')
            result = timing.collect(root)
            self.assertEqual(result['rougarou_stage_seconds'], 50)
            self.assertNotIn('private fixture', str(result))
            log.write_text('\n'.join(lines[:-1]))
            with self.assertRaisesRegex(AssertionError, 'durations'):
                timing.collect(root)


if __name__ == '__main__':
    unittest.main()
