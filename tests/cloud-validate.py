#!/usr/bin/env python3
"""Boot two independent NoCloud clones; never mutate the release image."""
import argparse, base64, importlib.util, json, os, re, shutil, subprocess, time
from pathlib import Path
spec=importlib.util.spec_from_file_location('cloud_image',Path(__file__).resolve().parents[1]/'scripts/cloud-image.py')
cloud=importlib.util.module_from_spec(spec); spec.loader.exec_module(cloud)

PROBE=r'''set -eu
cloud-init status --wait --format json
python3 - <<'GUEST'
import json,os,pwd,subprocess,sys
from pathlib import Path
run=lambda args: subprocess.check_output(args,text=True).strip()
u=pwd.getpwnam('cloudtest')
assert u.pw_uid==1000
assert os.stat('/').st_uid==0
hostkey=run(['cat','/etc/ssh/ssh_host_ed25519_key.pub'])
assert len(run(['cat','/etc/machine-id']))==32
policy=dict(line.split(' ',1) for line in run(['/usr/sbin/sshd','-T']).splitlines() if ' ' in line)
assert policy['passwordauthentication']=='no'
assert policy['kbdinteractiveauthentication']=='no'
assert policy['permitrootlogin']=='no'
for unit in ['docker.service','docker.socket','containerd.service']:
    result=subprocess.run(['systemctl','is-enabled',unit],capture_output=True,text=True)
    assert result.stdout.strip()=='masked',(unit,result.stdout)
    assert subprocess.run(['systemctl','is-active','--quiet',unit]).returncode!=0
for command in ['codex','gemini','herdr','podman']:
    assert subprocess.run(['which',command],stdout=subprocess.DEVNULL).returncode!=0,command
for unit in ['rougarou-worker.service','rougarou-notify.service','rougarou-docker.service']:
    assert not os.path.exists(f'/home/cloudtest/.config/systemd/user/default.target.wants/{unit}')
assert not os.path.exists('/home/cloudtest/.config/rougarou/credentials.json')
assert any(row.split(':')[:1]==['cloudtest'] and int(row.split(':')[2])>=65536 for row in open('/etc/subuid'))
active=[p for p in Path('/etc/apt/sources.list.d').iterdir() if p.suffix in {'.list','.sources'}]
assert {p.name for p in active}=={'rougarou.sources','debian-security.sources'},active
assert not any(Path('/etc/apt/preferences.d').iterdir())
assert not Path('/etc/apt/preferences').exists()
source_check='exact bootstrap+security files'
if Path('/usr/lib/rougarou/updates.py').exists():
    sys.path.insert(0,'/usr/lib/rougarou')
    from updates import source_policy
    source_policy()
    source_check='installed updater source_policy accepted'
size=os.statvfs('/'); capacity=size.f_blocks*size.f_frsize
print('ROUGAROU_PROBE_JSON='+json.dumps({'machine_id':run(['cat','/etc/machine-id']),'host_key':hostkey,'hostname':run(['hostname']),'capacity_bytes':capacity,'uid':u.pw_uid,'source_policy':source_check,'base_version':run(['dpkg-query','-W','-f=${Version}','rougarou-base']),'selection':open('/etc/rougarou/install-software').read(),'cloud_init':run(['cloud-init','--version'])},sort_keys=True))
GUEST
'''

def clone(image,root,firmware,size,headless,expected_base):
    directory=root/firmware; directory.mkdir(mode=0o700)
    key=directory/'test-key'
    cloud.run(['ssh-keygen','-q','-t','ed25519','-N','','-C','disposable-cloud-test','-f',key])
    pub=key.with_suffix('.pub').read_text().strip()
    user={'name':'cloudtest','shell':'/bin/bash','lock_passwd':True,'ssh_authorized_keys':[pub],
          'groups':['users'],'sudo':['ALL=(ALL) NOPASSWD:ALL'] if headless else None}
    seed=cloud.make_seed(directory/'seed','rougarou-test-'+firmware,{'hostname':'rougarou-'+firmware,'users':[user],
              'disable_root':True,'ssh_pwauth':False,'package_update':False,'package_upgrade':False},
              {'version':2,'ethernets':{'lan':{'match':{'name':'en*'},'dhcp4':True}}})
    disk=directory/'clone.qcow2'
    cloud.run(['qemu-img','create','-f','qcow2','-F','qcow2','-b',image,disk,size])
    args=cloud.qemu_command(disk,directory,seed=seed,firmware=firmware,network=True)
    with (directory/'qemu.log').open('w') as log:
        guest=subprocess.Popen(args,stdout=log,stderr=log)
        try:
            cloud.wait_ready(guest,directory,timeout=300)
            raw=cloud.guest_exec(directory/'qga.sock',PROBE,240)
            (directory/'probe.log').write_text(raw)
            lines=[s for s in raw.splitlines() if s.startswith('ROUGAROU_PROBE_JSON=')]
            assert len(lines)==1
            result=json.loads(lines[0].split('=',1)[1])
            assert result['base_version']==expected_base,result
            minimum=(int(size[:-1])-2)*1024**3
            assert result['capacity_bytes']>minimum,result
            assert result['hostname']=='rougarou-'+firmware,result
            # Trust the exact host key read via this clone's private QGA channel.
            known=directory/'known_hosts'; known.write_text('[127.0.0.1]:2222 '+result['host_key']+'\n')
            command=['ssh','-F','/dev/null','-o','BatchMode=yes','-o','IdentitiesOnly=yes','-o','StrictHostKeyChecking=yes',
                     '-o','UserKnownHostsFile='+str(known),'-i',key,'-p','2222','cloudtest@127.0.0.1']
            login=cloud.run(command+['id -u; sudo -n -k -- /usr/bin/true >/dev/null 2>&1; printf "sudo-status=%s\\n" "$?"'],capture_output=True,text=True)
            assert login.stdout.splitlines()==['1000','sudo-status='+('0' if headless else '1')],login.stdout
            result['ssh_key_login']=True; result['explicit_sudo_authority']='headless' if headless else 'unprivileged'
            container_test=(Path(__file__).parent/'vm/rootless-containers.py').read_text()
            proof=cloud.run(command+['python3 - --docker-only'],input=container_test,capture_output=True,text=True,timeout=240)
            result['rootless_container']=json.loads(proof.stdout)
            cloud.run(command+['rougarou-docker stop'],capture_output=True,text=True,timeout=120)
            result.pop('host_key'); result['ssh_host_key_fingerprint']=cloud.guest_exec(directory/'qga.sock',"ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub | awk '{print $2}'").strip()
            cloud.shutdown(guest,directory)
            result['shutdown']=True
            cloud.write_json(directory/'results.json',result)
            return result
        finally:
            if guest.poll() is None: guest.terminate();guest.wait(timeout=30)
            # Test secrets never enter sanitized acceptance JSON or cloud image.
            key.unlink(missing_ok=True)

p=argparse.ArgumentParser();p.add_argument('image',type=Path);p.add_argument('manifest',type=Path);p.add_argument('output',type=Path);args=p.parse_args()
image=args.image.resolve();output=args.output.resolve();output.mkdir(parents=True,exist_ok=True)
assert not any(output.iterdir()),'Fresh evidence directory required'
before=cloud.digest(image)
manifest=json.loads(args.manifest.read_text())
assert before==manifest['artifact']['sha256'],'Artifact does not match reviewed manifest'
expected=manifest['base_package']['version']
results=[clone(image,output,'bios','12G',True,expected),clone(image,output,'uefi','16G',False,expected)]
assert results[0]['machine_id']!=results[1]['machine_id']
assert results[0]['ssh_host_key_fingerprint']!=results[1]['ssh_host_key_fingerprint']
assert cloud.digest(image)==before
cloud.write_json(output/'results.json',{'passed':True,'image_sha256':before,'clones':results,'image_unchanged':True})
print(json.dumps({'passed':True,'image_sha256':before,'clones':2}))
