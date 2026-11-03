"""Small, position-bound public metadata allowlist; never exempt data values."""
import json
import math
import re

from .core import encoded, Refused


# Verified against PostgreSQL 17.10 system_views.sql and guc_tables.c.
# Additions require source evidence and negative disclosure tests.
STAT_COLUMNS = frozenset(('last_analyze', 'last_autoanalyze', 'analyze_count',
                          'autoanalyze_count', 'n_mod_since_analyze'))
SETTING_NAMES = frozenset(('autovacuum_analyze_scale_factor',
                           'autovacuum_analyze_threshold'))
FIRST_READ_FIELDS = frozenset(('first_ms', 'first_plan_available', 'first_shared_read_blocks',
                               'first_local_read_blocks', 'first_temp_read_blocks'))
FIRST_READ_LOCATION = ('pages', '*', 'statements', '*')
LOCATIONS = {
    'health.json': {('tables', '*')},
    'deltas.json': {('before', 'tables', '*'), ('after', 'tables', '*'),
                    ('delta', 'tables', '*', 'values')},
}


def declaration():
    return dict(source_version='PostgreSQL 17.10',
                statistics_columns=sorted(STAT_COLUMNS), setting_names=sorted(SETTING_NAMES),
                statistics_locations={name:sorted('.'.join(path) for path in paths)
                                      for name, paths in LOCATIONS.items()},
                setting_location='settings.json:values[].name',
                first_read_numeric_fields=sorted(FIRST_READ_FIELDS),
                first_read_location='first-read.json:pages[].statements[]',
                rule='仅固定名称及对应清单、摘要引用；所有业务值和动态文本仍严格检查')


def field_path(path):
    return '.'.join(path).replace('.*', '[]')


def scan_views(files):
    """Mask approved names only in scan copies; exported bytes retain their names.

    Objects become lists of pairs so masking multiple keys never drops a value.
    SUMMARY and FIELDS only inherit exemptions at exact generated locations.
    """
    views = {}
    approved_paths = {}
    for name, raw in files.items():
        if name in ('SUMMARY.txt', 'FIELDS.json', 'PUBLIC_METADATA.json'):
            continue
        if name not in LOCATIONS and name not in ('settings.json', 'first-read.json'):
            views[name] = raw
            continue
        approved_paths[name] = set()
        def walk(value, path=()):
            if isinstance(value, dict):
                pairs = []
                for key, item in value.items():
                    approved = path in LOCATIONS.get(name, ()) and key in STAT_COLUMNS
                    if name == 'first-read.json' and path == FIRST_READ_LOCATION and key in FIRST_READ_FIELDS:
                        if type(item) not in (int, float) or not math.isfinite(item) or item < 0:
                            raise Refused('invalid_first_read_metric')
                        approved = True
                    if approved:
                        approved_paths[name].add(field_path(path+(key,)))
                    pairs.append(['' if approved else key, walk(item, path+(key,))])
                return pairs
            if isinstance(value, list):
                return [walk(item, path+('*',)) for item in value]
            if (name == 'settings.json' and path == ('values', '*', 'name')
                    and isinstance(value, str) and value in SETTING_NAMES):
                return ''
            return value
        views[name] = encoded(walk(json.loads(raw)))
    if 'FIELDS.json' in files:
        fields = json.loads(files['FIELDS.json'])
        fields['files'] = {
            name: ['' if path in approved_paths.get(name, ()) else path for path in paths]
            for name, paths in fields['files'].items()}
        views['FIELDS.json'] = encoded(fields)
    if 'SUMMARY.txt' in files:
        prefixes = {name.removesuffix('.json')+'.'+path
                    for name, paths in approved_paths.items() for path in paths}
        lines = []
        for line in files['SUMMARY.txt'].decode().splitlines():
            prefix, separator, rest = line.partition(' = ')
            normalized = re.sub(r'\[\d+\]', '[]', prefix)
            if separator and normalized in prefixes:
                prefix = ''
            if separator and normalized == 'settings.values[].name':
                value, marker, tail = rest.partition('；参照：')
                if value in SETTING_NAMES:
                    rest = marker+tail
            lines.append(prefix+separator+rest)
        views['SUMMARY.txt'] = ('\n'.join(lines)+'\n').encode()
    if 'PUBLIC_METADATA.json' in files:
        if files['PUBLIC_METADATA.json'] != encoded(declaration()):
            raise Refused('public_metadata_declaration_changed')
        # Exact source-owned declaration, containing only the approved names.
        view = declaration()
        view['statistics_columns'] = []
        view['setting_names'] = []
        view['first_read_numeric_fields'] = []
        views['PUBLIC_METADATA.json'] = encoded(view)
    return views
