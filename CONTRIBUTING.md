# Contributing to Rougarou OS

Rougarou is a headless Debian stable derivative for terminal-first server work,
starting with Proxmox VMs. Contributions can include code, documentation,
reproducible test results, accessibility improvements and product feedback.

## Ask for help or propose an idea

Use [Discussions](https://github.com/VirtualDataPro/rougarou-os/discussions)
for setup questions, workflow advice and ideas that need community input.
Search existing topics first. Include the Rougarou version, installation method,
VM or hardware configuration, what you tried and the result you expected.

Use [Issues](https://github.com/VirtualDataPro/rougarou-os/issues) for reproducible
bugs and actionable feature proposals. A useful feature proposal describes the
operator's problem, a concrete workflow and how to tell whether it is solved.
Community support is best effort; there is no guaranteed response time.

Remove tokens, passwords, private keys, webhook URLs and private infrastructure
details from logs and screenshots before posting. For a suspected vulnerability,
follow [SECURITY.md](SECURITY.md).

## Make a pull request

1. Read [AGENTS.md](AGENTS.md), the [design decisions](docs/design-decisions.md)
   and the documentation for the area you want to change. Discuss changes to
   installer behavior, operator authority or update policy before implementation.
2. Fork the repository, create a branch from `main` and keep each PR focused on
   one problem. Small documentation fixes can go directly to a PR.
3. Update documentation when behavior changes. Add regression coverage when it
   demonstrates a bug or verifies a new behavior.
4. Run `scripts/check.sh` from the repository root. It runs the unit tests,
   syntax checks and publication checks. Describe additional VM testing and any
   checks you could not run in the PR.
5. Open a PR against `main`. Explain the problem, resulting behavior, related
   issue or discussion, and exact validation performed. Draft PRs are welcome
   for work that needs early feedback.

Changes to shipped package contents require a Debian package version bump.
Community-only documentation and GitHub templates do not change the package.
Preserve upstream notices and identify sources and licenses for new dependencies
or artwork. Original integration code uses the repository's MIT license.

## Test safely

Never run installation scripts on your development host. Use a disposable VM
with explicitly created disk images, without real block devices. Keep credentials,
SSH host keys, machine IDs, VM disks and generated images out of commits.
A successful ISO build alone does not establish that installation works.

Keep shell and recovery access available without an AI provider. Preserve the
headless default image, explicit operator authority choices and interactive
confirmation of destructive disk actions. Updates enter testing first; stable
promotion requires the owner's recorded test approval. See AGENTS.md for the
Debian security-update exception and managed-worker authority constraints.

## Review and community conduct

Maintainers review scope, safety, compatibility and validation before merging.
A proposal or PR does not guarantee acceptance or a release date. Explain tradeoffs
and respond to review with reproducible evidence.

Follow the [community conduct policy](CODE_OF_CONDUCT.md). Help newcomers by
sharing commands with their purpose, expected result and any destructive effects.
