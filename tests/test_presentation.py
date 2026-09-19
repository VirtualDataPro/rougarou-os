"""Exercise the terminal boundaries that made the original login cluttered."""
import importlib.util
from pathlib import Path
import re
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1] / "rootfs/usr"
spec = importlib.util.spec_from_file_location("presentation", BASE / "lib/rougarou/presentation.py")
presentation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(presentation)
SHARE = BASE / "share/rougarou"


class PresentationTests(unittest.TestCase):
    def unicode_welcome(self, **kwargs):
        with patch.object(presentation.sys, "stdout", SimpleNamespace(encoding="utf-8")):
            return presentation.welcome_text(SHARE, **kwargs)

    def test_wide_output_matches_exact_owner_reference(self):
        version = (SHARE / "version").read_text().strip()
        expected = (Path(__file__).parent / "fixtures/welcome-wide.txt").read_text().replace("@VERSION@", version).replace("@HOSTNAME@", "RougarouOS")
        with patch.object(presentation.os, "uname", return_value=SimpleNamespace(nodename="RougarouOS")):
            value = self.unicode_welcome(status="Worker ready  |  0 queued  0 running  1 unsuccessful runs", columns=101, rows=25, term="xterm-256color", color=False, tty=True)
        self.assertEqual(value, expected)
        self.assertEqual(value.splitlines()[4].index("ROUGAROU OS"), 44)
        self.assertEqual(value.splitlines()[10].index("Worker ready"), 44)

    def test_wrapping_does_not_move_heading_hostname_or_status_anchor(self):
        status = "Worker ready  |  123 queued  45 running  6 unsuccessful runs"
        for columns in (78, 80, 100, 120):
            with self.subTest(columns=columns), patch.object(presentation.os, "uname", return_value=SimpleNamespace(nodename="worker-host")):
                lines = self.unicode_welcome(status=status, columns=columns, rows=25, term="xterm-256color", color=False, tty=True).splitlines()
                self.assertEqual(lines[4][44:], "ROUGAROU OS")
                self.assertEqual(lines[7][44:], "worker-host")
                self.assertTrue(lines[10][44:].startswith("Worker ready"))
                panel = " ".join(line[44:].strip() for line in lines[10:])
                for count in ("123 queued", "45 running", "6 unsuccessful runs"):
                    self.assertIn(count, panel)
                self.assertTrue(all(presentation.cells(line) <= columns for line in lines))
                self.assertTrue(all(line == line.rstrip() for line in lines))

    def test_dynamic_values_are_read_at_render_time(self):
        with tempfile.TemporaryDirectory() as directory:
            share = Path(directory)
            for name in ("version", "colors.toml", "wolf.txt", "wolf-ascii.txt"):
                (share / name).write_bytes((SHARE / name).read_bytes())
            (share / "version").write_text("9.8.7-test\n")
            with patch.object(presentation.os, "uname", return_value=SimpleNamespace(nodename="new-host")):
                value = presentation.welcome_text(share, "Worker offline  |  42 queued  7 running  3 unsuccessful runs", columns=120, rows=25, term="xterm-256color", color=False, tty=True)
            for field in ("9.8.7-test", "new-host", "Worker offline", "42 queued", "7 running", "3 unsuccessful runs"):
                self.assertIn(field, value)

    def test_compact_layout_keeps_wrapped_worker_counts_when_room_allows(self):
        value = self.unicode_welcome(status="Worker ready  |  12 queued  3 running  4 unsuccessful runs", columns=40, rows=12, term="xterm-256color", color=False, tty=True)
        for field in ("12 queued", "3 running", "4 unsuccessful runs"):
            self.assertIn(field, value)
        self.assertIn("rougarou help", value)
        self.assertFalse(any(0x2800 <= ord(char) <= 0x28ff for char in value))

    def test_ack_hint_and_attention_count_fit_normal_and_compact_welcome(self):
        status = "Worker offline  |  12 queued  3 running  4 need review; rougarou jobs ack"
        for columns, rows, term in ((78, 25, "xterm-256color"), (80, 25, "xterm-256color"),
                                    (101, 25, "xterm-256color"), (80, 25, "linux"),
                                    (40, 12, "xterm-256color")):
            with self.subTest(columns=columns, term=term):
                value = self.unicode_welcome(status=status, columns=columns, rows=rows, term=term, color=False, tty=True)
                lines = value.splitlines()
                if term == "xterm-256color" and columns >= 78:
                    panel = " ".join(line[44:].strip() for line in lines[10:])
                    self.assertEqual(lines[4][44:], "ROUGAROU OS")
                    self.assertLessEqual(len(lines), 23)
                else:
                    panel = " ".join(lines)
                for field in ("12 queued", "3 running", "4 need review", "rougarou jobs ack"):
                    self.assertIn(field, panel)
                self.assertTrue(all(presentation.cells(line) <= columns for line in lines))

    def test_welcome_fits_common_terminal_sizes(self):
        for columns, rows, term in [(80, 25, "xterm-256color"), (80, 24, "linux"), (40, 12, "vt100"), (120, 32, "xterm-256color"), (20, 6, "xterm-256color"), (1, 1, "dumb")]:
            value = presentation.welcome_text(SHARE, "Worker ready | 2 queued, 0 running", columns=columns, rows=rows, term=term, color=False, tty=True)
            self.assertTrue(all(presentation.cells(line) <= columns for line in value.splitlines()))
            self.assertLessEqual(len(value.splitlines()), max(1, rows - 2))
            if columns >= len("ROUGAROU OS"):
                self.assertEqual(value.count("ROUGAROU OS"), 1)

    def test_console_uses_ascii_not_unsupported_braille(self):
        value = presentation.welcome_text(SHARE, columns=80, rows=25, term="linux", color=False, tty=True)
        self.assertFalse(any(0x2800 <= ord(c) <= 0x28ff for c in value))

    def test_unicode_terminal_uses_owner_screensaver_art(self):
        value = presentation.welcome_text(SHARE, columns=100, rows=30, term="xterm-256color", color=False, tty=True)
        for line in (SHARE / "wolf.txt").read_text().splitlines():
            self.assertIn(line, value)

    def test_plain_output_is_compact_and_control_free(self):
        value = presentation.welcome_text(SHARE, "ready\033[31m\nnew", tty=False, color=False)
        self.assertNotIn("\033", value)
        self.assertLessEqual(len(value.splitlines()), 5)

    def test_color_always_resets_and_never_moves_cursor(self):
        value = presentation.welcome_text(SHARE, tty=True, term="xterm-256color", color=True)
        self.assertIn("\033[38;2;121;197;142m", value)
        self.assertEqual(len(re.findall(r"\033\[38;2;", value)), value.count("\033[0m"))
        self.assertNotRegex(value, r"\033\[[0-9;]*[HJf]")
