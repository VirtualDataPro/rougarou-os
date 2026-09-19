# Reviewed downloads after login

The installer offers OpenCode and Claude Code, with “download after login” in
the choice label. Their executables are not redistributed on the ISO or in
Rougarou APT. The server installs normally without Internet access, and the
operator confirms the download separately during `rougarou provider`.

You can also run `rougarou-agent-install opencode` or
`rougarou-agent-install claude` as your ordinary account. The helper downloads
one exact upstream artifact over HTTPS, checks its pinned size and SHA256,
and installs below `~/.local/share/rougarou/agents`. It creates a private
`~/.local/bin` launcher without changing existing installations or credentials.
No publisher installation script is executed. A failed download can be retried.

The Claude 2.1.267 Linux amd64 executable is pinned from Anthropic's signed
manifest, verified using fingerprint
`31DDDE24DDFAB679F42D7BD2BAA929FF1A7ECACE`. The public key, manifest and detached
signature are retained in `/usr/share/rougarou/agents`; the helper trusts the
reviewed hash delivered by signed Rougarou packaging rather than fetching a
moving version during installation. See [Anthropic setup](https://code.claude.com/docs/en/setup).

The OpenCode 1.18.31 Linux amd64 baseline archive is pinned from its official
[release](https://github.com/anomalyco/opencode/releases/tag/v1.18.31), including
the archive digest and extracted executable digest. It contains one expected
regular file, which is copied without general archive extraction. The source
and Bun runtime have separate licenses; this release uses an upstream download
rather than claiming complete redistribution notices for a repackaged binary.

The launchers disable automatic updates. Claude uses `DISABLE_UPDATES=1`;
OpenCode uses `OPENCODE_DISABLE_AUTOUPDATE=1` and rejects its explicit `upgrade`
subcommand. Agent pins change through Rougarou's testing and promotion process.
This is a default update policy, not confinement of the operator: the ordinary
account owns these files and can intentionally replace them. Authentication,
remote models and API access remain native to the selected agent.

Both exact executables were tested with `--version` and `--help` as an ordinary
user in a disposable Debian trixie container with networking disabled. These
checks do not claim authenticated provider or model execution.
