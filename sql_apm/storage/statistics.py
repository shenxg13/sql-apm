"""Snapshot-bound builds, TEMP decisions, bounded result writes and atomic finish."""
from collections import Counter
import csv
import io
import itertools
import json
import os
import subprocess
import sys
import time
import uuid

from psycopg2.extras import Json

from sql_apm.baseline.statistics import METRICS, calculate_group
from sql_apm.storage.ingestion import connect
from sql_apm.training.config import TZ

TIMINGS = ('request', 'execute_first', 'execute_fetch', 'parse', 'bind')
STAT_COLUMNS = ('build_id', 'group_id', 'partition_id', 'layer', 'bucket_date', 'bucket_number',
    'range_start', 'range_end', 'partial_week', 'included_count', 'excluded_count',
    'exclusions_by_reason', 'active_dates', 'active_week_starts', 'first_sample_at', 'last_sample_at') + METRICS + ('metric_null_reasons',)
COVER_COLUMNS = ('build_id', 'group_id', 'partition_id', 'layer', 'computed_keys', 'empty_keys')


class StatisticsError(ValueError):
    """Public fixed code; never disclose PostgreSQL error text or identities."""


def json_value(value):
    return json.dumps(value, default=lambda item: item.isoformat(), separators=(',', ':'), sort_keys=True)


class ResultWriter:
    def __init__(self, db, table, columns):
        self.db, self.table, self.columns = db, table, columns
        self.rows = []

    def add(self, row):
        self.rows.append(row)
        if len(self.rows) >= 2000:
            self.flush()

    def flush(self):
        if not self.rows:
            return
        data = io.StringIO()
        writer = csv.writer(data, lineterminator='\n')
        for row in self.rows:
            values = []
            for name in self.columns:
                value = row[name]
                if value is None:
                    value = r'\N'
                elif name in ('active_dates', 'active_week_starts'):
                    value = '{' + ','.join(d.isoformat() for d in value) + '}'
                elif isinstance(value, (dict, list)):
                    value = json_value(value)
                values.append(value)
            writer.writerow(values)
        data.seek(0)
        with self.db.cursor() as cur:
            cur.copy_expert('COPY ' + self.table + ' (' + ','.join(self.columns) +
                            ") FROM STDIN WITH (FORMAT csv, NULL '\\N')", data)
        self.rows.clear()


class StatisticsStore:
    def __init__(self, dsn='', schema='sql_apm'):
        self.dsn, self.schema = dsn, schema
        self.db = connect(dsn, schema)

    def close(self):
        self.db.close()

    def _create(self, scope, input_id, config_id, retry_of):
        with self.db, self.db.cursor() as cur:
            cur.execute('''SELECT c.normalization_id,c.profile,c.window_start,c.window_end,
                       c.statistics_version,training_version(t.decision_version)
                FROM input_snapshot i JOIN input_manifest m USING(input_id)
                JOIN config_snapshot c USING(scope_id) JOIN training_config t USING(config_id)
                WHERE i.input_id=%s AND c.config_id=%s AND c.scope_id=%s
                  AND EXISTS (SELECT FROM input_file_analysis f WHERE f.input_id=i.input_id)''',
                (input_id, config_id, scope))
            row = cur.fetchone()
            if not row:
                raise StatisticsError('sealed_snapshot_pair_required')
            normalization, profile, start, end, version, _ = row
            if profile != 'hashdata-csv/1' or version != 'baseline-formulas/1':
                raise StatisticsError('unsupported_statistics_context')
            if retry_of:
                cur.execute('SELECT state,scope_id,input_id,config_id FROM build WHERE build_id=%s', (retry_of,))
                previous = cur.fetchone()
                if not previous or previous[0] not in ('failed', 'interrupted') or previous[1:] != (scope, input_id, config_id):
                    raise StatisticsError('invalid_retry_reference')
            cur.execute('SELECT clock_timestamp()')
            now = cur.fetchone()[0].astimezone(TZ)
            build_id = 'B:' + now.strftime('%Y%m%dT%H%M%S') + ':' + uuid.uuid4().hex
            cur.execute('SELECT mpp_ensure_result_partition(%s,%s)', (scope, now.date().replace(day=1)))
            partition = cur.fetchone()[0]
            cur.execute('''INSERT INTO build (build_id,scope_id,input_id,config_id,normalization_id,profile,
                retry_of,state,started_at,results_saved,partition_id)
                VALUES (%s,%s,%s,%s,%s,%s,%s,'running',%s,false,%s)''',
                (build_id,scope,input_id,config_id,normalization,profile,retry_of,now,partition))
        return build_id, partition, start, end

    def _failure(self, build_id, state, reason):
        self.db.rollback()
        with self.db, self.db.cursor() as cur:
            cur.execute("UPDATE build SET state=%s,finished_at=clock_timestamp(),results_saved=false WHERE build_id=%s AND state='running'", (state, build_id))
            if cur.rowcount:
                cur.execute('''INSERT INTO problem (problem_id,level,build_id,code,reason,effect,count_unit,count,resolution)
                    VALUES (%s,'build',%s,%s,%s,'block_publication','problem',1,'open')''',
                    ('P:'+uuid.uuid4().hex,build_id,reason,reason))

    def calculate(self, scope, input_id, config_id, retry_of=None, progress=None):
        started = time.monotonic()
        build_id, partition, start, end = self._create(scope,input_id,config_id,retry_of)
        # EOF is detected even on SIGKILL/os._exit. This independent connection
        # never reads SQL text and only changes a still-running, unsaved build.
        env = dict(os.environ, SQL_APM_DSN=self.dsn)
        watcher = None
        try:
            watcher = subprocess.Popen([sys.executable, '-m', 'sql_apm.baseline.watchdog',
                                        self.schema, build_id], stdin=subprocess.PIPE,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env, start_new_session=True)
            if progress:
                progress(dict(phase='build_created',build_id=build_id))
            with self.db, self.db.cursor() as cur:
                cur.execute('''CREATE TEMP TABLE statistics_decisions ON COMMIT DROP AS
                    SELECT analysis_id,occurrence_id,group_id,fingerprint_id,timing_type,state,count_scope,
                           reason_codes,estimated_start_at,duration_ms
                    FROM mpp_training_decisions(%s,%s)''', (input_id,config_id))
                cur.execute('ANALYZE statistics_decisions')
                summary = {}
                for label, query in {
                    'states': 'SELECT state,count(*) FROM statistics_decisions GROUP BY 1 ORDER BY 1',
                    'count_scopes': 'SELECT count_scope,state,count(*) FROM statistics_decisions GROUP BY 1,2 ORDER BY 1,2',
                    'reasons': 'SELECT count_scope,reason,count(*) FROM statistics_decisions CROSS JOIN LATERAL unnest(reason_codes) reason GROUP BY 1,2 ORDER BY 1,2',
                }.items():
                    cur.execute(query)
                    summary[label] = cur.fetchall()
                cur.execute('''INSERT INTO mpp_baseline_group
                    SELECT d.group_id,b.scope_id,b.profile,b.normalization_id,o.database,o.execution_user,
                           d.fingerprint_id,f.value,d.timing_type
                    FROM (SELECT DISTINCT ON (group_id) group_id,analysis_id,occurrence_id,fingerprint_id,timing_type
                          FROM statistics_decisions WHERE count_scope='group'
                          ORDER BY group_id,analysis_id,occurrence_id) d
                    JOIN mpp_occurrence o USING(analysis_id,occurrence_id)
                    JOIN mpp_fingerprint f USING(fingerprint_id) CROSS JOIN build b
                    WHERE b.build_id=%s ON CONFLICT (group_id) DO NOTHING''', (build_id,))
                cur.execute('''INSERT INTO mpp_build_group
                    SELECT %s,%s,group_id FROM statistics_decisions WHERE count_scope='group' GROUP BY group_id''',
                    (partition,build_id))
                groups = cur.rowcount
                if progress:
                    progress(dict(phase='decisions_derived',build_id=build_id,groups=groups,
                                  seconds=round(time.monotonic()-started,3)))
                stats = ResultWriter(self.db,'mpp_statistic',STAT_COLUMNS)
                covers = ResultWriter(self.db,'mpp_build_coverage',COVER_COLUMNS)
                layer_counts, timing_counts = Counter(), {t:[0,0] for t in TIMINGS}
                processed = 0
                with self.db.cursor(name='statistics_groups') as stream:
                    stream.itersize = 4000
                    stream.execute('''SELECT group_id,timing_type,estimated_start_at,duration_ms,state='included',reason_codes
                        FROM statistics_decisions WHERE count_scope='group'
                        ORDER BY group_id,estimated_start_at,duration_ms,state,reason_codes''')
                    for gid, items in itertools.groupby(stream, key=lambda row: row[0]):
                        events = list(items)
                        timing = events[0][1]
                        computed, coverage = calculate_group((row[2:] for row in events),start,end)
                        context = dict(build_id=build_id,group_id=gid,partition_id=partition)
                        for row in computed:
                            stats.add(dict(row,**context))
                            layer_counts[row['layer']] += 1
                            if row['layer'] == 'overall':
                                timing_counts[timing][0] += row['included_count']
                                timing_counts[timing][1] += row['excluded_count']
                        for row in coverage:
                            covers.add(dict(row,**context))
                        processed += 1
                        if progress and processed % 10000 == 0:
                            progress(dict(phase='statistics_progress',build_id=build_id,
                                          groups=processed,total_groups=groups))
                stats.flush()
                covers.flush()
                for timing,(included,excluded) in timing_counts.items():
                    cur.execute('INSERT INTO mpp_build_timing_coverage VALUES (%s,%s,%s,%s)',
                                (build_id,timing,included,excluded))
                summary.update(groups=groups,layers=dict(layer_counts),timings=timing_counts)
                cur.execute('UPDATE build SET diagnostics=%s WHERE build_id=%s',(Json(summary),build_id))
                if progress:
                    progress(dict(phase='results_written',build_id=build_id,groups=groups))
                cur.execute("UPDATE build SET state='calculated',results_saved=true,finished_at=clock_timestamp() WHERE build_id=%s",(build_id,))
            return dict(build_id=build_id,state='calculated',results_saved=True,
                        seconds=round(time.monotonic()-started,3),**summary)
        except BaseException as error:
            interrupted = isinstance(error, (KeyboardInterrupt, SystemExit))
            state, reason = ('interrupted','operator_interrupt') if interrupted else ('failed','statistics_calculation_failed')
            try:
                self._failure(build_id,state,reason)
            except Exception:
                # The watchdog reconnects when this connection has failed.
                pass
            if interrupted:
                raise
            raise StatisticsError(reason) from None
        finally:
            if watcher is not None:
                watcher.stdin.close()
                try:
                    watcher.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    # Reconnection continues independently for a bounded period.
                    pass
