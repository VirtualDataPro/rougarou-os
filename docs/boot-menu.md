# Installed boot menu

The default is a plain text GRUB menu with **RougarouOS GNU/Linux**,
a five-second visible countdown, light text on black, and a green selection.
The console palette approximates the operator's green theme with GRUB's portable
text colors. No desktop, font download, wallpaper, or custom kernel is required.

Debian's unchanged kernel generator supplies the main entry, advanced kernel
choices, and recovery entry. Its native naming adds `GNU/Linux` to the chosen
`RougarouOS` distributor. Kernel versions stay visible inside the advanced menu.
See the [Debian 2.12 kernel menu generator](https://sources.debian.org/src/grub2/2.12-9%2Bdeb13u2/util/grub.d/10_linux.in/).

The fresh installer explicitly runs:

```sh
/usr/lib/rougarou-system/configure-grub --install-defaults
```

This creates `/etc/default/grub.d/50-rougarou.cfg` only when absent, then runs
`update-grub`. Existing installations opt in with the same command using `sudo`.
Package upgrades do not invoke it or replace the generated local defaults file.
The helper does not run `grub-install`, edit firmware boot entries, or reboot.

Existing timeout, terminal, serial, and kernel-command-line settings remain in
effect. Only the distributor is explicitly branded when the defaults are applied.
An existing Rougarou defaults file is preserved byte for byte. Later local files
such as `/etc/default/grub.d/90-local.cfg` can override these settings. Set
`ROUGAROU_GRUB_PALETTE=no` there to disable the palette. An explicitly configured
`GRUB_THEME` or `GRUB_BACKGROUND` also suppresses it. Use the [GRUB configuration
reference](https://www.gnu.org/software/grub/manual/grub/html_node/Simple-configuration.html)
for terminal and serial options.

After editing boot settings, regenerate the installed menu explicitly:

```sh
sudo /usr/lib/rougarou-system/configure-grub --refresh
```

The shipped `/etc/grub.d/06_rougarou_theme` emits colors only when enabled by the
defaults file. It does not produce boot entries. Debian continues to generate
root-device, initramfs, encryption, and recovery commands from the target system.
Retaining that generator does not constitute a new encrypted-install test.

## Validation procedure

The source tests cover initial creation, idempotence, local-edit preservation,
unsafe files, console defaults, serial/kernel/recovery settings and custom
palette behavior. In a disposable installed guest, `tests/vm/grub-check.py`
checks native GRUB syntax, branding, timeout, colors and recovery entries.
Review actual BIOS and UEFI boot frames from the final artifact; see
[release validation](validation.md). Recovery boot needs a separate check.

A serial installation can intentionally produce a serial GRUB menu. Rougarou
preserves that configuration; test the ordinary VGA path separately when
verifying the default console appearance.
