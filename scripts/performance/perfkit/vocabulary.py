"""Preflight fixed export names. This never changes the fail-closed pack scan."""
import ast
import json
from pathlib import Path
import re

from . import plan_vocabulary
from . import collect


class Vocabulary:
    def __init__(self, groups):
        # Include JSON spelling: a quoted input such as "state" hits a JSON key.
        self.groups = [(location, '\0'.join(sorted({part for value in [location,*values]
            for part in (value, json.dumps(value, ensure_ascii=False)) if value})))
            for location, values in sorted(groups.items())]

    def collision(self, value):
        return next((location for location, text in self.groups
                     if isinstance(value, str) and value and value in text), None)


def fixed(session=None):
    # JSON plan labels/enums are plain words, not C include paths, error format
    # strings or XML/HTML renderer fragments in the same upstream source.
    groups = {'catalog:plan': [s for s in plan_vocabulary.STRINGS
                              if re.fullmatch(r'[A-Za-z][A-Za-z0-9 _/-]*',s)]}
    directory = Path(__file__).resolve().parent
    for path in sorted(directory.glob('*.py')) + [directory.parent/'check.py']:
        if path.name in ('plan_vocabulary.py','vocabulary.py'):
            continue
        tree = ast.parse(path.read_text())
        docstrings = {id(node.body[0].value) for node in ast.walk(tree)
            if isinstance(node,(ast.Module,ast.ClassDef,ast.FunctionDef,ast.AsyncFunctionDef))
            and node.body and isinstance(node.body[0],ast.Expr)
            and isinstance(node.body[0].value,ast.Constant)}
        # Conservative source-owned literals include field names, fixed summary
        # prose, reason codes, f-string fragments and dict(keyword=...) keys.
        # These two internal strings are normalized by pack.export_document:
        # the process classifier/connection OS account and its old RSS key.
        # They are not exported fixed names. pack.scan still scans both values.
        groups['tool:'+path.name] = [node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and id(node) not in docstrings
            and not re.match(r'^\s*\(?\s*(SELECT|WITH|SET|SHOW|LOAD|EXPLAIN|UNION)(?:\s|$)',node.value,re.I)
            and node.value not in ('postgres','postgres_rss_kib')] + [
            node.arg for node in ast.walk(tree) if isinstance(node, ast.keyword) and node.arg]
    manifest = directory.parent/'MANIFEST.json'
    if manifest.exists():
        from .pack import strings
        groups['tool:manifest'] = list(strings(json.loads(manifest.read_text())))
    # These public field names come from the running kernel, not Python literals.
    groups['host:columns'] = [line.split()[0].rstrip(':')
        for name in ('stat','meminfo','vmstat')
        for line in (collect.read('/proc/'+name) or '').splitlines() if line.split()]
    if session is not None:
        groups['catalog:names'] = [row['name'] for row in session.query("""
            SELECT relname::text name FROM pg_class
            UNION SELECT p.proname::text FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                WHERE n.nspname='sql_apm' OR p.proretset
            UNION SELECT nspname::text FROM pg_namespace
            UNION SELECT attname::text FROM pg_attribute WHERE attnum>0 AND NOT attisdropped
            UNION SELECT name FROM pg_settings
            UNION SELECT unit FROM pg_settings WHERE unit IS NOT NULL
            UNION SELECT source FROM pg_settings
            UNION SELECT vartype FROM pg_settings""")]
        # The product's Function Scan nodes call its own functions or built-in
        # set-returning functions. Scalar expressions are redacted by plans.py.
        # enumvals itself is not exported: only retained setting/boot values are.
        groups['catalog:settings'] = [str(row[field]) for row in session.query(collect.SETTINGS_SQL)
            if row['vartype'] in ('enum','bool') for field in ('setting','boot_val') if row[field] is not None]
    return Vocabulary(groups)
