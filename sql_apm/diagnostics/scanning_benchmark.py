"""Single-process parse timings for eight explicitly located private inputs."""
import argparse
import importlib.util
import json
from pathlib import Path
import platform
import sqlite3
import sys
import time

from sql_apm.diagnostics.scanning_equivalence import equal_results, sha256, save
from sql_apm.sql import mpp_parser
from sql_apm.sql.structure import dumps
import hashlib


def run(args):
    if args.output.exists():
        raise ValueError('fresh_output_required')
    selection = json.loads(args.selection.read_text())
    samples = selection['samples']
    if len(samples) != 8 or len({row['id'] for row in samples}) != 8:
        raise ValueError('eight_distinct_inputs_required')
    source = args.reference / 'sql_apm/sql/mpp_parser.py'
    spec = importlib.util.spec_from_file_location('_timing_reference', str(source))
    old = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = old
    spec.loader.exec_module(old)
    rows = []
    with sqlite3.connect(args.index.resolve().as_uri() + '?mode=ro', uri=True) as db:
        for sample in samples:
            record = db.execute('SELECT sha256,sql FROM inputs WHERE id=?', (sample['id'],)).fetchone()
            if record is None or record[0] != sample['sha256'] or hashlib.sha256(record[1]).hexdigest() != record[0]:
                raise ValueError('sample_checksum_mismatch')
            sql = record[1].decode('utf-8', 'strict')
            before, after = [], []
            for _ in range(args.repetitions):
                started = time.perf_counter()
                previous = old.parse(sql)
                before.append(time.perf_counter() - started)
                started = time.perf_counter()
                current = mpp_parser.parse(sql)
                after.append(time.perf_counter() - started)
                if not equal_results(previous, current):
                    raise ValueError('parse_structure_difference')
            rows.append(dict(id=sample['id'], sha256=record[0], bytes=len(record[1]),
                             characters=len(sql), non_ascii=sum(not c.isascii() for c in sql),
                             before_seconds=before, after_seconds=after,
                             structure_sha256=hashlib.sha256(dumps(current).encode()).hexdigest()))
    report = dict(method='measured; single process; alternating old/new complete parse; no product timeout',
                  selection=selection, selection_sha256=sha256(args.selection),
                  reference_sha256=sha256(source),
                  candidate_sha256=sha256(Path(mpp_parser.__file__)),
                  scanner_sha256=sha256(Path(mpp_parser.__file__).with_name('scanning.py')),
                  python=platform.python_version(), platform=platform.platform(),
                  repetitions=args.repetitions, samples=rows, structures_equal=True)
    save(args.output, report)
    print(json.dumps(dict(samples=len(rows), structures_equal=True)))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--index', type=Path, required=True)
    parser.add_argument('--reference', type=Path, required=True)
    parser.add_argument('--selection', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--repetitions', type=int, choices=range(1, 6), default=3)
    try:
        run(parser.parse_args())
    except Exception:
        print(json.dumps(dict(reason='scanning_benchmark_failed')))
        raise SystemExit(1) from None
