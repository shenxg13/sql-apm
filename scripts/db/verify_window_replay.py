#!/usr/bin/env python3
"""Small end-to-end check of the #35 replay and comparison harness itself."""
import argparse
from contextlib import redirect_stdout
import io
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT/'tests')]
from ingestion.test_reader import row, write_csv
from verify_window_full import scenario, compare
from verify_mpp_naming_full import digest, save


def verify(reference, pg_bin):
    with tempfile.TemporaryDirectory(prefix='window-replay-') as temporary:
        root = Path(temporary)
        manifest, bounds = dict(clusters={}), {}
        for scope, dates in {
            '119':['2026-07-01','2026-07-28','2026-07-29','2026-07-30','2026-07-31'],
            '120':['2026-09-16','2026-09-17','2026-09-18','2026-09-19'],
        }.items():
            (root/scope).mkdir()
            files = []
            for day in dates:
                path = root/scope/('gpdb-'+day+'_000000.csv')
                write_csv(path, [row(text=text,message='duration: 0 ms',**{'0':day+' 12:00:00 CST'})
                                 for text in ('SELECT 1','SELECT * FROM (')])
                files.append(dict(file=path.name,sha256=digest(path)))
                bounds[path.name] = dict(first=day+'T12:00:00+08:00',last=day+'T12:00:00+08:00')
            manifest['clusters'][scope] = dict(files=files)
        reports = []
        for candidate, app in ((False,reference),(True,ROOT)):
            output = root/('candidate' if candidate else 'reference'); output.mkdir()
            args = SimpleNamespace(app_root=app.resolve(),output=output,logs=root,pg_bin=pg_bin,
                                   workers=1,candidate=candidate)
            report = dict(candidate=candidate,complete=True,manifest_sha256='synthetic',bounds=bounds)
            for label, daily in [('nine',False),('daily',True)]:
                report[label] = scenario(args,manifest,bounds,daily,expected_outside=2)
            path = output/'report.json'; save(path,report); reports.append(path)
        compare(reports)
        # A changed metric digest must be rejected, even with identical counts.
        report['daily']['daily-119-rebuild']['equivalence']['formal']['sha256'] = 'changed'
        save(reports[-1],report)
        try:
            with redirect_stdout(io.StringIO()): compare(reports)
        except AssertionError: pass
        else: raise AssertionError('changed_metric_was_accepted')
        print('PASS: nine-task/daily replay, transaction boundaries, stable comparison and changed-metric rejection')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference',type=Path,required=True)
    parser.add_argument('--pg-bin',type=Path,default=Path('/usr/pgsql-17/bin'))
    args=parser.parse_args()
    verify(args.reference,args.pg_bin)
