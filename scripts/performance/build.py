#!/usr/bin/env python3
"""Build the independent inspection archive and offline handbook reproducibly."""
import argparse
import gzip
import hashlib
from html import escape
import io
import json
from pathlib import Path
import re
import subprocess
import sys
import tarfile
from urllib.parse import unquote, urlsplit

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts/deployment'))
from render_manual import CSS,converter,code_blocks,inspect
from perfkit import VERSION
from perfkit.core import digest,encoded
from perfkit.focus import extract, extract_settings


def build(root,module,out,allow_dirty=False):
    root,module,out=map(lambda p:Path(p).resolve(),(root,module,out))
    commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip()
    dirty=bool(subprocess.check_output(['git','status','--porcelain'],cwd=root,text=True))
    if dirty and not allow_dirty:
        raise ValueError('commit the reviewable source before building a delivery')
    if out.exists():
        raise ValueError('fresh_output_directory_required')
    files={}
    source=root/'scripts/performance'
    for path in sorted((source/'perfkit').glob('*.py'))+[source/'check.py',source/'perfcheck']:
        files[str(path.relative_to(source))]=path.read_bytes()
    files['internal.json']=encoded(extract((root/'sql_apm/storage/schema.sql').read_text()))
    files['internal-settings.json']=encoded(extract_settings((root/'sql_apm/storage/schema.sql').read_text()))
    for name in ('auto_explain.so','module.json','COPYRIGHT'):
        files['module/'+name]=(module/name).read_bytes()
    module_info=json.loads(files['module/module.json'])
    if hashlib.sha256(files['module/auto_explain.so']).hexdigest()!=module_info['module_sha256']:
        raise ValueError('module_checksum_mismatch')
    manual=(root/'docs/runbooks/performance-check.md').read_text()
    renderer=converter('manual')
    body=renderer.convert(manual)
    def repository_link(match):
        parts=urlsplit(unquote(match[1].replace('&amp;','&')))
        if parts.scheme or parts.netloc or not parts.path:
            return match[0]
        target=(root/'docs/runbooks'/parts.path).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            raise ValueError('missing manual reference')
        url='https://github.com/shenxg13/sql-apm/blob/'+commit+'/'+target.relative_to(root).as_posix()
        if parts.fragment:
            url+='#'+parts.fragment
        return 'href="'+escape(url,quote=True)+'"'
    body=re.sub(r'href="([^"]+)"',repository_link,body)
    document='<!doctype html>\n<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>SQL APM 性能检查工具包</title><style>'+CSS+'</style></head><body><p>工具包 '+escape(VERSION)+'；产品 v1.2.0。源码提交 '+commit+'</p><nav>'+renderer.toc+'</nav>'+body+'</body></html>\n'
    inspection=inspect(document,code_blocks(manual))
    files['PERFORMANCE.html']=document.encode()
    manifest=dict(format='sql-apm-performance-toolkit/1',version=VERSION,commit=commit,
                  source_state='working_tree' if dirty else 'committed',product_version='v1.2.0',
                  schema_version='1.13.0',html_checks=inspection,
                  files={name:hashlib.sha256(raw).hexdigest() for name,raw in sorted(files.items())})
    files['MANIFEST.json']=encoded(manifest)
    files['SHA256SUMS']=''.join(hashlib.sha256(raw).hexdigest()+'  '+name+'\n' for name,raw in sorted(files.items())).encode()
    out.mkdir(parents=True)
    tree=out/'performance-toolkit'
    tree.mkdir()
    for name,raw in files.items():
        path=tree/name
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(raw)
        path.chmod(0o755 if name=='perfcheck' else 0o644)
    archive=out/'performance-toolkit.tar.gz'
    with archive.open('wb') as raw,gzip.GzipFile(filename='',fileobj=raw,mode='wb',mtime=0) as zipped,tarfile.open(fileobj=zipped,mode='w|') as tar:
        for name,data in sorted(files.items()):
            item=tarfile.TarInfo('performance-toolkit/'+name)
            item.size,item.mode,item.mtime=len(data),0o755 if name=='perfcheck' else 0o644,0
            tar.addfile(item,io.BytesIO(data))
    (out/'performance-toolkit.tar.gz.sha256').write_text(digest(archive)+'  '+archive.name+'\n')
    print(json.dumps(dict(archive=str(archive),bytes=archive.stat().st_size,sha256=digest(archive))))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--module',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--allow-dirty',action='store_true',help='development preview only')
    args=parser.parse_args()
    build(ROOT,args.module,args.output,args.allow_dirty)
