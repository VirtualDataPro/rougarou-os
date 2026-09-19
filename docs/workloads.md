# Three roles, one headless base

The same Rougarou VM can run all three roles. For isolation and maintenance, use
separate VMs for sensitive infrastructure, untrusted code workers and GPU model
serving when resources allow. This initial release bundles the general operator
and container tooling; GPU drivers and model runtimes are separate additions.

## Containers and self-hosted services

Rootless Podman, user namespaces and networking helpers are included. Keep
service data in named volumes or explicit directories with a backup plan. Use
pinned images and systemd units/Quadlets for services. Reverse proxy, TLS, DNS,
firewall rules and secret stores depend on your actual network and are not
silently provisioned by the installer. Start with outbound-only workloads,
then expose only the intended service ports.

## AI development and automation workers

Git, GitHub CLI, Python, tmux and Codex are included. Keep each job in its own
worktree or disposable container and issue narrowly scoped credentials. Set
budget/rate controls with the selected provider. Persistent CI runners should
use their own accounts and registration credentials, not the operator's login.
Do not give a pull request from an untrusted source access to host secrets.

`rougarou ai` remains an interactive operator command. The optional
[managed jobs](managed-jobs.md) service supplies a persistent per-operator queue
with explicit retries, timeouts and bounded logs. Its jobs cannot elevate
privileges. This alpha does not add an autonomous root daemon, recurring
scheduler, multi-tenant job queue or billing service. Those features need their
own access model and tests.

## Local models and GPU passthrough

First decide which GPU, model, quantization and context length you need. A
bootable guest with 4 GiB RAM does not establish adequate model capacity.
Reserve memory and disk for the model separately from the OS and containers.

1. Check the hypervisor's IOMMU support and GPU isolation, then follow the
   [Proxmox PCI passthrough guide](https://pve.proxmox.com/wiki/PCI_Passthrough).
   This changes the hypervisor and must be planned separately from guest setup.
2. Verify that the assigned device appears inside the guest using `lspci`.
3. Install the driver supported by the GPU/runtime combination. The base image
   does not guess between NVIDIA, AMD or CPU-only execution.
4. Install a reviewed, pinned runtime, such as Ollama, using its
   [Linux installation guide](https://docs.ollama.com/linux). Check its
   [hardware support matrix](https://docs.ollama.com/gpu) before buying hardware.
5. Pull a model compatible with your selected agent, start the model service on
   loopback, verify inference, then choose the Ollama option in Rougarou setup.
   Alternatively point a compatible agent at a model server on another host.
6. Confirm actual GPU utilization under load. A working endpoint can silently
   be serving inference on the CPU. Keep the API private or behind explicit
   authentication and network restrictions before exposing it beyond localhost.

Offline OS installation is separate from offline inference: model weights,
drivers and a runtime must also be staged to operate without external services.
No GPU passthrough or local inference claim is validated by this alpha's VM tests.

Bring new driver/runtime packages into Rougarou's staging process as well.
Enabling a vendor's automatic updater or APT source directly bypasses the
testing-to-stable policy for that component; make that an explicit choice.
