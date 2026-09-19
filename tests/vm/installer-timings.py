#!/usr/bin/env python3
"""Emit only timing and aggregate package facts from a disposable guest."""
import json
from pathlib import Path
import re
import subprocess


def collect(root=Path('/')):
    log = (root / 'var/log/rougarou-install.log').read_text(errors='replace')
    starts = re.findall(r'^ROUGAROU_STAGE stage=([a-z]+) started=(\d+) utc=([0-9TZ:-]+)$', log, re.M)
    durations = re.findall(r'^ROUGAROU_TIMING stage=([a-z]+) seconds=(\d+)$', log, re.M)
    expected = ['verify', 'unpack', 'packages', 'configure', 'finish']
    assert [row[0] for row in starts] == expected, 'Missing or repeated installer stage starts'
    assert [row[0] for row in durations] == expected, 'Missing or repeated installer stage durations'
    stages = [{'stage': name, 'started_monotonic_seconds': int(started), 'utc': utc,
               'elapsed_seconds': int(duration[1])} for (name, started, utc), duration in zip(starts, durations)]
    assert all(a['started_monotonic_seconds'] <= b['started_monotonic_seconds'] for a, b in zip(stages, stages[1:]))
    events = []
    for line in (root / 'var/log/installer/syslog').read_text(errors='replace').splitlines():
        match = re.search(r"main-menu\[\d+\]: INFO: Menu item '([a-z0-9-]+)' selected", line)
        if match:
            events.append({'time': line[:15], 'menu': match[1]})
    transactions = [line for line in (root / 'var/log/apt/history.log').read_text(errors='replace').splitlines()
                    if line.startswith(('Start-Date:', 'End-Date:'))]
    repository = root / 'var/cache/rougarou/repo'
    return {'stages': stages, 'rougarou_stage_seconds': sum(stage['elapsed_seconds'] for stage in stages),
            'installer_menu_events': events, 'apt_transaction_times': transactions,
            'retained_repository_bytes': sum(path.stat().st_size for path in repository.rglob('*') if path.is_file()),
            'apt_archive_bytes': sum(path.stat().st_size for path in (root / 'var/cache/apt/archives').glob('*.deb'))}


if __name__ == '__main__':
    result = collect()
    packages = subprocess.check_output(['dpkg-query', '-W', '-f=${Status}\t${Installed-Size}\n'], text=True)
    installed = [line.split('\t') for line in packages.splitlines() if line.startswith('install ok installed\t')]
    result['installed_package_count'] = len(installed)
    result['installed_package_size_kib'] = sum(int(row[1] or 0) for row in installed)
    print('INSTALL_TIMINGS=' + json.dumps(result, sort_keys=True))
