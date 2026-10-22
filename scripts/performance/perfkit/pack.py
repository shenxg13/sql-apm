"""Result allowlist, explicit field inventory and fail-closed sensitive-value scan."""
import gzip
import io
import json
from pathlib import Path
import shlex
import tarfile

from .core import digest, encoded, Refused, save
from . import metadata


ALLOWED = {'identity.json','cases.json','host.json','inventory.json','health.json','settings.json',
           'timing.json','samples.json','deltas.json','plans.json','focus.json','distribution.json',
           'main-state.json','system-state.json','timing-state.json','cases-state.json',
           'host-state.json','inventory-state.json','plans-state.json','focus-state.json'}


def paths(value,prefix=''):
    result=set()
    if isinstance(value,dict):
        for key,item in value.items():
            path=prefix+'.'+key if prefix else key
            result.add(path)
            result.update(paths(item,path))
    elif isinstance(value,list):
        for item in value:
            result.update(paths(item,prefix+'[]'))
    return result


def strings(value):
    if isinstance(value,str):
        yield value
    elif isinstance(value,dict):
        for key,item in value.items():
            yield key
            yield from strings(item)
    elif isinstance(value,list):
        for item in value:
            yield from strings(item)


def scan(files,secrets):
    for name,raw in files.items():
        texts=[raw.decode('utf-8')]
        if name.endswith('.json'):
            texts.extend(strings(json.loads(raw)))
        for secret in secrets:
            if secret and any(secret in text for text in texts):
                # Neither the sensitive value nor a surrounding excerpt is reported.
                raise Refused('sensitive_value_in:'+name)


def export_document(name,value,secrets):
    """Use neutral field names; redact approved paths that collide with identities."""
    if name=='samples.json':
        for row in value:
            processes=row.get('processes',{})
            if 'postgres_rss_kib' in processes:
                processes['database_rss_kib']=processes.pop('postgres_rss_kib')
    if name=='settings.json':
        for row in value['values']:
            if row['vartype']=='string':
                for field in ('setting','boot_val'):
                    if isinstance(row[field],str) and any(s in row[field] for s in secrets if s):
                        row[field]='<redacted>'
                        row['path_or_setting_redacted']=True
        for row in value['build']:
            if any(s in row['setting'] for s in secrets if s):
                if row['name']=='CONFIGURE':
                    row['setting']=' '.join((arg.split('=',1)[0]+'=<redacted>')
                        if '=' in arg and any(s in arg for s in secrets if s) else arg
                        for arg in shlex.split(row['setting']))
                else:
                    row['setting']='<redacted>'
                row['path_or_setting_redacted']=True
    return value


def summary(files):
    def document(name):
        return json.loads(files[name])
    lines=['SQL APM 使用性能检查结果','',
        '库级累计统计包含导入和构建；计时增量见 deltas.json。',
        '首次与后两次分别记录；参照值用于关注提示，不是通过门槛。',
        '页面估计 = 变量链 + 最慢面板；不包含浏览器和并行争用。','']
    for part in ('host','inventory','cases','timing','system','plans','focus'):
        name=part+'-state.json'
        state=document(name) if name in files else {'part':part,'state':'missing'}
        lines.append(f"{part}: {state['state']}，{state.get('seconds',0):.3f} 秒；"+
                     ('完整' if state['state']=='complete' else '需要关注：未完成'))
    def measurement(row):
        goal=row.get('target_ms',1000)
        lines.append(f"{row['item']}: {row['ms']:.3f} 毫秒；参照 {goal} 毫秒；"
                     f"返回行数 {row.get('rows','见逐条语句')}；{row['state']}；"+
                     ('需要关注' if row.get('attention') else '参照以内'))
    if 'timing.json' in files:
        data=document('timing.json')
        lines.extend(['','查询计时（毫秒）'])
        if data.get('coverage'):
            coverage=data['coverage']
            lines.append(f"计时用例 {len(coverage['selected_cases'])}/{coverage['available_cases']}；每项三次；"+
                         ('需要关注：操作者选择了部分用例' if coverage['mode']=='partial' else '全部用例'))
        for row in data.get('pages',[]):
            mark='需要关注' if row['incomplete'] or any(s['attention'] for s in row['statements']) else '逐项参照以内'
            lines.append(f"{row['item']}: 页面估计 {row['page_estimate_ms']:.3f}；变量链 {row['variables_ms']:.3f}；"
                         f"面板合计 {row['panels_ms']:.3f}；最慢面板 {row['slowest_panel_ms']:.3f}；{mark}")
            for statement in row['statements']:
                measurement(statement)
        for row in data.get('commands',[]):
            measurement(row)
            lines.append(f"退出码 {row['exit_code']}；丢弃的输出 {row['output_bytes']} 字节")
            for statement in row['statements']:
                measurement(statement)
        for row in data.get('http',[]):
            lines.append(f"{row['item']}: {row['ms']:.3f}；参照 3000；"+('需要关注' if row['attention'] else '参照以内'))
    if 'focus.json' in files:
        lines.extend(['','重点分析：列表排行与历史'])
        data=document('focus.json')
        for row in data.get('measurements',[])+data.get('split',[])+data.get('shape',[]):
            stats=row.get('scan',{})
            lines.append(f"{row['item']}: {row['ms']:.3f} 毫秒；{row['state']}；"
                         f"全表扫描 {stats.get('sequential_scan','未知')}；"
                         f"最大估计行数 {stats.get('largest_estimated_rows','未知')}；"
                         f"最大实际行数 {stats.get('largest_actual_rows','未知')}；参照 1000 毫秒；"+
                         ('需要关注' if row.get('attention') else '参照以内'))
    lines.extend(['','规模、健康、设置及主机数值',
        '以下各项无统一通过门槛；大小单位以字段 _bytes 为字节，_ms 为毫秒，_kib 为 KiB。',
        '缓存命中比例、顺序扫描、临时文件、死元组和低使用索引需结合计划、起算时间及业务负载判断。',
        '参照：库级统计包含导入和构建；配置及主机数值仅作记录，无自动推荐值。'])
    def flatten(value,path):
        if isinstance(value,dict):
            for key,item in value.items():
                flatten(item,path+'.'+key)
        elif isinstance(value,list):
            for index,item in enumerate(value):
                flatten(item,path+'['+str(index)+']')
        else:
            lines.append(path+' = '+str(value)+'；参照：无统一门槛；'+
                         ('需要关注：不可得' if value is None or 'unavailable' in path else '需结合上下文判断'))
    for name in ('inventory','health','settings','host','deltas','distribution'):
        if name+'.json' in files:
            flatten(document(name+'.json'),name)
    return '\n'.join(lines)+'\n'


def package(work,toolkit):
    work.flush()
    files={}
    for path in sorted(work.public.iterdir()):
        if path.is_symlink() or not path.is_file() or path.name not in ALLOWED:
            raise Refused('unexpected_result_file')
        files[path.name]=encoded(export_document(path.name,json.loads(path.read_text()),work.secrets))
    files['SUMMARY.txt']=summary(files).encode()
    identity=Path(toolkit)/'MANIFEST.json'
    files['TOOLKIT.json']=encoded(dict(manifest_sha256=digest(identity),manifest=json.loads(identity.read_text())))
    files['PUBLIC_METADATA.json']=encoded(metadata.declaration())
    fields={name:sorted(paths(json.loads(raw))) if name.endswith('.json') else ['中文逐项摘要，由 JSON 数值派生']
            for name,raw in files.items()}
    fields.update({'FIELDS.json':['files：逐文件字段路径，不含业务值'],
                   'SHA256SUMS':['每行 SHA-256 和文件名；不包含自身摘要']})
    files['FIELDS.json']=encoded(dict(files=fields))
    scan(metadata.scan_views(files),work.secrets)
    manifest={name:__import__('hashlib').sha256(raw).hexdigest() for name,raw in files.items()}
    files['SHA256SUMS']=''.join(value+'  '+name+'\n' for name,value in sorted(manifest.items())).encode()
    target=work.path/'performance-results.tar.gz'
    temporary=target.with_name(target.name+'.tmp')
    try:
        with temporary.open('wb') as out,gzip.GzipFile(filename='',mode='wb',fileobj=out,mtime=0) as zipped,tarfile.open(fileobj=zipped,mode='w|') as archive:
            for name,raw in sorted(files.items()):
                info=tarfile.TarInfo(name)
                info.size,info.mode,info.mtime=len(raw),0o600,0
                archive.addfile(info,io.BytesIO(raw))
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    (work.path/'performance-results.tar.gz.sha256').write_text(digest(target)+'  '+target.name+'\n')
    print('结果包已生成；请按手册审阅后再带出。',flush=True)
    return target
