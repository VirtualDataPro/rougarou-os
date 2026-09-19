#!/usr/bin/env python3
"""Real sudo acceptance checks, exclusively inside a disposable root container.

Mount source at /source (read-only) and install Debian sudo before invoking.
This is deliberately outside unittest discovery: it creates container accounts.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess


def run(argv, success=True):
    result = subprocess.run(argv, text=True, capture_output=True)
    assert (result.returncode == 0) == success, (argv, result.returncode, result.stdout, result.stderr)
    return result.stdout.strip()


def main():
    assert Path("/.dockerenv").is_file() and os.geteuid() == 0, "Disposable root container only"
    helper = "/usr/lib/rougarou-system/operator-access"
    Path(helper).parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2("/source/rootfs" + helper, helper)
    shutil.copytree("/source/rootfs/usr/lib/rougarou", "/usr/lib/rougarou", dirs_exist_ok=True)
    shutil.copy2("/source/rootfs/usr/local/bin/rougarou", "/usr/local/bin/rougarou")
    for name, uid in (("owner", "1000"), ("other", "1001")):
        run(["useradd", "--create-home", "--uid", uid, "--groups", "sudo", name])
    owner = ["runuser", "-u", "owner", "--"]
    other = ["runuser", "-u", "other", "--"]
    run(owner + ["sudo", "-n", "-k", "/usr/bin/id", "-u"], success=False)
    run([helper, "set", "owner", "headless"], success=False)
    run([helper, "set", "owner", "headless", "--acknowledge-root-access"])
    assert run(owner + ["sudo", "-n", "-k", "/usr/bin/id", "-u"]) == "0"
    run(other + ["sudo", "-n", "-k", "/usr/bin/id", "-u"], success=False)
    assert run(owner + ["id", "-u"]) == "1000"
    run(owner + ["setpriv", "--no-new-privs", "sudo", "-n", "-k", "/usr/bin/id", "-u"], success=False)
    Path("/etc/sudoers.d").chmod(0o750)
    status = json.loads(run(owner + ["rougarou", "access", "--json"]))
    assert status["mode"] == "headless" and status["passwordless_probe"]
    config = Path("/home/owner/.config/rougarou")
    config.mkdir(parents=True, mode=0o700)
    for path in (config.parent, config):
        os.chown(path, 1000, 1000)
    path = config / "config.json"
    path.write_text(json.dumps({"schema_version": 1, "provider": {"kind": "command", "argv": ["/usr/bin/id", "-u"]}}))
    path.chmod(0o600)
    os.chown(path, 1000, 1000)
    assert run(owner + ["rougarou", "ai"]) == "1000"
    run(owner + ["rougarou", "access", "password"])
    run(owner + ["sudo", "-n", "-k", "/usr/bin/id", "-u"], success=False)
    custom = Path("/etc/sudoers.d/99-local-policy")
    original = "owner ALL=(ALL:ALL) NOPASSWD: /usr/bin/true\n"
    custom.write_text(original)
    custom.chmod(0o440)
    run([helper, "set", "owner", "headless", "--acknowledge-root-access"])
    run([helper, "set", "owner", "password"])
    assert custom.read_text() == original
    status = json.loads(run(owner + ["rougarou", "access", "--json"]))
    assert status["mode"] == "password" and status["passwordless_probe"]
    assert "separate sudo policy" in run(owner + ["rougarou", "access"])
    # The live operator had explicitly edited Debian's main sudoers file.
    # Managing Rougarou's separate mode must preserve that administrator policy.
    sudoers = Path("/etc/sudoers")
    group_policy = sudoers.read_text() + "\n%sudo ALL=(ALL:ALL) NOPASSWD: ALL\n"
    sudoers.write_text(group_policy)
    run([helper, "set", "owner", "password"])
    assert sudoers.read_text() == group_policy
    assert run(other + ["sudo", "-n", "-k", "/usr/bin/id", "-u"]) == "0"
    assert "separate sudo policy" in run(owner + ["rougarou", "access"])
    run(["visudo", "-c"])
    print(json.dumps({"headless_cold_cache_sudo": "passed", "other_operator_no_grant": "passed",
                      "ai_process_uid": 1000, "no_new_privileges_blocks_sudo": "passed",
                      "password_mode_revocation": "passed", "custom_policy_preserved": "passed",
                      "cli_reports_external_policy": "passed", "manual_main_sudoers_group_preserved": "passed",
                      "full_visudo_check": "passed"}, indent=2))


if __name__ == "__main__":
    main()
