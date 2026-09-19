#!/usr/bin/env python3
"""Check actual Codex startup bytes with fresh state; never submit a prompt.

Run only in an offline disposable test environment. No provider credentials or
operator HOME are used. This validates the switch, not the original delay.
"""
import errno
import json
import os
from pathlib import Path
import pty
import re
import select
import shutil
import signal
import tempfile
import time


def capture(binary, disabled):
    with tempfile.TemporaryDirectory(prefix="rougarou-codex-keyboard-") as temporary:
        home = Path(temporary)
        (home / ".codex").mkdir(mode=0o700)
        env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(home),
               "CODEX_HOME": str(home / ".codex"), "TERM": "xterm-256color",
               "LC_ALL": "C.UTF-8", "CODEX_TUI_DISABLE_KEYBOARD_ENHANCEMENT": disabled}
        pid, terminal = pty.fork()
        if pid == 0:
            os.chdir(home)
            os.execve(binary, [binary, "--no-alt-screen"], env)
        output = bytearray()
        try:
            deadline = time.monotonic() + 4
            while time.monotonic() < deadline:
                if not select.select([terminal], [], [], 0.1)[0]:
                    continue
                try:
                    part = os.read(terminal, 65536)
                except OSError as error:
                    if error.errno == errno.EIO:
                        break
                    raise
                if not part:
                    break
                output.extend(part)
                # Respond only to the terminal cursor query. No keyboard input,
                # trust confirmation, authentication or model request occurs.
                if b"\x1b[6n" in part:
                    os.write(terminal, b"\x1b[1;1R")
            return bytes(output)
        finally:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            os.waitpid(pid, 0)
            os.close(terminal)


if __name__ == "__main__":
    binary = shutil.which("codex")
    if binary is None:
        raise SystemExit("Pinned Codex must be installed")
    counts = {}
    for disabled in ("0", "1"):
        data = capture(binary, disabled)
        pushes = re.findall(rb"\x1b\[>[0-9;]*u", data)
        queries = data.count(b"\x1b[?u")
        counts[disabled] = {"pushes": len(pushes), "queries": queries}
        if disabled == "0":
            assert pushes, "Control run did not enter Codex keyboard setup"
        else:
            assert not pushes and not queries, "Disabled run still enhanced/probed keyboard"
    print(json.dumps({"result": "pass", "keyboard_enhancement_disabled": counts,
                      "fresh_state": True, "model_requests": 0}, sort_keys=True))
