# Rootless Docker and Podman

The installer preselects **Rootless Docker**. You can choose No Docker or System
Docker instead; Podman is a separate choice and defaults to No. Each selected
engine has its dependencies available offline. Rootless Docker starts only when
you run setup below. Existing installations keep their tools and settings.

System Docker enables the root-owned service. Manage it with `sudo docker`;
Rougarou does not add the operator to the privileged docker group. Podman is
independent and uses its native commands.

With Rootless Docker selected, log in as your ordinary operator and run:

```sh
rougarou-docker setup
docker info
rougarou-docker status --json
```

Setup starts and enables `rougarou-docker.service` in your **user** systemd
manager. For a fresh Docker CLI configuration it selects `rougarou-rootless`,
so ordinary `docker` commands use that daemon. An existing configuration or
`DOCKER_HOST`/`DOCKER_CONTEXT` environment is preserved. Use the context
explicitly, or deliberately change your saved default:

```sh
docker --context rougarou-rootless info
rougarou-docker setup --use-context
```

Environment variables can still override a saved default. Setup refuses to
replace an existing `rougarou-rootless` context pointing to another endpoint.
It does not edit shell startup files or contact a registry. Images must be
pulled with working networking or imported from a trusted local archive.
Podman remains available independently through ordinary `podman` commands.

To add rootless Docker later to a server with no existing Docker daemon:

```sh
sudo systemctl mask docker.service docker.socket containerd.service
sudo apt install docker.io docker-cli rootlesskit uidmap slirp4netns fuse-overlayfs dbus-user-session
rougarou-docker setup
```

Review existing workloads before changing a server that already runs Docker.
To add Podman later, use `sudo apt install podman uidmap slirp4netns fuse-overlayfs`.
These package downloads use the configured signed Rougarou channel or local baseline.

## What setup changes

| Item | Location |
| --- | --- |
| Packaged user service | `/usr/lib/systemd/user/rougarou-docker.service` |
| Docker socket | `/run/user/UID/rougarou-docker/docker.sock` |
| Docker images, volumes and containers | `~/.local/share/rougarou/docker` |
| Dedicated daemon configuration | `~/.config/rougarou/docker/daemon.json` |
| Named client context | `rougarou-rootless` in Docker's client configuration |

The separate paths avoid reusing an existing Docker or Podman store. Setup
creates an empty daemon configuration only when absent and preserves existing
content. The service owns its socket/data/runtime paths; conflicting `hosts`,
`data-root`, `exec-root` or `pidfile` configuration entries cause a clear error.
Other daemon options can be configured in that file. Restart the user service
after intentional configuration changes.

To stop Docker without deleting its data or context:

```sh
rougarou-docker stop
```

Setup does not change lingering. With lingering disabled, the user manager
normally follows login sessions. If you want this user's services to run at
boot and after logout, explicitly enable lingering using your administrator's
normal policy. An already lingering account retains that setting.

## Privileges and limits

Docker's daemon and containers run in an unprivileged user's namespace. Setup
requires the signed Debian `uidmap` tools and at least 65,536 subordinate IDs
allocated to the account in each of `/etc/subuid` and `/etc/subgid`. It refuses
root execution and never allocates ranges, changes sysctls or adds Docker group
membership. See [Docker's rootless prerequisites](https://docs.docker.com/engine/security/rootless/).

Fresh installs with **Rootless Docker selected** mask the system Docker
service/socket and the system containerd service before package installation.
The System Docker choice explicitly enables those services instead. Debian may create an unused
`docker` group as part of its package; Rougarou adds no operator to it. The
rootless daemon starts its own containerd. Existing servers need a separate
review before disabling any pre-existing rootful runtime.

The user service needs `newuidmap` and `newgidmap` to establish subordinate ID
mappings, so it cannot use the managed worker's `NoNewPrivileges` restriction.
The Docker socket gives control of the owner's containers and files accessible
to that account. Treat it as owner authority; do not expose it over TCP or
mount it into untrusted workloads.

Published ports should use ordinary unprivileged host ports. The supplied
RootlessKit networking blocks access to host loopback by default. Cgroup
resource limits depend on cgroup v2, systemd and delegated controllers; inspect
`docker info` before relying on them. Rootless networking and storage have
upstream limitations; see [Docker's guidance](https://docs.docker.com/engine/security/rootless/tips/)
and [known limitations](https://docs.docker.com/engine/security/rootless/troubleshoot/#known-limitations).

## Provenance and validation

The runtime uses Debian's signed `docker.io`, `docker-cli`, `rootlesskit`,
`uidmap`, `slirp4netns`, `fuse-overlayfs` and `dbus-user-session` packages.
The rootless launcher is supplied by
[Debian's docker.io package](https://packages.debian.org/trixie/amd64/docker.io/filelist)
at `/usr/share/docker.io/contrib/dockerd-rootless.sh`; no installation-time
download script is used.

The source tests cover setup, context and data preservation. The disposable
`tests/vm/rootless-containers.py` check imports a tiny filesystem from the
installed shell and libraries, runs an offline container in each selected
runtime, verifies rootless status and ordinary-user ownership of its output,
and removes only its own uniquely named test images. It records the actual
storage and OCI runtime rather than assuming them. Rootless-profile checks
also verify that system Docker/containerd remain masked and inactive.

Run that check against the exact public image; [validation](validation.md)
records release acceptance. Existing manually installed engines, contexts and
data must be inspected and preserved during an upgrade; do not start a second
rootless daemon automatically.
