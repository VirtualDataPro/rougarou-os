#!/usr/bin/env python3
"""Package the pinned official Gemini JS bundle with source and license evidence.

No npm hooks or downloaded code are executed. The guest uses Debian's Node.js;
the resulting package has no installation scripts or network installation step.
"""
from concurrent.futures import ThreadPoolExecutor
import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.request
import zipfile

LOCKS = {}
GUARD = threading.Lock()


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def verify(path, integrity):
    if re.fullmatch(r'[0-9a-f]{64}', integrity):
        valid = sha(path) == integrity
    else:
        algorithm, encoded = integrity.split('-', 1)
        if algorithm != 'sha512':
            raise RuntimeError('Unreviewed dependency integrity algorithm')
        with path.open('rb') as stream:
            valid = hashlib.file_digest(stream, algorithm).digest() == base64.b64decode(encoded, validate=True)
    if not valid:
        raise RuntimeError(f'Pinned artifact checksum mismatch: {path.name}')


def fetch(url, target, integrity):
    if not url.startswith('https://'):
        raise RuntimeError('Artifact source must use HTTPS')
    with GUARD:
        lock = LOCKS.setdefault(str(target), threading.Lock())
    with lock:
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            request = urllib.request.Request(url, headers={'User-Agent': 'RougarouOS-builder'})
            for attempt in range(4):
                descriptor, temporary = tempfile.mkstemp(prefix='.download-', dir=target.parent)
                try:
                    with os.fdopen(descriptor, 'wb') as output, urllib.request.urlopen(request, timeout=45) as response:
                        shutil.copyfileobj(response, output)
                    verify(Path(temporary), integrity)
                    os.replace(temporary, target)
                    break
                except OSError:
                    if attempt == 3:
                        raise
                    time.sleep(attempt + 1)
                finally:
                    Path(temporary).unlink(missing_ok=True)
        verify(target, integrity)
        return target


def relative(name):
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts or '\\' in name:
        raise RuntimeError('Unsafe archive path')
    return path


def unpack_bundle(path, destination):
    seen = set()
    with zipfile.ZipFile(path) as archive:
        if sum(item.file_size for item in archive.infolist()) > 256 * 1024 * 1024:
            raise RuntimeError('Unexpected expanded bundle size')
        for item in archive.infolist():
            name = relative(item.filename)
            kind = stat.S_IFMT(item.external_attr >> 16)
            if str(name) in seen or kind not in (0, stat.S_IFDIR, stat.S_IFREG):
                raise RuntimeError('Duplicate or non-regular bundle entry')
            seen.add(str(name))
            target = destination / name
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(item))
                target.chmod(0o644)
    if not (destination / 'gemini.js').is_file():
        raise RuntimeError('Official CLI entry point missing')


def is_notice(name):
    return PurePosixPath(name).name.upper().startswith(('LICENSE', 'LICENCE', 'COPYING', 'NOTICE', 'COPYRIGHT', 'UNLICENSE'))


def copy_notices(archive, destination, *, selected=()):
    copied = []
    for item in archive:
        if not item.isfile() or not (is_notice(item.name) or item.name in selected):
            continue
        name = relative(item.name)
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(archive.extractfile(item).read())
        copied.append(str(name))
    if not set(selected) <= set(copied):
        raise RuntimeError('Reviewed dependency notice disappeared')
    return sorted(copied)


def bundle_dependencies(bundle, locked):
    # esbuild retains input module paths in its official release. Conservatively
    # retain every locked version of each referenced package, not an exact SBOM.
    names = set()
    for path in bundle.rglob('*.js'):
        if 'third_party' in path.relative_to(bundle).parts:
            continue  # This upstream sub-bundle ships its own complete notices.
        for comment in re.findall(r'(?m)^\s*//\s+([^\n]*node_modules/[^\n]+)', path.read_text()):
            names.update(re.findall(r'node_modules/((?:@[^/\s]+/)?[^/\s]+)', comment))
    selected = {}
    covered = set()
    for key, item in locked.items():
        name = key.rsplit('node_modules/', 1)[-1]
        if (name not in names and item.get('name') not in names) or not item.get('resolved'):
            continue
        if not item['resolved'].startswith('https://registry.npmjs.org/'):
            raise RuntimeError('Unreviewed dependency registry')
        covered.update((name, item.get('name')))
        entry = selected.setdefault(item['resolved'], {**item, 'name': item.get('name', name), 'lock_paths': []})
        if entry['integrity'] != item['integrity'] or entry['version'] != item['version']:
            raise RuntimeError('Conflicting locked dependency metadata')
        entry['lock_paths'].append(key)
    if not names or names - covered:
        raise RuntimeError('Bundle dependency provenance requires review')
    return list(selected.values())


def main():
    os.umask(0o022)
    work, repository, cache = map(Path, sys.argv[1:])
    version = os.environ['GEMINI_VERSION']
    revision = os.environ['ROUGAROU_PACKAGE_REVISION']
    asset = os.environ['GEMINI_ASSET']
    metadata = json.loads((Path(__file__).parent / 'gemini-upstream.json').read_text())
    if (version, asset, os.environ['GEMINI_SHA256'], os.environ['GEMINI_SOURCE_SHA256']) != (
            metadata['version'], metadata['asset'], metadata['sha256'], metadata['source_sha256']):
        raise RuntimeError('Gemini release pins differ from reviewed upstream metadata')
    bundle_archive = fetch(metadata['download'], cache / f'{version}-{asset}', metadata['sha256'])
    source_archive = fetch(metadata['source_archive'], cache / f'{version}-source.tar.gz', metadata['source_sha256'])
    if bundle_archive.stat().st_size != metadata['size']:
        raise RuntimeError('Gemini bundle size changed')
    package = work / 'gemini-package'
    runtime = package / 'usr/lib/rougarou-gemini'
    documentation = package / 'usr/share/doc/rougarou-gemini'
    documentation.mkdir(parents=True)
    runtime.mkdir(parents=True)
    unpack_bundle(bundle_archive, runtime)
    sources = work / 'sources/gemini'
    sources.mkdir(parents=True)
    shutil.copyfile(bundle_archive, sources / asset)
    shutil.copyfile(source_archive, sources / f'gemini-cli-{version}-source.tar.gz')
    with tarfile.open(source_archive) as archive:
        prefix = f'gemini-cli-{version}'
        root_metadata = json.loads(archive.extractfile(f'{prefix}/package.json').read())
        cli_metadata = json.loads(archive.extractfile(f'{prefix}/packages/cli/package.json').read())
        if root_metadata['version'] != version or cli_metadata['license'] != 'Apache-2.0':
            raise RuntimeError('Changed Gemini version/license requires review')
        license_bytes = archive.extractfile(f'{prefix}/LICENSE').read()
        if b'Apache License' not in license_bytes:
            raise RuntimeError('Unexpected upstream license')
        (documentation / 'copyright').write_bytes(license_bytes)
        copy_notices(archive, documentation / 'upstream-notices')
        locked = json.loads(archive.extractfile(f'{prefix}/package-lock.json').read())['packages']
    overrides = json.loads((Path(__file__).parent / 'gemini-license-overrides.json').read_text())

    def dependency(item):
        url = item['resolved']
        filename = hashlib.sha256(url.encode()).hexdigest() + '.tgz'
        path = fetch(url, cache / 'npm' / filename, item['integrity'])
        shutil.copyfile(path, sources / ('npm-' + filename))
        identity = item['name'] + '@' + item['version']
        supplemental = overrides.get(identity, {})
        location = documentation / 'dependency-notices' / filename.removesuffix('.tgz')
        with tarfile.open(path) as archive:
            notices = copy_notices(archive, location, selected=supplemental.get('archive_notices', ()))
        for entry in supplemental.get('supplemental', ()):
            if Path(entry['name']).name != entry['name']:
                raise RuntimeError('Unsafe supplemental notice name')
            notice = fetch(entry['url'], cache / 'overrides' / entry['sha256'], entry['sha256'])
            location.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(notice, location / entry['name'])
            shutil.copyfile(notice, sources / ('license-' + entry['sha256'] + '.txt'))
        if not notices and not supplemental.get('supplemental'):
            raise RuntimeError(f'Missing dependency notices: {identity}')
        return {'name': item['name'], 'version': item['version'], 'url': url, 'sha256': sha(path),
                'integrity': item['integrity'], 'license': item.get('license'), 'lock_paths': item['lock_paths'],
                'notices': notices, 'supplemental_notices': supplemental.get('supplemental', [])}

    with ThreadPoolExecutor(max_workers=12) as pool:
        dependencies = list(pool.map(dependency, bundle_dependencies(runtime, locked)))
    dependencies.sort(key=lambda item: (item['name'], item['version']))
    (documentation / 'dependencies.json').write_text(json.dumps(dependencies, indent=2) + '\n')
    bundled_notices = runtime / 'bundled/third_party/THIRD_PARTY_NOTICES'
    if not bundled_notices.is_file():
        raise RuntimeError('Upstream browser-tool dependency notices missing')
    shutil.copyfile(bundled_notices, documentation / 'THIRD_PARTY_NOTICES')
    # The release zip lacks its package root. Add only Node's ESM metadata;
    # preserve all released JavaScript/assets byte-for-byte and execute no npm.
    (runtime / 'package.json').write_text(json.dumps({
        'name': '@google/gemini-cli', 'version': version, 'type': 'module',
        'license': 'Apache-2.0', 'engines': root_metadata['engines']}, indent=2) + '\n')
    executable = package / 'usr/bin/gemini'
    executable.parent.mkdir(parents=True)
    executable.write_text('#!/bin/sh\nexec /usr/bin/node /usr/lib/rougarou-gemini/gemini.js "$@"\n')
    executable.chmod(0o755)
    settings = package / 'etc/gemini-cli/settings.json'
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({'general': {'enableAutoUpdate': False, 'enableAutoUpdateNotification': False}}, indent=2) + '\n')
    (documentation / 'README.Rougarou').write_text(
        f'Official Gemini CLI {version}: {metadata["source"]}\n'
        'This optional package uses Debian Node.js, git and ripgrep. It installs no npm, browser, daemon, or credentials.\n'
        'Update this CLI through the signed Rougarou APT channel; automatic npm updates are disabled in the system settings conffile.\n'
        'Upstream optional native PTY/keytar modules are absent from the official JS bundle. Upstream fallbacks apply.\n'
        'Cloud model use still requires operator authentication and a network connection; offline version/help were tested.\n'
        'The ISO retains the original release zip, tagged source, and conservatively selected locked npm archives under /rougarou/sources/gemini.\n'
        'Dependency notices retain every locked version of each bundled package name; this is not an exact runtime SBOM.\n'
        'The embedded browser-tool sub-bundle retains its upstream THIRD_PARTY_NOTICES and bundled-packages.json; no browser is installed.\n')
    installed_size = int(subprocess.check_output(['du', '-sk', str(package)], text=True).split()[0])
    control = package / 'DEBIAN'
    control.mkdir()
    package_version = f'{version}+rougarou{revision}'
    (control / 'control').write_text(
        f'Package: rougarou-gemini\nVersion: {package_version}\nArchitecture: all\nInstalled-Size: {installed_size}\n'
        'Maintainer: Rougarou OS maintainers <rougarou-os@users.noreply.github.com>\n'
        'Section: devel\nPriority: optional\nDepends: nodejs (>= 20), ca-certificates, git, ripgrep\n'
        'Description: Pinned official Gemini terminal CLI for Rougarou OS\n')
    (control / 'conffiles').write_text('/etc/gemini-cli/settings.json\n')
    repository.mkdir(parents=True, exist_ok=True)
    subprocess.run(['dpkg-deb', '--root-owner-group', '-Zgzip', '--build', str(package),
                    str(repository / f'rougarou-gemini_{package_version}_all.deb')], check=True)
    manifest = {**metadata, 'package': 'rougarou-gemini', 'architecture': 'all',
                'dependency_source_count': len(dependencies), 'installed_size_kib': installed_size,
                'dependency_source_policy': 'all locked versions of bundle-referenced npm package names',
                'corresponding_source_directory': '/rougarou/sources/gemini',
                'sources_sha256': {path.name: sha(path) for path in sorted(sources.iterdir())}}
    (work / 'external-packages.json').write_text(json.dumps({'gemini': manifest}, indent=2) + '\n')
    print(f'Packaged Gemini CLI {version}; retained {len(dependencies)} locked dependency archives and notices')


if __name__ == '__main__':
    main()
