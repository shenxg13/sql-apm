SELECT set_config('search_path',quote_ident(current_setting('apm.schema'))||',pg_catalog',true);
ALTER TABLE mpp_sql_text ADD COLUMN search_text text GENERATED ALWAYS AS (translate(text,E'ABCDEFGHIJKLMNOPQRSTUVWXYZ \t\n\r\f\013','abcdefghijklmnopqrstuvwxyz')) STORED;

-- Search/query API v1. ASCII folding is independent of database collation.
CREATE OR REPLACE FUNCTION mpp_search_fold(value text) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE
RETURN translate(value,E'ABCDEFGHIJKLMNOPQRSTUVWXYZ \t\n\r\f\013','abcdefghijklmnopqrstuvwxyz');

CREATE OR REPLACE FUNCTION mpp_search_terms(value text) RETURNS text[]
LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE AS $function$
DECLARE terms text[] := '{}'; token text := ''; c text; i integer := 1; close_at integer;
BEGIN
    WHILE i <= length(value) LOOP
        c := substr(value,i,1);
        IF c='"' THEN
            close_at := strpos(substr(value,i+1),'"');
            IF close_at>0 THEN
                token := token || substr(value,i+1,close_at-1);
                i := i+close_at+1;
                CONTINUE;
            END IF;
        END IF;
        IF strpos(E' \t\n\r\f\013',c)>0 THEN
            IF token<>'' THEN terms:=array_append(terms,token); token:=''; END IF;
        ELSE token:=token||c;
        END IF;
        i:=i+1;
    END LOOP;
    IF token<>'' THEN terms:=array_append(terms,token); END IF;
    terms := ARRAY(SELECT mpp_search_fold(t) FROM unnest(terms) t WHERE mpp_search_fold(t)<>'');
    IF cardinality(terms)=0 THEN RAISE EXCEPTION 'empty_search_input'; END IF;
    IF cardinality(terms)>20 THEN RAISE EXCEPTION 'too_many_search_terms'; END IF;
    RETURN terms;
END $function$;

CREATE OR REPLACE FUNCTION mpp_query_occurrences(
    p_normalization text, p_fingerprint text DEFAULT NULL, p_scope text DEFAULT NULL,
    p_database text DEFAULT NULL, p_user text DEFAULT NULL,
    p_start timestamptz DEFAULT NULL, p_end timestamptz DEFAULT NULL)
RETURNS TABLE (analysis_id text, occurrence_id text, scope_id text, database text,
    execution_user text, fingerprint text, timing_type text, unit text, request_shape text,
    outcome text, end_at timestamptz, estimated_start_at timestamptz, duration_ms numeric,
    sql_id text, file_id text, record_no bigint, line_start bigint, line_end bigint)
LANGUAGE sql STABLE
BEGIN ATOMIC
    SELECT o.analysis_id,o.occurrence_id,o.scope_id,o.database,o.execution_user,f.value,
        o.timing_type,o.unit,o.request_shape,o.outcome,o.end_at,
        CASE WHEN o.timing_type IS NOT NULL THEN o.estimated_start_at END,
        CASE WHEN o.timing_type IS NOT NULL THEN o.duration_ms END,
        o.sql_id,e.file_id,e.record_no,e.line_start,e.line_end
    FROM mpp_fingerprint f JOIN mpp_occurrence o USING(sql_id)
    JOIN evidence_record e ON e.record_id=o.anchor_ref
    WHERE f.normalization_id=p_normalization AND f.profile='mpp-csv/1' AND f.state='reliable'
        AND (p_fingerprint IS NULL OR f.value=p_fingerprint)
        AND (p_scope IS NULL OR o.scope_id=p_scope)
        AND (p_database IS NULL OR o.database=p_database)
        AND (p_user IS NULL OR o.execution_user=p_user)
        AND (p_start IS NULL OR o.end_at>=p_start) AND (p_end IS NULL OR o.end_at<p_end);
END;

CREATE OR REPLACE FUNCTION mpp_query_versions(p_normalization text,p_scope text DEFAULT NULL)
RETURNS TABLE (scope_id text, build_id text, built_at timestamptz, published_at timestamptz,
    window_start timestamptz, window_end timestamptz, window_days integer,
    normalization_id text, algorithm_version text, parser_version text, dictionary_rules_version text,
    is_current boolean, results_cleaned boolean, cleaned_at timestamptz, rules_match boolean)
LANGUAGE sql STABLE
BEGIN ATOMIC
    SELECT b.scope_id,b.build_id,b.finished_at,max(p.at),c.window_start,c.window_end,c.window_days,
        b.normalization_id,n.algorithm_version,n.parser_version,n.dictionary_rules_version,
        coalesce(v.build_id=b.build_id,false),r.cleaned_at IS NOT NULL,r.cleaned_at,
        b.normalization_id=p_normalization
    FROM build b JOIN publication p ON p.build_id=b.build_id AND p.result='published'
    JOIN config_snapshot c USING(config_id) JOIN mpp_normalization n ON n.normalization_id=b.normalization_id
    LEFT JOIN current_version v ON v.scope_id=b.scope_id
    LEFT JOIN mpp_result_partition r ON r.partition_id=b.partition_id
    WHERE p_scope IS NULL OR b.scope_id=p_scope
    GROUP BY b.scope_id,b.build_id,c.config_id,n.normalization_id,v.build_id,r.partition_id;
END;

CREATE OR REPLACE FUNCTION mpp_query_hits(
    p_normalization text,p_fingerprint text,p_scope text DEFAULT NULL,
    p_database text DEFAULT NULL,p_user text DEFAULT NULL)
RETURNS TABLE (scope_id text,database text,execution_user text,fingerprint text,
    record_count bigint,first_at timestamptz,last_at timestamptz,timing_types text[],
    has_unknown_timing boolean,has_baseline boolean,current_build text,current_rules_match boolean)
LANGUAGE sql STABLE
BEGIN ATOMIC
    WITH hits AS MATERIALIZED (
        SELECT o.scope_id,o.database,o.execution_user,count(*) records,min(o.end_at) first_at,
            max(o.end_at) last_at,array_remove(array_agg(DISTINCT o.timing_type ORDER BY o.timing_type),NULL) timings,
            bool_or(o.timing_type IS NULL) unknown_timing
        FROM mpp_query_occurrences(p_normalization,p_fingerprint,p_scope,p_database,p_user) o
        GROUP BY o.scope_id,o.database,o.execution_user)
    SELECT h.scope_id,h.database,h.execution_user,p_fingerprint,h.records,h.first_at,h.last_at,
        h.timings,h.unknown_timing,EXISTS(
            SELECT FROM mpp_baseline_group g JOIN mpp_statistic s USING(group_id)
            WHERE g.scope_id=h.scope_id AND g.database=h.database AND g.execution_user=h.execution_user
                AND g.normalization_id=p_normalization AND g.fingerprint_value=p_fingerprint
                AND s.build_id=v.build_id AND s.layer='overall'),v.build_id,
        coalesce(b.normalization_id=p_normalization,false)
    FROM hits h LEFT JOIN current_version v ON v.scope_id=h.scope_id LEFT JOIN build b ON b.build_id=v.build_id
    ORDER BY h.records DESC,h.scope_id,h.database,h.execution_user;
END;

CREATE OR REPLACE FUNCTION mpp_query_fuzzy(
    p_normalization text,p_input text,p_scope text DEFAULT NULL,p_database text DEFAULT NULL,
    p_user text DEFAULT NULL,p_start timestamptz DEFAULT NULL,p_end timestamptz DEFAULT NULL,
    p_order text DEFAULT 'count')
RETURNS TABLE (fingerprint text,example_sql_id text,matched_texts bigint,record_count bigint,
    scopes text[],databases text[],execution_users text[],last_at timestamptz,total_structures bigint)
LANGUAGE plpgsql STABLE AS $function$
DECLARE terms text[];
BEGIN
    terms:=mpp_search_terms(p_input);
    IF p_order IS NULL OR p_order NOT IN ('count','recent') THEN RAISE EXCEPTION 'invalid_search_order'; END IF;
    IF p_start>=p_end THEN RAISE EXCEPTION 'invalid_time_range'; END IF;
    RETURN QUERY
    WITH matched AS MATERIALIZED (
        SELECT t.sql_id,f.value FROM mpp_sql_text t JOIN mpp_fingerprint f USING(sql_id)
        WHERE f.normalization_id=p_normalization AND f.profile='mpp-csv/1' AND f.state='reliable'
          AND NOT EXISTS (SELECT FROM unnest(terms) term WHERE strpos(t.search_text,term)=0)
    ), grouped AS MATERIALIZED (
        SELECT m.value,min(m.sql_id) example_sql_id,count(DISTINCT m.sql_id) matched_texts,
            count(*) records,max(o.end_at) last_at,
            array_agg(DISTINCT o.scope_id ORDER BY o.scope_id) scopes,
            array_agg(DISTINCT o.database ORDER BY o.database) databases,
            array_agg(DISTINCT o.execution_user ORDER BY o.execution_user) users
        FROM matched m JOIN mpp_occurrence o ON o.sql_id=m.sql_id
        WHERE (p_scope IS NULL OR o.scope_id=p_scope)
            AND (p_database IS NULL OR o.database=p_database)
            AND (p_user IS NULL OR o.execution_user=p_user)
            AND (p_start IS NULL OR o.end_at>=p_start) AND (p_end IS NULL OR o.end_at<p_end)
        GROUP BY m.value
    )
    SELECT g.value,g.example_sql_id,g.matched_texts,g.records,g.scopes,g.databases,g.users,g.last_at,count(*) OVER ()
    FROM grouped g
    ORDER BY CASE WHEN p_order='count' THEN g.records END DESC,
        CASE WHEN p_order='recent' THEN g.last_at END DESC NULLS LAST,g.value
    LIMIT 50;
END $function$;


CREATE OR REPLACE FUNCTION mpp_query_missing_rules(p_normalization text) RETURNS bigint
LANGUAGE sql STABLE
BEGIN ATOMIC
    SELECT count(*) FROM mpp_sql_text t WHERE NOT EXISTS (
        SELECT FROM mpp_fingerprint f WHERE f.sql_id=t.sql_id AND f.normalization_id=p_normalization
            AND f.profile='mpp-csv/1');
END;

CREATE OR REPLACE FUNCTION mpp_query_text(p_sql_id text) RETURNS TABLE(sql_id text,sql_text text)
LANGUAGE sql STABLE
BEGIN ATOMIC
    SELECT t.sql_id,t.text FROM mpp_sql_text t WHERE t.sql_id=p_sql_id;
END;

CREATE OR REPLACE FUNCTION mpp_query_select_version(p_normalization text,p_scope text,p_build text DEFAULT NULL)
RETURNS text LANGUAGE plpgsql AS $function$
DECLARE chosen record;
BEGIN
    SELECT * INTO chosen FROM mpp_query_versions(p_normalization,p_scope) v
        WHERE CASE WHEN p_build IS NULL THEN v.is_current ELSE v.build_id=p_build END;
    IF NOT FOUND THEN
        IF p_build IS NULL THEN RAISE EXCEPTION 'no_current_baseline';
        ELSE RAISE EXCEPTION 'published_version_not_found'; END IF;
    END IF;
    PERFORM mpp_require_results(chosen.build_id);
    IF NOT chosen.rules_match THEN RAISE EXCEPTION 'normalization_version_mismatch'; END IF;
    RETURN chosen.build_id;
END $function$;

CREATE OR REPLACE FUNCTION mpp_query_training(p_build text,p_records jsonb)
RETURNS TABLE(analysis_id text,occurrence_id text,decision text,reason_codes text[],detail jsonb)
LANGUAGE plpgsql STABLE AS $function$
DECLARE b record; r record;
BEGIN
    SELECT x.input_id,x.config_id,t.decision_version INTO b FROM build x
        LEFT JOIN training_config t USING(config_id) WHERE x.build_id=p_build;
    IF NOT FOUND THEN RAISE EXCEPTION 'published_version_not_found'; END IF;
    IF jsonb_typeof(p_records) IS DISTINCT FROM 'array' OR jsonb_array_length(p_records)>1000 THEN
        RAISE EXCEPTION 'invalid_training_records'; END IF;
    FOR r IN SELECT * FROM jsonb_to_recordset(p_records) AS x(analysis_id text,occurrence_id text) LOOP
        IF r.analysis_id IS NULL OR r.occurrence_id IS NULL THEN RAISE EXCEPTION 'invalid_training_records'; END IF;
        IF NOT EXISTS(SELECT FROM mpp_occurrence o WHERE o.analysis_id=r.analysis_id AND o.occurrence_id=r.occurrence_id) THEN
            RAISE EXCEPTION 'occurrence_not_found';
        END IF;
        IF NOT EXISTS(SELECT FROM mpp_occurrence o JOIN evidence_record e ON e.record_id=o.anchor_ref
            JOIN input_file_analysis i ON i.file_id=e.file_id AND i.analysis_id=o.analysis_id
            WHERE i.input_id=b.input_id AND o.analysis_id=r.analysis_id AND o.occurrence_id=r.occurrence_id) THEN
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

CREATE OR REPLACE FUNCTION mpp_query_time_bounds(p_normalization text,p_fingerprint text,
    p_scope text,p_database text,p_user text,p_start timestamptz DEFAULT NULL,p_end timestamptz DEFAULT NULL)
RETURNS TABLE(start_at timestamptz,end_at timestamptz)
LANGUAGE sql STABLE
BEGIN ATOMIC
    SELECT coalesce(p_start,coalesce(p_end,max(o.end_at))-interval '7 days'),
        coalesce(p_end,max(o.end_at)+interval '1 microsecond')
    FROM mpp_query_occurrences(p_normalization,p_fingerprint,p_scope,p_database,p_user) o;
END;

CREATE OR REPLACE FUNCTION mpp_query_timeline(p_normalization text,p_fingerprint text,
    p_scope text,p_database text,p_user text,p_start timestamptz,p_end timestamptz,p_bucket text DEFAULT 'hour')
RETURNS TABLE(timing_type text,bucket_at timestamptz,record_count bigint,status_counts jsonb,
    known_duration_count bigint,p50_ms double precision,p95_ms double precision,max_ms numeric)
LANGUAGE plpgsql STABLE AS $function$
BEGIN
    IF p_bucket IS NULL OR p_bucket NOT IN ('hour','day') THEN RAISE EXCEPTION 'invalid_time_bucket'; END IF;
    IF p_start>=p_end THEN RAISE EXCEPTION 'invalid_time_range'; END IF;
    RETURN QUERY WITH facts AS MATERIALIZED (
        SELECT o.timing_type,date_trunc(p_bucket,o.end_at AT TIME ZONE 'Asia/Shanghai') AT TIME ZONE 'Asia/Shanghai' bucket_at,
            o.outcome,o.duration_ms
        FROM mpp_query_occurrences(p_normalization,p_fingerprint,p_scope,p_database,p_user,p_start,p_end) o
    ), counts AS (
        SELECT f.timing_type,f.bucket_at,jsonb_object_agg(f.outcome,f.n) status_counts
        FROM (SELECT a.timing_type,a.bucket_at,a.outcome,count(*) n FROM facts a
            GROUP BY a.timing_type,a.bucket_at,a.outcome) f GROUP BY f.timing_type,f.bucket_at
    ), metrics AS (
        SELECT a.timing_type,a.bucket_at,count(*) n,count(a.duration_ms) known,
            percentile_cont(0.5) WITHIN GROUP(ORDER BY a.duration_ms::double precision) p50,
            percentile_cont(0.95) WITHIN GROUP(ORDER BY a.duration_ms::double precision) p95,max(a.duration_ms) maximum
        FROM facts a GROUP BY a.timing_type,a.bucket_at
    ) SELECT m.timing_type,m.bucket_at,m.n,c.status_counts,m.known,m.p50,m.p95,m.maximum
        FROM metrics m JOIN counts c ON c.timing_type IS NOT DISTINCT FROM m.timing_type AND c.bucket_at IS NOT DISTINCT FROM m.bucket_at
        ORDER BY m.bucket_at,m.timing_type NULLS LAST;
END $function$;

CREATE OR REPLACE FUNCTION mpp_query_history(p_normalization text,p_fingerprint text,
    p_scope text,p_database text,p_user text,p_start timestamptz DEFAULT NULL,p_end timestamptz DEFAULT NULL,
    p_limit integer DEFAULT 100,p_cursor jsonb DEFAULT NULL,p_build text DEFAULT NULL,p_bucket text DEFAULT NULL)
RETURNS jsonb LANGUAGE plpgsql AS $function$
DECLARE bounds record; chosen text; rows jsonb; page jsonb; cursor_value jsonb; missing bigint;
BEGIN
    IF p_limit IS NULL OR p_limit<1 OR p_limit>1000 THEN RAISE EXCEPTION 'invalid_page_size'; END IF;
    IF p_start>=p_end THEN RAISE EXCEPTION 'invalid_time_range'; END IF;
    IF p_cursor IS NOT NULL AND (jsonb_typeof(p_cursor) IS DISTINCT FROM 'object'
        OR NOT p_cursor ?& ARRAY['end_at','analysis_id','occurrence_id']) THEN RAISE EXCEPTION 'invalid_page_cursor'; END IF;
    SELECT * INTO bounds FROM mpp_query_time_bounds(p_normalization,p_fingerprint,p_scope,p_database,p_user,p_start,p_end);
    IF p_build IS NOT NULL THEN chosen:=mpp_query_select_version(p_normalization,p_scope,p_build); END IF;
    IF p_bucket IS NOT NULL THEN
        SELECT coalesce(jsonb_agg(to_jsonb(t) ORDER BY t.bucket_at,t.timing_type),'[]') INTO page
            FROM mpp_query_timeline(p_normalization,p_fingerprint,p_scope,p_database,p_user,bounds.start_at,bounds.end_at,p_bucket) t;
    ELSE
        SELECT coalesce(jsonb_agg(to_jsonb(o) ORDER BY o.end_at DESC,o.analysis_id DESC,o.occurrence_id DESC),'[]') INTO rows
        FROM (SELECT * FROM mpp_query_occurrences(p_normalization,p_fingerprint,p_scope,p_database,p_user,bounds.start_at,bounds.end_at) x
            WHERE p_cursor IS NULL OR (x.end_at,x.analysis_id,x.occurrence_id)<
                ((p_cursor->>'end_at')::timestamptz,p_cursor->>'analysis_id',p_cursor->>'occurrence_id')
            ORDER BY x.end_at DESC,x.analysis_id DESC,x.occurrence_id DESC LIMIT p_limit+1) o;
        IF jsonb_array_length(rows)>p_limit THEN
            cursor_value:=jsonb_build_object('end_at',rows->(p_limit-1)->'end_at',
                'analysis_id',rows->(p_limit-1)->'analysis_id','occurrence_id',rows->(p_limit-1)->'occurrence_id');
        END IF;
        SELECT coalesce(jsonb_agg(x.value ORDER BY x.ordinality),'[]') INTO page
            FROM jsonb_array_elements(rows) WITH ORDINALITY x WHERE x.ordinality<=p_limit;
        IF chosen IS NOT NULL THEN
            SELECT coalesce(jsonb_agg(x.value||jsonb_build_object('training',to_jsonb(d)-ARRAY['analysis_id','occurrence_id']) ORDER BY x.ordinality),'[]') INTO page
            FROM jsonb_array_elements(page) WITH ORDINALITY x JOIN mpp_query_training(chosen,page) d
                ON d.analysis_id=x.value->>'analysis_id' AND d.occurrence_id=x.value->>'occurrence_id';
        END IF;
    END IF;
    missing:=mpp_query_missing_rules(p_normalization);
    RETURN jsonb_build_object('state','ok','start_at',bounds.start_at,'end_at',bounds.end_at,
        'build_id',chosen,'next_cursor',cursor_value,'groups',(
            SELECT coalesce(jsonb_agg(jsonb_build_object('timing_type',t.timing,
                'record_kind',CASE WHEN t.timing='request' THEN 'complete_request' WHEN t.timing IS NULL THEN 'unknown_timing' ELSE 'stage_or_call' END,
                'rows',coalesce((SELECT jsonb_agg(x.value ORDER BY x.ordinality) FROM jsonb_array_elements(page) WITH ORDINALITY x
                    WHERE x.value->>'timing_type' IS NOT DISTINCT FROM t.timing),'[]')) ORDER BY t.ord),'[]')
            FROM unnest(ARRAY['request','execute_first','execute_fetch','parse','bind',NULL]) WITH ORDINALITY t(timing,ord)))
        || CASE WHEN missing>0 THEN jsonb_build_object('warning',jsonb_build_object('reason','history_incomplete_current_rules',
            'missing_sql_texts',missing,'message','这些原文尚未按当前规则生成指纹，其执行记录不在结果中')) ELSE '{}'::jsonb END;
END $function$;

CREATE OR REPLACE FUNCTION mpp_query_observations(p_approximate text,p_scope text DEFAULT NULL,
    p_database text DEFAULT NULL,p_user text DEFAULT NULL)
RETURNS TABLE(scope_id text,database text,execution_user text,build_id text,timing_type text,statistic jsonb)
LANGUAGE sql
BEGIN ATOMIC
    SELECT g.scope_id,g.database,g.execution_user,v.build_id,g.timing_type,to_jsonb(s)
    FROM mpp_observation_group g JOIN current_version v USING(scope_id)
    CROSS JOIN LATERAL mpp_read_statistics(v.build_id,true,g.group_id) s
    WHERE g.approximate_value=p_approximate AND (p_scope IS NULL OR g.scope_id=p_scope)
        AND (p_database IS NULL OR g.database=p_database) AND (p_user IS NULL OR g.execution_user=p_user)
        AND s.layer='overall';
END;

CREATE OR REPLACE FUNCTION mpp_query_statistics(p_normalization text,p_scope text,p_database text,
    p_user text,p_fingerprint text,p_build text DEFAULT NULL,p_layer text DEFAULT 'overall')
RETURNS TABLE(build_id text,scope_id text,database text,execution_user text,fingerprint text,
    timing_type text,layer text,bucket_date date,bucket_number smallint,range_start timestamptz,range_end timestamptz,
    sample_state text,included_count bigint,active_days integer,excluded_count bigint,exclusions_by_reason jsonb,
    sufficiency jsonb,metric_null_reasons jsonb,
    min_ms numeric,
    max_ms numeric,
    mean_ms numeric,
    p25_ms numeric,
    p50_ms numeric,
    p75_ms numeric,
    p90_ms numeric,
    p95_ms numeric,
    p99_ms numeric,
    stddev_ms numeric,
    cv numeric,
    mad_ms numeric,
    iqr_ms numeric,
    log_median numeric,
    log_mad numeric,
    p95_p50 numeric,
    p99_p50 numeric)
LANGUAGE plpgsql AS $function$
DECLARE chosen text;
BEGIN
    IF p_layer IS NULL OR p_layer NOT IN ('overall','day','week','weekday','hour') THEN RAISE EXCEPTION 'invalid_baseline_layer'; END IF;
    chosen:=mpp_query_select_version(p_normalization,p_scope,p_build);
    RETURN QUERY
    SELECT chosen,p_scope,p_database,p_user,p_fingerprint,t.timing,p_layer,s.bucket_date,s.bucket_number,
        coalesce(s.range_start,c.window_start),coalesce(s.range_end,c.window_end),
        CASE WHEN coalesce(s.included_count,0)=0 THEN 'no_samples' ELSE 'available' END,
        coalesce(s.included_count,0),coalesce(cardinality(s.active_dates),0),coalesce(s.excluded_count,0),coalesce(s.exclusions_by_reason,'{}'),
        mpp_statistic_sufficiency(c.statistics_version,c.thresholds,p_layer,coalesce(s.included_count,0),
            coalesce(s.active_dates,'{}'),coalesce(s.active_week_starts,'{}')),
        coalesce(s.metric_null_reasons,jsonb_build_object('all','no_samples')),
        s.min_ms,s.max_ms,s.mean_ms,s.p25_ms,s.p50_ms,s.p75_ms,s.p90_ms,s.p95_ms,s.p99_ms,s.stddev_ms,s.cv,s.mad_ms,s.iqr_ms,s.log_median,s.log_mad,s.p95_p50,s.p99_p50
    FROM build b JOIN config_snapshot c USING(config_id)
    CROSS JOIN unnest(ARRAY['request','execute_first','execute_fetch','parse','bind']) WITH ORDINALITY t(timing,ord)
    LEFT JOIN mpp_baseline_group g ON g.scope_id=p_scope AND g.database=p_database AND g.execution_user=p_user
        AND g.normalization_id=p_normalization AND g.fingerprint_value=p_fingerprint AND g.timing_type=t.timing
    LEFT JOIN LATERAL (SELECT r.* FROM mpp_read_statistics(chosen,false,g.group_id) r
        WHERE g.group_id IS NOT NULL AND r.layer=p_layer) s ON true
    WHERE b.build_id=chosen ORDER BY t.ord,s.bucket_date,s.bucket_number;
END $function$;

CREATE OR REPLACE FUNCTION mpp_query_exact(p_normalization text,p_fingerprint text,p_approximate text DEFAULT NULL,
    p_scope text DEFAULT NULL,p_database text DEFAULT NULL,p_user text DEFAULT NULL,p_reason text DEFAULT NULL)
RETURNS jsonb LANGUAGE sql
BEGIN ATOMIC
    WITH hits AS MATERIALIZED (SELECT * FROM mpp_query_hits(p_normalization,p_fingerprint,p_scope,p_database,p_user)
        WHERE p_fingerprint IS NOT NULL)
    SELECT jsonb_build_object('match_kind','structural_fingerprint','fingerprint',p_fingerprint,
        'state',CASE WHEN p_fingerprint IS NULL THEN 'unreliable_fingerprint'
            WHEN NOT EXISTS(SELECT FROM hits) THEN 'not_seen'
            WHEN EXISTS(SELECT FROM hits WHERE has_baseline) THEN 'has_baseline' ELSE 'records_without_baseline' END,
        'reason',p_reason,'hits',coalesce((SELECT jsonb_agg(to_jsonb(h) ORDER BY h.record_count DESC,h.scope_id,h.database,h.execution_user) FROM hits h),'[]'),
        'observation_label',CASE WHEN p_fingerprint IS NULL AND p_approximate IS NOT NULL THEN '观察结果，不是正式基线' END,
        'observations',CASE WHEN p_fingerprint IS NULL AND p_approximate IS NOT NULL THEN
            coalesce((SELECT jsonb_agg(to_jsonb(o)) FROM mpp_query_observations(p_approximate,p_scope,p_database,p_user) o),'[]') ELSE '[]'::jsonb END);
END;

CREATE OR REPLACE FUNCTION mpp_query_search(p_normalization text,p_input text,p_scope text DEFAULT NULL,
    p_database text DEFAULT NULL,p_user text DEFAULT NULL,p_start timestamptz DEFAULT NULL,
    p_end timestamptz DEFAULT NULL,p_order text DEFAULT 'count') RETURNS jsonb LANGUAGE plpgsql AS $function$
DECLARE result jsonb;
BEGIN
    IF p_input ~ '^struct:[a-zA-Z0-9_./-]+:[0-9a-f]{64}$' THEN
        RETURN mpp_query_exact(p_normalization,p_input,NULL,p_scope,p_database,p_user);
    END IF;
    SELECT jsonb_build_object('match_kind','text','state','ok','total_structures',coalesce(max(f.total_structures),0),
        'rows',coalesce(jsonb_agg(to_jsonb(f)-'total_structures' ORDER BY
            CASE WHEN p_order='count' THEN f.record_count END DESC,
            CASE WHEN p_order='recent' THEN f.last_at END DESC NULLS LAST,f.fingerprint),'[]')) INTO result
    FROM mpp_query_fuzzy(p_normalization,p_input,p_scope,p_database,p_user,p_start,p_end,p_order) f;
    RETURN result;
END $function$;

CREATE OR REPLACE FUNCTION mpp_query_baseline(p_normalization text,p_scope text,p_database text,p_user text,
    p_fingerprint text,p_build text DEFAULT NULL,p_layer text DEFAULT 'overall') RETURNS jsonb LANGUAGE plpgsql AS $function$
DECLARE chosen text; result jsonb;
BEGIN
    chosen:=mpp_query_select_version(p_normalization,p_scope,p_build);
    SELECT jsonb_build_object('state','ok','version',to_jsonb(v),'layer',p_layer,
        'rows',(SELECT jsonb_agg(to_jsonb(s)) FROM mpp_query_statistics(p_normalization,p_scope,p_database,p_user,p_fingerprint,chosen,p_layer) s))
        INTO result FROM mpp_query_versions(p_normalization,p_scope) v WHERE v.build_id=chosen;
    RETURN result;
END $function$;

SET LOCAL search_path = pg_catalog;
