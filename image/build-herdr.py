#!/usr/bin/env python3
"""Package the pinned official Herdr binary and authenticated dependency notices."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tarfile
import threading
import time
import tomllib
import urllib.request


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


FETCH_LOCKS = {}
FETCH_GUARD = threading.Lock()


def fetch(url, target, expected):
    with FETCH_GUARD:
        lock = FETCH_LOCKS.setdefault(str(target), threading.Lock())
    with lock:
        return fetch_locked(url, target, expected)


def fetch_locked(url, target, expected):
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        temporary = target.with_suffix(target.suffix + '.part')
        request = urllib.request.Request(url, headers={'User-Agent': 'RougarouOS-builder'})
        for attempt in range(4):
            try:
                with urllib.request.urlopen(request, timeout=45) as response, temporary.open('wb') as stream:
                    shutil.copyfileobj(response, stream)
                break
            except OSError:
                temporary.unlink(missing_ok=True)
                if attempt == 3:
                    raise
                time.sleep(1 + attempt * 2)
        temporary.replace(target)
    if sha(target) != expected:
        raise RuntimeError(f'Pinned artifact checksum mismatch: {target.name}')
    return target


def notice(name):
    return PurePosixPath(name).name.upper().startswith(('LICENSE', 'COPYING', 'NOTICE', 'COPYRIGHT'))


def copy_notices(archive, destination, prefix):
    copied = []
    for member in archive:
        relative = PurePosixPath(member.name).relative_to(prefix)
        if member.isfile() and notice(member.name):
            if relative.is_absolute() or '..' in relative.parts:
                raise RuntimeError('Unsafe archive notice path')
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.extractfile(member).read())
            copied.append(str(relative))
    return sorted(copied)


def main():
    work, repository, cache = map(Path, sys.argv[1:])
    version = os.environ['HERDR_VERSION']
    revision = os.environ['ROUGAROU_PACKAGE_REVISION']
    asset = os.environ['HERDR_ASSET']
    base_url = 'https://github.com/herdrdev/herdr'
    source_url = f'https://codeload.github.com/herdrdev/herdr/tar.gz/refs/tags/v{version}'
    binary = fetch(f'{base_url}/releases/download/v{version}/{asset}',
                   cache / f'{version}-{asset}', os.environ['HERDR_SHA256'])
    source = fetch(source_url, cache / f'{version}-source.tar.gz', os.environ['HERDR_SOURCE_SHA256'])
    package = work / 'herdr-package'
    documentation = package / 'usr/share/doc/rougarou-herdr'
    documentation.mkdir(parents=True)
    sources = work / 'sources/herdr'
    sources.mkdir(parents=True)
    shutil.copyfile(source, sources / f'herdr-{version}.tar.gz')
    prefix = f'herdr-{version}'
    with tarfile.open(source) as archive:
        license_bytes = archive.extractfile(f'{prefix}/LICENSE').read()
        assert b'Apache License' in license_bytes, 'Review changed upstream license'
        (documentation / 'copyright').write_bytes(license_bytes)
        lock = tomllib.loads(archive.extractfile(f'{prefix}/Cargo.lock').read().decode())
        copy_notices(archive, documentation / 'upstream-notices', prefix)

    # Registry checksums are pinned transitively by the source archive's Cargo.lock.
    # Include all locked targets rather than guessing which static dependencies
    # an upstream cross-compiled binary retained. No dependency code is executed.
    dependencies = [item for item in lock['package'] if item.get('source', '').startswith('registry+')]
    unexpected = [item for item in lock['package'] if item.get('source') and not item['source'].startswith('registry+')]
    if unexpected:
        raise RuntimeError('New non-registry source dependency requires provenance review')
    overrides = json.loads((Path(__file__).parent / 'herdr-license-overrides.json').read_text())

    def dependency(item):
        name, number = item['name'], item['version']
        filename = f'{name}-{number}.crate'
        url = f'https://static.crates.io/crates/{name}/{filename}'
        path = fetch(url, cache / 'crates' / filename, item['checksum'])
        shutil.copyfile(path, sources / filename)
        with tarfile.open(path) as archive:
            metadata = tomllib.loads(archive.extractfile(f'{name}-{number}/Cargo.toml').read().decode())['package']
            files = copy_notices(archive, documentation / 'dependency-notices' / f'{name}-{number}', f'{name}-{number}')
        supplemental = overrides.get(f'{name}-{number}', [])
        for notice_item in supplemental:
            if Path(notice_item['name']).name != notice_item['name']:
                raise RuntimeError('Unsafe supplemental license path')
            path = fetch(notice_item['url'], cache / 'license-overrides' / notice_item['sha256'], notice_item['sha256'])
            target = documentation / 'dependency-notices' / f'{name}-{number}' / notice_item['name']
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
            shutil.copyfile(path, sources / f"license-{notice_item['sha256']}.txt")
        if not files and not supplemental:
            raise RuntimeError(f'No license/notice files found for {filename}; manual review needed')
        return {'name': name, 'version': number, 'sha256': item['checksum'], 'url': url,
                'license': metadata.get('license'), 'notices': files, 'supplemental_notices': supplemental}

    with ThreadPoolExecutor(max_workers=12) as pool:
        dependency_manifest = list(pool.map(dependency, dependencies))
    dependency_manifest.sort(key=lambda item: (item['name'], item['version']))
    (documentation / 'dependencies.json').write_text(json.dumps(dependency_manifest, indent=2) + '\n')
    executable = package / 'usr/bin/herdr'
    executable.parent.mkdir(parents=True)
    shutil.copyfile(binary, executable)
    executable.chmod(0o755)
    control = package / 'DEBIAN'
    control.mkdir()
    package_version = f'{version}+rougarou{revision}'
    (control / 'control').write_text(
        f'Package: rougarou-herdr\nVersion: {package_version}\nArchitecture: amd64\n'
        'Maintainer: Rougarou OS maintainers <rougarou-os@users.noreply.github.com>\n'
        'Section: devel\nPriority: optional\nDepends: ca-certificates, curl, git, openssh-client\n'
        'Description: Pinned Herdr terminal workspace manager for AI coding agents\n')
    (documentation / 'README.Rougarou').write_text(
        f'Upstream: {base_url}/tree/v{version}\nLicense: Apache-2.0\n'
        'Update this distribution-owned binary through the signed Rougarou APT channel.\n'
        'No Herdr service is enabled by this package. Starting Herdr is an operator action.\n'
        'The installation ISO includes the exact upstream and locked dependency sources\n'
        'under /rougarou/sources/herdr, including all platform targets in Cargo.lock.\n')
    installed_size = int(subprocess.check_output(['du', '-sk', '--exclude=DEBIAN', str(package)]).split()[0])
    with (control / 'control').open('a') as stream:
        stream.write(f'Installed-Size: {installed_size}\n')
    filename = f'rougarou-herdr_{package_version}_amd64.deb'
    subprocess.run(['dpkg-deb', '--root-owner-group', '-Zgzip', '--build', str(package), str(repository / filename)], check=True)
    manifest = {'package': 'rougarou-herdr', 'version': version, 'asset': asset,
                'sha256': sha(binary), 'source_sha256': sha(source), 'source': f'{base_url}/tree/v{version}',
                'source_archive': source_url, 'license': 'Apache-2.0',
                'dependency_source_count': len(dependency_manifest),
                'corresponding_source_directory': '/rougarou/sources/herdr',
                'sources_sha256': {path.name: sha(path) for path in sorted(sources.iterdir())}}
    (work / 'external-packages.json').write_text(json.dumps({'herdr': manifest}, indent=2) + '\n')
    print(f'Packaged Herdr {version}; retained {len(dependency_manifest)} locked dependency sources and notices')


if __name__ == '__main__':
    main()
