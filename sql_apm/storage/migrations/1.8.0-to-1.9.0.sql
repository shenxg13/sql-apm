-- Retention metadata only: existing content and historical receipts stay intact.
SELECT set_config('search_path',quote_ident(current_setting('apm.schema'))||',pg_catalog',true);
ALTER TABLE mpp_result_partition ADD COLUMN cleaned_at timestamptz,
    ADD COLUMN groups_cleaned_at timestamptz,
    ADD CONSTRAINT mpp_result_partition_check CHECK
        (groups_cleaned_at IS NULL OR (cleaned_at IS NOT NULL AND groups_cleaned_at >= cleaned_at));
ALTER TABLE task DROP CONSTRAINT task_mode_check, DROP CONSTRAINT task_stage_check,
    ADD CONSTRAINT task_mode_check CHECK (mode IN ('full','import_only','rebuild','snapshot','statistics','cleanup')),
    ADD CONSTRAINT task_stage_check CHECK (stage IN ('import','snapshot','build','check','publish','none','cleanup'));
CREATE OR REPLACE FUNCTION mpp_check_build_groups() RETURNS trigger LANGUAGE plpgsql AS $function$
BEGIN
    IF EXISTS (SELECT FROM added_groups a JOIN mpp_result_partition p USING(partition_id)
        WHERE p.cleaned_at IS NOT NULL) THEN RAISE EXCEPTION 'results_cleaned'; END IF;
    IF EXISTS (SELECT FROM added_groups a JOIN build b USING(build_id)
        JOIN mpp_baseline_group g USING(group_id)
        WHERE (b.scope_id,b.normalization_id,b.profile) IS DISTINCT FROM
              (g.scope_id,g.normalization_id,g.profile)) THEN
        RAISE EXCEPTION 'build_group_context_mismatch';
    END IF;
    RETURN NULL;
END $function$;
CREATE OR REPLACE FUNCTION mpp_check_build_observation_groups() RETURNS trigger LANGUAGE plpgsql AS $function$
BEGIN
    IF EXISTS (SELECT FROM added_groups a JOIN mpp_result_partition p USING(partition_id)
        WHERE p.cleaned_at IS NOT NULL) THEN RAISE EXCEPTION 'results_cleaned'; END IF;
    IF EXISTS (SELECT FROM added_groups a JOIN build b USING(build_id)
        JOIN mpp_observation_group g USING(group_id)
        WHERE (b.scope_id,b.profile) IS DISTINCT FROM (g.scope_id,g.profile)) THEN
        RAISE EXCEPTION 'build_observation_group_context_mismatch';
    END IF;
    RETURN NULL;
END $function$;
CREATE OR REPLACE FUNCTION mpp_result_context_guard() RETURNS trigger LANGUAGE plpgsql AS $function$
BEGIN
    IF TG_TABLE_NAME = 'build' THEN
        IF (TG_OP = 'INSERT' OR NEW.partition_id IS DISTINCT FROM OLD.partition_id)
            AND EXISTS (SELECT FROM mpp_result_partition WHERE partition_id=NEW.partition_id
                AND cleaned_at IS NOT NULL) THEN RAISE EXCEPTION 'results_cleaned'; END IF;
        IF NEW.partition_id IS NOT NULL AND NOT EXISTS (
            SELECT FROM mpp_result_partition WHERE partition_id=NEW.partition_id
                AND scope_id=NEW.scope_id
                AND build_month=date_trunc('month',NEW.started_at AT TIME ZONE 'Asia/Shanghai')::date) THEN
            RAISE EXCEPTION 'build_partition_month_mismatch';
        END IF;
        IF TG_OP = 'UPDATE' AND (NEW.scope_id,NEW.normalization_id,NEW.profile) IS DISTINCT FROM
            (OLD.scope_id,OLD.normalization_id,OLD.profile)
            AND EXISTS (SELECT FROM mpp_build_group WHERE build_id=OLD.build_id) THEN
            RAISE EXCEPTION 'build_result_context_immutable';
        END IF;
    ELSIF (NEW.scope_id,NEW.normalization_id,NEW.profile) IS DISTINCT FROM
            (OLD.scope_id,OLD.normalization_id,OLD.profile)
            AND EXISTS (SELECT FROM mpp_build_group WHERE group_id=OLD.group_id) THEN
        RAISE EXCEPTION 'group_result_context_immutable';
    END IF;
    RETURN NEW;
END $function$;
CREATE OR REPLACE FUNCTION mpp_ensure_result_partition(selected_scope text, selected_month date)
RETURNS bigint LANGUAGE plpgsql AS $function$
DECLARE pid bigint; table_name text; child_name text;
BEGIN
    IF extract(day FROM selected_month) <> 1 THEN RAISE EXCEPTION 'invalid_build_month'; END IF;
    pid := ('x'||substr(encode(sha256(convert_to(jsonb_build_array(selected_scope,selected_month)::text,'UTF8')),'hex'),1,15))::bit(60)::bigint;
    -- A brief transaction lock only coordinates DDL for this cluster/month.
    PERFORM pg_advisory_xact_lock(pid);
    INSERT INTO mpp_result_partition (partition_id,scope_id,build_month) VALUES (pid,selected_scope,selected_month) ON CONFLICT DO NOTHING;
    IF NOT EXISTS (SELECT FROM mpp_result_partition WHERE partition_id=pid AND scope_id=selected_scope AND build_month=selected_month) THEN
        RAISE EXCEPTION 'partition_identity_collision';
    END IF;
    IF EXISTS (SELECT FROM mpp_result_partition WHERE partition_id=pid AND cleaned_at IS NOT NULL) THEN
        RAISE EXCEPTION 'results_cleaned';
    END IF;
    FOREACH table_name IN ARRAY ARRAY['mpp_statistic','mpp_observation_statistic'] LOOP
        child_name := table_name||'_p'||pid;
        IF to_regclass(child_name) IS NULL THEN
            EXECUTE format('CREATE TABLE %I (LIKE %I INCLUDING ALL)',child_name,table_name);
            EXECUTE format('ALTER TABLE %I ATTACH PARTITION %I FOR VALUES IN (%s)',table_name,child_name,pid);
        END IF;
    END LOOP;
    RETURN pid;
END $function$;
CREATE OR REPLACE FUNCTION mpp_require_results(selected_build text)
RETURNS bigint LANGUAGE plpgsql AS $function$
DECLARE pid bigint; saved boolean; cleaned timestamptz;
BEGIN
    LOCK TABLE ONLY mpp_statistic,ONLY mpp_observation_statistic IN ACCESS SHARE MODE;
    SELECT b.partition_id,b.results_saved,p.cleaned_at INTO pid,saved,cleaned
        FROM build b LEFT JOIN mpp_result_partition p USING(partition_id) WHERE b.build_id=selected_build;
    IF saved AND cleaned IS NOT NULL THEN RAISE EXCEPTION 'results_cleaned'; END IF;
    RETURN pid;
END $function$;
CREATE OR REPLACE FUNCTION mpp_read_statistics(selected_build text, observation boolean DEFAULT false,
    selected_group text DEFAULT NULL)
RETURNS SETOF mpp_statistic LANGUAGE plpgsql AS $function$
DECLARE pid bigint;
BEGIN
    pid := mpp_require_results(selected_build);
    IF observation THEN
        RETURN QUERY SELECT * FROM mpp_observation_statistic WHERE partition_id=pid AND build_id=selected_build
            AND (selected_group IS NULL OR group_id=selected_group);
    ELSE
        RETURN QUERY SELECT * FROM mpp_statistic WHERE partition_id=pid AND build_id=selected_build
            AND (selected_group IS NULL OR group_id=selected_group);
    END IF;
END $function$;
CREATE OR REPLACE FUNCTION mpp_coverage(selected_build text, observation boolean DEFAULT false,
    selected_group text DEFAULT NULL)
RETURNS TABLE (group_id text,layer text,computed_keys jsonb,empty_keys jsonb)
LANGUAGE plpgsql AS $function$
DECLARE group_table text; statistic_table text;
BEGIN
    PERFORM mpp_require_results(selected_build);
    group_table := CASE WHEN observation THEN 'mpp_build_observation_group' ELSE 'mpp_build_group' END;
    statistic_table := CASE WHEN observation THEN 'mpp_observation_statistic' ELSE 'mpp_statistic' END;
    RETURN QUERY EXECUTE format($query$
        WITH context AS MATERIALIZED (
            SELECT b.partition_id,c.window_start,c.window_end FROM build b
            JOIN config_snapshot c USING(config_id) WHERE b.build_id=$1),
        days AS (SELECT d::date AS sample_day FROM context CROSS JOIN LATERAL generate_series(
            window_start AT TIME ZONE 'Asia/Shanghai',
            (window_end AT TIME ZONE 'Asia/Shanghai')-interval '1 day',interval '1 day') d),
        keys AS MATERIALIZED (
            SELECT 'overall'::text layer,'null'::jsonb key
            UNION ALL SELECT 'day',to_jsonb(sample_day) FROM days
            UNION SELECT 'week',to_jsonb(date_trunc('week',sample_day)::date) FROM days
            UNION ALL SELECT 'weekday',to_jsonb(i) FROM generate_series(1,7) i
            UNION ALL SELECT 'hour',to_jsonb(i) FROM generate_series(0,23) i),
        expected AS (SELECT layer,jsonb_agg(key ORDER BY key) keys FROM keys GROUP BY layer),
        actual AS (
            SELECT s.group_id,s.layer,jsonb_agg(coalesce(to_jsonb(bucket_date),to_jsonb(bucket_number),'null'::jsonb)
                ORDER BY bucket_date,bucket_number) computed
            FROM %I s JOIN context c USING(partition_id)
            WHERE s.build_id=$1 AND ($2 IS NULL OR s.group_id=$2) GROUP BY s.group_id,s.layer)
        SELECT g.group_id,k.layer,coalesce(a.computed,'[]'::jsonb),
            coalesce((SELECT jsonb_agg(key ORDER BY key) FROM jsonb_array_elements(k.keys) key
                      WHERE NOT coalesce(a.computed,'[]'::jsonb) @> jsonb_build_array(key)),'[]'::jsonb)
        FROM %I g JOIN context c USING(partition_id) CROSS JOIN expected k
        LEFT JOIN actual a ON a.group_id=g.group_id AND a.layer=k.layer
        WHERE g.build_id=$1 AND ($2 IS NULL OR g.group_id=$2)
    $query$,statistic_table,group_table) USING selected_build,selected_group;
END $function$;
CREATE TABLE IF NOT EXISTS mpp_cleanup_month (
    task_id text NOT NULL REFERENCES task,
    partition_id bigint NOT NULL REFERENCES mpp_result_partition,
    state text NOT NULL CHECK (state IN ('pending','removing_groups','succeeded','retained','protected','already_cleaned','lock_timeout','failed','interrupted')),
    reason text NOT NULL CHECK (reason <> ''),
    published_builds bigint NOT NULL CHECK (published_builds >= 0),
    unpublished_builds bigint NOT NULL CHECK (unpublished_builds >= 0),
    before_bytes bigint NOT NULL CHECK (before_bytes >= 0),
    after_bytes bigint NOT NULL CHECK (after_bytes >= 0),
    released_bytes bigint NOT NULL DEFAULT 0 CHECK (released_bytes >= 0),
    formal_groups_deleted bigint NOT NULL DEFAULT 0 CHECK (formal_groups_deleted >= 0),
    observation_groups_deleted bigint NOT NULL DEFAULT 0 CHECK (observation_groups_deleted >= 0),
    started_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    finished_at timestamptz,
    exclusive_seconds double precision NOT NULL DEFAULT 0 CHECK (exclusive_seconds >= 0),
    group_seconds double precision NOT NULL DEFAULT 0 CHECK (group_seconds >= 0),
    PRIMARY KEY (task_id,partition_id)
);
SET LOCAL search_path = pg_catalog;
