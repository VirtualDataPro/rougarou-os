#!/usr/bin/python3 -I
"""Read-only installed-menu acceptance for a fresh disposable Rougarou VM."""
import hashlib
import json
from pathlib import Path
import re
import subprocess


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


configuration = Path('/boot/grub/grub.cfg')
text = configuration.read_text()
subprocess.run(['/usr/bin/grub-script-check', str(configuration)], check=True)
require(re.search(r"^menuentry 'RougarouOS GNU/Linux' ", text, re.M), 'Default boot entry lacks RougarouOS branding')
require("submenu 'Advanced options for RougarouOS GNU/Linux'" in text, 'Native advanced menu is missing')
require(re.search(r"menuentry 'RougarouOS GNU/Linux, with Linux [^']+ \(recovery mode\)'", text), 'Native recovery entry is missing')
require('terminal_output console' in text, 'Fresh installed menu is not on the plain console')
require('set timeout_style=menu' in text and 'set timeout=5' in text, 'Fresh boot menu is not visible for five seconds')
normal = re.findall(r'^set menu_color_normal=(.+)$', text, re.M)
highlight = re.findall(r'^set menu_color_highlight=(.+)$', text, re.M)
require(normal and normal[-1] == 'light-gray/black', 'Final normal menu colors differ')
require(highlight and highlight[-1] == 'black/green', 'Final selected menu colors differ')
conffiles = subprocess.check_output(['dpkg-query', '-W', '-f=${Conffiles}', 'grub-common'], text=True)
entry = re.search(r'^ /etc/grub\.d/10_linux ([0-9a-f]{32})(?: |$)', conffiles, re.M)
require(entry is not None, 'Debian kernel generator package checksum is missing')
native = Path('/etc/grub.d/10_linux').read_bytes()
require(hashlib.md5(native, usedforsecurity=False).hexdigest() == entry[1], 'Debian kernel/recovery generator was modified')
print(json.dumps({'result': 'PASS', 'title': 'RougarouOS GNU/Linux', 'terminal': 'console',
                  'menu_timeout_seconds': 5, 'palette': 'light-gray/black; black/green',
                  'native_kernel_generator_unchanged': True, 'recovery_entry_present': True,
                  'grub_script_check': 'PASS', 'configuration_sha256': hashlib.sha256(configuration.read_bytes()).hexdigest()}))
