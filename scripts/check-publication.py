#!/usr/bin/env python3
"""Reject accidental infrastructure details and private key blocks in source.

This complements a secret scanner and manual release/image review. Findings
contain only filenames, line numbers and categories, never matched values.
"""
import argparse
import ipaddress
import json
from pathlib import Path
import re
import subprocess

PRIVATE_NETWORKS = tuple(ipaddress.ip_network(value) for value in (
    (0x0A000000, 8), (0xAC100000, 12), (0xC0A80000, 16)))
DOCUMENTED_HOME_USERS = {'operator', 'user', 'testuser', 'alice', 'bob',
                         'rougarou', 'cloudtest', 'runner', 'tester', 'owner',
                         'other', 'updateowner', 'upgradeowner'}
KEY_BLOCK = re.compile(r'-----BEGIN (?:[A-Z0-9]+ )?PRIVATE KEY-----')
IPV4 = re.compile(r'(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])')
HOME = re.compile(r'/(?:home|Users)/([A-Za-z0-9_.-]+)(?:/|\b)')
MAC = re.compile(r'(?<![\w:])(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}(?![\w:])')


def inspect_text(name, text):
    findings = []
    for number, line in enumerate(text.splitlines(), 1):
        categories = set()
        if KEY_BLOCK.search(line):
            categories.add('private-key-block')
        for match in HOME.finditer(line):
            if match[1] not in DOCUMENTED_HOME_USERS:
                categories.add('personal-home-path')
        for match in MAC.finditer(line):
            if not match[0].lower().startswith('02:00:00:00:00:'):
                categories.add('hardware-address-literal')
        for match in IPV4.finditer(line):
            try:
                address = ipaddress.ip_address(match[0])
            except ValueError:
                continue
            if any(address in network for network in PRIVATE_NETWORKS):
                categories.add('private-network-address')
        findings.extend({'file': name, 'line': number, 'category': category}
                        for category in sorted(categories))
    return findings


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    root = args.root.resolve()
    names = subprocess.check_output(
        ['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'],
        cwd=root).decode().split('\0')
    findings = []
    count = 0
    for name in sorted(set(names) - {''}):
        path = root / name
        if path.is_symlink():
            continue
        data = path.read_bytes()
        if b'\0' in data:
            continue  # Binary assets require a separate artifact/image audit.
        count += 1
        findings.extend(inspect_text(name, data.decode('utf-8', errors='replace')))
    print(json.dumps({'text_files_checked': count, 'findings': findings,
                      'passed': not findings}, indent=2))
    return bool(findings)


if __name__ == '__main__':
    raise SystemExit(main())
