#!/usr/bin/env python3
"""Host-only disk checkpoint helper; never ships credentials inside Rougarou."""
import argparse, hashlib, ipaddress, json, os, re, stat, subprocess, uuid
from pathlib import Path


def call(args,check=True):
    return subprocess.run(args,text=True,capture_output=True,check=check)

def validate_id(value):
    vmid=int(value)
    if not 100<=vmid<=999999999: raise ValueError('Invalid VM ID')
    return vmid

def config(vmid,snapshot=None):
    args=['qm','config',str(vmid)]
    if snapshot: args+=['--snapshot',snapshot]
    result={}
    for line in call(args).stdout.splitlines():
        if ': ' in line:
            k,v=line.split(': ',1)
            if k=='cipassword': raise ValueError('This key-only checkpoint helper refuses guests with a configured cloud-init password')
            if k=='sshkeys': result['cloud_public_keys_sha256']=hashlib.sha256(v.encode()).hexdigest()
            if k in {'name','smbios1','machine','bios','ciuser','citype','nameserver','searchdomain','cicustom','ciupgrade','memory','balloon','cores','sockets','cpu','scsihw','boot','onboot'} or re.fullmatch(r'(net|ipconfig|scsi|sata|virtio|ide|efidisk|tpmstate)\d+',k): result[k]=v
    if 'name' not in result or 'net0' not in result: raise ValueError('Missing explicit VM identity/network')
    return result

def snapshot_receipt(vmid,name):
    fields={}
    for line in call(['qm','config',str(vmid),'--snapshot',name]).stdout.splitlines():
        if ': ' in line:
            key,value=line.split(': ',1)
            if key in {'snaptime','description'}: fields[key]=value
    if set(fields)!={'snaptime','description'} or not fields['snaptime'].isdigit():
        raise ValueError('Snapshot lacks stable creation metadata')
    return fields

def status(vmid):
    return call(['qm','status',str(vmid)]).stdout.strip().removeprefix('status: ')

def guest_identity(vmid):
    script="""set -eu
python3 - <<'GUEST'
import json,subprocess
run=lambda cmd:subprocess.check_output(cmd,text=True).strip()
links=json.loads(run(['ip','-j','address','show']))
ips=sorted(a['local'] for link in links for a in link.get('addr_info',[]) if a.get('scope')=='global')
print(json.dumps({'machine_id':open('/etc/machine-id').read().strip(),'hostname':run(['hostname']),'ssh_fingerprint':run(['ssh-keygen','-lf','/etc/ssh/ssh_host_ed25519_key.pub']).split()[1],'ips':ips},sort_keys=True))
GUEST
"""
    data=json.loads(call(['qm','guest','exec',str(vmid),'--','/bin/sh','-c',script]).stdout)
    if data.get('exitcode')!=0 or not data.get('exited'): raise ValueError('Guest identity probe failed')
    return json.loads(data['out-data'])

def read_record(path,vmid):
    meta=path.lstat()
    if not stat.S_ISREG(meta.st_mode) or meta.st_mode&0o077 or meta.st_uid!=os.geteuid(): raise ValueError('Record must be a private file owned by operator')
    value=json.loads(path.read_text())
    if value.get('vmid')!=vmid or value.get('node')!=os.uname().nodename: raise ValueError('Record VM/node mismatch')
    return value

def save(path,value,create=False):
    flags=os.O_WRONLY|os.O_CREAT|os.O_NOFOLLOW|(os.O_EXCL if create else os.O_TRUNC)
    fd=os.open(path,flags,0o600)
    with os.fdopen(fd,'w') as stream: json.dump(value,stream,indent=2,sort_keys=True);stream.write('\n')

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['capture','create','restore','verify']);p.add_argument('vmid',type=validate_id)
    p.add_argument('--record',type=Path,required=True);p.add_argument('--expected-name');p.add_argument('--expected-mac');p.add_argument('--expected-ip');p.add_argument('--snapshot');p.add_argument('--confirm')
    a=p.parse_args(argv); vmid=a.vmid
    if os.geteuid()!=0: raise ValueError('Run on the Proxmox node as an authorized administrator')
    if a.action=='capture':
        if not a.expected_name or not a.expected_mac or not a.expected_ip: raise ValueError('Capture requires expected name, MAC, and IP')
        ipaddress.ip_address(a.expected_ip)
        if not re.fullmatch(r'(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}',a.expected_mac): raise ValueError('Invalid MAC')
        if status(vmid)!='running': raise ValueError('Capture requires running guest')
        current=config(vmid)
        if current['name']!=a.expected_name or a.expected_mac.upper() not in current['net0'].upper(): raise ValueError('VM name/MAC differs from expected')
        identity=guest_identity(vmid)
        if a.expected_ip not in identity['ips']: raise ValueError('Expected IP not present in guest')
        save(a.record,{'schema':1,'vmid':vmid,'node':os.uname().nodename,'config':current,'identity':identity,'snapshot':None},create=True)
        print(json.dumps({'captured':True,'vmid':vmid})); return
    value=read_record(a.record,vmid)
    if config(vmid)!=value['config']: raise ValueError('VM disk/network/identity configuration changed')
    if a.action=='verify':
        if status(vmid)!='running' or guest_identity(vmid)!=value['identity']: raise ValueError('Post-restore guest identity/network mismatch')
        print(json.dumps({'verified':True,'vmid':vmid,'snapshot':value['snapshot']})); return
    if not a.snapshot or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,39}',a.snapshot): raise ValueError('Invalid snapshot name')
    if a.confirm!=f'{vmid}:{a.snapshot}': raise ValueError('Confirm exact VMID:snapshot token')
    if status(vmid)!='stopped': raise ValueError('Stop this guest explicitly before checkpoint or restore')
    if a.action=='create':
        if value['snapshot'] is not None: raise ValueError('Record already has a checkpoint')
        if call(['qm','config',str(vmid),'--snapshot',a.snapshot],check=False).returncode==0: raise ValueError('Snapshot already exists')
        token=uuid.uuid4().hex
        description=f'Rougarou checkpoint {token}; stopped VM disk snapshot; no memory; shutdown method not attested'
        call(['qm','snapshot',str(vmid),a.snapshot,'--vmstate','0','--description',description])
        if config(vmid,a.snapshot)!=value['config']: raise ValueError('Created snapshot configuration mismatch')
        receipt=snapshot_receipt(vmid,a.snapshot)
        if receipt['description']!=description: raise ValueError('Created checkpoint token mismatch')
        value['snapshot']=a.snapshot;value['snapshot_receipt']=receipt
        value['consistency']='Stopped VM disk snapshot; graceful shutdown/application quiescence not independently attested'
        save(a.record,value)
        print(json.dumps({'created':True,'recovery_point':f"{value['node']} VM{vmid} snapshot {a.snapshot}"}));return
    if value['snapshot']!=a.snapshot or config(vmid,a.snapshot)!=value['config']: raise ValueError('Checkpoint identity/disk/network mismatch')
    if snapshot_receipt(vmid,a.snapshot)!=value.get('snapshot_receipt'): raise ValueError('Checkpoint was replaced or its creation metadata changed')
    call(['qm','rollback',str(vmid),a.snapshot,'--start','0'])
    if status(vmid)!='stopped' or config(vmid)!=value['config']: raise ValueError('Unexpected rollback state/configuration')
    print(json.dumps({'restored':True,'vmid':vmid,'started':False,'next':'Operator starts guest, then runs verify'}))

if __name__=='__main__':
    try: main()
    except (ValueError,OSError,subprocess.CalledProcessError) as error:
        raise SystemExit(str(error))
