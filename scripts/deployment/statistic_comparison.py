"""Stream exact non-log fields and the two explicitly bounded log metrics."""
from decimal import Decimal, localcontext
import gzip
import hashlib
from itertools import zip_longest
import json
from pathlib import Path

from verify_package import digest

FORMAT = 'sql-apm-guide-statistics/1'
TABLES = ('mpp_statistic', 'mpp_observation_statistic')
METRICS = ('log_median', 'log_mad')
LIMIT = Decimal('1e-12')


def exact_fields(evidence):
    return {table: {key: evidence[table][key] for key in ('rows', 'sha256')} for table in TABLES}


def export_statistics(db, build, directory, case):
    """One ordered SELECT per table; never export SQL text or materialize all rows."""
    from psycopg2 import sql
    evidence = {}
    for table in TABLES:
        path = directory / (case + '-' + table + '.jsonl.gz')
        count, h, previous = 0, hashlib.sha256(), ''
        with path.open('xb') as raw, gzip.GzipFile(filename='', fileobj=raw, mode='wb', mtime=0) as out:
            with db, db.cursor(name='guide_statistics') as cur:
                cur.itersize = 10000
                cur.execute(sql.SQL('''SELECT encode(sha256(convert_to(
                    (to_jsonb(t)-%s::text[])::text,'UTF8')),'hex') h,
                    log_median::text,log_mad::text FROM {} t WHERE build_id=%s ORDER BY h''').format(
                    sql.Identifier(table)), (['build_id', 'partition_id', *METRICS], build))
                for key, median, mad in cur:
                    if key <= previous:
                        raise ValueError('statistics row identity is not unique')
                    previous = key
                    h.update(key.encode('ascii'))
                    out.write((json.dumps([key, median, mad], separators=(',', ':'))+'\n').encode('ascii'))
                    count += 1
        evidence[table] = dict(rows=count, sha256=h.hexdigest(),
                              log_values=dict(file=path.name, sha256=digest(path)))
    return evidence


def verify_values(directory, evidence):
    if set(evidence) != set(TABLES):
        raise ValueError('both statistic tables required')
    for table in TABLES:
        meta = evidence[table]
        name = meta['log_values']['file']
        if not isinstance(name, str) or Path(name).name != name or not name.endswith('.jsonl.gz'):
            raise ValueError('statistics filename must be a local basename')
        path = directory / name
        if path.is_symlink() or digest(path) != meta['log_values']['sha256']:
            raise ValueError('statistics values checksum differs')
        if type(meta['rows']) is not int or meta['rows'] < 0:
            raise ValueError('invalid statistics row count')


def rows(path):
    previous = ''
    with gzip.open(path, 'rt', encoding='ascii') as stream:
        for line in stream:
            row = json.loads(line)
            if not isinstance(row, list) or len(row) != 3:
                raise ValueError('invalid statistics value row')
            key = row[0]
            if (not isinstance(key, str) or len(key) != 64 or
                    any(c not in '0123456789abcdef' for c in key) or key <= previous):
                raise ValueError('statistics keys must be unique sorted SHA-256 values')
            previous = key
            values = [None if v is None else Decimal(v) for v in row[1:]]
            if any(v is not None and (not v.is_finite() or v < 0) for v in values):
                raise ValueError('invalid statistics numeric value')
            yield key, values


def compare_statistics(actual, actual_dir, expected, expected_dir, *, absolute_limit=LIMIT):
    if absolute_limit not in (Decimal(0), LIMIT):
        raise ValueError('statistics limit must be zero or the confirmed cross-machine limit')
    verify_values(actual_dir, actual)
    verify_values(expected_dir, expected)
    report = dict(format=FORMAT, absolute_limit=str(absolute_limit), passed=True, tables={})
    for table in TABLES:
        a, b = actual[table], expected[table]
        result = dict(exact_fields_equal=exact_fields(actual)[table] == exact_fields(expected)[table],
                      actual_rows=0, expected_rows=0, key_mismatches=0, different_rows=0,
                      metrics={name: dict(different=0, null_mismatches=0, exceeded=0,
                                          max_absolute=Decimal(0)) for name in METRICS})
        left = rows(actual_dir / a['log_values']['file'])
        right = rows(expected_dir / b['log_values']['file'])
        actual_hash, expected_hash = hashlib.sha256(), hashlib.sha256()
        with localcontext() as context:
            context.prec = 80
            for current, reference in zip_longest(left, right):
                for row, label, h in ((current, 'actual_rows', actual_hash),
                                      (reference, 'expected_rows', expected_hash)):
                    if row is not None:
                        result[label] += 1
                        h.update(row[0].encode('ascii'))
                if current is None or reference is None or current[0] != reference[0]:
                    result['key_mismatches'] += 1
                    continue
                different = False
                for name, value, original in zip(METRICS, current[1], reference[1]):
                    metric = result['metrics'][name]
                    if value is None or original is None:
                        metric['null_mismatches'] += (value is None) != (original is None)
                        different |= (value is None) != (original is None)
                    else:
                        delta = abs(value-original)
                        metric['different'] += delta != 0
                        metric['exceeded'] += delta > absolute_limit
                        metric['max_absolute'] = max(delta, metric['max_absolute'])
                        different |= delta != 0
                result['different_rows'] += different
        result['passed'] = (result['exact_fields_equal'] and not result['key_mismatches'] and
            result['actual_rows'] == a['rows'] and result['expected_rows'] == b['rows'] and
            actual_hash.hexdigest() == a['sha256'] and expected_hash.hexdigest() == b['sha256'] and
            all(not m['exceeded'] and not m['null_mismatches'] for m in result['metrics'].values()))
        for metric in result['metrics'].values():
            metric['max_absolute'] = str(metric['max_absolute'])
        report['tables'][table] = result
        report['passed'] &= result['passed']
    return report
