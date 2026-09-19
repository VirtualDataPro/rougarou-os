#!/usr/bin/env python3
"""Build a standalone cloud disk from a pinned pristine upstream image, offline."""
from __future__ import annotations
import argparse, base64, hashlib, json, os, re, runpy, shutil, socket, subprocess, tarfile, time
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]

def run(args, **kwargs):
    return subprocess.run([str(x) for x in args], check=True, **kwargs)

def digest(path, name='sha256'):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, name).hexdigest()

def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True)+'\n')

def public_archive_member(member):
    """Generated evidence archives must not retain workstation identity."""
    if not member.isfile():
        raise ValueError('Expected a regular provenance evidence file')
    member.uid = member.gid = 0
    member.uname = member.gname = 'root'
    member.mode = 0o644
    member.mtime = 0
    member.pax_headers = {}
    return member

def zero_filesystem_free_space(image, *, mountpoints=('/boot/efi',), appliance_mounts=None):
    """Zero FAT free space and every free ext4 block on a new standalone disk."""
    info=json.loads(run(['qemu-img','info','--output=json',image],capture_output=True,text=True).stdout)
    if info.get('format')!='qcow2' or 'backing-filename' in info:
        raise ValueError('Free-space sealing requires a standalone QCOW2 working image')
    command=['guestfish','--rw','-a',image]
    if appliance_mounts is None:
        command+=['-i']
    else:
        for mount in appliance_mounts:
            command+=['-m',mount]
    if any(not re.fullmatch(r'/[A-Za-z0-9/_-]*',point) for point in mountpoints):
        raise ValueError('Invalid sealing mountpoint')
    # The pinned image has its ext4 root on partition1. Filling one large file
    # until ENOSPC can leave allocator-skipped free blocks untouched. zerofree
    # scans the allocation bitmap directly, and must run with root unmounted.
    commands='available zerofree\n'+''.join('zero-free-space '+point+'\n' for point in mountpoints)
    commands+='sync\numount-all\nzerofree /dev/sda1\nsync\n'
    run(command,input=commands,
        text=True,timeout=1800,env={**os.environ,'LIBGUESTFS_MEMSIZE':'512'})

def guest_call(path, execute, arguments=None):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as channel:
        channel.settimeout(20)
        channel.connect(str(path))
        request = {'execute':execute, 'id':1}
        if arguments is not None: request['arguments']=arguments
        channel.sendall(json.dumps(request).encode()+b'\n')
        stream=channel.makefile('rb')
        while True:
            raw=stream.readline()
            if not raw: raise RuntimeError('QGA disconnected')
            response=json.loads(raw)
            if response.get('id') != 1: continue
            if 'error' in response: raise RuntimeError(response['error'])
            return response['return']

def guest_exec(path, script, timeout=120):
    task=guest_call(path, 'guest-exec', {'path':'/bin/sh', 'arg':['-c',script], 'capture-output':True})
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        state=guest_call(path, 'guest-exec-status', {'pid':task['pid']})
        if state.get('exited'):
            out=base64.b64decode(state.get('out-data','')).decode(errors='replace')
            err=base64.b64decode(state.get('err-data','')).decode(errors='replace')
            if state.get('out-truncated') or state.get('err-truncated'): raise RuntimeError('QGA output truncated')
            if state.get('exitcode') != 0: raise RuntimeError(f'Guest command failed: {state.get("exitcode")}\n{out}\n{err}')
            return out
        time.sleep(.4)
    raise TimeoutError('Guest command timed out')

def make_seed(directory, instance, userdata, network=None):
    directory.mkdir()
    (directory/'meta-data').write_text(json.dumps({'instance-id':instance,'local-hostname':'rougarou-cloud-build'})+'\n')
    (directory/'user-data').write_text('#cloud-config\n'+json.dumps(userdata,indent=2)+'\n')
    if network is not None: (directory/'network-config').write_text(json.dumps(network)+'\n')
    seed=directory.with_suffix('.iso')
    run(['xorriso','-as','mkisofs','-quiet','-R','-J','-V','CIDATA','-o',seed,directory])
    return seed

def qemu_command(disk, directory, *, seed=None, payload=None, firmware='bios', network=False, sshport=2222):
    args=['qemu-system-x86_64','-enable-kvm','-machine','q35','-cpu','host','-m','3072','-smp','2',
          '-display','none','-monitor','none','-serial',f'file:{directory}/serial.log',
          '-drive',f'file={disk},format=qcow2,if=virtio,discard=unmap',
          '-device','virtio-serial-pci','-chardev',f'socket,path={directory}/qga.sock,server=on,wait=off,id=qga0',
          '-device','virtserialport,chardev=qga0,name=org.qemu.guest_agent.0']
    if firmware=='uefi':
        vars=directory/'OVMF_VARS.fd'; shutil.copyfile('/usr/share/OVMF/OVMF_VARS_4M.fd',vars)
        args+=['-drive','if=pflash,format=raw,readonly=on,file=/usr/share/OVMF/OVMF_CODE_4M.fd',
               '-drive',f'if=pflash,format=raw,file={vars}']
    if network:
        args+=['-netdev',f'user,id=net0,restrict=on,hostfwd=tcp:127.0.0.1:{sshport}-:22',
               '-device','virtio-net-pci,netdev=net0,mac=52:54:00:12:34:'+('11' if firmware=='bios' else '12')]
    else: args+=['-nic','none']
    if seed: args+=['-drive',f'file={seed},format=raw,media=cdrom,readonly=on']
    if payload: args+=['-drive',f'file={payload},format=raw,media=cdrom,readonly=on']
    return args

def wait_ready(process, directory, marker=None, timeout=900):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        if process.poll() is not None: raise RuntimeError('Guest terminated before ready')
        serial=directory/'serial.log'
        if serial.exists() and 'ROUGAROU_CLOUD_BUILD_FAILED' in serial.read_text(errors='replace'):
            raise RuntimeError('Cloud image configuration failed; inspect serial evidence')
        try:
            guest_call(directory/'qga.sock','guest-ping')
            if marker: guest_exec(directory/'qga.sock',f'test -f {marker}',10)
            return
        except (OSError,RuntimeError,ValueError): time.sleep(1)
    raise TimeoutError('Cloud guest did not become ready')

def shutdown(process,directory):
    # QGA guest-shutdown deliberately sends no success response.
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as channel:
        channel.settimeout(20); channel.connect(str(directory/'qga.sock'))
        channel.sendall(json.dumps({'execute':'guest-shutdown','arguments':{'mode':'powerdown'}}).encode()+b'\n')
    process.wait(timeout=120)
    if process.returncode: raise RuntimeError('QEMU shutdown failed')

def build(args):
    output=args.output.resolve(); work=args.work.resolve(); project=args.project.resolve(); repo=args.repo.resolve()
    for path in (output,work):
        path.mkdir(parents=True,exist_ok=True)
        if any(path.iterdir()): raise ValueError(f'Expected empty directory: {path}')
    if output==work or output.is_relative_to(work) or work.is_relative_to(output): raise ValueError('Separate output and work directories required')
    frozen={str(p.relative_to(HERE)):digest(p) for p in sorted((HERE/'image/cloud').iterdir()) if p.is_file()}
    frozen['scripts/cloud-image.py']=digest(__file__)
    frozen['tests/cloud-sealed-audit.py']=digest(HERE/'tests/cloud-sealed-audit.py')
    project_inputs={name:digest(project/name) for name in ['versions.env','packages.txt','image/package-profiles.txt','scripts/repo.py','rootfs/usr/share/keyrings/rougarou-archive-keyring.gpg']}
    pins=json.loads((HERE/'image/cloud/upstream.json').read_text())
    for name in ['SHA512SUMS','debian-13-generic-amd64-20260914-2601.json']:
        shutil.copyfile(args.source.parent/name,output/('upstream-'+name))
    if digest(args.source,'sha512')!=pins['sha512']: raise ValueError('Upstream image checksum mismatch')
    repository_hashes={name:digest(repo/name) for name in ['InRelease','Release','Packages']}
    versions=dict(line.split('=',1) for line in (project/'versions.env').read_text().splitlines() if line and not line.startswith('#'))
    expected_version=versions['ROUGAROU_VERSION'].replace('-alpha.','~alpha')+'-'+versions.get('ROUGAROU_BASE_REVISION',versions['ROUGAROU_PACKAGE_REVISION'])
    candidate_version=run(['dpkg-deb','-f',args.base_package,'Version'],capture_output=True,text=True).stdout.strip()
    candidate_name=run(['dpkg-deb','-f',args.base_package,'Package'],capture_output=True,text=True).stdout.strip()
    if candidate_name!='rougarou-base' or candidate_version!=expected_version: raise ValueError('Candidate does not match current declared base version')
    accepted_inputs=json.loads((args.base_package.parent/'package-inputs.json').read_text())
    current_inputs=json.loads(run(['python3',project/'image/package-input-manifest.py'],capture_output=True,text=True).stdout)
    if accepted_inputs!=current_inputs: raise ValueError('Candidate input binding differs from the frozen project')
    base_hash=digest(args.base_package)
    records=runpy.run_path(str(project/'scripts/repo.py'))['records'](repo/'Packages')
    bases=[row for row in records if row['Package']=='rougarou-base']
    if len(bases)!=1 or bases[0]['Version']!=expected_version or bases[0]['SHA256']!=base_hash: raise ValueError('Signed repository does not contain the exact accepted base candidate')
    cloud_provenance=json.loads(args.cloud_provenance.read_text())
    for added in cloud_provenance['added_packages']:
        if not any(all(row.get(k)==added[k] for k in ['Package','Version','Architecture','SHA256','Size']) for row in records):
            raise ValueError('Cloud Debian package provenance does not match repository')
    shutil.copyfile(args.cloud_provenance,output/'cloud-source-provenance.json')
    with tarfile.open(output/'debian-apt-provenance.tar.xz','w:xz') as archive:
        evidence=args.source.parent/'apt-lists'
        expected={item['file']:item['sha256'] for item in cloud_provenance['apt_signed_releases']}
        expected.update({item['file']:item['stored_sha256'] for item in cloud_provenance['apt_package_indices']})
        for name,value in sorted(expected.items()):
            if Path(name).name!=name or digest(evidence/name)!=value: raise ValueError('APT provenance archive differs from reviewed hashes')
            archive.add(evidence/name,arcname=name,recursive=False,filter=public_archive_member)
    run(['python3',project/'scripts/repo.py','verify','--repo',repo,'--keyring',project/'rootfs/usr/share/keyrings/rougarou-archive-keyring.gpg'])
    core=[s.strip() for s in (project/'packages.txt').read_text().splitlines() if s.strip() and not s.startswith('#')]
    profiles=dict(s.split('|',1) for s in (project/'image/package-profiles.txt').read_text().splitlines() if s.strip() and not s.startswith('#'))
    cloud_roots=[s.strip() for s in (HERE/'image/cloud/packages.txt').read_text().splitlines() if s.strip() and not s.startswith('#')]
    roots=list(dict.fromkeys(['rougarou-base='+expected_version]+core+profiles['docker-rootless'].split()+cloud_roots))
    if any(not re.fullmatch(r'[a-z0-9][a-z0-9+.-]*(?:=[A-Za-z0-9:+.~\-]+)?',x) for x in roots): raise ValueError('Invalid package root')
    (work/'install-roots').write_text('\n'.join(roots)+'\n')
    payload=work/'repository.iso'
    run(['xorriso','-as','mkisofs','-quiet','-R','-J','-V','ROUGAROU_PKGS','-o',payload,'-graft-points',
         'repository='+str(repo),'install-roots='+str(work/'install-roots'),
         'archive-keyring.gpg='+str(project/'rootfs/usr/share/keyrings/rougarou-archive-keyring.gpg')])
    files=[]
    for name,destination in [('configure-image.sh','/usr/local/sbin/rougarou-cloud-build'),('clean-image.sh','/usr/local/sbin/rougarou-cloud-clean')]:
        files.append({'path':destination,'permissions':'0700','encoding':'b64','content':base64.b64encode((HERE/'image/cloud'/name).read_bytes()).decode()})
    seed=make_seed(work/'seed','rougarou-build-only',{'users':[],'disable_root':True,'ssh_pwauth':False,
        'package_update':False,'package_upgrade':False,'write_files':files,
        'runcmd':[['/usr/local/sbin/rougarou-cloud-build']]}, {'version':2,'ethernets':{}})
    disk=work/'build.qcow2'
    run(['qemu-img','create','-f','qcow2','-F','qcow2','-b',args.source.resolve(),disk,'8G'])
    guestdir=work/'guest'; guestdir.mkdir()
    command=qemu_command(disk,guestdir,seed=seed,payload=payload)
    write_json(guestdir/'command.json',command)
    # Leave at least2GiB available alongside this3GiB guest and other tests.
    resource_deadline=time.monotonic()+900
    while True:
        available=int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:')))
        if available>=5*1024*1024: break
        if time.monotonic()>resource_deadline: raise RuntimeError('Insufficient available memory for isolated cloud guest')
        print('Waiting for5GiB available memory before cloud guest boot',flush=True)
        time.sleep(15)
    with (guestdir/'qemu.log').open('w') as log:
        process=subprocess.Popen(command,stdout=log,stderr=log)
        try:
            wait_ready(process,guestdir,'/var/lib/rougarou-cloud-build-ready')
            packages=guest_exec(guestdir/'qga.sock',"cat /var/lib/rougarou-cloud-build-packages.tsv")
            if dict(line.split('\t') for line in packages.splitlines()).get('rougarou-base')!=expected_version: raise RuntimeError('Installed base version differs from candidate')
            (output/'packages.tsv').write_text(packages)
            status=guest_exec(guestdir/'qga.sock','cloud-init status --wait --format json',180)
            write_json(work/'build-cloud-init-status.json',json.loads(status))
            guest_exec(guestdir/'qga.sock','/usr/local/sbin/rougarou-cloud-clean',180)
            # Check sanitation before shutdown without importing credentials/logs.
            guest_exec(guestdir/'qga.sock',"test \"$(cat /etc/machine-id)\" = uninitialized; test -z \"$(find /etc/ssh -name 'ssh_host_*' -print)\"; test ! -e /var/lib/cloud/instance; test ! -d /root/.ssh; test ! -e /var/lib/systemd/random-seed; ! systemctl is-active --quiet systemd-random-seed.service")
            shutdown(process,guestdir)
        finally:
            if process.poll() is None: process.terminate(); process.wait(timeout=30)
    # Flatten before zeroing: a discard on a backing overlay is insufficient
    # evidence that previously allocated bytes cannot reappear in the output.
    sealing=work/'standalone-sealing.qcow2'
    run(['qemu-img','convert','-O','qcow2',disk,sealing])
    zero_filesystem_free_space(sealing)
    artifact=output/('rougarou-os-'+versions['ROUGAROU_VERSION']+'-cloud-amd64.qcow2')
    run(['qemu-img','convert','-O','qcow2','-c',sealing,artifact])
    info=json.loads(run(['qemu-img','info','--output=json',artifact],capture_output=True,text=True).stdout)
    if 'backing-filename' in info: raise RuntimeError('Final image must be standalone')
    run(['python3',HERE/'tests/cloud-sealed-audit.py',artifact,output/'sealed-audit.json'],env={**os.environ,'LIBGUESTFS_MEMSIZE':'512'})
    assert all(digest(HERE/name)==value for name,value in frozen.items()),'Cloud build inputs changed during assembly'
    assert all(digest(project/name)==value for name,value in project_inputs.items()),'Project build inputs changed during assembly'
    assert {name:digest(repo/name) for name in repository_hashes}==repository_hashes,'Repository changed during assembly'
    assert digest(args.base_package)==base_hash,'Accepted candidate changed during assembly'
    manifest={'schema':1,'kind':'rougarou-cloud-image','upstream':pins,'repository':repository_hashes,'base_package':{'version':expected_version,'sha256':base_hash},'cloud_provenance_sha256':digest(output/'cloud-source-provenance.json'),'debian_apt_provenance_sha256':digest(output/'debian-apt-provenance.tar.xz'),
              'artifact':{'file':artifact.name,'sha256':digest(artifact),'bytes':artifact.stat().st_size,'virtual_size':info['virtual-size']},
              'sealed_audit_sha256':digest(output/'sealed-audit.json'),'package_manifest_sha256':digest(output/'packages.tsv'),'installed_roots':roots,'build_network':False,
              'free_space_sealing':{'method':'offline ext4 allocation-bitmap zerofree after unmount; FAT zero-free-space; standalone work disk before compression','filesystems':['/','/boot/efi'],'upstream_modified':False},
              'inputs':frozen,'project_inputs':project_inputs,
              'builder_script_sha256':digest(__file__),'builder_image_id':os.environ.get('ROUGAROU_BUILDER_IMAGE_ID'),'upstream_metadata':{p.name:digest(p) for p in output.glob('upstream-*')},'image_policy':{'operator_accounts':0,'password_authentication':False,'docker':'rootless prerequisites only','agent':'none'}}
    write_json(output/'build-manifest.json',manifest)
    (output/'SHA256SUMS').write_text(''.join(f'{digest(p)}  {p.name}\n' for p in sorted(output.iterdir()) if p.is_file()))
    print(json.dumps(manifest['artifact'],sort_keys=True))

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--repo',type=Path,required=True)
    parser.add_argument('--base-package',type=Path,required=True)
    parser.add_argument('--cloud-provenance',type=Path,required=True)
    parser.add_argument('--project',type=Path,required=True)
    parser.add_argument('--work',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    build(parser.parse_args())

if __name__=='__main__': main()
