-- Run as sfmon with the installed authenticated psql; see PERFORMANCE.html.
-- Required psql variables: since (timestamp with offset), program_version, program_commit.
-- No raw SQL, filenames, error messages, connection details or credentials are selected.
\set ON_ERROR_STOP on
\pset format unaligned
\pset tuples_only on
BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET LOCAL statement_timeout = '5min';
SET LOCAL lock_timeout = '3s';
SET LOCAL TIME ZONE 'Asia/Hong_Kong';
WITH wanted_runs AS MATERIALIZED (
    SELECT run_id, started_at, finished_at, state, failed
    FROM sql_apm.mpp_daily_run
    WHERE started_at >= :'since'::timestamptz
), days AS MATERIALIZED (
    SELECT d.run_id, d.scope_id, d.source_id, d.log_date, d.batch_id,
           d.state, d.file_count, d.byte_count, d.added_records, d.seconds,
           r.started_at AS run_started_at, r.finished_at AS run_finished_at
    FROM wanted_runs r JOIN sql_apm.mpp_daily_day d USING (run_id)
), day_totals AS (
    SELECT run_id, scope_id, count(*) AS day_source_count,
           count(DISTINCT log_date) AS log_day_count,
           sum(seconds) AS day_seconds, sum(file_count) AS file_count,
           sum(byte_count) AS byte_count, sum(added_records) AS added_records
    FROM days GROUP BY run_id, scope_id
), attempts AS (
    SELECT d.run_id, d.scope_id, d.source_id, d.log_date,
           row_number() OVER (PARTITION BY d.run_id,d.scope_id,d.source_id,d.log_date
                              ORDER BY a.started_at,a.attempt_id) AS attempt_number,
           a.state, a.started_at, a.finished_at,
           extract(epoch FROM a.finished_at-a.started_at) AS before_commit_seconds,
           a.reliable_record_count, f.byte_count
    FROM days d JOIN sql_apm.import_attempt a
        ON a.batch_id=d.batch_id AND a.scope_id=d.scope_id
        AND a.started_at>=d.run_started_at
        AND (d.run_finished_at IS NULL OR a.started_at<=d.run_finished_at)
    JOIN sql_apm.source_file f ON f.file_id=a.file_id AND f.scope_id=a.scope_id
), scope_texts AS (
    -- Authoritative complete texts observed in each cluster, including unsupported fingerprints.
    -- This scans event keys, never mpp_query_record, and does not read text/TOAST.
    SELECT scope_id, count(DISTINCT sql_id) AS distinct_complete_texts
    FROM sql_apm.mpp_occurrence WHERE sql_id IS NOT NULL GROUP BY scope_id
)
SELECT jsonb_pretty(jsonb_build_object(
    'format','sql-apm-import-breakdown/2',
    'collected_at',clock_timestamp(), 'snapshot_at',transaction_timestamp(),
    'since',:'since'::timestamptz,
    'identity',jsonb_build_object('program_version',:'program_version',
        'program_commit',:'program_commit',
        'schema_version',(SELECT version FROM sql_apm.schema_version
                          ORDER BY string_to_array(version,'.')::int[] DESC LIMIT 1)),
    'stats_reset',(SELECT stats_reset FROM pg_stat_database WHERE datname=current_database()),
    'postmaster_started_at',pg_postmaster_start_time(),
    'run_count',(SELECT count(*) FROM wanted_runs),
    'runs',coalesce((SELECT jsonb_agg(to_jsonb(r) ORDER BY r.started_at,r.run_id) FROM wanted_runs r),'[]'::jsonb),
    'counters',coalesce((
        SELECT jsonb_agg(jsonb_build_object('relname',s.relname,'seq_scan',s.seq_scan,
            'seq_tup_read',s.seq_tup_read,'n_tup_ins',s.n_tup_ins,'n_live_tup',s.n_live_tup) ORDER BY s.relname)
        FROM pg_stat_user_tables s
        WHERE s.schemaname='sql_apm' AND s.relname IN ('mpp_query_record','mpp_query_pending')),'[]'::jsonb),
    'text_snapshot',jsonb_build_object(
        'global_distinct_texts',(SELECT item_count FROM sql_apm.mpp_sql_text_count WHERE singleton),
        'by_scope',coalesce((SELECT jsonb_agg(to_jsonb(t) ORDER BY t.scope_id) FROM scope_texts t),'[]'::jsonb)),
    'stage_reconciliation',coalesce((
        SELECT jsonb_agg(jsonb_build_object(
            'run_id',c.run_id,'scope_id',c.scope_id,'state',c.state,'import_state',c.import_state,
            'import_seconds',(c.stage_seconds->>'import')::double precision,
            'sum_day_seconds',coalesce(t.day_seconds,0),
            'outside_day_seconds',(c.stage_seconds->>'import')::double precision-coalesce(t.day_seconds,0),
            'day_source_count',coalesce(t.day_source_count,0),'log_day_count',coalesce(t.log_day_count,0),
            'file_count',coalesce(t.file_count,0),'byte_count',coalesce(t.byte_count,0),
            'added_records',coalesce(t.added_records,0)) ORDER BY c.run_id,c.scope_id)
        FROM wanted_runs r JOIN sql_apm.mpp_daily_cluster c USING(run_id)
        LEFT JOIN day_totals t USING(run_id,scope_id)), '[]'::jsonb),
    'days',coalesce((SELECT jsonb_agg(to_jsonb(d)-'batch_id'-'run_started_at'-'run_finished_at'
        ORDER BY d.run_id,d.scope_id,d.log_date,d.source_id) FROM days d),'[]'::jsonb),
    'attempts',coalesce((SELECT jsonb_agg(to_jsonb(a)
        ORDER BY a.run_id,a.scope_id,a.log_date,a.source_id,a.attempt_number) FROM attempts a),'[]'::jsonb)
));
ROLLBACK;
