"""One numeric/alias-only overview shared by the terminal and result bundle."""
import json
import re
import unicodedata


PARTS = ('host','inventory','cases','timing','system','plans','focus')
STATES = {'complete','failed','interrupted','running','missing'}


def width(text):
    return sum(2 if unicodedata.east_asian_width(c) in ('W','F') else 1 for c in text)


def clip(text, columns):
    result = ''
    for char in text:
        if width(result+char) > columns-3:
            return result+'...'
        result += char
    return result


def alias(value):
    # Measurements create these aliases; never echo an error or arbitrary value.
    if isinstance(value,str) and re.fullmatch(r'[A-Za-z0-9_./:-]{1,240}',value):
        return value
    return 'unavailable'


def groups(documents, part):
    if part == 'timing':
        data = documents.get('timing.json',{})
        return [(key, data.get(key,[])) for key in ('pages','commands','http')]
    if part == 'plans':
        return [('plans', documents.get('plans.json',{}).get('items',[]))]
    if part == 'focus':
        data = documents.get('focus.json',{})
        return [('focus',data.get('shape',[])+data.get('measurements',[])+data.get('split',[]))]
    return []


def counts(rows):
    def states(row):
        # A page is one operation even if several of its SQL statements fail.
        return [s.get('state') for s in row.get('statements',[])] + [row.get('state')]
    def exceeds(row):
        if 'page_estimate_ms' in row:
            return any(exceeds(statement) for statement in row.get('statements',[]))
        return row.get('ms',0)>row.get('target_ms',1000)
    return dict(total=len(rows), timeout=sum(any(s in ('timeout','over_limit') for s in states(r)) for r in rows),
        over_reference=sum(exceeds(r) for r in rows),
        attention=sum(bool(r.get('attention')) or any(s.get('attention') for s in r.get('statements',[])) for r in rows),
        failed=sum(any(s not in (None,'ok','timeout','over_limit',
            'has_baseline','records_without_baseline','not_seen','unreliable_fingerprint') for s in states(r)) for r in rows))


def count_line(label, rows):
    values = counts(rows)
    return (f"{label}: {values['total']} 项；超时 {values['timeout']}；"
            f"超参照 {values['over_reference']}；失败 {values['failed']}")


def elapsed(row):
    return row.get('page_estimate_ms',row.get('ms',0))


def slow_lines(label, rows, limit):
    result = []
    for row in sorted(rows,key=lambda r:(-elapsed(r),alias(r.get('item'))))[:limit]:
        prefix, suffix = label+' 慢项 ', f" {elapsed(row):.3f} ms"
        result.append(prefix+clip(alias(row.get('item')),80-width(prefix+suffix))+suffix)
    return result


def state_line(documents, part):
    state = documents.get(part+'-state.json',{})
    value = state.get('state','missing')
    return f"{part}: {value if value in STATES else 'failed'}，{state.get('seconds',0):.3f} 秒"


def selection_line(documents):
    rows = documents.get('cases.json',[])
    return (f"用例类别：可用 {sum(r.get('state')=='available' for r in rows)}；"
            f"超时 {sum(r.get('reason')=='selection_timeout' for r in rows)}；"
            f"冲突跳过 {sum(r.get('reason')=='fixed_name_collision' and r.get('state')=='skipped' for r in rows)}")


def focus_line(documents):
    data = documents.get('focus.json',{})
    rows = data.get('measurements',[])+data.get('split',[])
    if not rows:
        return '重点分析：尚无执行结果。'
    values = counts(rows)
    scans = sum(bool(r.get('scan',{}).get('sequential_scan')) for r in rows)
    return f"重点分析：关注 {values['attention']} 项；含顺序扫描 {scans} 项；结合详细计划判断。"


def terminal(directory, part):
    names = {part+'-state.json',part+'.json'}
    documents = {name:json.loads((directory/name).read_text()) for name in names if (directory/name).exists()}
    lines = [state_line(documents,part)]
    if part == 'cases':
        lines.append(selection_line(documents))
    for label, rows in groups(documents,part):
        lines.append(count_line(label,rows))
        lines.extend(slow_lines(label,rows,1))
    if part == 'timing':
        data = documents.get('timing.json',{})
        statements = [s for key in ('pages','commands') for row in data.get(key,[]) for s in row.get('statements',[])]
        lines.append(count_line('SQL',statements))
    if part == 'focus':
        lines.append(focus_line(documents))
    return '\n'.join(clip(line,80) if width(line)>80 else line for line in lines)


def bundle(documents):
    lines = ['快速概要（操作与 SQL 分列；页面超参照按其 SQL 判断）']
    lines.extend(state_line(documents,part) for part in PARTS)
    lines.append(selection_line(documents))
    timing = documents.get('timing.json',{})
    coverage = timing.get('coverage')
    if coverage:
        lines.append(f"计时用例 {len(coverage['selected_cases'])}/{coverage['available_cases']}；每项三次。")
    for part in ('timing','plans','focus'):
        for label, rows in groups(documents,part):
            lines.append(count_line(label,rows))
            lines.extend(slow_lines(label,rows,3))
    statements = [s for key in ('pages','commands') for row in timing.get(key,[]) for s in row.get('statements',[])]
    lines.append(count_line('SQL',statements))
    lines.append(focus_line(documents))
    lines.append('页面耗时为变量链加最慢面板的估计；失败命令含预期不命中，详见逐项结果。')
    return lines
