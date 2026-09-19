# Measuring installation performance

Installation duration depends on the selected software, virtual disk and
storage backend, CPU scheduling, memory, and competing host load. The ISO holds
all supported offline profiles; the installer installs only the core and the
operator's selected profiles. Download size is not the same as installed size.

Use the disposable VM harness in [tests/vm](../tests/vm/) to capture timestamps
for partitioning, base installation, the Rougarou package hook and completion.
Compare the same firmware, CPU, RAM, storage, package selection and host load.
Report the exact artifact hash and whether measurements ran concurrently.

A useful comparison includes the default Rootless Docker profile, a minimal
No Docker/no-agent profile, and a fuller profile with an agent and workspace.
Check successful installed-disk boot and runtime behavior along with elapsed
time; speed is not a substitute for a complete install.

Do not infer the cause of a slow install from a later storage repair or from
one timing sample. Collect guest and host I/O/CPU evidence during the delay.
Avoid changing a working server's CPU, disk or storage configuration merely
to make a benchmark comparable. Public release timings belong in the
[validation record](validation.md) after the exact release is tested.
