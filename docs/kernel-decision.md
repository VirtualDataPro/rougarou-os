# Kernel decision

Keep Debian's supported `linux-image-amd64` kernel for the current release.
No custom kernel is required by the integration. Consult the final release
package manifest for the exact kernel version.

There is no measured evidence that a custom kernel would make Codex faster.
For a remotely hosted model, the guest runs the CLI and its local tools; model
inference runs at the provider. OpenAI identifies model processing, generated
tokens and request round trips as major latency factors. A guest kernel change
cannot accelerate the provider's token generation. This is an inference from
that architecture, not a benchmark result. Local file searches, process startup,
builds and tests can still benefit from improvements to the VM or kernel when
profiling identifies a bottleneck. See the [OpenAI latency guide](https://developers.openai.com/api/docs/guides/latency-optimization)
and [Codex CLI documentation](https://learn.chatgpt.com/docs/codex/cli).

Synthetic local inference tests are not measurements of Codex task latency
or comparisons between kernels.

Debian's general kernel provides broad hardware support. Some features are
built in; others are loadable modules. A module file on disk is not evidence
that its code is running: the kernel requests modules as needed. Removing
unused modules can reduce package size without producing a corresponding
runtime speedup. Debian notes that a narrower kernel can reduce memory use,
but also explicitly transfers responsibility for custom-kernel security updates
to its maintainer. See [Linux module loading](https://docs.kernel.org/admin-guide/sysctl/kernel.html#modprobe)
and [Debian kernel compilation guidance](https://www.debian.org/doc/manuals/debian-handbook/sect.kernel-compilation.en.html).

A future VM footprint experiment should first compare Debian's existing
`linux-image-cloud-amd64` flavour. Both flavours use the same Debian kernel
source. At version `6.12.107-1`, Debian lists installed package sizes of
108,326 KiB for the [general kernel](https://packages.debian.org/trixie/linux-image-6.12.107%2Bdeb13-amd64)
and 34,672 KiB for the [cloud kernel](https://packages.debian.org/trixie/linux-image-6.12.107%2Bdeb13-cloud-amd64).
The roughly 72 MiB difference is package disk footprint, not a measured RAM or
Codex latency improvement. Boot, VirtIO storage/networking, encrypted LVM,
rootless containers and any required device passthrough must pass before a
cloud flavour replaces the general kernel. Keep a working fallback kernel.
See the [Debian kernel package handbook](https://kernel-team.pages.debian.net/kernel-handbook/ch-packaging.html).

Performance comparisons must hold VM CPU, RAM, storage and host load constant.
Host CPU passthrough and a migration-compatible virtual CPU can behave
differently; choosing one involves a migration compatibility tradeoff. See
[QEMU CPU guidance](https://www.qemu.org/docs/master/system/i386/cpu.html).

Before revisiting the kernel, collect a baseline in disposable snapshots with
the same CPU, RAM, storage, package set and controlled host load:

1. Record kernel/config versions, idle memory, loaded modules, CPU pressure,
   swap activity and storage wait time.
2. Measure CLI startup separately from local searches, Git operations and a
   representative project's tests. Use fixed inputs, separate cold and warm
   runs, and at least 30 warm samples; retain raw timings and median/p95.
3. For end-to-end agent tasks, hold provider, model, reasoning settings and
   task inputs constant. Separate provider time from local tool time and
   assess correctness alongside speed. Do not substitute `codex --version`
   timing or synthetic inference for this measurement.
4. Compare a supported cloud flavour before maintaining a custom build. Only
   pursue a custom kernel when profiling identifies kernel work and repeated
   tests show a meaningful benefit beyond run-to-run variation.

A custom kernel would also require tracked security advisories, prompt rebuilds,
signed packages, matching headers/modules, a recovery kernel and the complete
installation/runtime regression suite for every update. The direct Debian
security source does not maintain a separately named Rougarou kernel. That
ongoing cost is not justified by a speedup that has not been demonstrated.
