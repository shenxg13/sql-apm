#!/usr/bin/env python3
"""Bounded category replay of the saved 55-file original index; no SQL output."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
import types

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from sql_apm.sql.lexical import diagnose
from sql_apm.training import categories

BASELINE = 'b0cc66a495e27b071d25a171c9f517f095841658'
INDEX_SHA256 = 'b50c2e2660dab8c4d22f9c1e90a1640555c4caadcc0ed96589dcc97f46db59b2'


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def verify(index, output):
    if output.exists():
        raise ValueError("fresh_output_required")
    started = time.monotonic()
    assert digest(index) == INDEX_SHA256, 'unexpected_source_index'
    manifest_path = ROOT / 'docs/reports/data/log-supplement-manifest-2026-09-28.json'
    manifest = json.loads(manifest_path.read_text())
    before = subprocess.check_output(['git', 'show', BASELINE+':sql_apm/training/categories.py'], cwd=ROOT)
    old = types.ModuleType('training_categories_v1')
    exec(compile(before, '<frozen-training-categories-v1>', 'exec'), old.__dict__)
    # Only the category module changed; the prefilter and full parse dependency
    # must retain their measured meaning across both sides of this replay.
    dependencies = ['sql_apm/sql/lexical.py', 'sql_apm/sql/mpp_parser.py']
    for name in dependencies:
        assert subprocess.check_output(['git', 'show', BASELINE+':'+name], cwd=ROOT) == (ROOT/name).read_bytes()
    db = sqlite3.connect(index.resolve().as_uri()+'?mode=ro', uri=True)
    try:
        assert json.loads(db.execute("SELECT value FROM meta WHERE key='collection_complete'").fetchone()[0]) is True
        files = [json.loads(r[0]) for r in db.execute('SELECT evidence FROM files')]
        expected = {(scope,f['file'],f['sha256'],f['bytes']) for scope,details in manifest['clusters'].items() for f in details['files']}
        actual = {(f['cluster'],f['file'],f['sha256'],f['bytes']) for f in files}
        assert actual == expected and len(files) == 55
        assert all(f['read_to_eof'] and f['stat_unchanged'] and f['full_file_hash_rechecked'] for f in files)
        total = db.execute('SELECT count(*) FROM inputs').fetchone()[0]
        assert total == 1497418
        candidates = parsed = changed = 0
        transitions, changed_occurrences = Counter(), Counter()
        # Only newly deferred parsed names can change the result. Their literal
        # spellings contain "transaction" or "session". Unicode-escaped quoted
        # identifiers use U&"..."; keep every ampersand input as a safe superset.
        # Comments may separate tokens but cannot split a keyword/name token.
        # An actual SET keyword is also required. This filters false positives
        # only; the unchanged lexer and complete parser still make the decision.
        query="""SELECT id,sql FROM inputs
            WHERE instr(lower(CAST(sql AS TEXT)),'set')>0 AND
              (instr(lower(CAST(sql AS TEXT)),'transaction')>0 OR
               instr(lower(CAST(sql AS TEXT)),'session')>0 OR instr(CAST(sql AS TEXT),'&')>0)"""
        for input_id, raw in db.execute(query):
            candidates += 1
            text = raw.decode('utf-8', 'surrogateescape') if isinstance(raw, bytes) else raw
            sequence, issues = diagnose(text)
            if issues or 'SET' not in sequence:
                continue
            parsed += 1
            prior, current = old.classify(text), categories.classify(text)
            assert prior == old.classify(text, grammar_verified=True)
            assert current == categories.classify(text, grammar_verified=True)
            if prior != current:
                changed += 1
                transitions[(prior['kind'],current['kind'])] += 1
                # These are raw SQL-bearing field occurrences, not executions.
                for field,count in db.execute('SELECT field,sum(count) FROM occurrences WHERE input_id=? GROUP BY field',(input_id,)):
                    changed_occurrences[field] += count
        report = dict(baseline_head=BASELINE,index_sha256=INDEX_SHA256,
            manifest_sha256=digest(manifest_path),files=len(files),originals=total,
            prefilter="SET and (transaction or session or ampersand); ASCII case-insensitive",
            substring_candidates=candidates,parsed_set_originals=parsed,changed_originals=changed,
            transitions=[dict(before=a,after=b,count=n) for (a,b),n in sorted(transitions.items())],
            changed_raw_field_occurrences=dict(changed_occurrences),
            category_versions=[old.VERSION,categories.VERSION],
            code_sha256={name:digest(ROOT/name) for name in ['sql_apm/training/categories.py',__file__.removeprefix(str(ROOT)+'/')]+dependencies},
            before_category_sha256=hashlib.sha256(before).hexdigest(),seconds=round(time.monotonic()-started,3))
        report["complete"] = True
        with output.open("x") as stream:
            stream.write(json.dumps(report,indent=2,sort_keys=True)+'\n')
        print(json.dumps(report,sort_keys=True))
    finally:
        db.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--index',type=Path,default=ROOT/'var/parser-probe/issue13/full-scan.sqlite')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    verify(args.index,args.output)
