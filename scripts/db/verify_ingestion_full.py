#!/usr/bin/env python3
"""Explicit 55-file acceptance. Private local data only; public output is aggregate."""
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from verify import instance, Verification
from sql_apm.ingestion.config import load_config, canonical, identity
from sql_apm.ingestion.importer import Importer
from sql_apm.sql.normalization import Normalizer


class MemoryMonitor:
    def __init__(self, pg_pid):
        self.stop = threading.Event()
        self.pg_pid = pg_pid
        self.peaks = Counter()
        self.thread = threading.Thread(target=self.sample, daemon=True)

    def sample(self):
        while not self.stop.is_set():
            processes = {}
            for path in Path('/proc').glob('[0-9]*/status'):
                try:
                    fields = dict(line.split(':', 1) for line in path.read_text().splitlines() if ':' in line)
                    processes[int(path.parent.name)] = (int(fields['PPid']), int(fields.get('VmRSS', '0 kB').split()[0]))
                except (OSError, ValueError, KeyError):
                    pass
            for root, label in [(os.getpid(), 'importer_tree_rss_kib'), (self.pg_pid, 'postgres_tree_rss_kib')]:
                selected = {root}
                while True:
                    extra = {pid for pid, (ppid, rss) in processes.items() if ppid in selected} - selected
                    if not extra:
                        break
                    selected.update(extra)
                value = sum(processes.get(pid, (0, 0))[1] for pid in selected)
                self.peaks[label] = max(self.peaks[label], value)
            self.stop.wait(0.5)


def main(args):
    args.output.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(args.manifest.read_text())
    declaration = {'version': 1, 'clusters': sorted(manifest['clusters']), 'sources': {}, 'batches': {}}
    inputs = []
    for cluster, details in sorted(manifest['clusters'].items()):
        source, batch = 'full-' + cluster, 'full-import-' + cluster
        declaration['sources'][source] = dict(cluster=cluster, build='HashData Warehouse 3.13.13',
                                             timezone='UTC+08:00', declaration='Issue18-confirmed-local-master-files')
        entries = []
        for file in sorted(details['files'], key=lambda f: f['file']):
            entries.append(dict(path=str((args.root / cluster / file['file']).resolve()), closed_and_copied=True,
                                origin_key=cluster + '/' + file['file']))
            inputs.append(dict(cluster=cluster, sha256=file['sha256'], bytes=file['bytes']))
        declaration['batches'][batch] = dict(source=source, files_confirmed_complete=True,
                dates=sorted({entry['file'][5:15] for entry in details['files']}), files=entries)
    cfg = args.output / 'config.json'
    cfg.write_text(canonical(declaration))
    source_paths = sorted(set(Path('sql_apm').glob('*.py')) | set(Path('sql_apm/ingestion').rglob('*.py')) |
                          set(Path('sql_apm/storage').glob('*.py')) | set(Path('sql_apm/sql').glob('*.py')))
    report = dict(code_sha256={str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in source_paths},
                  normalization_context=Normalizer().context, inputs=inputs, manifest_sha256=hashlib.sha256(args.manifest.read_bytes()).hexdigest(),
                  method='private PG17, product importer, file transactions, sampled RSS every 0.5s',
                  first_runs=[], repeat_runs=[])
    with instance(args.pg_bin) as (directory, env):
        v = Verification(args.pg_bin, directory, env)
        v.init()
        pid = int((directory / 'data/postmaster.pid').read_text().splitlines()[0])
        monitor = MemoryMonitor(pid)
        monitor.thread.start()
        dsn = 'host=' + str(directory / 'socket') + ' port=55473 dbname=sql_apm user=sql_apm'
        report['database_bytes_before'] = int(v.sql('SELECT pg_database_size(current_database())'))
        started = time.monotonic()
        try:
            importer = Importer(dsn, workers=args.workers)
            try:
                for cluster in sorted(manifest['clusters']):
                    result = importer.run(load_config(cfg, 'full-' + cluster, 'full-import-' + cluster))
                    report['first_runs'].append(dict(cluster=cluster, **result))
                    (args.output / 'progress.json').write_text(canonical(report))
                    expected = {'I:' + identity('full-' + cluster, f['sha256']) for f in manifest['clusters'][cluster]['files']}
                    if {f['file_id'] for f in result['files']} != expected:
                        raise RuntimeError('source_checksums_differ')
                    if result['state'] != 'complete':
                        raise RuntimeError('full_import_failed')
                report['first_seconds'] = round(time.monotonic() - started, 3)
                report['database_bytes_after'] = int(v.sql('SELECT pg_database_size(current_database())'))
                report['database_growth_bytes'] = report['database_bytes_after'] - report['database_bytes_before']
                queries = {
                    'table_counts': "SELECT jsonb_object_agg(name,n) FROM (SELECT 'records' name,count(*) n FROM evidence_record UNION ALL SELECT 'occurrences',count(*) FROM mpp_occurrence UNION ALL SELECT 'sql_texts',count(*) FROM mpp_sql_text UNION ALL SELECT 'approximate_inputs',count(*) FROM mpp_approximate_input UNION ALL SELECT 'problems',count(*) FROM problem) t",
                    'timings': "SELECT jsonb_object_agg(k,n) FROM (SELECT scope_id||':'||coalesce(timing_type,'unknown') k,count(*) n FROM mpp_occurrence GROUP BY 1) t",
                    'execute_pairing': "SELECT jsonb_object_agg(k,n) FROM (SELECT scope_id||':'||coalesce(timing_type,'unknown') k,count(*) n FROM mpp_occurrence WHERE unit='call' AND (timing_type IN ('execute_first','execute_fetch') OR timing_type IS NULL) GROUP BY 1) t",
                    'unpaired_examples': "SELECT coalesce(jsonb_agg(t),'[]') FROM (SELECT e.file_id,e.record_no,o.association_reason FROM mpp_occurrence o JOIN evidence_record e ON e.record_id=o.anchor_ref WHERE o.unit='call' AND o.timing_type IS NULL ORDER BY e.file_id,e.record_no LIMIT 20) t",
                    'outcomes': "SELECT jsonb_object_agg(k,n) FROM (SELECT scope_id||':'||outcome k,count(*) n FROM mpp_occurrence GROUP BY 1) t",
                    'association': "SELECT jsonb_object_agg(k,n) FROM (SELECT scope_id||':'||association_state||':'||coalesce(association_reason,'paired_or_direct') k,count(*) n FROM mpp_occurrence GROUP BY 1) t",
                    'sql_states': "SELECT jsonb_object_agg(k,n) FROM (SELECT sql_state k,count(*) n FROM mpp_occurrence GROUP BY 1) t",
                    'problem_codes': "SELECT jsonb_object_agg(k,n) FROM (SELECT code k,count(*) n FROM problem GROUP BY 1) t",
                    'fingerprints': "SELECT jsonb_object_agg(k,n) FROM (SELECT state||':'||coalesce(reason,'reliable') k,count(*) n FROM mpp_fingerprint GROUP BY 1) t",
                    'approximate': "SELECT jsonb_object_agg(k,n) FROM (SELECT state||':'||coalesce(reason,'available')||':'||structural_reason k,count(*) n FROM mpp_approximate_result GROUP BY 1) t",
                    'relation_bytes': "SELECT jsonb_object_agg(relname,pg_total_relation_size(relid)) FROM pg_stat_user_tables",
                }
                for label, query in queries.items():
                    report[label] = json.loads(v.sql(query))
                # Compare full byte-hash identity sets; no SQL leaves the private databases.
                audit = sqlite3.connect(str(args.output / 'identity-audit.sqlite'))
                audit.execute('CREATE TABLE stored (sha TEXT PRIMARY KEY, kind TEXT, state TEXT)')
                with importer.sql_db.cursor(name='identity_scan') as cur:
                    cur.itersize = 10000
                    cur.execute("SELECT encode(s.content_sha256,'hex'),'complete',f.state FROM mpp_sql_text s JOIN mpp_fingerprint f USING(sql_id) UNION SELECT encode(i.source_sha256,'hex'),'fragment',r.structural_reason FROM mpp_approximate_input i JOIN mpp_approximate_result r USING(input_id)")
                    for row in cur:
                        audit.execute('INSERT OR IGNORE INTO stored VALUES (?,?,?)', row)
                importer.sql_db.commit()
                audit.execute('ATTACH DATABASE ? AS historical', (str(args.index.resolve()),))
                report['identity_comparison'] = {
                    'historical': audit.execute('SELECT count(*) FROM historical.inputs').fetchone()[0],
                    'stored_unique': audit.execute('SELECT count(*) FROM stored').fetchone()[0],
                    'historical_missing': audit.execute('SELECT count(*) FROM historical.inputs h LEFT JOIN stored s ON h.sha256=s.sha WHERE s.sha IS NULL').fetchone()[0],
                    'stored_extra': audit.execute('SELECT count(*) FROM stored s LEFT JOIN historical.inputs h ON h.sha256=s.sha WHERE h.id IS NULL').fetchone()[0],
                    'missing_by_first_field': dict(audit.execute("SELECT json_extract(h.locator,'$.field'),count(*) FROM historical.inputs h LEFT JOIN stored s ON h.sha256=s.sha WHERE s.sha IS NULL GROUP BY 1")),
                    'stored_states': dict(audit.execute('SELECT kind||\':\'||state,count(*) FROM stored GROUP BY 1')),
                }
                audit.commit()
                audit.close()
                for cluster in sorted(manifest['clusters']):
                    result = importer.run(load_config(cfg, 'full-' + cluster, 'full-import-' + cluster))
                    if result['added_records'] or result['added_occurrences'] or result['state'] != 'complete':
                        raise RuntimeError('idempotency_failed')
                    report['repeat_runs'].append(dict(cluster=cluster, **result))
            finally:
                importer.close()
        finally:
            monitor.stop.set()
            monitor.thread.join()
            report['peak_memory'] = dict(monitor.peaks)
            report['total_seconds'] = round(time.monotonic() - started, 3)
            report['complete'] = len(report['repeat_runs']) == len(manifest['clusters'])
            (args.output / 'report.json').write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
    print(canonical(dict(complete=report['complete'], seconds=report['total_seconds'], peak_memory=report['peak_memory'])))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--index', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--pg-bin', type=Path, default=Path('/usr/pgsql-17/bin'))
    main(parser.parse_args())
