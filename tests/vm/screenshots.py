"""QMP capture of guest VGA output; no network listener or keyboard injection."""
import hashlib
import json
from pathlib import Path
import socket
import time


class QMP:
    def __init__(self, path, timeout=15):
        deadline = time.monotonic() + timeout
        self.socket = None
        while time.monotonic() < deadline:
            channel = socket.socket(socket.AF_UNIX)
            channel.settimeout(timeout)
            try:
                channel.connect(str(path))
                self.socket = channel
                break
            except (FileNotFoundError, ConnectionRefusedError):
                channel.close()
                time.sleep(.1)
        if self.socket is None:
            raise RuntimeError('QMP screenshot channel did not become available')
        self.stream = self.socket.makefile('rb')
        greeting = json.loads(self.stream.readline())
        assert 'QMP' in greeting, greeting
        self.serial = 0
        self.call('qmp_capabilities')

    def call(self, execute, arguments=None):
        self.serial += 1
        request = {'execute': execute, 'id': self.serial}
        if arguments is not None:
            request['arguments'] = arguments
        self.socket.sendall(json.dumps(request).encode() + b'\n')
        while True:
            line = self.stream.readline()
            if not line:
                raise RuntimeError('QMP screenshot channel ended')
            response = json.loads(line)
            if response.get('id') != self.serial:
                continue
            if 'error' in response:
                raise RuntimeError(response['error'])
            return response['return']

    def screenshot(self, filename):
        filename = Path(filename)
        self.call('screendump', {'filename': str(filename), 'format': 'png'})
        data = filename.read_bytes()
        assert data.startswith(b'\x89PNG\r\n\x1a\n'), 'QEMU did not produce a PNG'
        return {'file': filename.name, 'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}

    def close(self):
        self.stream.close()
        self.socket.close()


def capture_boot_frames(path, directory):
    """Retain a short sequence so firmware timing cannot hide a five-second menu.

    Captured frames still need visual review. Saving PNG files does not assert
    that a particular menu was visible or that branding was correct.
    """
    directory = Path(directory)
    directory.mkdir()
    qmp = QMP(path)
    frames = []
    started = time.monotonic()
    try:
        for number in range(24):
            fact = qmp.screenshot(directory / f'boot-{number:02d}.png')
            fact['elapsed_seconds'] = round(time.monotonic() - started, 3)
            frames.append(fact)
            time.sleep(.4)
    finally:
        qmp.close()
    (directory / 'frames.json').write_text(json.dumps({'visual_review_required': True, 'frames': frames}, indent=2) + '\n')
