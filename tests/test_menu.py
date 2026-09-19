"""PTY navigation plus real subprocess argv capture; no operator actions run."""
import contextlib
import io
import json
import os
from pathlib import Path
import pty
import select
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

LIB = Path(os.environ.get('ROUGAROU_TEST_LIB', Path(__file__).resolve().parents[1] / 'rootfs/usr/lib/rougarou'))
sys.path.insert(0, str(LIB))
STAGE = Path(__file__).parent
if (STAGE / 'menu.py').exists():
    sys.path.insert(0, str(STAGE))
else:
    STAGE = LIB
import cli
import menu


class MenuTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.capture = self.root / 'argv.jsonl'
        fixture = self.root / 'usr/lib/rougarou/menu.py'
        fixture.parent.mkdir(parents=True)
        fixture.write_text('# fixture path only\n')
        entry = self.root / 'usr/local/bin/rougarou'
        entry.parent.mkdir(parents=True)
        entry.write_text('import json,os,sys\nwith open(os.environ["MENU_CAPTURE"], "a") as stream:\n'
                         '    stream.write(json.dumps(sys.argv[1:])+"\\n")\n'
                         'raise SystemExit(int(os.environ.get("MENU_EXIT", "0")))\n')
        env = patch.dict(os.environ, {'HOME': str(self.root), 'MENU_CAPTURE': str(self.capture), 'MENU_EXIT': '0'})
        env.start()
        self.addCleanup(env.stop)
        location = patch.object(menu, '__file__', str(fixture))
        location.start()
        self.addCleanup(location.stop)
        self.output = io.StringIO()

    def calls(self):
        return [json.loads(line) for line in self.capture.read_text().splitlines()] if self.capture.exists() else []

    def run_menu(self, function, answers, confirmations=()):
        with patch.object(cli, 'field', side_effect=answers), patch.object(cli, 'confirm', side_effect=confirmations) as confirm, \
                contextlib.redirect_stdout(self.output):
            function()
        return confirm

    def test_paths_and_shell_metacharacters_are_single_literal_arguments(self):
        marker = self.root / 'shell-was-executed'
        directory = f'/mounted backup/$(touch {marker}); echo ignored'
        password = '/private password/`id`'
        include = '/service data/one; two'
        self.run_menu(menu.backups_menu, ['1', directory, password, include, '0'], [True])
        self.assertEqual(self.calls(), [['backup', 'init', '--repository', directory, '--password-file', password,
                                        '--existing', '--include', include]])
        self.assertFalse(marker.exists())

    def test_failed_restore_preview_prevents_confirmation_or_apply(self):
        snapshot = 'a' * 64
        with patch.dict(os.environ, {'MENU_EXIT': '7'}):
            confirm = self.run_menu(menu.backups_menu, ['5', snapshot, '0'])
        confirm.assert_not_called()
        self.assertEqual(self.calls(), [['backup', 'restore', snapshot]])
        self.assertIn('status 7', self.output.getvalue())

    def test_restore_requires_full_id_preview_success_and_explicit_confirmation(self):
        snapshot = 'b' * 64
        target = '/new recovery/$(never-evaluate)'
        confirm = self.run_menu(menu.backups_menu, ['5', 'latest', '5', 'b' * 8, '5', snapshot, target, '0'], [True])
        self.assertEqual(confirm.call_count, 1)
        self.assertEqual(self.calls(), [['backup', 'restore', snapshot],
                                        ['backup', 'restore', snapshot, '--apply', '--target', target]])
        self.assertIn('full snapshot ID', self.output.getvalue())

    def test_restore_default_decline_has_no_apply_or_target_prompt(self):
        snapshot = 'c' * 64
        self.run_menu(menu.backups_menu, ['5', snapshot, '0'], [False])
        self.assertEqual(self.calls(), [['backup', 'restore', snapshot]])

    def test_cancelled_configuration_and_updates_have_no_side_effects(self):
        self.run_menu(menu.backups_menu, ['1', '', '5', '', '0'])
        self.run_menu(menu.updates_menu, ['3', '', '3', 'plan-id', '', '0'])
        self.run_menu(menu.jobs_menu, ['2', '', '5', '0'], [False])
        self.assertEqual(self.calls(), [])

    def test_update_plan_reference_and_notification_token_file_remain_literal(self):
        plan, reference = 'plan; $(id)', 'snapshot name; echo unchanged'
        self.run_menu(menu.updates_menu, ['3', plan, reference, '4', '0'], [True])
        token = '/private token/$(id)'
        self.run_menu(menu.notifications_menu, ['2', 'slack', token, '0'])
        self.assertEqual(self.calls(), [['update', 'apply', '--plan', plan, '--recovery-point', reference, '--confirm'],
                                        ['update', 'status'], ['notify', 'configure', '--format', 'slack', '--token-file', token]])

    def test_menu_requires_terminal_and_does_not_consume_piped_input(self):
        with patch.object(cli, 'require_operator'), patch.object(cli.sys, 'stdin', io.StringIO('1\n')), \
                patch.object(cli.sys, 'stdout', self.output), self.assertRaisesRegex(cli.OperatorError, 'interactive terminal'):
            menu.main([])
        self.assertEqual(self.calls(), [])

    @unittest.skipIf(os.geteuid() == 0, 'PTY operator invocation requires an ordinary account')
    def test_real_pty_navigation_blank_back_eof_and_interrupt(self):
        # Exercise the same cancellation boundary that cli.main provides while
        # staging before its menu dispatch is merged into the frozen tree.
        code = ('import sys; import menu,cli\ntry:\n rc=menu.main([])\n'
                'except (EOFError,KeyboardInterrupt):\n print("RETURNED_TO_SHELL"); rc=0\n'
                'except cli.OperatorError as error:\n print(str(error)); rc=1\n'
                'raise SystemExit(rc)\n')
        for inputs, expected in (([b'\n'], b'Back to shell'),
                                 ([b'not-a-choice\n', b'3\n', b'0\n', b'0\n'], b'Managed jobs'),
                                 ([b'\x04'], b'RETURNED_TO_SHELL'),
                                 ([b'\x03'], b'RETURNED_TO_SHELL')):
            with self.subTest(inputs=inputs):
                child, master = pty.fork()
                if child == 0:
                    environment = {**os.environ, 'PYTHONPATH': os.pathsep.join([str(STAGE), str(LIB)]),
                                   'PYTHONDONTWRITEBYTECODE': '1'}
                    os.execve(sys.executable, [sys.executable, '-c', code], environment)
                data = bytearray()
                status = None
                deadline = time.monotonic() + 5
                sent = 0
                next_at = 0
                try:
                    while time.monotonic() < deadline:
                        if select.select([master], [], [], .03)[0]:
                            try:
                                block = os.read(master, 8192)
                            except OSError:
                                block = b''
                            if block:
                                data.extend(block)
                        if b'Choose [0]:' in data and sent < len(inputs) and time.monotonic() >= next_at:
                            os.write(master, inputs[sent])
                            sent += 1
                            next_at = time.monotonic() + .1
                        done, status_value = os.waitpid(child, os.WNOHANG)
                        if done:
                            status = status_value
                            break
                    self.assertIsNotNone(status, 'PTY menu did not return: ' + data.decode(errors='replace'))
                    self.assertEqual(os.waitstatus_to_exitcode(status), 0, data.decode(errors='replace'))
                    self.assertIn(expected, data)
                    self.assertNotIn(b'Traceback', data)
                finally:
                    os.close(master)
                    if status is None:
                        os.kill(child, signal.SIGKILL)
                        os.waitpid(child, 0)
        self.assertEqual(self.calls(), [])


if __name__ == '__main__':
    unittest.main()
