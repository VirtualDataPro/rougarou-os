#!/usr/bin/env python3
"""Offline libguestfs regression: erase deleted data only on a new standalone disk."""
import hashlib,importlib.util,json,subprocess,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('cloud_image',ROOT/'scripts/cloud-image.py');cloud=importlib.util.module_from_spec(spec);spec.loader.exec_module(cloud)
run=lambda args,**kw:subprocess.run([str(x) for x in args],check=True,**kw)
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
with tempfile.TemporaryDirectory(prefix='rougarou-zero-proof-') as tmp:
 root=Path(tmp);source=root/'source.qcow2';overlay=root/'overlay.qcow2';standalone=root/'standalone.qcow2';raw=root/'decoded.raw'
 marker=b'ROUGAROU_DISPOSABLE_DELETED_DATA_PROOF_9f301467'*1024
 (root/'marker').write_bytes(marker)
 run(['qemu-img','create','-f','qcow2',source,'128M'],capture_output=True)
 commands='run\npart-disk /dev/sda mbr\nmkfs ext4 /dev/sda1\nmount /dev/sda1 /\nwrite /retained-marker retained-file-remains-intact\nupload '+str(root/'marker')+' /deleted-marker\nsync\nrm /deleted-marker\nsync\n'
 run(['guestfish','--rw','-a',source],input=commands,text=True,capture_output=True)
 run(['qemu-img','convert','-O','raw',source,raw]);assert marker[:128] in raw.read_bytes(),'Fixture must contain recoverable deleted data'
 before=sha(source)
 run(['qemu-img','create','-f','qcow2','-F','qcow2','-b',source,overlay],capture_output=True)
 try:cloud.zero_filesystem_free_space(overlay,mountpoints=(),appliance_mounts=('/dev/sda1:/',))
 except ValueError:pass
 else:raise AssertionError('Backing overlay must be refused')
 run(['qemu-img','convert','-O','qcow2',overlay,standalone])
 cloud.zero_filesystem_free_space(standalone,mountpoints=(),appliance_mounts=('/dev/sda1:/',))
 run(['qemu-img','convert','-O','raw',standalone,raw]);assert marker[:128] not in raw.read_bytes(),'Deleted marker remains after zeroing'
 retained=run(['guestfish','--ro','-a',standalone,'-m','/dev/sda1:/','cat','/retained-marker'],capture_output=True,text=True).stdout.strip()
 assert retained=='retained-file-remains-intact','Allocated file changed'
 assert sha(source)==before,'Original source changed'
 print(json.dumps({'result':'PASS','deleted_data_before':True,'deleted_data_after':False,'backing_overlay_refused':True,'source_unchanged':True,'allocated_file_preserved':True,'unmounted_ext4_bitmap_zeroing':True}))
