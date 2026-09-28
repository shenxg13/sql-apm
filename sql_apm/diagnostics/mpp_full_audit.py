"""Audit full-scan failures and earlier fixed replays without exporting raw SQL."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sqlite3

from pglast import parser

from sql_apm.diagnostics.mpp_full_scan import ROOT, MAX_BYTES, get_meta

# IDENT text is otherwise never exported, even when it looks like a keyword.
MPP_WORDS = frozenset(('ROOTPARTITION', 'SPLIT', 'EXCHANGE', 'FILL', 'MISSING',
                       'FIELDS', 'ERRORS', 'REJECT', 'DISTRIBUTED', 'PARTITIONS'))


def file_sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def signature(raw):
    """Grammar labels only. A feature is not proof of whole-input validity."""
    if len(raw) > MAX_BYTES:
        return dict(features=['size_limit'], token_head=[], token_tail=[])
    sql = raw.decode('utf-8', 'surrogateescape')
    try:
        tokens = parser.scan(sql)
    except (parser.ParseError, UnicodeError):
        return dict(features=['scanner_unavailable'], token_head=[], token_tail=[])
    names = []
    for token in tokens:
        if token.name in ('SQL_COMMENT', 'C_COMMENT'):
            continue
        name = token.name
        if name == 'IDENT':
            text = sql[token.start:token.end + 1].upper()
            if text in MPP_WORDS:
                name = text
        names.append(name)
    joined = ' '.join('ANALYZE' if name == 'ANALYSE' else name for name in names)
    features = []
    for label, phrase in (('analyze_rootpartition', 'ANALYZE ROOTPARTITION'),
                          ('alter_truncate_partition', 'TRUNCATE PARTITION'),
                          ('alter_drop_partition', 'DROP PARTITION'),
                          ('alter_add_partition', 'ADD_P PARTITION'),
                          ('partition_by', 'PARTITION BY'),
                          ('legacy_with_oids', 'WITH OIDS'),
                          ('copy_fill_missing_fields', 'FILL MISSING FIELDS'),
                          ('external_table', 'EXTERNAL TABLE'),
                          ('copy_on_segment', 'ON SEGMENT')):
        if phrase in joined:
            features.append(label)
    return dict(features=features, token_head=names[:12], token_tail=names[-12:])


def audit(db, baselines=()):
    if not get_meta(db, 'collection_complete'):
        raise ValueError('collection incomplete')
    if db.execute('SELECT count(*) FROM inputs WHERE result IS NULL').fetchone()[0]:
        raise ValueError('parsing incomplete')
    groups, lengths, failed = {}, Counter(), Counter()
    query = '''SELECT id,sha256,sql,locator,result FROM inputs
        WHERE result NOT LIKE '{"state": "prototype_parsed"%' ORDER BY id'''
    for uid, digest, raw, locator, encoded in db.execute(query):
        result = json.loads(encoded)
        if result['state'] == 'prototype_parsed':
            continue
        if hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError('cached SQL hash mismatch')
        reason = result.get('reason', result.get('exception', result['state']))
        observed = signature(raw)
        key = (reason, tuple(observed['features']), tuple(result.get('categories', [])))
        count = db.execute('SELECT sum(count) FROM occurrences WHERE input_id=?', (uid,)).fetchone()[0]
        group = groups.setdefault(key, dict(reason=reason, features=observed['features'],
                                 categories=result.get('categories', []), unique_inputs=0,
                                 occurrences=0, examples=[]))
        group['unique_inputs'] += 1
        group['occurrences'] += count
        failed[reason] += 1
        lengths[len(raw)] += 1
        if len(group['examples']) < 3:
            group['examples'].append(dict(sql_sha256=digest, bytes=len(raw), locator=json.loads(locator),
                                           occurrences=count, **observed))
    regressions = []
    for baseline in baselines:
        previous = json.loads(baseline.read_text())
        checks = []
        for old in previous['records']:
            found = db.execute('SELECT result FROM inputs WHERE sha256=?', (old['sql_sha256'],)).fetchone()
            result = json.loads(found[0]) if found else None
            if result:
                result.pop('categories', None)
                result.pop('lexical_issues', None)
            checks.append(dict(sql_sha256=old['sql_sha256'], present=found is not None,
                               same_complete_diagnostic=result == old['result']))
        regressions.append(dict(baseline=baseline.name, sha256=file_sha(baseline),
                                checks=checks, all_same=all(c['same_complete_diagnostic'] for c in checks)))
    return dict(purpose='Failure feature census and historical result comparison, not semantic validity classification',
                parser_context=get_meta(db, 'context'), source_sha256=file_sha(Path(__file__)),
                failure_unique_reasons=failed, failure_byte_lengths=lengths,
                failure_groups=sorted(groups.values(), key=lambda g: (-g['unique_inputs'], g['reason'])),
                baseline_regressions=regressions)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--database', type=Path, default=ROOT / 'var/parser-probe/full-scan.sqlite')
    ap.add_argument('--baseline', type=Path, action='append', default=[])
    ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args()
    database = args.database.resolve()
    if (ROOT / 'var').resolve() not in database.parents:
        ap.error('raw database must stay under ignored repository var/')
    if args.output.resolve() == database:
        ap.error('output conflicts with database')
    with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as db:
        result = audit(db, args.baseline)
    result['database_sha256'] = file_sha(database)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
    print(json.dumps(dict(failure_unique_reasons=result['failure_unique_reasons'],
                         baseline_all_same=all(r['all_same'] for r in result['baseline_regressions']))))


if __name__ == '__main__':
    main()
