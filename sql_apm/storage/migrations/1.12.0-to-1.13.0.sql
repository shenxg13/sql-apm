SELECT set_config('search_path',quote_ident(current_setting('apm.schema'))||',pg_catalog',true);

-- Only the overall rows that can enter the current-baseline ranking. Dates
-- are fetched after LIMIT, so large training windows do not enlarge this index.
CREATE INDEX IF NOT EXISTS mpp_statistic_ranking_idx ON mpp_statistic (partition_id,build_id,group_id)
    INCLUDE (included_count,p50_ms,p95_ms,p99_ms,max_ms,mean_ms)
    WHERE layer='overall' AND included_count>0;

-- Close the gap between the backfill snapshot and trigger installation. The
-- upgrade already requires stopped writers; fail after the entry's 5s lock
-- timeout if an overlooked writer is still active. Readers may continue.
LOCK TABLE mpp_sql_text,mpp_fingerprint,mpp_occurrence IN SHARE ROW EXCLUSIVE MODE;
-- Filled before installing the maintenance triggers, in the upgrade transaction.
CREATE TABLE mpp_sql_text_count (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    item_count bigint NOT NULL CHECK (item_count >= 0)
);
INSERT INTO mpp_sql_text_count SELECT true,count(*) FROM mpp_sql_text;
CREATE TABLE mpp_fingerprint_count (
    normalization_id text PRIMARY KEY REFERENCES mpp_normalization ON DELETE CASCADE,
    item_count bigint NOT NULL CHECK (item_count >= 0)
);
INSERT INTO mpp_fingerprint_count SELECT normalization_id,count(*) FROM mpp_fingerprint GROUP BY normalization_id;
CREATE TABLE mpp_occurrence_name (
    analysis_id text NOT NULL REFERENCES analysis,
    kind text NOT NULL CHECK (kind IN ('database','user')),
    name text NOT NULL CHECK (name <> ''),
    item_count bigint NOT NULL CHECK (item_count >= 0),
    PRIMARY KEY (analysis_id,kind,name)
);
INSERT INTO mpp_occurrence_name
    SELECT analysis_id,'database',database,count(*) FROM mpp_occurrence WHERE database IS NOT NULL GROUP BY analysis_id,database
    UNION ALL
    SELECT analysis_id,'user',execution_user,count(*) FROM mpp_occurrence WHERE execution_user IS NOT NULL GROUP BY analysis_id,execution_user;

-- The remainder is synchronized with the 1.13.0 definitions in schema.sql.
CREATE OR REPLACE FUNCTION mpp_maintain_text_count() RETURNS trigger LANGUAGE plpgsql AS $function$
DECLARE delta bigint := 0;
BEGIN
    IF TG_OP='TRUNCATE' THEN
        UPDATE mpp_sql_text_count SET item_count=0;
    ELSE
        IF TG_OP IN ('DELETE','UPDATE') THEN SELECT -count(*) INTO delta FROM old_rows; END IF;
        IF TG_OP IN ('INSERT','UPDATE') THEN delta := delta + (SELECT count(*) FROM new_rows); END IF;
        IF delta<>0 THEN UPDATE mpp_sql_text_count SET item_count=item_count+delta; END IF;
    END IF;
    RETURN NULL;
END $function$;

CREATE OR REPLACE FUNCTION mpp_maintain_fingerprint_count() RETURNS trigger LANGUAGE plpgsql AS $function$
BEGIN
    IF TG_OP='TRUNCATE' THEN
        DELETE FROM mpp_fingerprint_count;
    ELSE
        IF TG_OP IN ('DELETE','UPDATE') THEN
            UPDATE mpp_fingerprint_count c SET item_count=c.item_count-r.n
                FROM (SELECT normalization_id,count(*) n FROM old_rows GROUP BY normalization_id) r
                WHERE c.normalization_id=r.normalization_id;
        END IF;
        IF TG_OP IN ('INSERT','UPDATE') THEN
            INSERT INTO mpp_fingerprint_count SELECT normalization_id,count(*) FROM new_rows
                GROUP BY normalization_id ORDER BY normalization_id
                ON CONFLICT (normalization_id) DO UPDATE
                    SET item_count=mpp_fingerprint_count.item_count+excluded.item_count;
        END IF;
    END IF;
    DELETE FROM mpp_fingerprint_count WHERE item_count=0;
    RETURN NULL;
END $function$;

CREATE OR REPLACE FUNCTION mpp_maintain_occurrence_names() RETURNS trigger LANGUAGE plpgsql AS $function$
BEGIN
    IF TG_OP='TRUNCATE' THEN
        DELETE FROM mpp_occurrence_name;
    ELSE
        IF TG_OP IN ('DELETE','UPDATE') THEN
            UPDATE mpp_occurrence_name c SET item_count=c.item_count-r.n
                FROM (SELECT analysis_id,v.kind,v.name,count(*) n FROM old_rows
                    CROSS JOIN LATERAL (VALUES ('database',database),('user',execution_user)) v(kind,name)
                    WHERE v.name IS NOT NULL GROUP BY analysis_id,v.kind,v.name) r
                WHERE (c.analysis_id,c.kind,c.name)=(r.analysis_id,r.kind,r.name);
        END IF;
        IF TG_OP IN ('INSERT','UPDATE') THEN
            INSERT INTO mpp_occurrence_name
                SELECT analysis_id,v.kind,v.name,count(*) FROM new_rows
                CROSS JOIN LATERAL (VALUES ('database',database),('user',execution_user)) v(kind,name)
                WHERE v.name IS NOT NULL GROUP BY analysis_id,v.kind,v.name ORDER BY analysis_id,v.kind,v.name
                ON CONFLICT (analysis_id,kind,name) DO UPDATE
                    SET item_count=mpp_occurrence_name.item_count+excluded.item_count;
        END IF;
    END IF;
    DELETE FROM mpp_occurrence_name WHERE item_count=0;
    RETURN NULL;
END $function$;

DO $block$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_trigger WHERE tgrelid='mpp_sql_text'::regclass AND tgname='mpp_sql_text_summary_insert') THEN
        CREATE TRIGGER mpp_sql_text_summary_insert AFTER INSERT ON mpp_sql_text
            REFERENCING NEW TABLE AS new_rows
            FOR EACH STATEMENT EXECUTE FUNCTION mpp_maintain_text_count();
    END IF;
END $block$;
DO $block$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_trigger WHERE tgrelid='mpp_sql_text'::regclass AND tgname='mpp_sql_text_summary_delete') THEN
        CREATE TRIGGER mpp_sql_text_summary_delete AFTER DELETE ON mpp_sql_text
            REFERENCING OLD TABLE AS old_rows
            FOR EACH STATEMENT EXECUTE FUNCTION mpp_maintain_text_count();
    END IF;
END $block$;
DO $block$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_trigger WHERE tgrelid='mpp_sql_text'::regclass AND tgname='mpp_sql_text_summary_update') THEN
        CREATE TRIGGER mpp_sql_text_summary_update AFTER UPDATE ON mpp_sql_text
            REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows
            FOR EACH STATEMENT EXECUTE FUNCTION mpp_maintain_text_count();
    END IF;
END $block$;
DO $block$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_trigger WHERE tgrelid='mpp_sql_text'::regclass AND tgname='mpp_sql_text_summary_truncate') THEN
        CREATE TRIGGER mpp_sql_text_summary_truncate AFTER TRUNCATE ON mpp_sql_text
            FOR EACH STATEMENT EXECUTE FUNCTION mpp_maintain_text_count();
    END IF;
END $block$;
DO $block$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_trigger WHERE tgrelid='mpp_fingerprint'::regclass AND tgname='mpp_fingerprint_summary_insert') THEN
        CREATE TRIGGER mpp_fingerprint_summary_insert AFTER INSERT ON mpp_fingerprint
            REFERENCING NEW TABLE AS new_rows
            FOR EACH STATEMENT EXECUTE FUNCTION mpp_maintain_fingerprint_count();
    END IF;
END $block$;
DO $block$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_trigger WHERE tgrelid='mpp_fingerprint'::regclass AND tgname='mpp_fingerprint_summary_delete') THEN
        CREATE TRIGGER mpp_fingerprint_summary_delete AFTER DELETE ON mpp_fingerprint
            REFERENCING OLD TABLE AS old_rows
            FOR EACH STATEMENT EXECUTE FUNCTION mpp_maintain_fingerprint_count();
    END IF;
END $block$;
DO $block$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_trigger WHERE tgrelid='mpp_fingerprint'::regclass AND tgname='mpp_fingerprint_summary_update') THEN
        CREATE TRIGGER mpp_fingerprint_summary_update AFTER UPDATE ON mpp_fingerprint
            REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows
            FOR EACH STATEMENT EXECUTE FUNCTION mpp_maintain_fingerprint_count();
    END IF;
END $block$;
DO $block$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_trigger WHERE tgrelid='mpp_fingerprint'::regclass AND tgname='mpp_fingerprint_summary_truncate') THEN
        CREATE TRIGGER mpp_fingerprint_summary_truncate AFTER TRUNCATE ON mpp_fingerprint
            FOR EACH STATEMENT EXECUTE FUNCTION mpp_maintain_fingerprint_count();
    END IF;
END $block$;
DO $block$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_trigger WHERE tgrelid='mpp_occurrence'::regclass AND tgname='mpp_occurrence_summary_insert') THEN
        CREATE TRIGGER mpp_occurrence_summary_insert AFTER INSERT ON mpp_occurrence
            REFERENCING NEW TABLE AS new_rows
            FOR EACH STATEMENT EXECUTE FUNCTION mpp_maintain_occurrence_names();
    END IF;
END $block$;
DO $block$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_trigger WHERE tgrelid='mpp_occurrence'::regclass AND tgname='mpp_occurrence_summary_delete') THEN
        CREATE TRIGGER mpp_occurrence_summary_delete AFTER DELETE ON mpp_occurrence
            REFERENCING OLD TABLE AS old_rows
            FOR EACH STATEMENT EXECUTE FUNCTION mpp_maintain_occurrence_names();
    END IF;
END $block$;
DO $block$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_trigger WHERE tgrelid='mpp_occurrence'::regclass AND tgname='mpp_occurrence_summary_update') THEN
        CREATE TRIGGER mpp_occurrence_summary_update AFTER UPDATE ON mpp_occurrence
            REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows
            FOR EACH STATEMENT EXECUTE FUNCTION mpp_maintain_occurrence_names();
    END IF;
END $block$;
DO $block$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_trigger WHERE tgrelid='mpp_occurrence'::regclass AND tgname='mpp_occurrence_summary_truncate') THEN
        CREATE TRIGGER mpp_occurrence_summary_truncate AFTER TRUNCATE ON mpp_occurrence
            FOR EACH STATEMENT EXECUTE FUNCTION mpp_maintain_occurrence_names();
    END IF;
END $block$;

CREATE OR REPLACE FUNCTION mpp_query_missing_rules(p_normalization text) RETURNS bigint
LANGUAGE sql STABLE
BEGIN ATOMIC
    SELECT t.item_count-coalesce(f.item_count,0) FROM mpp_sql_text_count t
        LEFT JOIN mpp_fingerprint_count f ON f.normalization_id=p_normalization;
END;

CREATE OR REPLACE FUNCTION mpp_training_decisions(selected_input text, selected_config text,
    selected_analysis text DEFAULT NULL, selected_occurrence text DEFAULT NULL)
RETURNS TABLE (analysis_id text, occurrence_id text, scope_id text, timing_type text,
    fingerprint_id text, group_id text, state text, in_window boolean, count_scope text,
    reason_codes text[], reasons jsonb, rule_evaluations jsonb,
    category_kind text, categories text[], template_ids text[], interval_ids text[],
    estimated_start_at timestamptz, duration_ms numeric)
LANGUAGE sql STABLE
BEGIN ATOMIC
WITH requested AS MATERIALIZED (
    -- Keep an unseen, newly imported analysis out of constant selectivity
    -- estimates. Both existing key columns remain index conditions.
    SELECT selected_analysis AS analysis_id,selected_occurrence AS occurrence_id
), selected_occurrences AS NOT MATERIALIZED (
    -- The unfiltered build path folds to the original relation when inlined.
    SELECT o.* FROM mpp_occurrence o
    WHERE (selected_analysis IS NULL OR selected_occurrence IS NULL)
        AND (selected_analysis IS NULL OR o.analysis_id=selected_analysis)
        AND (selected_occurrence IS NULL OR o.occurrence_id=selected_occurrence)
    UNION ALL
    SELECT o.* FROM requested k JOIN mpp_occurrence o USING(analysis_id,occurrence_id)
    WHERE selected_analysis IS NOT NULL AND selected_occurrence IS NOT NULL
), context AS MATERIALIZED (
    SELECT c.*,tc.rule_id,tc.decision_version,r.template_rules
    FROM config_snapshot c JOIN training_config tc USING(config_id)
    JOIN mpp_training_rule r USING(rule_id)
    JOIN input_snapshot i ON i.scope_id=c.scope_id AND i.input_id=selected_input
    JOIN input_manifest im ON im.input_id=i.input_id
    WHERE c.config_id=selected_config AND training_version(tc.decision_version)
), facts AS (
    SELECT o.*,c.config_id,c.rule_id,c.normalization_id,c.profile,
        s.category_kind,s.categories,f.fingerprint_id,f.value AS fingerprint_value,
        f.state AS fingerprint_state,
        o.estimated_start_at>=c.window_start AND o.estimated_start_at<c.window_end AS in_window,
        ARRAY(SELECT t->>'id' FROM jsonb_array_elements(c.template_rules) t
              WHERE t->>'id'=ANY(s.template_ids)
              AND (NOT t ? 'cluster' OR t->>'cluster'=o.scope_id)
              AND (NOT t ? 'database' OR t->>'database'=o.database)
              AND (NOT t ? 'execution_user' OR t->>'execution_user'=o.execution_user)
              ORDER BY t->>'id') AS templates,
        EXISTS(SELECT FROM jsonb_array_elements(c.template_rules) t
              WHERE t->>'id'=ANY(s.template_ids)
              AND (NOT t ? 'cluster' OR t->>'cluster'=o.scope_id)
              AND (NOT t ? 'database' OR o.database IS NULL OR t->>'database'=o.database)
              AND (NOT t ? 'execution_user' OR o.execution_user IS NULL OR t->>'execution_user'=o.execution_user)
              AND ((t ? 'database' AND o.database IS NULL) OR (t ? 'execution_user' AND o.execution_user IS NULL))) AS template_unknown,
        ARRAY(SELECT e->>'rule_id' FROM jsonb_array_elements(c.exclusions) e
              WHERE e->>'scope_id'=o.scope_id AND o.estimated_start_at IS NOT NULL
              AND CASE WHEN o.duration_ms=0 THEN
                  o.estimated_start_at>=(e->>'start')::timestamptz AND o.estimated_start_at<(e->>'end')::timestamptz
                  ELSE o.estimated_start_at<(e->>'end')::timestamptz AND o.end_at>(e->>'start')::timestamptz END
              ORDER BY e->>'rule_id') AS intervals
    FROM context c JOIN input_file_analysis m ON m.input_id=selected_input
    JOIN evidence_record e ON e.file_id=m.file_id
    JOIN selected_occurrences o ON o.analysis_id=m.analysis_id AND o.anchor_ref=e.record_id
    LEFT JOIN mpp_training_sql s ON s.sql_id=o.sql_id AND s.rule_id=c.rule_id
    LEFT JOIN mpp_fingerprint f ON f.fingerprint_id=s.fingerprint_id
    WHERE CASE WHEN o.sql_id IS NULL OR s.sql_id IS NOT NULL THEN true
               ELSE training_cache_required() END
), flags AS (
    SELECT facts.*,
        CASE WHEN database IS NOT NULL AND execution_user IS NOT NULL AND timing_type IS NOT NULL
                  AND fingerprint_state='reliable' AND association_state='reliable'
             THEN 'G:'||encode(sha256(convert_to(jsonb_build_array(scope_id,profile,normalization_id,
                  database,execution_user,fingerprint_value,timing_type)::text,'UTF8')),'hex') END AS group_key,
        array_remove(ARRAY[
            CASE outcome WHEN 'failed' THEN 'execution_failed' WHEN 'cancelled' THEN 'execution_cancelled'
                 WHEN 'timed_out' THEN 'execution_timed_out' WHEN 'unknown' THEN 'outcome_unknown' END,
            CASE WHEN duration_ms IS NULL THEN 'duration_unknown' END,
            CASE WHEN association_state<>'reliable' THEN 'association_unreliable' END,
            CASE WHEN timing_type IS NULL THEN 'timing_unknown' END,
            CASE sql_state WHEN 'missing' THEN 'sql_missing' WHEN 'incomplete' THEN 'sql_incomplete'
                 WHEN 'invalid_encoding' THEN 'sql_encoding_invalid' WHEN 'uncertain' THEN 'sql_uncertain' END,
            CASE WHEN sql_id IS NOT NULL AND fingerprint_state IS DISTINCT FROM 'reliable' THEN 'fingerprint_failed' END,
            CASE WHEN database IS NULL OR execution_user IS NULL THEN 'identity_missing' END,
            CASE WHEN estimated_start_at IS NULL THEN 'start_unknown' END,
            CASE WHEN category_kind='pure' THEN 'blacklist_category' END,
            CASE WHEN cardinality(templates)>0 THEN 'blacklist_template' END,
            CASE WHEN cardinality(intervals)>0 THEN 'excluded_interval' END,
            CASE WHEN in_window IS FALSE THEN 'outside_window' END
        ],NULL) AS codes
    FROM facts
), states AS (
    SELECT flags.*, CASE WHEN in_window IS FALSE THEN 'outside_window'
        WHEN codes && ARRAY['execution_failed','execution_cancelled','execution_timed_out',
                            'blacklist_category','blacklist_template','excluded_interval'] THEN 'excluded'
        WHEN cardinality(codes)>0 THEN 'unresolved' ELSE 'included' END AS decision_state
    FROM flags
)
SELECT analysis_id,occurrence_id,scope_id,timing_type,fingerprint_id,group_key,decision_state,in_window,
    CASE WHEN in_window IS FALSE THEN 'none'
         WHEN group_key IS NOT NULL AND estimated_start_at IS NOT NULL THEN 'group' ELSE 'batch' END,
    codes,
    (SELECT coalesce(jsonb_agg(jsonb_build_object('code',code,'rule_ref',
        CASE code WHEN 'blacklist_category' THEN rule_id||'/categories'
                  WHEN 'blacklist_template' THEN config_id||'/templates'
                  WHEN 'excluded_interval' THEN config_id||'/exclusions'
                  WHEN 'outside_window' THEN config_id||'/window'
                  ELSE 'training-decision/1/'||code END,
        'evidence_refs',jsonb_build_array(anchor_ref)) ORDER BY ord),'[]') FROM unnest(codes) WITH ORDINALITY AS r(code,ord)),
    jsonb_build_object(
        'outcome',CASE WHEN outcome='success' THEN 'not_matched' ELSE 'matched' END,
        'duration',CASE WHEN duration_ms IS NULL THEN 'matched' ELSE 'not_matched' END,
        'association',CASE WHEN association_state<>'reliable' OR timing_type IS NULL THEN 'matched' ELSE 'not_matched' END,
        'sql',CASE WHEN sql_state<>'complete' THEN 'matched' ELSE 'not_matched' END,
        'fingerprint',CASE WHEN sql_id IS NULL THEN 'not_evaluated' WHEN fingerprint_state='reliable' THEN 'not_matched' ELSE 'matched' END,
        'blacklist',CASE WHEN category_kind='pure' OR cardinality(templates)>0 THEN 'matched'
                        WHEN template_unknown OR category_kind IS NULL OR category_kind='unknown' OR fingerprint_state IS DISTINCT FROM 'reliable'
                        THEN 'not_evaluated' ELSE 'not_matched' END,
        'exclusion_interval',CASE WHEN estimated_start_at IS NULL OR duration_ms IS NULL OR end_at IS NULL THEN 'not_evaluated'
                                 WHEN cardinality(intervals)>0 THEN 'matched' ELSE 'not_matched' END,
        'window',CASE WHEN in_window IS NULL THEN 'not_evaluated' WHEN in_window THEN 'not_matched' ELSE 'matched' END),
    category_kind,categories,templates,intervals,estimated_start_at,duration_ms
FROM states;
END;

CREATE OR REPLACE FUNCTION mpp_query_training(p_build text,p_records jsonb)
RETURNS TABLE(analysis_id text,occurrence_id text,decision text,reason_codes text[],detail jsonb)
LANGUAGE plpgsql STABLE AS $function$
DECLARE b record; r record; anchor text;
BEGIN
    SELECT x.input_id,x.config_id,t.decision_version INTO b FROM build x
        LEFT JOIN training_config t USING(config_id) WHERE x.build_id=p_build;
    IF NOT FOUND THEN RAISE EXCEPTION 'published_version_not_found'; END IF;
    IF jsonb_typeof(p_records) IS DISTINCT FROM 'array' OR jsonb_array_length(p_records)>1000 THEN
        RAISE EXCEPTION 'invalid_training_records'; END IF;
    FOR r IN SELECT * FROM jsonb_to_recordset(p_records) AS x(analysis_id text,occurrence_id text) LOOP
        IF r.analysis_id IS NULL OR r.occurrence_id IS NULL THEN RAISE EXCEPTION 'invalid_training_records'; END IF;
        WITH requested AS MATERIALIZED (SELECT r.analysis_id AS analysis_id,r.occurrence_id AS occurrence_id)
        SELECT o.anchor_ref INTO anchor FROM requested k JOIN mpp_occurrence o USING(analysis_id,occurrence_id);
        IF NOT FOUND THEN RAISE EXCEPTION 'occurrence_not_found'; END IF;
        IF NOT EXISTS(SELECT FROM evidence_record e JOIN input_file_analysis i ON i.file_id=e.file_id
            WHERE e.record_id=anchor AND i.input_id=b.input_id AND i.analysis_id=r.analysis_id) THEN
            RETURN QUERY SELECT r.analysis_id,r.occurrence_id,'not_in_version_input'::text,ARRAY[]::text[],'{}'::jsonb;
        ELSIF b.decision_version IS DISTINCT FROM 'training-decision/1' THEN
            RETURN QUERY SELECT r.analysis_id,r.occurrence_id,'decision_unavailable'::text,
                ARRAY['unsupported_decision_version'],jsonb_build_object('decision_version',b.decision_version);
        ELSE
            RETURN QUERY SELECT d.analysis_id,d.occurrence_id,d.state,d.reason_codes,
                to_jsonb(d)-ARRAY['analysis_id','occurrence_id','state','reason_codes']
                FROM mpp_training_decisions(b.input_id,b.config_id,r.analysis_id,r.occurrence_id) d;
        END IF;
    END LOOP;
END $function$;


CREATE OR REPLACE FUNCTION mpp_view_baseline_ranking(p_normalization text,
    p_scope text DEFAULT NULL,p_database text DEFAULT NULL,p_user text DEFAULT NULL,
    p_timings text DEFAULT 'request,execute_first',p_order text DEFAULT 'p95',
    p_min_samples bigint DEFAULT 0,p_limit integer DEFAULT 100)
RETURNS TABLE(scope_id text,database text,execution_user text,fingerprint text,timing_type text,build_id text,
    included_count bigint,active_days integer,p50_ms numeric,p95_ms numeric,p99_ms numeric,max_ms numeric,
    mean_ms numeric,basic_met boolean,p95_met boolean,p99_met boolean,identity text,ranked_rows bigint)
LANGUAGE plpgsql SET plan_cache_mode=force_custom_plan
    SET parallel_setup_cost=0 SET parallel_tuple_cost=0 AS $function$
DECLARE wanted text[] := string_to_array(nullif(p_timings,''),','); current record; partitions bigint[] := '{}'; builds text[] := '{}';
BEGIN
    IF p_order IS NULL OR p_order NOT IN ('samples','p50','p95','p99','max','mean') THEN RAISE EXCEPTION 'invalid_ranking_order'; END IF;
    IF p_limit IS NULL OR p_limit<1 OR p_limit>1000 THEN RAISE EXCEPTION 'invalid_page_size'; END IF;
    FOR current IN SELECT v.build_id FROM current_version v JOIN build b USING(build_id)
        WHERE v.build_id IS NOT NULL AND b.normalization_id=p_normalization AND (p_scope IS NULL OR v.scope_id=p_scope) LOOP
        BEGIN
            partitions := partitions || mpp_require_results(current.build_id);
            builds := builds || current.build_id;
        EXCEPTION WHEN raise_exception THEN
            IF SQLERRM<>'results_cleaned' THEN RAISE; END IF;
        END;
    END LOOP;
    RETURN QUERY
    SELECT r.scope_id,r.database,r.execution_user,r.fingerprint_value,r.timing_type,r.build_id,r.included_count,
        cardinality(coverage.active_dates),r.p50_ms,r.p95_ms,r.p99_ms,r.max_ms,r.mean_ms,
        (x.sufficiency->'basic'->>'met')::boolean,(x.sufficiency->'p95'->>'met')::boolean,
        (x.sufficiency->'p99'->>'met')::boolean,mpp_view_identity_token(r.scope_id,r.database,r.execution_user),r.rows
    FROM (SELECT g.scope_id,g.database,g.execution_user,g.fingerprint_value,g.timing_type,s.build_id,s.included_count,
            s.partition_id,s.group_id,s.p50_ms,s.p95_ms,s.p99_ms,s.max_ms,s.mean_ms,count(*) OVER () rows
        FROM mpp_statistic s JOIN mpp_baseline_group g USING(group_id)
        WHERE s.partition_id=ANY(partitions) AND s.build_id=ANY(builds) AND s.layer='overall'
            AND s.included_count>0 AND s.included_count>=coalesce(p_min_samples,0)
            AND (wanted IS NULL OR g.timing_type=ANY(wanted))
            AND (p_database IS NULL OR g.database=p_database)
            AND (p_user IS NULL OR g.execution_user=p_user)
        ORDER BY CASE p_order WHEN 'samples' THEN s.included_count WHEN 'p50' THEN s.p50_ms WHEN 'p95' THEN s.p95_ms
                WHEN 'p99' THEN s.p99_ms WHEN 'max' THEN s.max_ms ELSE s.mean_ms END DESC NULLS LAST,s.group_id
        LIMIT p_limit) r
    -- Fetch coverage arrays only for the bounded output page. Keeping them out
    -- of the ranking scan also keeps the covering index independent of window size.
    JOIN mpp_statistic coverage ON coverage.partition_id=r.partition_id AND coverage.build_id=r.build_id
        AND coverage.group_id=r.group_id AND coverage.layer='overall'
        AND coverage.partition_id=ANY(partitions) AND coverage.build_id=ANY(builds)
    JOIN build b ON b.build_id=r.build_id JOIN config_snapshot c USING(config_id)
    CROSS JOIN LATERAL (SELECT mpp_statistic_sufficiency(c.statistics_version,c.thresholds,'overall',
        r.included_count,coverage.active_dates,coverage.active_week_starts) sufficiency) x
    ORDER BY CASE p_order WHEN 'samples' THEN r.included_count WHEN 'p50' THEN r.p50_ms WHEN 'p95' THEN r.p95_ms
            WHEN 'p99' THEN r.p99_ms WHEN 'max' THEN r.max_ms ELSE r.mean_ms END DESC NULLS LAST,
        r.scope_id,r.database,r.execution_user,r.fingerprint_value,r.timing_type;
END $function$;

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
