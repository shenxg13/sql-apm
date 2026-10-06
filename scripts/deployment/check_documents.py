#!/usr/bin/env python3
"""Compare maintained operator documentation with configuration and schema code."""
import argparse
import ast
import fnmatch
import json
from pathlib import Path
import re
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from sql_apm.ingestion.config import load_config as import_config
from sql_apm.training.config import validate, THRESHOLDS

GUIDE = 'docs/runbooks/configuration-guide.md'
STRUCTURE = 'docs/design/database-structure.md'


def read_keys(root):
    """Read literal allowlists and consumed import keys from the validators' AST."""
    tree = ast.parse((root / 'sql_apm/training/config.py').read_text())
    names = {'document': 'root', 'window': 'window', 'values': 'thresholds.{layer}',
             'retention': 'retention'}
    training = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Sub):
            call, literal = node.left, node.right
        elif isinstance(node, ast.Compare) and len(node.ops) == 1 and isinstance(node.ops[0], ast.NotEq):
            call, literal = node.left, node.comparators[0]
        else:
            continue
        if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                and call.func.id == 'set' and len(call.args) == 1
                and isinstance(call.args[0], ast.Name) and isinstance(literal, ast.Set)):
            continue
        values = ast.literal_eval(literal)
        variable = call.args[0].id
        name = names.get(variable)
        if variable == 'item':
            name = 'templates[]' if 'sql' in values else 'exclusions[]'
        if name:
            training[name] = sorted(values)
    training['thresholds'] = sorted(THRESHOLDS)
    training['retention.clusters'] = ['{cluster}']
    tree = ast.parse((root / 'sql_apm/ingestion/config.py').read_text())
    names = {'document': 'root', 'source': 'sources.{source}', 'batch': 'batches.{batch}',
             'item': 'batches.{batch}.files[]'}
    imported = {value: set() for value in names.values()}
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript):
            owner, key = node.value, node.slice
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
              and node.func.attr == 'get' and node.args):
            owner, key = node.func.value, node.args[0]
        else:
            continue
        if isinstance(owner, ast.Name) and owner.id in names and isinstance(key, ast.Constant) and isinstance(key.value, str):
            imported[names[owner.id]].add(key.value)
    imported = {name: sorted(values) for name, values in imported.items()}
    imported['extra_keys'] = 'accepted'
    return dict(training=training, **{'import': imported})


def json_block(source, marker):
    match = re.search(re.escape(marker) + r'\s*```json\n(.*?)\n```', source, re.S)
    if match is None:
        raise ValueError('missing documentation marker: ' + marker)
    return json.loads(match[1])


def examples(source):
    return [(name, kind, json.loads(raw)) for name, kind, raw in re.findall(
        r'<!-- example:([a-z]+) (training|import) -->\s*```json\n(.*?)\n```', source, re.S)]


def check_guide(root, source):
    documented = json_block(source, '<!-- configuration-keys -->')
    for kind in documented:
        for name, values in documented[kind].items():
            if isinstance(values, list):
                if len(values) != len(set(values)):
                    raise ValueError('duplicate documented config key')
                documented[kind][name] = sorted(values)
    if documented != read_keys(root):
        raise ValueError('configuration key set differs from product validators')
    cases = examples(source)
    if {case[0] for case in cases} != {'window', 'threshold', 'template', 'exclusion', 'retention', 'import'}:
        raise ValueError('missing or unexpected configuration examples')
    for name, kind, document in cases:
        try:
            if kind == 'training':
                for cluster in document['clusters']:
                    validate(document, cluster)
            else:
                with tempfile.TemporaryDirectory() as temporary:
                    path = Path(temporary) / 'import.json'
                    path.write_text(json.dumps(document))
                    for batch, values in document['batches'].items():
                        import_config(path, values['source'], batch)
                    # Explicitly verify the permissive contract, without changing product behavior.
                    document['documented_extra_key'] = True
                    next(iter(document['sources'].values()))['documented_extra_key'] = True
                    next(iter(document['batches'].values()))['documented_extra_key'] = True
                    next(iter(document['batches'].values()))['files'][0]['documented_extra_key'] = True
                    path.write_text(json.dumps(document))
                    for batch, values in document['batches'].items():
                        import_config(path, values['source'], batch)
        except ValueError as error:
            raise ValueError('invalid configuration example: ' + name) from error
    return dict(keys_equal=True, validated_examples=[name for name, _, _ in cases])


def schema_columns(root):
    """Read the repository's explicit CREATE TABLE definitions (no dynamic leaves)."""
    source = (root / 'sql_apm/storage/schema.sql').read_text()
    result = {}
    for name, body in re.findall(r'CREATE TABLE IF NOT EXISTS (\w+) \(\n(.*?)\n\)(?: PARTITION BY[^;]+)?;', source, re.S):
        result[name] = set(re.findall(r'^    ([a-z][a-z_0-9]*)\s+[a-z]', body, re.M))
    if len(result) != 57 or any(not columns for columns in result.values()):
        raise ValueError('schema inventory changed or unsupported CREATE TABLE syntax')
    return result


def check_structure(root, source):
    actual, rows, inventory = schema_columns(root), [], False
    for line in source.splitlines():
        if line.startswith('| 表 |'):
            inventory = line == '| 表 | 一行代表 | 重点字段 | 关联 |'
            continue
        if not line.startswith('|'):
            inventory = False
        if not inventory or not line.startswith('| `'):
            continue
        cells = line.split('|')
        name = cells[1].strip().strip('`')
        columns = re.findall(r'`([a-z][a-z_0-9]*)`', cells[3])
        if name not in actual or set(columns) - actual[name]:
            raise ValueError('unknown documented table or column: ' + name + ': ' + ','.join(sorted(set(columns)-actual.get(name,set()))))
        rows.append(name)
    if len(rows) != len(set(rows)) or set(rows) != set(actual):
        raise ValueError('documented table inventory differs from schema: ' + ','.join(sorted(set(actual)-set(rows))))
    return dict(tables=len(rows), table_sets_equal=True, documented_columns_exist=True)


def check_paths(root):
    rules = json.loads((root / 'scripts/deployment/package-files.json').read_text())
    patterns = rules['program'] + rules['verification']
    paths = set()
    for name in ('docs/runbooks/kylin-offline-deployment.md', GUIDE):
        source = (root / name).read_text()
        # These explicit spans are read only on the development machine.
        source = re.sub(r'<!-- developer-only:start -->.*?<!-- developer-only:end -->', '', source, flags=re.S)
        paths.update(re.findall(r'\bscripts/[A-Za-z0-9_./-]+\.(?:py|sh)', source))
    for path in paths:
        if not (root / path).is_file() or not any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns):
            raise ValueError('manual command missing from program/verification kit: ' + path)
    return dict(checked_commands=sorted(paths), packaged=True)


def check(root=ROOT):
    return dict(configuration=check_guide(root, (root / GUIDE).read_text()),
                structure=check_structure(root, (root / STRUCTURE).read_text()), paths=check_paths(root))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    args = parser.parse_args()
    print(json.dumps(check(args.root), ensure_ascii=False, indent=2))
