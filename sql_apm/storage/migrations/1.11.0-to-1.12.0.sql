SELECT set_config('search_path',quote_ident(current_setting('apm.schema'))||',pg_catalog',true);
-- 1.12.0: records of the daily automatic run. One row per execution of the daily
-- command, then what it saw and did for each configured cluster. Task, build and
-- publication identifiers are plain text: these rows only describe, never own.
CREATE TABLE IF NOT EXISTS mpp_daily_run (
    run_id text PRIMARY KEY CHECK (run_id <> ''),
    started_by text NOT NULL CHECK (started_by IN ('timer','manual')),
    state text NOT NULL CHECK (state IN ('running','finished','aborted','unfinished')),
    failed boolean,
    reason text CHECK (reason <> ''),
    started_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    finished_at timestamptz,
    local_date date NOT NULL,
    stale_after_hours integer NOT NULL CHECK (stale_after_hours > 0),
    CHECK ((state = 'running') = (finished_at IS NULL)),
    CHECK ((state = 'finished') = (failed IS NOT NULL)),
    CHECK (state NOT IN ('aborted','unfinished') OR reason IS NOT NULL),
    CHECK (finished_at >= started_at)
);
CREATE INDEX IF NOT EXISTS mpp_daily_run_started_idx ON mpp_daily_run (started_at);
CREATE TABLE IF NOT EXISTS mpp_daily_cluster (
    run_id text NOT NULL REFERENCES mpp_daily_run,
    scope_id text NOT NULL CHECK (scope_id <> ''),
    ordinal integer NOT NULL CHECK (ordinal >= 0),
    state text NOT NULL CHECK (state IN ('pending','running','done','skipped','aborted')),
    reason text CHECK (reason <> ''),
    failed boolean NOT NULL DEFAULT false,
    import_state text NOT NULL DEFAULT 'not_reached' CHECK (import_state IN ('not_reached','incomplete','done')),
    -- Days this run looked at and came to a conclusion about, by log source:
    -- {"S1": ["2026-10-01", ...]}. Written as the run goes, like the problems it finds,
    -- so a run that is stopped or killed has still said what it had seen.
    examined_days jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(examined_days) = 'object'),
    newest_imported date,
    build_state text NOT NULL DEFAULT 'not_reached' CHECK (build_state IN
        ('not_reached','disabled','no_data','not_due','published','no_samples','failed')),
    build_reason text CHECK (build_reason <> ''),
    cutoff_date date,
    build_id text CHECK (build_id <> ''),
    publication_id text CHECK (publication_id <> ''),
    cleanup_state text NOT NULL DEFAULT 'not_reached' CHECK (cleanup_state IN
        ('not_reached','disabled','nothing','cleaned','pending','failed')),
    cleanup_reason text CHECK (cleanup_reason <> ''),
    months_cleaned integer NOT NULL DEFAULT 0 CHECK (months_cleaned >= 0),
    months_pending integer NOT NULL DEFAULT 0 CHECK (months_pending >= 0),
    released_bytes bigint NOT NULL DEFAULT 0 CHECK (released_bytes >= 0),
    raw_state text NOT NULL DEFAULT 'not_reached' CHECK (raw_state IN
        ('not_reached','disabled','nothing','deleted','failed')),
    raw_reason text CHECK (raw_reason <> ''),
    raw_days integer NOT NULL DEFAULT 0 CHECK (raw_days >= 0),
    raw_files bigint NOT NULL DEFAULT 0 CHECK (raw_files >= 0),
    raw_bytes bigint NOT NULL DEFAULT 0 CHECK (raw_bytes >= 0),
    stage_seconds jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(stage_seconds) = 'object'),
    started_at timestamptz,
    finished_at timestamptz,
    PRIMARY KEY (run_id, scope_id),
    UNIQUE (run_id, ordinal),
    CHECK (state <> 'skipped' OR reason IS NOT NULL),
    CHECK (build_state <> 'failed' OR build_reason IS NOT NULL),
    CHECK (cleanup_state <> 'failed' OR cleanup_reason IS NOT NULL),
    CHECK (raw_state <> 'failed' OR raw_reason IS NOT NULL)
);
CREATE TABLE IF NOT EXISTS mpp_daily_day (
    run_id text NOT NULL,
    scope_id text NOT NULL,
    source_id text NOT NULL CHECK (source_id <> ''),
    log_date date NOT NULL,
    state text NOT NULL CHECK (state IN ('complete','failed','conflict','interrupted')),
    reason text CHECK (reason <> ''),
    batch_id text NOT NULL CHECK (batch_id <> ''),
    file_count integer NOT NULL CHECK (file_count >= 0),
    byte_count bigint NOT NULL CHECK (byte_count >= 0),
    added_records bigint NOT NULL DEFAULT 0 CHECK (added_records >= 0),
    seconds double precision NOT NULL DEFAULT 0 CHECK (seconds >= 0),
    files jsonb NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(files) = 'array'),
    PRIMARY KEY (run_id, source_id, log_date),
    FOREIGN KEY (run_id, scope_id) REFERENCES mpp_daily_cluster (run_id, scope_id),
    CHECK ((state = 'complete') = (reason IS NULL))
);
CREATE INDEX IF NOT EXISTS mpp_daily_day_source_idx ON mpp_daily_day (source_id, log_date);
-- What a run left for a person, written at the moment it is found.
-- nonconforming_file is an observation of the receiving directory, not a problem.
CREATE TABLE IF NOT EXISTS mpp_daily_problem (
    run_id text NOT NULL,
    scope_id text NOT NULL,
    seq integer NOT NULL CHECK (seq > 0),
    kind text NOT NULL CHECK (kind IN ('day_failed','files_without_marker','marker_without_files',
        'marker_not_before_today','cleanup_pending','build_not_succeeded','nonconforming_file')),
    source_id text CHECK (source_id <> ''),
    log_date date,
    result_month date,
    reason text CHECK (reason <> ''),
    file_name text CHECK (file_name <> ''),
    file_count integer CHECK (file_count >= 0),
    PRIMARY KEY (run_id, scope_id, seq),
    FOREIGN KEY (run_id, scope_id) REFERENCES mpp_daily_cluster (run_id, scope_id)
);

-- One row per file name of an imported day. file_id is the content the import stored
-- under that name; the stat values are those of the very file that was last read and
-- found to be that content (taken from the descriptor it was read through), so a later
-- run can tell an untouched file without reading it and a deletion can tell that the
-- name still leads to the file that was proven. They are empty when the last look
-- found something else: the file is then read at every look until it is the content
-- again. removing_* says a run decided to delete the day's files, removed_* that this
-- file is gone, cleared_* that the day's marker is gone as well, which ends the
-- deletion of the day: a deletion cut short at any point is told from a change of the
-- input and finished later. raw_files and raw_bytes of mpp_daily_cluster move in the
-- transaction that sets removed_*, raw_days in the one that sets cleared_*, so the
-- records of all runs add up to what was removed.
CREATE TABLE IF NOT EXISTS mpp_daily_file (
    source_id text NOT NULL CHECK (source_id <> ''),
    log_date date NOT NULL,
    file_name text NOT NULL CHECK (file_name <> ''),
    scope_id text NOT NULL CHECK (scope_id <> ''),
    batch_id text NOT NULL CHECK (batch_id <> ''),
    file_id text NOT NULL CHECK (file_id <> ''),
    byte_count bigint NOT NULL CHECK (byte_count >= 0),
    device bigint,
    inode bigint,
    mtime_ns bigint,
    ctime_ns bigint,
    verified_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    verified_run_id text NOT NULL CHECK (verified_run_id <> ''),
    removing_at timestamptz,
    removing_run_id text CHECK (removing_run_id <> ''),
    removed_at timestamptz,
    removed_run_id text CHECK (removed_run_id <> ''),
    cleared_at timestamptz,
    cleared_run_id text CHECK (cleared_run_id <> ''),
    PRIMARY KEY (source_id, log_date, file_name),
    CHECK ((device IS NULL) = (inode IS NULL) AND (device IS NULL) = (mtime_ns IS NULL)
        AND (device IS NULL) = (ctime_ns IS NULL)),
    CHECK ((removing_at IS NULL) = (removing_run_id IS NULL)),
    CHECK ((removed_at IS NULL) = (removed_run_id IS NULL)),
    CHECK ((cleared_at IS NULL) = (cleared_run_id IS NULL)),
    CHECK (removed_at IS NULL OR removing_at IS NOT NULL),
    CHECK (cleared_at IS NULL OR removing_at IS NOT NULL)
);

-- Only one daily run at a time: the run holds this session lock for its whole life.
CREATE OR REPLACE FUNCTION mpp_daily_lock_key() RETURNS bigint
LANGUAGE sql STABLE
RETURN hashtextextended(current_schema()||':mpp_daily_run',1835102);

-- A run still marked running whose lock nobody holds was killed. The next run
-- records that; until then readers already see it as unfinished.
CREATE OR REPLACE FUNCTION mpp_daily_runs()
RETURNS TABLE(run_id text,started_by text,state text,failed boolean,reason text,
    started_at timestamptz,finished_at timestamptz,local_date date,stale_after_hours integer)
LANGUAGE sql STABLE
BEGIN ATOMIC
    WITH held AS (SELECT EXISTS (SELECT FROM pg_locks l
        WHERE l.locktype='advisory' AND l.granted AND l.objsubid=1
          AND l.database=(SELECT d.oid FROM pg_database d WHERE d.datname=current_database())
          AND l.classid::bigint=((mpp_daily_lock_key()>>32) & 4294967295)
          AND l.objid::bigint=(mpp_daily_lock_key() & 4294967295)) AS yes)
    SELECT r.run_id,r.started_by,
        CASE WHEN r.state='running' AND NOT held.yes THEN 'unfinished' ELSE r.state END,
        r.failed,
        CASE WHEN r.state='running' AND NOT held.yes THEN 'owner_exited' ELSE r.reason END,
        r.started_at,r.finished_at,r.local_date,r.stale_after_hours
    FROM mpp_daily_run r CROSS JOIN held;
END;

-- Open problems. What a run did not look at, it says nothing about; what it did look
-- at, it has said, whether or not it got any further. A problem of a day therefore
-- comes from the newest run that looked at that day: the newest run that finished its
-- import step looked at every day, and a later run that was skipped, stopped or killed
-- speaks only for the days it lists in examined_days. A problem of the build or of
-- the cleanup comes from the newest run that went through that step. A problem goes
-- when a later run has really dealt with it, not when a run failed to get to it.
-- Derived here: the newest run that got to the cluster skipped it, the newest run did
-- not end normally, and no run has succeeded (finished without any failure) for longer
-- than the configured time, counted from the first run when none has.
CREATE OR REPLACE FUNCTION mpp_daily_problems()
RETURNS TABLE(kind text,scope_id text,source_id text,log_date date,result_month date,
    reason text,file_count integer,run_id text,seen_at timestamptz)
LANGUAGE sql STABLE
BEGIN ATOMIC
    WITH runs AS (SELECT * FROM mpp_daily_runs()),
    latest AS (SELECT * FROM runs r ORDER BY r.started_at DESC,r.run_id DESC LIMIT 1),
    turns AS (SELECT c.scope_id,c.run_id,c.state,c.reason,c.import_state,c.examined_days,c.build_state,c.cleanup_state,
            r.started_at
        FROM mpp_daily_cluster c JOIN runs r USING(run_id)
        WHERE c.scope_id IN (SELECT n.scope_id FROM mpp_daily_cluster n JOIN latest USING(run_id))),
    whole AS (SELECT DISTINCT ON (t.scope_id) t.scope_id,t.run_id,t.started_at FROM turns t
        WHERE t.import_state='done' ORDER BY t.scope_id,t.started_at DESC,t.run_id DESC),
    since AS (SELECT t.scope_id,t.run_id,t.started_at,t.import_state,t.examined_days
        FROM turns t LEFT JOIN whole w USING(scope_id)
        WHERE w.run_id IS NULL OR (t.started_at,t.run_id)>=(w.started_at,w.run_id)),
    day_found AS (SELECT p.kind,p.scope_id,p.source_id,p.log_date,p.result_month,p.reason,p.file_count,p.run_id,s.started_at
        FROM mpp_daily_problem p JOIN since s USING(run_id,scope_id)
        WHERE p.kind IN ('day_failed','files_without_marker','marker_without_files','marker_not_before_today')),
    day_verdict AS (SELECT DISTINCT ON (d.scope_id,d.source_id,d.log_date) d.scope_id,d.source_id,d.log_date,s.run_id
        FROM (SELECT DISTINCT f.scope_id,f.source_id,f.log_date FROM day_found f) d JOIN since s USING(scope_id)
        WHERE s.import_state='done' OR (s.examined_days->d.source_id) ? to_char(d.log_date,'YYYY-MM-DD')
           OR EXISTS (SELECT FROM day_found q WHERE q.run_id=s.run_id AND q.scope_id=d.scope_id
                      AND q.source_id=d.source_id AND q.log_date=d.log_date)
        ORDER BY d.scope_id,d.source_id,d.log_date,s.started_at DESC,s.run_id DESC),
    steps(step,kinds) AS (VALUES ('build',ARRAY['build_not_succeeded']),('cleanup',ARRAY['cleanup_pending'])),
    examined AS (SELECT DISTINCT ON (t.scope_id,s.step) t.scope_id,s.kinds,t.run_id,t.started_at
        FROM turns t CROSS JOIN steps s
        WHERE CASE s.step WHEN 'build' THEN t.build_state<>'not_reached' ELSE t.cleanup_state<>'not_reached' END
        ORDER BY t.scope_id,s.step,t.started_at DESC,t.run_id DESC)
    SELECT f.kind,f.scope_id,f.source_id,f.log_date,f.result_month,f.reason,f.file_count,f.run_id,f.started_at
    FROM day_found f JOIN day_verdict v USING(run_id,scope_id,source_id,log_date)
    UNION ALL
    SELECT p.kind,p.scope_id,p.source_id,p.log_date,p.result_month,p.reason,p.file_count,e.run_id,e.started_at
    FROM mpp_daily_problem p JOIN examined e ON e.run_id=p.run_id AND e.scope_id=p.scope_id AND p.kind=ANY(e.kinds)
    UNION ALL
    SELECT 'cluster_skipped',d.scope_id,NULL,NULL,NULL,d.reason,NULL,d.run_id,d.started_at
    FROM (SELECT DISTINCT ON (t.scope_id) t.scope_id,t.state,t.reason,t.run_id,t.started_at
          FROM turns t WHERE t.state<>'pending' ORDER BY t.scope_id,t.started_at DESC,t.run_id DESC) d
    WHERE d.state='skipped'
    UNION ALL
    SELECT 'run_not_finished',NULL,NULL,NULL,NULL,l.state,NULL,l.run_id,l.started_at
    FROM latest l WHERE l.state IN ('aborted','unfinished')
    UNION ALL
    SELECT 'no_recent_success',NULL,NULL,NULL,NULL,NULL,NULL,l.run_id,x.since
    FROM latest l CROSS JOIN LATERAL (SELECT coalesce(
        (SELECT max(r.finished_at) FROM runs r WHERE r.state='finished' AND NOT r.failed),
        (SELECT min(r.started_at) FROM runs r)) AS since) x
    WHERE clock_timestamp()-x.since>make_interval(hours => l.stale_after_hours);
END;

-- One row per cluster of the newest run: where its data and version stand now.
CREATE OR REPLACE FUNCTION mpp_daily_clusters()
RETURNS TABLE(scope_id text,ordinal integer,newest_imported date,version_cutoff date,
    version_published_at timestamptz,run_id text,run_started_at timestamptz,run_state text,
    cluster_state text,cluster_failed boolean,cluster_reason text,open_problems bigint)
LANGUAGE sql STABLE
BEGIN ATOMIC
    WITH latest AS (SELECT * FROM mpp_daily_runs() r ORDER BY r.started_at DESC,r.run_id DESC LIMIT 1)
    SELECT c.scope_id,c.ordinal,
        (SELECT max(d.declared_date) FROM batch_date d JOIN import_batch b USING(batch_id)
            WHERE b.scope_id=c.scope_id AND b.state='complete'),
        (SELECT s.cutoff_date FROM current_version v JOIN build b USING(build_id)
            JOIN config_snapshot s USING(config_id) WHERE v.scope_id=c.scope_id),
        (SELECT v.last_success_at FROM current_version v WHERE v.scope_id=c.scope_id AND v.build_id IS NOT NULL),
        l.run_id,l.started_at,l.state,c.state,c.failed,c.reason,
        (SELECT count(*) FROM mpp_daily_problems() p WHERE p.scope_id=c.scope_id)
    FROM mpp_daily_cluster c JOIN latest l USING(run_id);
END;

-- The newest runs, one row per run and cluster (a run without clusters gives one row).
CREATE OR REPLACE FUNCTION mpp_daily_recent(p_limit integer DEFAULT 20)
RETURNS TABLE(run_id text,started_by text,run_state text,run_failed boolean,run_reason text,
    run_started_at timestamptz,run_finished_at timestamptz,
    scope_id text,ordinal integer,cluster_state text,cluster_failed boolean,cluster_reason text,
    import_state text,imported_days date[],failed_days jsonb,newest_imported date,
    build_state text,build_reason text,cutoff_date date,build_id text,publication_id text,
    cleanup_state text,cleanup_reason text,months_cleaned integer,months_pending integer,released_bytes bigint,
    raw_state text,raw_reason text,raw_days integer,raw_files bigint,raw_bytes bigint,
    nonconforming_files bigint,stage_seconds jsonb,cluster_started_at timestamptz,cluster_finished_at timestamptz)
LANGUAGE sql STABLE
BEGIN ATOMIC
    SELECT r.run_id,r.started_by,r.state,r.failed,r.reason,r.started_at,r.finished_at,
        c.scope_id,c.ordinal,c.state,c.failed,c.reason,c.import_state,
        ARRAY(SELECT d.log_date FROM mpp_daily_day d WHERE d.run_id=c.run_id AND d.scope_id=c.scope_id
            AND d.state='complete' ORDER BY d.log_date,d.source_id),
        (SELECT coalesce(jsonb_agg(jsonb_build_object('source',d.source_id,'date',d.log_date,'state',d.state,'reason',d.reason)
                ORDER BY d.log_date,d.source_id),'[]'::jsonb)
            FROM mpp_daily_day d WHERE d.run_id=c.run_id AND d.scope_id=c.scope_id AND d.state<>'complete'),
        c.newest_imported,c.build_state,c.build_reason,c.cutoff_date,c.build_id,c.publication_id,
        c.cleanup_state,c.cleanup_reason,c.months_cleaned,c.months_pending,c.released_bytes,
        c.raw_state,c.raw_reason,c.raw_days,c.raw_files,c.raw_bytes,
        (SELECT coalesce(sum(p.file_count),0) FROM mpp_daily_problem p
            WHERE p.run_id=c.run_id AND p.scope_id=c.scope_id AND p.kind='nonconforming_file'),
        c.stage_seconds,c.started_at,c.finished_at
    FROM (SELECT * FROM mpp_daily_runs() x ORDER BY x.started_at DESC,x.run_id DESC LIMIT greatest(p_limit,0)) r
    LEFT JOIN mpp_daily_cluster c USING(run_id);
END;

-- Dashboard wording for the codes above; an unknown code is shown as it is.
CREATE OR REPLACE FUNCTION mpp_view_daily_label(p_kind text,p_code text) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE
RETURN coalesce(CASE p_kind
    WHEN 'started_by' THEN CASE p_code WHEN 'timer' THEN '定时' WHEN 'manual' THEN '手工' END
    WHEN 'run' THEN CASE p_code WHEN 'running' THEN '运行中' WHEN 'ok' THEN '完成'
        WHEN 'failed' THEN '完成，有失败' WHEN 'aborted' THEN '被中止' WHEN 'unfinished' THEN '未正常结束' END
    WHEN 'cluster' THEN CASE p_code WHEN 'pending' THEN '没有轮到' WHEN 'running' THEN '处理中'
        WHEN 'ok' THEN '完成' WHEN 'failed' THEN '完成，有失败' WHEN 'skipped' THEN '被跳过：集群正忙'
        WHEN 'aborted' THEN '被中止' WHEN 'unfinished' THEN '未正常结束' END
    WHEN 'build' THEN CASE p_code WHEN 'not_reached' THEN '没有做到这一步' WHEN 'disabled' THEN '自动构建已关闭'
        WHEN 'no_data' THEN '还没有导入成功的日期' WHEN 'not_due' THEN '未到构建间隔'
        WHEN 'published' THEN '已发布' WHEN 'no_samples' THEN '没有有效样本，版本未更新' WHEN 'failed' THEN '失败' END
    WHEN 'cleanup' THEN CASE p_code WHEN 'not_reached' THEN '没有做到这一步' WHEN 'disabled' THEN '自动清理已关闭'
        WHEN 'nothing' THEN '没有过期月份' WHEN 'cleaned' THEN '已清理' WHEN 'pending' THEN '有月份等待清理'
        WHEN 'failed' THEN '失败' END
    WHEN 'raw' THEN CASE p_code WHEN 'not_reached' THEN '没有做到这一步' WHEN 'disabled' THEN '自动删除已关闭'
        WHEN 'nothing' THEN '没有到期的文件' WHEN 'deleted' THEN '已删除' WHEN 'failed' THEN '失败' END
    WHEN 'problem' THEN CASE p_code WHEN 'day_failed' THEN '导入失败或有冲突的日期'
        WHEN 'files_without_marker' THEN '有文件但没有齐全标记' WHEN 'marker_without_files' THEN '有齐全标记但没有文件'
        WHEN 'marker_not_before_today' THEN '标记日期不早于今天' WHEN 'cleanup_pending' THEN '等待清理的月份'
        WHEN 'cluster_skipped' THEN '上次运行跳过了这个集群' WHEN 'build_not_succeeded' THEN '构建或发布尚未成功'
        WHEN 'run_not_finished' THEN '上次运行没有正常结束' WHEN 'no_recent_success' THEN '太久没有成功的运行' END
    WHEN 'hint' THEN CASE p_code
        WHEN 'day_failed' THEN '按原因处理后等下一次运行自动重试；多出或内容变了的文件移出接收目录'
        WHEN 'files_without_marker' THEN '确认文件齐全后放上齐全标记，或把文件移出接收目录'
        WHEN 'marker_without_files' THEN '把这一天的文件拷入接收目录，或删除这个标记'
        WHEN 'marker_not_before_today' THEN '撤掉这个标记；那一天结束并拷全文件后再放'
        WHEN 'cleanup_pending' THEN '下一次运行会再试；长期不成功时检查是否有查询一直占着统计表'
        WHEN 'cluster_skipped' THEN '下一次运行会补上；查看当时占用集群的任务'
        WHEN 'build_not_succeeded' THEN '下一次运行会重试；用 status 命令查看未发布的原因'
        WHEN 'run_not_finished' THEN '下一次运行会接上；需要时手工执行一次每日运行命令'
        WHEN 'no_recent_success' THEN '先处理列表里的其他问题；没有别的问题时检查定时器是否启用、数据库是否在运行，再手工执行一次' END
    WHEN 'reason' THEN CASE p_code
        WHEN 'cluster_busy' THEN '集群正被其他任务占用' WHEN 'operator_interrupt' THEN '被停止'
        WHEN 'owner_exited' THEN '进程意外退出' WHEN 'aborted' THEN '被中止' WHEN 'unfinished' THEN '未正常结束'
        WHEN 'files_changed_after_import' THEN '导入之后文件有增减或内容变化'
        WHEN 'batch_manifest_changed' THEN '首次登记之后文件清单变了' WHEN 'batch_member_changed' THEN '已导入的文件被替换或移走'
        WHEN 'origin_content_changed' THEN '同名文件的内容变了' WHEN 'record_edge_overlap' THEN '与已导入的文件内容重叠'
        WHEN 'file_unreadable' THEN '文件读不了' WHEN 'file_changed_during_read' THEN '读取期间文件在变化'
        WHEN 'file_processing_failed' THEN '文件处理失败' WHEN 'import_interrupted' THEN '导入被中断'
        WHEN 'batch_incomplete' THEN '有文件没有导入成功' WHEN 'ingestion_failed' THEN '导入失败'
        WHEN 'source_mapping_changed' THEN '来源的登记内容变了' WHEN 'scope_mapping_changed' THEN '集群的登记内容变了'
        WHEN 'workflow_failed' THEN '构建流程失败' WHEN 'publication_checks_failed' THEN '发布检查没有通过'
        WHEN 'publication_write_failed' THEN '发布写入失败' WHEN 'no_valid_samples' THEN '整窗没有有效样本'
        WHEN 'cleanup_lock_timeout' THEN '拿不到锁' WHEN 'cleanup_groups_pending' THEN '结果已删，分组行尚未删完'
        WHEN 'cleanup_month_failed' THEN '清理失败' WHEN 'cleanup_failed' THEN '清理失败'
        WHEN 'raw_delete_failed' THEN '删除原始文件失败' END
    END,p_code);

CREATE OR REPLACE FUNCTION mpp_view_daily_days(p_days date[]) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE
RETURN CASE WHEN coalesce(cardinality(p_days),0)=0 THEN ''
    WHEN cardinality(p_days)<=4 THEN array_to_string(ARRAY(SELECT to_char(d,'MM-DD') FROM unnest(p_days) d ORDER BY d),'、')
    ELSE cardinality(p_days)||' 天：'||(SELECT to_char(min(d),'MM-DD')||' 至 '||to_char(max(d),'MM-DD') FROM unnest(p_days) d) END;

CREATE OR REPLACE FUNCTION mpp_view_daily_bytes(p_bytes bigint) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE
RETURN CASE WHEN p_bytes>=1073741824 THEN round(p_bytes/1073741824.0,1)||' GB'
    WHEN p_bytes>=1048576 THEN round(p_bytes/1048576.0,1)||' MB'
    WHEN p_bytes>=1024 THEN round(p_bytes/1024.0,1)||' KB' ELSE p_bytes||' B' END;

-- The newest run, once: its time and result describe every row of the cluster list.
-- A successful run is one that finished without any failure or skipped cluster.
CREATE OR REPLACE FUNCTION mpp_view_daily_last()
RETURNS TABLE(started_at timestamptz,finished_at timestamptz,seconds double precision,started_by text,
    result text,last_success_at timestamptz,open_problems bigint)
LANGUAGE sql STABLE
BEGIN ATOMIC
    SELECT r.started_at,r.finished_at,extract(epoch FROM r.finished_at-r.started_at)::double precision,
        mpp_view_daily_label('started_by',r.started_by),
        mpp_view_daily_label('run',CASE WHEN r.state='finished' THEN CASE WHEN r.failed THEN 'failed' ELSE 'ok' END
            ELSE r.state END),
        (SELECT max(x.finished_at) FROM mpp_daily_runs() x WHERE x.state='finished' AND NOT x.failed),
        (SELECT count(*) FROM mpp_daily_problems())
    FROM mpp_daily_runs() r ORDER BY r.started_at DESC,r.run_id DESC LIMIT 1;
END;

CREATE OR REPLACE FUNCTION mpp_view_daily_clusters()
RETURNS TABLE(cluster text,newest_imported text,version_cutoff text,version_published_at timestamptz,
    last_result text,open_problems bigint)
LANGUAGE sql STABLE
BEGIN ATOMIC
    SELECT c.scope_id,to_char(c.newest_imported,'YYYY-MM-DD'),to_char(c.version_cutoff,'YYYY-MM-DD'),
        date_trunc('second',c.version_published_at),
        mpp_view_daily_label('cluster',CASE
            WHEN c.cluster_state='running' AND c.run_state<>'running' THEN c.run_state
            WHEN c.cluster_state='done' THEN CASE WHEN c.cluster_failed THEN 'failed' ELSE 'ok' END
            ELSE c.cluster_state END),
        c.open_problems
    FROM mpp_daily_clusters() c ORDER BY c.ordinal;
END;

CREATE OR REPLACE FUNCTION mpp_view_daily_problems()
RETURNS TABLE(problem text,cluster text,source text,subject text,detail text,hint text,seen_at timestamptz)
LANGUAGE sql STABLE
BEGIN ATOMIC
    SELECT mpp_view_daily_label('problem',p.kind),coalesce(p.scope_id,'全部'),coalesce(p.source_id,''),
        coalesce(to_char(p.log_date,'YYYY-MM-DD'),to_char(p.result_month,'YYYY-MM'),''),
        coalesce(mpp_view_daily_label('reason',p.reason),'')
            ||CASE WHEN p.file_count IS NOT NULL THEN CASE WHEN p.reason IS NULL THEN '' ELSE '，' END||p.file_count||' 个文件' ELSE '' END,
        mpp_view_daily_label('hint',p.kind),date_trunc('second',p.seen_at)
    FROM mpp_daily_problems() p
    ORDER BY p.scope_id NULLS FIRST,p.kind,p.source_id,p.log_date,p.result_month;
END;

CREATE OR REPLACE FUNCTION mpp_view_daily_recent(p_limit integer DEFAULT 20)
RETURNS TABLE(started_at timestamptz,finished_at timestamptz,seconds double precision,started_by text,
    run_result text,cluster text,cluster_result text,imported text,failed text,build text,cleanup text,
    raw_files text,other_files bigint,cluster_seconds double precision)
LANGUAGE sql STABLE
BEGIN ATOMIC
    SELECT date_trunc('second',r.run_started_at),date_trunc('second',r.run_finished_at),
        extract(epoch FROM r.run_finished_at-r.run_started_at)::double precision,
        mpp_view_daily_label('started_by',r.started_by),
        mpp_view_daily_label('run',CASE WHEN r.run_state='finished' THEN CASE WHEN r.run_failed THEN 'failed' ELSE 'ok' END
            ELSE r.run_state END),
        coalesce(r.scope_id,''),
        CASE WHEN r.scope_id IS NULL THEN '' ELSE mpp_view_daily_label('cluster',CASE
            WHEN r.cluster_state='running' AND r.run_state<>'running' THEN r.run_state
            WHEN r.cluster_state='done' THEN CASE WHEN r.cluster_failed THEN 'failed' ELSE 'ok' END
            ELSE r.cluster_state END) END,
        mpp_view_daily_days(r.imported_days),
        coalesce((SELECT string_agg(to_char((f->>'date')::date,'MM-DD')||'（'||mpp_view_daily_label('reason',f->>'reason')||'）','、'
            ORDER BY f->>'date',f->>'source') FROM jsonb_array_elements(r.failed_days) f),''),
        CASE WHEN r.scope_id IS NULL THEN '' ELSE mpp_view_daily_label('build',r.build_state)
            ||CASE WHEN r.build_state IN ('published','no_samples') THEN '，截止日 '||to_char(r.cutoff_date,'YYYY-MM-DD')
                   WHEN r.build_state='failed' THEN '：'||mpp_view_daily_label('reason',r.build_reason) ELSE '' END END,
        CASE WHEN r.scope_id IS NULL THEN '' ELSE mpp_view_daily_label('cleanup',r.cleanup_state)
            ||CASE WHEN r.cleanup_state='cleaned' THEN ' '||r.months_cleaned||' 个月，释放 '||mpp_view_daily_bytes(r.released_bytes)
                   WHEN r.cleanup_state='pending' THEN '：'||r.months_pending||' 个月'
                   WHEN r.cleanup_state='failed' THEN '：'||mpp_view_daily_label('reason',r.cleanup_reason) ELSE '' END END,
        CASE WHEN r.scope_id IS NULL THEN '' ELSE mpp_view_daily_label('raw',r.raw_state)
            ||CASE WHEN r.raw_state='deleted' THEN ' '||r.raw_days||' 天 '||r.raw_files||' 个文件，'||mpp_view_daily_bytes(r.raw_bytes)
                   WHEN r.raw_state='failed' THEN '：'||mpp_view_daily_label('reason',r.raw_reason)
                       ||CASE WHEN r.raw_files>0 THEN '，已删除 '||r.raw_files||' 个文件，'||mpp_view_daily_bytes(r.raw_bytes) ELSE '' END
                   ELSE '' END END,
        r.nonconforming_files,
        extract(epoch FROM r.cluster_finished_at-r.cluster_started_at)::double precision
    FROM mpp_daily_recent(p_limit) r
    ORDER BY r.run_started_at DESC,r.run_id DESC,r.ordinal;
END;

-- 1.11.0: the read-only account may use the schema and read every table. Partitions
-- are read through their parents, so later result partitions need no grant.
DO $block$
DECLARE item record; reader text := current_setting('apm.readonly_role');
BEGIN
    EXECUTE format('GRANT USAGE ON SCHEMA %I TO %I',current_schema(),reader);
    FOR item IN SELECT c.relname FROM pg_class c WHERE c.relnamespace=current_schema()::regnamespace
        AND c.relkind IN ('r','p') AND NOT c.relispartition ORDER BY c.relname LOOP
        EXECUTE format('GRANT SELECT ON TABLE %I.%I TO %I',current_schema(),item.relname,reader);
    END LOOP;
END $block$;

SET LOCAL search_path = pg_catalog;
