# Terminal installer presentation

Rougarou retains Debian Installer's cdebconf/newt interface, translated native
questions, partitioner, network configuration and recovery tools. A small
presentation patch adds the Rougarou palette and a persistent ASCII header.
There is no second terminal program competing for keyboard input.

`patch-newt.py` modifies the reviewed cdebconf 0.280 source and fails if its
expected source blocks change. Fetch that source through authenticated Debian
APT source metadata, retain its license and record the source and patch hashes.
Build with the `pkg.cdebconf.nogtk` profile, then extract `newt.so` from the
resulting `cdebconf-newt-udeb` package.

`prepare-initrd.sh ORIGINAL_INITRD NEWT_SO OUTPUT_INITRD` preserves the original
Debian gzip archive byte-for-byte and appends a deterministic gzip/newc overlay.
Linux unpacks these concatenated archives in order. This retains device nodes
and drivers without requiring root privileges or `mknod` during the build.
The overlay replaces only the newt frontend and adds two small installer
components. Their metadata, templates and scripts are registered in the same
dpkg database used by `main-menu`. The welcome has menu number 900. The access
choice has number 2450 and depends on `user-setup-udeb`, placing it after the
operator account questions and before disk partitioning. Both components are
excluded from rescue mode.

The access screen explicitly describes the default Headless operator mode:
passwordless sudo gives the named operator, their AI and their programs full
root access. Password sudo is the alternative. The component records the
operator and selected mode under `/run/rougarou`; the target hook checks both
and invokes the validated access helper after normal target configuration.
The normal preseed disables a separate root login and never confirms disk
writes automatically.

The Linux virtual console receives the operator's green/charcoal ANSI palette.
Serial terminals retain their own palette and receive portable named colors.
All early text is ASCII; no braille font support or graphical display is
required. Native translated question text remains available.

The existing late installation hook updates Debian's active progress window
at five real boundaries: media verification, repository extraction, package
installation, target configuration and completion. These stages are not a
time estimate. Command output stays in `/var/log/rougarou-install.log` on the
installed target, and failures show an installer error.

Required visual acceptance includes an 80-column VGA console, serial console,
back navigation, both access selections, an error path, and the normal native
storage confirmation. Automated install tests may preseed
`rougarou/operator-access` explicitly and mark `rougarou/welcome` seen; normal
installation does neither.

References: [Debian Installer lifecycle and main-menu](https://d-i.debian.org/doc/internals/ch02.html),
[component metadata](https://d-i.debian.org/doc/internals/ch03.html),
[startup hooks](https://d-i.debian.org/doc/internals/apb.html),
[console frontends](https://www.debian.org/releases/trixie/amd64/ch05s03.en.html),
[cdebconf source](https://salsa.debian.org/installer-team/cdebconf), and
[Debian cdebconf source archive](https://deb.debian.org/debian/pool/main/c/cdebconf/cdebconf_0.280.tar.xz).
