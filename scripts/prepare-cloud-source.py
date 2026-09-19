#!/usr/bin/env python3
"""Merge a verified Rougarou snapshot with authenticated pinned cloud packages."""
import argparse, hashlib, json, os, re, runpy, shutil, subprocess
from pathlib import Path

def sha(path):
    with path.open('rb') as stream: return hashlib.file_digest(stream,'sha256').hexdigest()
p=argparse.ArgumentParser(); p.add_argument('baseline',type=Path); p.add_argument('cache',type=Path); p.add_argument('output',type=Path); p.add_argument('--project',type=Path,default=Path('/src')); args=p.parse_args()
api=runpy.run_path(str(args.project/'scripts/repo.py'))
subprocess.run(['python3',str(args.project/'scripts/repo.py'),'verify','--repo',str(args.baseline),'--keyring',str(args.project/'rootfs/usr/share/keyrings/rougarou-archive-keyring.gpg')],check=True)
args.output.mkdir(parents=True,exist_ok=True)
assert not any(args.output.iterdir()),'Output must be empty'
# APT already verified these signed indexes during download; independently
# retain and authenticate their signatures and raw Packages digests here.
lists=args.cache/'apt-lists'; signed=[]; releases=[]
for path in sorted(lists.glob('*_InRelease')):
    result=subprocess.run(['gpgv','--keyring','/usr/share/keyrings/debian-archive-keyring.pgp','--output','-',str(path)],check=True,capture_output=True)
    release=result.stdout.decode(); releases.append(release); signed.append({'file':path.name,'sha256':sha(path)})
assert signed,'No authenticated APT release metadata'
upstream=[]; indexes=[]
for path in sorted(lists.glob('*_Packages*')):
    result=subprocess.run(['/usr/lib/apt/apt-helper','cat-file',str(path)],check=True,capture_output=True)
    raw=result.stdout; digest=hashlib.sha256(raw).hexdigest()
    assert any(re.search(r'^ '+digest+r' +'+str(len(raw))+r' +[^\n]+$',r,re.M) for r in releases),f'Unsigned Packages index {path.name}'
    indexes.append({'file':path.name,'stored_sha256':sha(path),'uncompressed_sha256':digest})
    # Parse the RFC822 package index without writing decompressed metadata.
    for block in raw.decode().split('\n\n'):
        row={}
        for line in block.splitlines():
            if not line.startswith((' ','\t')) and ': ' in line:
                key,value=line.split(': ',1); row[key]=value
        if {'Package','Version','Architecture','SHA256','Size'}<=row.keys(): upstream.append(row)
base=api['records'](args.baseline/'Packages'); added=api['records'](args.cache/'Packages')
provenance=[]; (args.output/'pool').mkdir(); merged=[]
for root,rows,authenticate in [(args.baseline,base,False),(args.cache/'apt-debs',added,True)]:
    for row in rows:
        source=api['package_path'](root,row['Filename'])
        assert sha(source)==row['SHA256'] and source.stat().st_size==int(row['Size'])
        if authenticate:
            matches=[u for u in upstream if (u['Package'],u['Version'],u['Architecture'],u['SHA256'],u['Size'])==(row['Package'],row['Version'],row['Architecture'],row['SHA256'],row['Size'])]
            assert matches,f'Package not authenticated by saved Debian indices: {source.name}'
            provenance.append({key:row[key] for key in ['Package','Version','Architecture','SHA256','Size']})
        destination=args.output/'pool'/(row['SHA256']+'.deb')
        if not destination.exists():
            try: os.link(source,destination)
            except OSError: shutil.copyfile(source,destination)
        row=dict(row); row['Filename']='pool/'+destination.name; merged.append(row)
(args.output/'Packages').write_bytes(api['encode_records'](merged))
api['validate_packages'](args.output)
meta={'schema':1,'baseline':{name:sha(args.baseline/name) for name in ['InRelease','Packages']},'upstream_image_metadata_sha256':sha(args.cache/'debian-13-generic-amd64-20260914-2601.json'),'apt_signed_releases':signed,'apt_package_indices':indexes,'added_packages':provenance,'count':len(merged),'prepare_script_sha256':sha(Path(__file__)),'baseline_source_provenance':json.loads((args.baseline/'source-provenance.json').read_text()) if (args.baseline/'source-provenance.json').exists() else None}
(args.output/'cloud-source-provenance.json').write_text(json.dumps(meta,indent=2,sort_keys=True)+'\n')
print(json.dumps({'count':len(merged),'added':len(added),'output':str(args.output)}))
