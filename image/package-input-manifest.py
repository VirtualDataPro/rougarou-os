#!/usr/bin/env python3
"""Fingerprint all custom-package inputs so a tested candidate can be reused."""
import hashlib
import json
from pathlib import Path
import stat


EXTRA_INPUTS = (
    'versions.env', 'packages.txt', 'LICENSE', 'scripts/build-inside-container.sh',
    'image/package-input-manifest.py', 'image/build-herdr.py', 'image/herdr-license-overrides.json',
    'image/package-profiles.py', 'image/package-profiles.txt',
    'image/build-base-package.sh', 'scripts/build-base-update.sh', 'scripts/repo.py',
    'image/build-gemini.py', 'image/gemini-upstream.json', 'image/gemini-license-overrides.json',
    'image/rougarou-base.preinst', 'image/rougarou-base.postinst',
    'image/rougarou-base.postrm', 'image/rougarou-base.triggers')


def fingerprint(path):
    info = path.lstat()
    result = {'mode': f'{stat.S_IMODE(info.st_mode):04o}'}
    if stat.S_ISLNK(info.st_mode):
        result.update(type='symlink', target=str(path.readlink()))
    elif stat.S_ISREG(info.st_mode):
        with path.open('rb') as stream:
            result.update(type='file', sha256=hashlib.file_digest(stream, 'sha256').hexdigest())
    elif stat.S_ISDIR(info.st_mode):
        result.update(type='directory')
    else:
        raise ValueError(f'Unsupported package input type: {path}')
    return result


def manifest(source, paths=None):
    if paths is None:
        paths = list((source / 'rootfs').rglob('*'))
        paths += [source / name for name in EXTRA_INPUTS]
    entries = {}
    for path in sorted(paths):
        if '__pycache__' in path.parts or path.suffix in ('.pyc', '.pyo'):
            continue
        entries[str(path.relative_to(source))] = fingerprint(path)
    return {'schema_version': 2, 'inputs': entries}


if __name__ == '__main__':
    print(json.dumps(manifest(Path('/src')), sort_keys=True, indent=2))
