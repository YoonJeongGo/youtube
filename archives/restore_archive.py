"""Restore one complete editing/history group into a new empty directory."""
import argparse, hashlib, json, shutil, zipfile
from pathlib import Path, PurePosixPath

def restore(parts_folder, group, destination):
    if not group or any(c in group for c in '/\\*?[]'): raise ValueError('Invalid group')
    parts=sorted(parts_folder.glob(group+'-part*.zip'))
    if not parts: raise ValueError('No parts found')
    manifests=[]
    for part in parts:
        with zipfile.ZipFile(part) as z:
            if 'archive_manifest.json' in z.namelist(): manifests.append(json.loads(z.read('archive_manifest.json')))
    if len(manifests)!=1 or manifests[0]['group']!=group: raise ValueError('Expected one matching manifest')
    manifest=manifests[0]
    if manifest['kind']=='delivery': raise ValueError('Delivery ZIPs unzip normally')
    by_name={p.name:p for p in parts}
    needed={e['part'] for e in manifest['entries']}
    if needed-set(by_name):raise ValueError('Missing parts: '+', '.join(sorted(needed-set(by_name))))
    if destination.exists() and any(destination.iterdir()):raise ValueError('Destination must be empty; existing production is never overwritten')
    destination.mkdir(parents=True,exist_ok=True); destination=destination.resolve(); restored={};opened={}
    try:
        for e in manifest['entries']:
            rel=PurePosixPath(e['path'])
            if rel.is_absolute() or '..' in rel.parts or '\\' in e['path'] or ':' in e['path']:raise ValueError('Unsafe path')
            target=destination.joinpath(*rel.parts)
            if not target.resolve().is_relative_to(destination):raise ValueError('Outside destination')
            target.parent.mkdir(parents=True,exist_ok=True)
            if target.exists():raise ValueError('Duplicate destination')
            if e['sha256'] in restored:shutil.copyfile(restored[e['sha256']],target)
            else:
                if e['part'] not in opened:opened[e['part']]=zipfile.ZipFile(by_name[e['part']])
                h=hashlib.sha256()
                with opened[e['part']].open(e['member']) as src,target.open('xb') as dst:
                    for block in iter(lambda:src.read(1024*1024),b''):dst.write(block);h.update(block)
                if target.stat().st_size!=e['bytes'] or h.hexdigest()!=e['sha256']:raise ValueError('Restored hash mismatch: '+e['path'])
                restored[e['sha256']]=target
    finally:
        for z in opened.values():z.close()
    print(json.dumps({'restored_files':len(manifest['entries']),'unique_verified_files':len(restored),'destination':str(destination)},ensure_ascii=False))

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--parts-folder',type=Path,required=True);p.add_argument('--group',required=True);p.add_argument('--destination',type=Path,required=True)
    a=p.parse_args();restore(a.parts_folder,a.group,a.destination)
