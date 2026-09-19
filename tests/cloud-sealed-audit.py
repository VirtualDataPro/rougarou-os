#!/usr/bin/env python3
"""Read-only libguestfs inspection of a powered-off standalone cloud artifact."""
import argparse,json,subprocess
from pathlib import Path
p=argparse.ArgumentParser(description=__doc__);p.add_argument('image',type=Path);p.add_argument('output',type=Path);args=p.parse_args()
commands="""echo MACHINE
cat /etc/machine-id
echo RANDOM
is-file /var/lib/systemd/random-seed
echo INSTANCE
is-dir /var/lib/cloud/instance
echo ROOTSSH
is-dir /root/.ssh
echo SSHFILES
ls /etc/ssh
echo ACCOUNTS
cat /etc/passwd
echo END
"""
# guestfish --ro operates on a temporary appliance, not the host filesystem.
result=subprocess.run(['guestfish','--ro','-a',str(args.image),'-i'],input=commands,text=True,capture_output=True,timeout=180)
if result.returncode:
    raise SystemExit('Sealed cloud image audit appliance failed: '+result.stderr[-2000:])
sections={};key=None
for line in result.stdout.splitlines():
    if line in {'MACHINE','RANDOM','INSTANCE','ROOTSSH','SSHFILES','ACCOUNTS','END'}:
        key=line;sections[key]=[]
    elif key and line:sections[key].append(line)
assert sections['MACHINE']==['uninitialized'], 'Nonempty initialized machine identity'
remaining={k:sections[k] for k in ['RANDOM','INSTANCE','ROOTSSH'] if sections[k]!=['false']}
assert not remaining, f'Build state remains: {remaining}'
assert not any(x.startswith('ssh_host_') for x in sections['SSHFILES']), 'SSH host keys remain'
assert not any(1000<=int(x.split(':')[2])<60000 for x in sections['ACCOUNTS'] if x), 'Operator account baked in'
report={'passed':True,'method':'read-only libguestfs appliance; final image never booted','machine_id':'uninitialized','ssh_host_keys_absent':True,'random_seed_absent':True,'cloud_instance_cache_absent':True,'root_ssh_directory_absent':True,'operator_accounts_absent':True}
args.output.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))
