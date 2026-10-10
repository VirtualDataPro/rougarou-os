"""Real dialog keyboard navigation; runs on an installed guest with dialog."""
import fcntl
import json
import os
from pathlib import Path
import pty
import re
import select
import shutil
import signal
import struct
import sys
import tempfile
import termios
import time
import unittest

LIB = Path(os.environ.get('ROUGAROU_TEST_LIB', Path(__file__).resolve().parents[1] / 'rootfs/usr/lib/rougarou'))


@unittest.skipUnless(shutil.which('dialog'), 'dialog is required for native terminal tests')
class TerminalTests(unittest.TestCase):
    def exercise(self, code, steps, size=(24, 80), environment=None):
        child, master = pty.fork()
        if child == 0:
            fcntl.ioctl(0, termios.TIOCSWINSZ, struct.pack('HHHH', *size, 0, 0))
            os.execve(sys.executable, [sys.executable, '-c', 'import menu\n' + code],
                      {**os.environ, 'PYTHONPATH': str(LIB), 'TERM': 'xterm-256color',
                       'PYTHONDONTWRITEBYTECODE': '1', **(environment or {})})
        output, recent, status = bytearray(), bytearray(), None
        deadline = time.monotonic() + 15
        steps = iter(steps)
        step = next(steps, None)
        try:
            while time.monotonic() < deadline:
                if select.select([master], [], [], .05)[0]:
                    try:
                        block = os.read(master, 65536)
                    except OSError:
                        block = b''
                    output.extend(block)
                    recent.extend(block)
                if step and step[0] in recent:
                    time.sleep(.15)
                    os.write(master, step[1])
                    recent.clear()
                    step = next(steps, None)
                done, code = os.waitpid(child, os.WNOHANG)
                if done:
                    status = code
                    break
            self.assertIsNotNone(status, 'Terminal hung: ' + output.decode(errors='replace')[-2000:])
            self.assertEqual(os.waitstatus_to_exitcode(status), 0, output.decode(errors='replace')[-2000:])
            self.assertIn(b'PASS', output)
            self.assertNotIn(b'Traceback', output)
        finally:
            os.close(master)
            if status is None:
                os.kill(child, signal.SIGKILL)
                os.waitpid(child, 0)
        return bytes(output)

    def test_arrow_selection_and_escape_at_supported_sizes(self):
        for size in ((20, 60), (24, 80), (32, 120)):
            with self.subTest(size=size):
                self.exercise("value = menu.choose('Pick a record', [('First run', 'first'), ('Second run', 'second')])\nassert value == 'second'\ntry:\n menu.field('Cancel draft', 'Name')\nexcept menu.Back:\n print('PASS')\nelse:\n raise AssertionError('not cancelled')",
                              [(b'First run', b'\x1bOB\r'), (b'Cancel draft', b'\x1b')], size)

    def test_cached_model_can_be_selected_without_typing_identifier(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'models_cache.json').write_text(json.dumps({'models': [{'slug': 'fixture-model', 'display_name': 'Fixture model', 'visibility': 'list'}]}))
            self.exercise("assert menu.pick_model({'kind':'codex'}) == 'fixture-model'\nprint('PASS')",
                          [(b'ixture model', b'\x1bOB\r')], environment={'CODEX_HOME': directory})

    @unittest.skipIf(os.geteuid() == 0, 'overview requires an ordinary operator')
    def test_exit_escape_and_interrupt_restore_shell_screen_and_tty(self):
        code = ("import termios,sys\n"
                "before = termios.tcgetattr(0)\n"
                "print('SHELL_BEFORE', flush=True)\n"
                "try:\n menu.main([])\n"
                "except KeyboardInterrupt:\n pass\n"
                "assert termios.tcgetattr(0) == before, 'terminal modes were not restored'\n"
                "print('PASS SHELL_AFTER', flush=True)\n")
        for keys in (b'\t\r', b'\x1b', b'\x03'):
            with self.subTest(keys=keys), tempfile.TemporaryDirectory() as directory:
                output = self.exercise(code, [(b'Overview', keys)],
                                       environment={'XDG_STATE_HOME': directory})
                alternate, painted = False, False
                for match in re.finditer(rb'\x1b\[\?1049([hl])|CONTROL CENTER|SHELL_AFTER', output):
                    if match.group(1):
                        alternate = match.group(1) == b'h'
                    elif match.group() == b'CONTROL CENTER':
                        self.assertTrue(alternate, 'menu painted on the shell screen')
                        painted = True
                    else:
                        self.assertFalse(alternate, 'shell returned on the alternate screen')
                self.assertTrue(painted)
                self.assertFalse(alternate)

    @unittest.skipIf(os.geteuid() == 0, 'overview requires an ordinary operator')
    def test_overview_worker_screen_and_back(self):
        with tempfile.TemporaryDirectory() as directory:
            self.exercise("assert menu.main([]) == 0\nprint('PASS')",
                          [(b'Overview', b'\x1bOB\x1bOB\r'), (b'Your worker', b'\x1b'), (b'Overview', b'\x1b')],
                          environment={'XDG_STATE_HOME': directory})


if __name__ == '__main__':
    unittest.main()
