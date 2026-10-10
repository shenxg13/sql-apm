SELECT set_config('search_path',quote_ident(current_setting('apm.schema'))||',pg_catalog',true);
-- Changed signatures: the old overloads must not survive beside the new ones.
DROP FUNCTION mpp_query_search(text,text,text,text,text,timestamptz,timestamptz,text);
DROP FUNCTION mpp_query_fuzzy(text,text,text,text,text,timestamptz,timestamptz,text);

-- 1.11.0: one cutting rule for every entry. Words are split on the six ASCII
-- whitespace characters only; quotes are ordinary characters.
CREATE OR REPLACE FUNCTION mpp_search_terms(value text) RETURNS text[]
LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE AS $function$
DECLARE terms text[];
BEGIN
    terms := ARRAY(SELECT mpp_search_fold(t)
        FROM regexp_split_to_table(value,E'[ \t\n\r\f\013]+') t WHERE t<>'');
    IF cardinality(terms)=0 THEN RAISE EXCEPTION 'empty_search_input'; END IF;
    IF cardinality(terms)>20 THEN RAISE EXCEPTION 'too_many_search_terms'; END IF;
    RETURN terms;
END $function$;

-- The whole input is one contiguous passage; there is no term limit.
CREATE OR REPLACE FUNCTION mpp_search_passage(value text) RETURNS text[]
LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE AS $function$
DECLARE folded text := mpp_search_fold(value);
BEGIN
    IF folded IS NULL OR folded='' THEN RAISE EXCEPTION 'empty_search_input'; END IF;
    RETURN ARRAY[folded];
END $function$;

CREATE OR REPLACE FUNCTION mpp_query_fuzzy(
    p_normalization text,p_input text,p_scope text DEFAULT NULL,p_database text DEFAULT NULL,
    p_user text DEFAULT NULL,p_start timestamptz DEFAULT NULL,p_end timestamptz DEFAULT NULL,
    p_order text DEFAULT 'count',p_mode text DEFAULT 'words')
RETURNS TABLE (fingerprint text,example_sql_id text,matched_texts bigint,record_count bigint,
    scopes text[],databases text[],execution_users text[],last_at timestamptz,total_structures bigint,
    structure_texts bigint,identities bigint,top_scope text,top_database text,top_user text,
    top_records bigint,top_last_at timestamptz)
LANGUAGE plpgsql STABLE SET plan_cache_mode=force_custom_plan
    SET parallel_setup_cost=0 SET parallel_tuple_cost=0 AS $function$
DECLARE terms text[];
BEGIN
    IF p_mode IS NULL OR p_mode NOT IN ('words','passage') THEN RAISE EXCEPTION 'invalid_search_mode'; END IF;
    terms:=CASE p_mode WHEN 'words' THEN mpp_search_terms(p_input) ELSE mpp_search_passage(p_input) END;
    IF p_order IS NULL OR p_order NOT IN ('count','recent') THEN RAISE EXCEPTION 'invalid_search_order'; END IF;
    IF p_start>=p_end THEN RAISE EXCEPTION 'invalid_time_range'; END IF;
    RETURN QUERY
    WITH matched AS MATERIALIZED (
        SELECT t.sql_id,f.value FROM mpp_sql_text t JOIN mpp_fingerprint f USING(sql_id)
        WHERE f.normalization_id=p_normalization AND f.profile='mpp-csv/1' AND f.state='reliable'
          AND NOT EXISTS (SELECT FROM unnest(terms) term WHERE strpos(t.search_text,term)=0)
    ), cells AS MATERIALIZED (
        -- One row per text and identity keeps the later DISTINCT work off the raw records.
        SELECT m.value,m.sql_id,o.scope_id,o.database,o.execution_user,count(*) records,max(o.end_at) last_at
        FROM matched m JOIN mpp_occurrence o ON o.sql_id=m.sql_id
        WHERE (p_scope IS NULL OR o.scope_id=p_scope)
            AND (p_database IS NULL OR o.database=p_database)
            AND (p_user IS NULL OR o.execution_user=p_user)
            AND (p_start IS NULL OR o.end_at>=p_start) AND (p_end IS NULL OR o.end_at<p_end)
        GROUP BY m.value,m.sql_id,o.scope_id,o.database,o.execution_user
    ), grouped AS (
        SELECT c.value,min(c.sql_id) example_sql_id,count(DISTINCT c.sql_id) matched_texts,
            sum(c.records)::bigint records,max(c.last_at) last_at,
            array_agg(DISTINCT c.scope_id ORDER BY c.scope_id) scopes,
            array_agg(DISTINCT c.database ORDER BY c.database) databases,
            array_agg(DISTINCT c.execution_user ORDER BY c.execution_user) users
        FROM cells c GROUP BY c.value
    ), ranked AS (
        SELECT g.*,count(*) OVER () total FROM grouped g
        ORDER BY CASE WHEN p_order='count' THEN g.records END DESC,
            CASE WHEN p_order='recent' THEN g.last_at END DESC NULLS LAST,g.value
        LIMIT 50
    ), identities AS (
        SELECT c.value,c.scope_id,c.database,c.execution_user,sum(c.records)::bigint records,max(c.last_at) last_at
        FROM cells c WHERE c.value IN (SELECT r.value FROM ranked r)
        GROUP BY c.value,c.scope_id,c.database,c.execution_user
    ), top AS (
        SELECT DISTINCT ON (i.value) i.value,i.scope_id,i.database,i.execution_user,i.records,i.last_at,
            count(*) OVER (PARTITION BY i.value) identities
        FROM identities i ORDER BY i.value,i.records DESC,i.scope_id,i.database,i.execution_user
    )
    SELECT r.value,r.example_sql_id,r.matched_texts,r.records,r.scopes,r.databases,r.users,r.last_at,r.total,
        (SELECT count(*) FROM mpp_fingerprint x WHERE x.normalization_id=p_normalization
            AND x.profile='mpp-csv/1' AND x.value=r.value),
        t.identities,t.scope_id,t.database,t.execution_user,t.records,t.last_at
    FROM ranked r JOIN top t USING(value)
    ORDER BY CASE WHEN p_order='count' THEN r.records END DESC,
        CASE WHEN p_order='recent' THEN r.last_at END DESC NULLS LAST,r.value;
END $function$;

CREATE OR REPLACE FUNCTION mpp_query_search(p_normalization text,p_input text,p_scope text DEFAULT NULL,
    p_database text DEFAULT NULL,p_user text DEFAULT NULL,p_start timestamptz DEFAULT NULL,
    p_end timestamptz DEFAULT NULL,p_order text DEFAULT 'count',p_mode text DEFAULT 'words')
RETURNS jsonb LANGUAGE plpgsql AS $function$
DECLARE result jsonb; trimmed text;
BEGIN
    IF p_mode IS NULL OR p_mode NOT IN ('words','passage') THEN RAISE EXCEPTION 'invalid_search_mode'; END IF;
    trimmed:=btrim(p_input,E' \t\n\r\f\013');
    IF trimmed ~ '^struct:[a-zA-Z0-9_./-]+:[0-9a-f]{64}$' THEN
        RETURN mpp_query_exact(p_normalization,trimmed,NULL,p_scope,p_database,p_user);
    END IF;
    SELECT jsonb_build_object('match_kind','text','mode',p_mode,'state','ok',
        'total_structures',coalesce(max(f.total_structures),0),
        'rows',coalesce(jsonb_agg(to_jsonb(f)-'total_structures' ORDER BY
            CASE WHEN p_order='count' THEN f.record_count END DESC,
            CASE WHEN p_order='recent' THEN f.last_at END DESC NULLS LAST,f.fingerprint),'[]')) INTO result
    FROM mpp_query_fuzzy(p_normalization,p_input,p_scope,p_database,p_user,p_start,p_end,p_order,p_mode) f;
    RETURN result;
END $function$;

-- 1.11.0: dashboard-facing query functions (mpp_view_*). Free text and identities
-- travel as unpadded base64url tokens so no client-side quoting, escaping or
-- variable substitution can alter them; an empty string always means "not given".
CREATE OR REPLACE FUNCTION mpp_view_encode(p_value text) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE
RETURN rtrim(translate(encode(convert_to(p_value,'UTF8'),'base64'),E'+/\n','-_'),'=');

CREATE OR REPLACE FUNCTION mpp_view_decode(p_token text) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE
RETURN convert_from(decode(rpad(translate(p_token,'-_','+/'),4*((length(p_token)+3)/4),'='),'base64'),'UTF8');

CREATE OR REPLACE FUNCTION mpp_view_identity_token(p_scope text,p_database text,p_user text) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE
RETURN mpp_view_encode(jsonb_build_array(p_scope,p_database,p_user)::text);

-- Zero rows for an empty token; a JSON null stays NULL and matches only NULL.
CREATE OR REPLACE FUNCTION mpp_view_identity(p_token text)
RETURNS TABLE(scope_id text,database text,execution_user text)
LANGUAGE sql IMMUTABLE PARALLEL SAFE
BEGIN ATOMIC
    SELECT x.j->>0,x.j->>1,x.j->>2
    FROM (SELECT mpp_view_decode(nullif(p_token,''))::jsonb j) x WHERE x.j IS NOT NULL;
END;

CREATE OR REPLACE FUNCTION mpp_view_label(p_kind text,p_code text) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE
RETURN CASE p_kind
    WHEN 'timing' THEN CASE p_code WHEN 'request' THEN '请求整体' WHEN 'execute_first' THEN 'Execute 首次'
        WHEN 'execute_fetch' THEN 'Execute 续取' WHEN 'parse' THEN 'Parse' WHEN 'bind' THEN 'Bind'
        WHEN 'unknown' THEN '无计时类别' END
    WHEN 'outcome' THEN CASE p_code WHEN 'success' THEN '成功' WHEN 'failed' THEN '失败'
        WHEN 'cancelled' THEN '取消' WHEN 'timed_out' THEN '超时' WHEN 'unknown' THEN '结果未知' END
    WHEN 'shape' THEN CASE p_code WHEN 'single' THEN '单条' WHEN 'batch' THEN '整批' WHEN 'unknown' THEN '未知' END
    WHEN 'layer' THEN CASE p_code WHEN 'overall' THEN '整体' WHEN 'day' THEN '逐天' WHEN 'week' THEN '逐周'
        WHEN 'weekday' THEN '星期几' WHEN 'hour' THEN '各小时' END
    WHEN 'condition' THEN CASE p_code WHEN 'basic' THEN '基础' WHEN 'p95' THEN 'P95' WHEN 'p99' THEN 'P99' END
    WHEN 'state' THEN CASE p_code WHEN 'has_baseline' THEN '库里有这个结构，并且有基线'
        WHEN 'records_without_baseline' THEN '库里有这个结构的执行记录，但当前版本没有它的基线'
        WHEN 'not_seen' THEN '库里没有这个结构'
        WHEN 'unreliable_fingerprint' THEN '无法生成可靠指纹'
        WHEN 'service_unavailable' THEN '指纹服务不可用，无法按完整 SQL 检索；按词和整段不受影响' END
    WHEN 'decision' THEN CASE p_code WHEN 'included' THEN '参与训练' WHEN 'excluded' THEN '被排除'
        WHEN 'unresolved' THEN '无法判定，未参与' WHEN 'outside_window' THEN '在训练窗口之外'
        WHEN 'not_in_version_input' THEN '未选入该版本的输入'
        WHEN 'decision_unavailable' THEN '该版本的判定规则不支持复算' END
    WHEN 'reason' THEN CASE p_code
        WHEN 'sample_count_below_min' THEN '样本数不足' WHEN 'active_days_below_min' THEN '活跃天数不足'
        WHEN 'active_weeks_below_min' THEN '活跃周数不足'
        WHEN 'execution_failed' THEN '执行失败' WHEN 'execution_cancelled' THEN '执行被取消'
        WHEN 'execution_timed_out' THEN '执行超时' WHEN 'outcome_unknown' THEN '执行结果未知'
        WHEN 'duration_unknown' THEN '耗时未知' WHEN 'association_unreliable' THEN '记录关联不可靠'
        WHEN 'timing_unknown' THEN '计时类别未知' WHEN 'sql_missing' THEN '没有 SQL 原文'
        WHEN 'sql_incomplete' THEN 'SQL 原文不完整' WHEN 'sql_encoding_invalid' THEN 'SQL 原文编码无效'
        WHEN 'sql_uncertain' THEN 'SQL 原文不确定' WHEN 'fingerprint_failed' THEN '未能生成指纹'
        WHEN 'identity_missing' THEN '缺少数据库或执行用户' WHEN 'start_unknown' THEN '开始时间未知'
        WHEN 'blacklist_category' THEN '属于黑名单语句类别' WHEN 'blacklist_template' THEN '命中黑名单模板'
        WHEN 'excluded_interval' THEN '落在排除时段' WHEN 'outside_window' THEN '在训练窗口之外'
        WHEN 'unsupported_decision_version' THEN '判定规则版本不支持'
        WHEN 'input_size_limit' THEN '输入超过 512 KB 的上限' WHEN 'parser_failed' THEN '解析失败'
        WHEN 'normalization_failed' THEN '归一化失败' WHEN 'invalid_encoding' THEN '不是有效的 UTF-8' END
    END;

-- Rules of the newest current version; a database-only reader has no Python
-- normalizer. Each database holds one rule set today, so this is unambiguous.
CREATE OR REPLACE FUNCTION mpp_view_rules() RETURNS text
LANGUAGE sql STABLE
BEGIN ATOMIC
    SELECT coalesce(
        (SELECT b.normalization_id FROM current_version v JOIN build b USING(build_id)
            ORDER BY v.last_success_at DESC,v.scope_id LIMIT 1),
        (SELECT b.normalization_id FROM build b ORDER BY b.started_at DESC,b.build_id LIMIT 1),
        (SELECT n.normalization_id FROM mpp_normalization n ORDER BY
            (SELECT count(*) FROM mpp_fingerprint f WHERE f.normalization_id=n.normalization_id) DESC,
            n.normalization_id LIMIT 1));
END;

-- Exact stored text for a pasted statement: the digest index narrows, equality decides.
CREATE OR REPLACE FUNCTION mpp_query_text_id(p_text text) RETURNS text
LANGUAGE sql STABLE
BEGIN ATOMIC
    SELECT t.sql_id FROM mpp_sql_text t
    WHERE t.content_sha256=sha256(convert_to(p_text,'UTF8')) AND t.text=p_text LIMIT 1;
END;

-- Longest common prefix, in characters, by bisection.
CREATE OR REPLACE FUNCTION mpp_view_common_prefix(p_a text,p_b text) RETURNS integer
LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE AS $function$
DECLARE low integer := 0; high integer := least(length(p_a),length(p_b)); middle integer;
BEGIN
    WHILE low<high LOOP
        middle := (low+high+1)/2;
        IF left(p_a,middle)=left(p_b,middle) THEN low := middle; ELSE high := middle-1; END IF;
    END LOOP;
    RETURN coalesce(low,0);
END $function$;

CREATE OR REPLACE FUNCTION mpp_view_identities(p_normalization text,p_fingerprint text)
RETURNS TABLE(identity text,label text,scope_id text,database text,execution_user text,
    record_count bigint,last_at timestamptz,has_baseline boolean)
LANGUAGE sql STABLE
BEGIN ATOMIC
    SELECT mpp_view_identity_token(h.scope_id,h.database,h.execution_user),
        h.scope_id||' / '||coalesce(h.database,'（无数据库）')||' / '||coalesce(h.execution_user,'（无用户）')
            ||'（'||h.record_count||' 条记录）',
        h.scope_id,h.database,h.execution_user,h.record_count,h.last_at,h.has_baseline
    FROM mpp_query_hits(p_normalization,p_fingerprint) h
    ORDER BY h.record_count DESC,h.scope_id,h.database,h.execution_user;
END;

-- One identity's records of one timing category inside [p_start,p_end) by end time.
-- Failed, cancelled and timed-out records carry no timing category and no duration,
-- so they accompany every category; 'unknown' selects all records without a category.
CREATE OR REPLACE FUNCTION mpp_view_records(p_normalization text,p_fingerprint text,p_identity text,
    p_timing text,p_start timestamptz,p_end timestamptz,p_sql_id text DEFAULT NULL)
RETURNS TABLE(analysis_id text,occurrence_id text,sql_id text,outcome text,end_at timestamptz,
    duration_ms numeric,request_shape text,anchor_ref text)
LANGUAGE sql STABLE
BEGIN ATOMIC
    SELECT o.analysis_id,o.occurrence_id,o.sql_id,o.outcome,o.end_at,
        CASE WHEN o.timing_type IS NOT NULL THEN o.duration_ms END,o.request_shape,o.anchor_ref
    FROM mpp_fingerprint f JOIN mpp_occurrence o USING(sql_id)
    JOIN mpp_view_identity(p_identity) i ON o.scope_id=i.scope_id
        AND o.database IS NOT DISTINCT FROM i.database
        AND o.execution_user IS NOT DISTINCT FROM i.execution_user
    WHERE f.normalization_id=p_normalization AND f.profile='mpp-csv/1' AND f.state='reliable'
        AND f.value=p_fingerprint AND (o.timing_type=p_timing
            OR (o.timing_type IS NULL AND (p_timing='unknown' OR o.outcome<>'success')))
        AND o.end_at>=p_start AND o.end_at<p_end
        AND (nullif(p_sql_id,'') IS NULL OR o.sql_id=p_sql_id);
END;

-- Timing categories that have records for the identity, in the agreed default order.
-- 'unknown' is offered only when it adds something: untimed records that are not
-- failures, or an identity without any timed record.
CREATE OR REPLACE FUNCTION mpp_view_timings(p_normalization text,p_fingerprint text,p_identity text)
RETURNS TABLE(timing text,label text,record_count bigint)
LANGUAGE sql STABLE
BEGIN ATOMIC
    SELECT t.timing,mpp_view_label('timing',t.timing)||'（'
        ||CASE WHEN t.timing IN ('request','unknown') THEN '' ELSE '阶段或调用，' END||t.n||' 条）',t.n
    FROM (SELECT coalesce(o.timing_type,'unknown') timing,count(*) n,
            count(*) FILTER (WHERE o.outcome='success') plain,
            count(*) FILTER (WHERE o.timing_type IS NOT NULL) OVER () timed
        FROM mpp_fingerprint f JOIN mpp_occurrence o USING(sql_id)
        JOIN mpp_view_identity(p_identity) i ON o.scope_id=i.scope_id
            AND o.database IS NOT DISTINCT FROM i.database
            AND o.execution_user IS NOT DISTINCT FROM i.execution_user
        WHERE f.normalization_id=p_normalization AND f.profile='mpp-csv/1' AND f.state='reliable'
            AND f.value=p_fingerprint GROUP BY o.timing_type) t
    WHERE t.timing<>'unknown' OR t.plain>0 OR t.timed=0
    ORDER BY array_position(ARRAY['request','execute_first','execute_fetch','parse','bind','unknown'],t.timing);
END;

-- Every published version of the identity's cluster; only uncleaned versions built
-- with the current rules can be chosen as a reference.
CREATE OR REPLACE FUNCTION mpp_view_versions(p_normalization text,p_identity text)
RETURNS TABLE(build_id text,label text,selectable boolean,status text,is_current boolean,
    published_at timestamptz,built_at timestamptz,window_start timestamptz,window_end timestamptz,
    window_days integer,rules text,results_cleaned boolean,rules_match boolean)
LANGUAGE sql STABLE
BEGIN ATOMIC
    SELECT v.build_id,
        to_char(v.published_at AT TIME ZONE 'Asia/Shanghai','YYYY-MM-DD HH24:MI')||' 发布'
            ||CASE WHEN v.is_current THEN '（当前生效）' ELSE '' END,
        NOT v.results_cleaned AND v.rules_match,
        CASE WHEN v.results_cleaned THEN '已清理，不能选作参照'
            WHEN NOT v.rules_match THEN '规则版本不同，不能选作参照'
            WHEN v.is_current THEN '当前生效' ELSE '可选作参照' END,
        v.is_current,v.published_at,v.built_at,v.window_start,v.window_end,v.window_days,
        v.algorithm_version||' / '||v.parser_version||' / '||v.dictionary_rules_version,
        v.results_cleaned,v.rules_match
    FROM mpp_view_identity(p_identity) i CROSS JOIN LATERAL mpp_query_versions(p_normalization,i.scope_id) v
    ORDER BY v.is_current DESC,v.published_at DESC,v.build_id;
END;

-- mpp_query_statistics for a token identity. Returns no row instead of raising
-- when no version is chosen or the chosen version cannot serve as a reference.
CREATE OR REPLACE FUNCTION mpp_view_statistics(p_normalization text,p_fingerprint text,p_identity text,
    p_build text,p_layer text DEFAULT 'overall')
RETURNS TABLE(build_id text,scope_id text,database text,execution_user text,fingerprint text,
    timing_type text,layer text,bucket_date date,bucket_number smallint,range_start timestamptz,range_end timestamptz,
    sample_state text,included_count bigint,active_days integer,excluded_count bigint,exclusions_by_reason jsonb,
    sufficiency jsonb,metric_null_reasons jsonb,
    min_ms numeric,max_ms numeric,mean_ms numeric,p25_ms numeric,p50_ms numeric,p75_ms numeric,p90_ms numeric,
    p95_ms numeric,p99_ms numeric,stddev_ms numeric,cv numeric,mad_ms numeric,iqr_ms numeric,
    log_median numeric,log_mad numeric,p95_p50 numeric,p99_p50 numeric)
LANGUAGE plpgsql SET plan_cache_mode=force_custom_plan AS $function$
DECLARE i record;
BEGIN
    SELECT * INTO i FROM mpp_view_identity(p_identity);
    IF NOT FOUND OR nullif(p_build,'') IS NULL OR i.database IS NULL OR i.execution_user IS NULL THEN RETURN; END IF;
    BEGIN
        RETURN QUERY SELECT * FROM mpp_query_statistics(p_normalization,i.scope_id,i.database,i.execution_user,
            p_fingerprint,p_build,p_layer);
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM NOT IN ('results_cleaned','published_version_not_found','normalization_version_mismatch') THEN RAISE; END IF;
    END;
END $function$;

-- Per slot of p_step_ms, the slowest and the fastest execution, both real records at
-- their own end time; a slot holding one execution yields one point.
CREATE OR REPLACE FUNCTION mpp_view_points(p_normalization text,p_fingerprint text,p_identity text,
    p_timing text,p_start timestamptz,p_end timestamptz,p_step_ms bigint,p_sql_id text DEFAULT NULL,
    p_min_ms numeric DEFAULT NULL,p_max_ms numeric DEFAULT NULL)
RETURNS TABLE(end_at timestamptz,duration_ms numeric,kind text,sql_id text,slot_count bigint)
LANGUAGE sql STABLE
BEGIN ATOMIC
    SELECT r.end_at,r.duration_ms,CASE WHEN r.slowest=1 THEN 'slowest' ELSE 'fastest' END,r.sql_id,r.n
    FROM (SELECT x.end_at,x.duration_ms,x.sql_id,
            row_number() OVER (PARTITION BY x.slot ORDER BY x.duration_ms DESC,x.end_at,x.analysis_id,x.occurrence_id) slowest,
            row_number() OVER (PARTITION BY x.slot ORDER BY x.duration_ms,x.end_at DESC,x.analysis_id DESC,x.occurrence_id DESC) fastest,
            count(*) OVER (PARTITION BY x.slot) n
        FROM (SELECT o.*,floor(extract(epoch FROM o.end_at)*1000/greatest(p_step_ms,1)) slot
            FROM mpp_view_records(p_normalization,p_fingerprint,p_identity,p_timing,p_start,p_end,p_sql_id) o
            WHERE o.duration_ms IS NOT NULL AND (p_min_ms IS NULL OR o.duration_ms>=p_min_ms)
                AND (p_max_ms IS NULL OR o.duration_ms<=p_max_ms)) x) r
    WHERE r.slowest=1 OR (r.fastest=1 AND r.n>1);
END;

-- Records per slot and outcome, on the same slots as mpp_view_points.
CREATE OR REPLACE FUNCTION mpp_view_counts(p_normalization text,p_fingerprint text,p_identity text,
    p_timing text,p_start timestamptz,p_end timestamptz,p_step_ms bigint,p_sql_id text DEFAULT NULL)
RETURNS TABLE(slot_at timestamptz,outcome text,record_count bigint)
LANGUAGE sql STABLE
BEGIN ATOMIC
    SELECT to_timestamp(x.slot*greatest(p_step_ms,1)/1000.0),x.outcome,count(*)
    FROM (SELECT o.outcome,floor(extract(epoch FROM o.end_at)*1000/greatest(p_step_ms,1)) slot
        FROM mpp_view_records(p_normalization,p_fingerprint,p_identity,p_timing,p_start,p_end,p_sql_id) o) x
    GROUP BY x.slot,x.outcome;
END;

-- Executions in the range above the overall baseline's P50/P95/P99. A value whose
-- sample condition is unmet is not used as a reference.
CREATE OR REPLACE FUNCTION mpp_view_compare(p_normalization text,p_fingerprint text,p_identity text,
    p_timing text,p_build text,p_start timestamptz,p_end timestamptz,p_sql_id text DEFAULT NULL)
RETURNS TABLE(reference text,baseline_ms numeric,condition_met boolean,known_executions bigint,
    above bigint,above_share numeric,expected_share numeric,note text)
LANGUAGE plpgsql SET plan_cache_mode=force_custom_plan AS $function$
DECLARE base record; known bigint; over50 bigint; over95 bigint; over99 bigint;
BEGIN
    SELECT s.p50_ms,s.p95_ms,s.p99_ms,(s.sufficiency->'basic'->>'met')::boolean basic,
            (s.sufficiency->'p95'->>'met')::boolean p95,(s.sufficiency->'p99'->>'met')::boolean p99
        INTO base FROM mpp_view_statistics(p_normalization,p_fingerprint,p_identity,p_build,'overall') s
        WHERE s.timing_type=p_timing AND s.sample_state='available';
    SELECT count(*),count(*) FILTER (WHERE o.duration_ms>base.p50_ms),count(*) FILTER (WHERE o.duration_ms>base.p95_ms),
            count(*) FILTER (WHERE o.duration_ms>base.p99_ms) INTO known,over50,over95,over99
        FROM mpp_view_records(p_normalization,p_fingerprint,p_identity,p_timing,p_start,p_end,p_sql_id) o
        WHERE o.duration_ms IS NOT NULL;
    RETURN QUERY
    SELECT r.name,r.value,coalesce(r.met,false),known,CASE WHEN r.met THEN r.over END,
        CASE WHEN r.met AND known>0 THEN round(r.over::numeric/known,4) END,r.expected,
        CASE WHEN r.value IS NULL THEN '所选版本没有这一计时类别的基线'
            WHEN NOT r.met THEN '基线样本不足，不作参照' END
    FROM (VALUES (1,'P50',base.p50_ms,base.basic,over50,0.5),(2,'P95',base.p95_ms,base.p95,over95,0.05),
        (3,'P99',base.p99_ms,base.p99,over99,0.01)) r(ord,name,value,met,over,expected) ORDER BY r.ord;
END $function$;

-- Original texts of the structure seen in the range. Texts hit by the search that
-- led here come first; the differing part is what remains after trimming the
-- prefix and suffix shared by the listed texts (an approximation).
CREATE OR REPLACE FUNCTION mpp_view_texts(p_normalization text,p_fingerprint text,p_identity text,
    p_timing text,p_build text,p_start timestamptz,p_end timestamptz,p_order text DEFAULT 'count',
    p_hit_mode text DEFAULT NULL,p_hit_input text DEFAULT NULL,p_hit_sql_id text DEFAULT NULL,
    p_limit integer DEFAULT 10)
RETURNS TABLE(rank bigint,sql_id text,differing text,executions bigint,known_executions bigint,
    median_ms numeric,slowest_ms numeric,above_p95 bigint,above_p95_share numeric,
    search_hit boolean,range_texts bigint)
LANGUAGE plpgsql SET plan_cache_mode=force_custom_plan AS $function$
DECLARE terms text[]; reference numeric;
BEGIN
    IF p_order IS NULL OR p_order NOT IN ('count','median','slowest') THEN RAISE EXCEPTION 'invalid_text_order'; END IF;
    IF p_limit IS NULL OR p_limit<1 OR p_limit>100 THEN RAISE EXCEPTION 'invalid_page_size'; END IF;
    IF nullif(p_hit_input,'') IS NOT NULL AND p_hit_mode IN ('words','passage') THEN
        BEGIN
            terms := CASE p_hit_mode WHEN 'words' THEN mpp_search_terms(p_hit_input) ELSE mpp_search_passage(p_hit_input) END;
        EXCEPTION WHEN raise_exception THEN
            IF SQLERRM NOT IN ('empty_search_input','too_many_search_terms') THEN RAISE; END IF;
        END;
    END IF;
    SELECT s.p95_ms INTO reference FROM mpp_view_statistics(p_normalization,p_fingerprint,p_identity,p_build,'overall') s
        WHERE s.timing_type=p_timing AND s.sample_state='available' AND (s.sufficiency->'p95'->>'met')::boolean;
    RETURN QUERY
    WITH facts AS MATERIALIZED (
        SELECT o.sql_id,count(*) executions,count(o.duration_ms) known,
            percentile_cont(0.5) WITHIN GROUP (ORDER BY o.duration_ms::double precision) median,
            max(o.duration_ms) slowest,count(*) FILTER (WHERE o.duration_ms>reference) above
        FROM mpp_view_records(p_normalization,p_fingerprint,p_identity,p_timing,p_start,p_end) o GROUP BY o.sql_id
    ), marked AS (
        SELECT f.*,CASE WHEN terms IS NOT NULL THEN NOT EXISTS (SELECT FROM unnest(terms) term
                WHERE strpos((SELECT t.search_text FROM mpp_sql_text t WHERE t.sql_id=f.sql_id),term)=0)
            ELSE f.sql_id=nullif(p_hit_sql_id,'') END hit,count(*) OVER () total
        FROM facts f
    ), listed AS MATERIALIZED (
        SELECT m.*,t.text,row_number() OVER (ORDER BY coalesce(m.hit,false) DESC,
            CASE p_order WHEN 'count' THEN m.executions::double precision WHEN 'median' THEN m.median
                ELSE m.slowest::double precision END DESC NULLS LAST,m.sql_id) place
        FROM (SELECT * FROM marked x ORDER BY coalesce(x.hit,false) DESC,
            CASE p_order WHEN 'count' THEN x.executions::double precision WHEN 'median' THEN x.median
                ELSE x.slowest::double precision END DESC NULLS LAST,x.sql_id LIMIT p_limit) m
        JOIN mpp_sql_text t USING(sql_id)
    ), shared AS (
        SELECT least(mpp_view_common_prefix(min(l.text COLLATE "C"),max(l.text COLLATE "C")),min(length(l.text))) head,
            mpp_view_common_prefix(min(reverse(l.text) COLLATE "C"),max(reverse(l.text) COLLATE "C")) tail,
            min(length(l.text)) shortest,count(*) n FROM listed l
    ), cut AS (
        SELECT l.*,CASE WHEN s.n<2 THEN NULL ELSE
            substr(l.text,s.head+1,length(l.text)-s.head-least(s.tail,s.shortest-s.head)) END part
        FROM listed l CROSS JOIN shared s
    )
    SELECT c.place,c.sql_id,
        CASE WHEN c.part IS NULL THEN '（只有这一份原文）' WHEN c.part='' THEN '（与其他原文相比没有多出的内容）'
            WHEN length(c.part)>160 THEN left(c.part,100)||' … '||right(c.part,50) ELSE c.part END,
        c.executions,c.known,round(c.median::numeric,3),c.slowest,
        CASE WHEN reference IS NOT NULL THEN c.above END,
        CASE WHEN reference IS NOT NULL AND c.known>0 THEN round(c.above::numeric/c.known,4) END,
        coalesce(c.hit,false),c.total
    FROM cut c ORDER BY c.place;
END $function$;

-- Execution list for the detail page. Both orders are applied before the limit,
-- so the page always holds the first rows of the whole range.
CREATE OR REPLACE FUNCTION mpp_view_executions(p_normalization text,p_fingerprint text,p_identity text,
    p_timing text,p_build text,p_start timestamptz,p_end timestamptz,p_sql_id text DEFAULT NULL,
    p_outcomes text DEFAULT NULL,p_min_ms numeric DEFAULT NULL,p_max_ms numeric DEFAULT NULL,
    p_order text DEFAULT 'latest',p_limit integer DEFAULT 200)
RETURNS TABLE(end_at timestamptz,duration_ms numeric,outcome text,comparison text,request_shape text,
    sql_id text,source_file text,source_lines text,training text,analysis_id text,occurrence_id text,
    matching bigint)
LANGUAGE plpgsql SET plan_cache_mode=force_custom_plan AS $function$
DECLARE base record; chosen text := nullif(p_build,''); wanted text[];
BEGIN
    IF p_order IS NULL OR p_order NOT IN ('latest','slowest') THEN RAISE EXCEPTION 'invalid_execution_order'; END IF;
    IF p_limit IS NULL OR p_limit<1 OR p_limit>1000 THEN RAISE EXCEPTION 'invalid_page_size'; END IF;
    wanted := string_to_array(nullif(p_outcomes,''),',');
    IF chosen IS NOT NULL AND NOT EXISTS (SELECT FROM build b WHERE b.build_id=chosen) THEN chosen := NULL; END IF;
    SELECT s.p50_ms,s.p95_ms,s.p99_ms,(s.sufficiency->'basic'->>'met')::boolean basic,
            (s.sufficiency->'p95'->>'met')::boolean p95,(s.sufficiency->'p99'->>'met')::boolean p99
        INTO base FROM mpp_view_statistics(p_normalization,p_fingerprint,p_identity,chosen,'overall') s
        WHERE s.timing_type=p_timing AND s.sample_state='available';
    RETURN QUERY
    WITH page AS MATERIALIZED (
        SELECT o.*,count(*) OVER () matching
        FROM mpp_view_records(p_normalization,p_fingerprint,p_identity,p_timing,p_start,p_end,p_sql_id) o
        WHERE (wanted IS NULL OR o.outcome=ANY(wanted))
            AND ((p_min_ms IS NULL AND p_max_ms IS NULL) OR (o.duration_ms IS NOT NULL
                AND (p_min_ms IS NULL OR o.duration_ms>=p_min_ms) AND (p_max_ms IS NULL OR o.duration_ms<=p_max_ms)))
        ORDER BY CASE WHEN p_order='slowest' THEN o.duration_ms END DESC NULLS LAST,
            o.end_at DESC,o.analysis_id DESC,o.occurrence_id DESC
        LIMIT p_limit
    ), decided AS MATERIALIZED (
        SELECT d.* FROM mpp_query_training(chosen,(SELECT coalesce(jsonb_agg(jsonb_build_object(
            'analysis_id',p.analysis_id,'occurrence_id',p.occurrence_id)),'[]') FROM page p)) d
        WHERE chosen IS NOT NULL
    )
    SELECT p.end_at,p.duration_ms,p.outcome,
        CASE WHEN p.duration_ms IS NULL THEN '耗时未知'
            WHEN base.p50_ms IS NULL THEN '没有基线'
            WHEN NOT base.basic THEN '基线样本不足，不作参照'
            WHEN base.p99 AND p.duration_ms>base.p99_ms THEN '高于 P99'
            WHEN base.p95 AND p.duration_ms>base.p95_ms THEN '高于 P95'
            WHEN p.duration_ms>base.p50_ms THEN '高于 P50' ELSE '不高于 P50' END,
        p.request_shape,p.sql_id,regexp_replace(f.locator,'^.*/',''),
        CASE WHEN e.line_start=e.line_end THEN e.line_start::text ELSE e.line_start||'–'||e.line_end END,
        CASE WHEN chosen IS NULL THEN '未选择基线版本' ELSE mpp_view_label('decision',d.decision)
            ||CASE WHEN cardinality(d.reason_codes)>0 AND d.decision<>'outside_window'
                THEN '：'||(SELECT string_agg(coalesce(mpp_view_label('reason',c),c),'、') FROM unnest(d.reason_codes) c)
                ELSE '' END END,
        p.analysis_id,p.occurrence_id,p.matching
    FROM page p JOIN evidence_record e ON e.record_id=p.anchor_ref JOIN source_file f ON f.file_id=e.file_id
    LEFT JOIN decided d ON d.analysis_id=p.analysis_id AND d.occurrence_id=p.occurrence_id
    ORDER BY CASE WHEN p_order='slowest' THEN p.duration_ms END DESC NULLS LAST,
        p.end_at DESC,p.analysis_id DESC,p.occurrence_id DESC;
END $function$;

-- The selected original text, or an example of the structure when none is selected.
CREATE OR REPLACE FUNCTION mpp_view_sql_text(p_normalization text,p_fingerprint text,p_sql_id text DEFAULT NULL)
RETURNS TABLE(sql_id text,sql_text text,selected boolean,structure_texts bigint)
LANGUAGE sql STABLE
BEGIN ATOMIC
    WITH texts AS MATERIALIZED (
        SELECT f.sql_id FROM mpp_fingerprint f WHERE f.normalization_id=p_normalization
            AND f.profile='mpp-csv/1' AND f.state='reliable' AND f.value=p_fingerprint
    ), chosen AS (
        SELECT coalesce((SELECT x.sql_id FROM texts x WHERE x.sql_id=nullif(p_sql_id,'')),
            (SELECT min(x.sql_id) FROM texts x)) sql_id
    )
    SELECT t.sql_id,t.text,coalesce(t.sql_id=nullif(p_sql_id,''),false),(SELECT count(*) FROM texts)
    FROM chosen c JOIN mpp_sql_text t USING(sql_id);
END;

-- Search candidates in one shape for the three modes. 'exact' takes a structure
-- fingerprint (computed by the fingerprint service); a words or passage input that
-- is exactly one fingerprint value is looked up the same way. A rejected input
-- yields no row here; mpp_view_search_note states the reason.
CREATE OR REPLACE FUNCTION mpp_view_search(p_normalization text,p_mode text,p_input text,
    p_scope text DEFAULT NULL,p_database text DEFAULT NULL,p_user text DEFAULT NULL,
    p_start timestamptz DEFAULT NULL,p_end timestamptz DEFAULT NULL,p_order text DEFAULT 'count',
    p_exact_sql_id text DEFAULT NULL)
RETURNS TABLE(fingerprint text,example_sql_id text,matched_texts bigint,structure_texts bigint,
    record_count bigint,identities bigint,scopes text[],databases text[],execution_users text[],
    last_at timestamptz,total_structures bigint,top_identity text,top_label text,top_records bigint,
    top_last_at timestamptz,only_sql_id text)
LANGUAGE plpgsql SET plan_cache_mode=force_custom_plan AS $function$
DECLARE given text := btrim(p_input,E' \t\n\r\f\013');
BEGIN
    IF p_mode IS NULL OR p_mode NOT IN ('words','passage','exact') THEN RAISE EXCEPTION 'invalid_search_mode'; END IF;
    IF p_order IS NULL OR p_order NOT IN ('count','recent') THEN RAISE EXCEPTION 'invalid_search_order'; END IF;
    IF p_mode='exact' OR given ~ '^struct:[a-zA-Z0-9_./-]+:[0-9a-f]{64}$' THEN
        RETURN QUERY
        WITH hits AS MATERIALIZED (
            SELECT o.scope_id,o.database,o.execution_user,count(*) records,max(o.end_at) last_at
            FROM mpp_fingerprint f JOIN mpp_occurrence o USING(sql_id)
            WHERE f.normalization_id=p_normalization AND f.profile='mpp-csv/1' AND f.state='reliable'
                AND f.value=given AND (p_scope IS NULL OR o.scope_id=p_scope)
                AND (p_database IS NULL OR o.database=p_database)
                AND (p_user IS NULL OR o.execution_user=p_user)
                AND (p_start IS NULL OR o.end_at>=p_start) AND (p_end IS NULL OR o.end_at<p_end)
            GROUP BY o.scope_id,o.database,o.execution_user
        ), top AS (
            SELECT * FROM hits h ORDER BY h.records DESC,h.scope_id,h.database,h.execution_user LIMIT 1
        ), texts AS (
            SELECT min(f.sql_id) example,count(*) n FROM mpp_fingerprint f WHERE f.normalization_id=p_normalization
                AND f.profile='mpp-csv/1' AND f.state='reliable' AND f.value=given
        )
        SELECT given,x.example,NULL::bigint,x.n,(SELECT sum(h.records)::bigint FROM hits h),
            (SELECT count(*) FROM hits),
            ARRAY(SELECT DISTINCT h.scope_id FROM hits h ORDER BY 1),
            ARRAY(SELECT DISTINCT h.database FROM hits h ORDER BY 1),
            ARRAY(SELECT DISTINCT h.execution_user FROM hits h ORDER BY 1),
            (SELECT max(h.last_at) FROM hits h),1::bigint,
            mpp_view_identity_token(t.scope_id,t.database,t.execution_user),
            t.scope_id||' / '||coalesce(t.database,'（无数据库）')||' / '||coalesce(t.execution_user,'（无用户）'),
            t.records,t.last_at,nullif(p_exact_sql_id,'')
        FROM top t CROSS JOIN texts x;
        RETURN;
    END IF;
    BEGIN
        RETURN QUERY
        SELECT f.fingerprint,f.example_sql_id,f.matched_texts,f.structure_texts,f.record_count,f.identities,
            f.scopes,f.databases,f.execution_users,f.last_at,f.total_structures,
            mpp_view_identity_token(f.top_scope,f.top_database,f.top_user),
            f.top_scope||' / '||coalesce(f.top_database,'（无数据库）')||' / '||coalesce(f.top_user,'（无用户）'),
            f.top_records,f.top_last_at,CASE WHEN f.matched_texts=1 THEN f.example_sql_id END
        FROM mpp_query_fuzzy(p_normalization,p_input,p_scope,p_database,p_user,p_start,p_end,p_order,p_mode) f;
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM NOT IN ('empty_search_input','too_many_search_terms') THEN RAISE; END IF;
    END;
END $function$;

-- What this search did, in display order: the mode, how the input was cut or why
-- it was refused, and what arrived (length and digest, never the text itself).
CREATE OR REPLACE FUNCTION mpp_view_search_note(p_normalization text,p_mode text,p_input text,
    p_fingerprint text DEFAULT NULL,p_state text DEFAULT NULL,p_reason text DEFAULT NULL,
    p_exact_sql_id text DEFAULT NULL,p_scope text DEFAULT NULL,p_database text DEFAULT NULL,p_user text DEFAULT NULL)
RETURNS TABLE(seq integer,item text,content text)
LANGUAGE plpgsql STABLE AS $function$
DECLARE given text := btrim(p_input,E' \t\n\r\f\013'); pieces text[]; state text; fp text := nullif(p_fingerprint,'');
BEGIN
    IF p_mode IS NULL OR p_mode NOT IN ('words','passage','exact') THEN RAISE EXCEPTION 'invalid_search_mode'; END IF;
    IF p_mode='exact' THEN
        RETURN QUERY SELECT 1,'检索方式','完整 SQL：整个输入当作一个请求解析，结构指纹相同即命中，取值可以不同';
        IF fp IS NULL AND nullif(p_state,'') IS NULL THEN
            RETURN QUERY SELECT 2,'结果','还没有检索';
            RETURN;
        END IF;
        state := CASE WHEN fp IS NULL THEN p_state
            ELSE mpp_query_exact(p_normalization,fp,NULL,p_scope,p_database,p_user)->>'state' END;
        RETURN QUERY SELECT 2,'结果',coalesce(mpp_view_label('state',state),state)
            ||CASE WHEN state='unreliable_fingerprint' AND nullif(p_reason,'') IS NOT NULL
                THEN '：'||coalesce(mpp_view_label('reason',p_reason),'语法不在支持范围（'||p_reason||'）') ELSE '' END;
        IF fp IS NOT NULL THEN
            RETURN QUERY SELECT 3,'结构指纹',fp;
            RETURN QUERY SELECT 4,'一字不差的原文',CASE WHEN nullif(p_exact_sql_id,'') IS NULL
                THEN '库里没有与输入一字不差的原文' ELSE '库里有，进入详情后只看这一份（'||p_exact_sql_id||'）' END;
        END IF;
        RETURN;
    END IF;
    RETURN QUERY SELECT 1,'检索方式',CASE p_mode
        WHEN 'words' THEN '按词：只按空白切成词，每个词都要出现在原文里，位置和顺序不限；引号是普通字符'
        ELSE '整段：整个输入算一段，要在原文里连续出现；不受 20 个词的限制' END;
    IF given ~ '^struct:[a-zA-Z0-9_./-]+:[0-9a-f]{64}$' THEN
        RETURN QUERY SELECT 2,'输入','是一个结构指纹值，直接按这个指纹查找';
    ELSIF p_mode='words' THEN
        pieces := ARRAY(SELECT mpp_search_fold(t) FROM regexp_split_to_table(p_input,E'[ \t\n\r\f\013]+') t WHERE t<>'');
        RETURN QUERY SELECT 2,CASE WHEN cardinality(pieces) BETWEEN 1 AND 20 THEN '切出的词' ELSE '没有检索' END,
            CASE WHEN cardinality(pieces)=0 THEN '输入为空'
                WHEN cardinality(pieces)>20 THEN '输入切出 '||cardinality(pieces)||' 个词，超过 20 个；请改用“整段”方式'
                ELSE '共 '||cardinality(pieces)||' 个：'||array_to_string(pieces,'  ｜  ') END;
    ELSE
        RETURN QUERY SELECT 2,CASE WHEN mpp_search_fold(p_input)<>'' THEN '比较的内容' ELSE '没有检索' END,
            CASE WHEN coalesce(mpp_search_fold(p_input),'')='' THEN '输入为空'
                ELSE '去掉空白、字母转小写后共 '||length(mpp_search_fold(p_input))||' 个字符，按字面连续比较' END;
    END IF;
    RETURN QUERY SELECT 3,'到达数据库的输入',coalesce(length(p_input),0)||' 个字符，SHA-256 '
        ||encode(sha256(convert_to(coalesce(p_input,''),'UTF8')),'hex');
END $function$;

-- Per-statement hints of a batch that did not match as a whole. The token holds
-- the service's list [{statement,fingerprint}]; states are read from the database.
CREATE OR REPLACE FUNCTION mpp_view_hints(p_normalization text,p_hints text,
    p_scope text DEFAULT NULL,p_database text DEFAULT NULL,p_user text DEFAULT NULL)
RETURNS TABLE(statement integer,fingerprint text,state text,state_label text,record_count bigint)
LANGUAGE sql
BEGIN ATOMIC
    SELECT h.statement,h.fingerprint,x.result->>'state',mpp_view_label('state',x.result->>'state'),
        (SELECT coalesce(sum((e->>'record_count')::bigint),0)::bigint FROM jsonb_array_elements(x.result->'hits') e)
    FROM jsonb_to_recordset(coalesce(mpp_view_decode(nullif(p_hints,'')),'[]')::jsonb) h(statement integer,fingerprint text)
    CROSS JOIN LATERAL (SELECT mpp_query_exact(p_normalization,h.fingerprint,NULL,p_scope,p_database,p_user) result) x
    ORDER BY h.statement;
END;

-- SQL identities ranked over a time range, computed from the execution records.
-- One row per identity and timing category; stages are never added up. Failed,
-- cancelled and timed-out records have no timing category: not_success counts them
-- per identity, and an identity that only failed gets one row without a category.
CREATE OR REPLACE FUNCTION mpp_view_ranking(p_normalization text,p_start timestamptz,p_end timestamptz,
    p_scope text DEFAULT NULL,p_database text DEFAULT NULL,p_user text DEFAULT NULL,
    p_timings text DEFAULT 'request,execute_first',p_order text DEFAULT 'count',p_limit integer DEFAULT 100)
RETURNS TABLE(scope_id text,database text,execution_user text,fingerprint text,timing_type text,
    record_count bigint,known_durations bigint,total_ms numeric,mean_ms numeric,slowest_ms numeric,
    not_success bigint,last_at timestamptz,identity text,ranked_rows bigint)
LANGUAGE plpgsql STABLE SET plan_cache_mode=force_custom_plan
    SET parallel_setup_cost=0 SET parallel_tuple_cost=0 AS $function$
DECLARE wanted text[] := string_to_array(nullif(p_timings,''),',');
BEGIN
    IF p_order IS NULL OR p_order NOT IN ('count','total','mean','slowest','not_success') THEN RAISE EXCEPTION 'invalid_ranking_order'; END IF;
    IF p_limit IS NULL OR p_limit<1 OR p_limit>1000 THEN RAISE EXCEPTION 'invalid_page_size'; END IF;
    IF p_start IS NULL OR p_end IS NULL OR p_start>=p_end THEN RAISE EXCEPTION 'invalid_time_range'; END IF;
    RETURN QUERY
    WITH cells AS MATERIALIZED (
        SELECT o.scope_id,o.database,o.execution_user,f.value,o.timing_type,count(*) n,count(o.duration_ms) known,
            sum(o.duration_ms) total,max(o.duration_ms) slowest,
            count(*) FILTER (WHERE o.outcome<>'success') bad,max(o.end_at) last_at
        FROM mpp_occurrence o JOIN mpp_fingerprint f ON f.sql_id=o.sql_id
        WHERE f.normalization_id=p_normalization AND f.profile='mpp-csv/1' AND f.state='reliable'
            AND o.end_at>=p_start AND o.end_at<p_end
            AND (o.timing_type IS NULL OR wanted IS NULL OR o.timing_type=ANY(wanted))
            AND (p_scope IS NULL OR o.scope_id=p_scope)
            AND (p_database IS NULL OR o.database=p_database)
            AND (p_user IS NULL OR o.execution_user=p_user)
        GROUP BY o.scope_id,o.database,o.execution_user,f.value,o.timing_type
    ), errors AS (
        SELECT c.scope_id,c.database,c.execution_user,c.value,c.bad,c.last_at FROM cells c WHERE c.timing_type IS NULL
    ), ranked AS (
        SELECT c.scope_id,c.database,c.execution_user,c.value,c.timing_type,c.n,c.known,c.total,c.slowest,
            c.bad+coalesce(e.bad,0) bad,greatest(c.last_at,e.last_at) last_at
        FROM cells c LEFT JOIN errors e ON e.scope_id=c.scope_id AND e.value=c.value
            AND e.database IS NOT DISTINCT FROM c.database AND e.execution_user IS NOT DISTINCT FROM c.execution_user
        WHERE c.timing_type IS NOT NULL
        UNION ALL
        SELECT e.scope_id,e.database,e.execution_user,e.value,NULL,0,0,NULL,NULL,e.bad,e.last_at
        FROM errors e WHERE e.bad>0 AND NOT EXISTS (SELECT FROM cells c WHERE c.timing_type IS NOT NULL
            AND c.scope_id=e.scope_id AND c.value=e.value AND c.database IS NOT DISTINCT FROM e.database
            AND c.execution_user IS NOT DISTINCT FROM e.execution_user)
    )
    SELECT g.scope_id,g.database,g.execution_user,g.value,g.timing_type,g.n::bigint,g.known::bigint,g.total,
        round(g.total/nullif(g.known,0),3),g.slowest,g.bad::bigint,g.last_at,
        mpp_view_identity_token(g.scope_id,g.database,g.execution_user),count(*) OVER ()
    FROM ranked g
    ORDER BY CASE p_order WHEN 'count' THEN g.n WHEN 'total' THEN g.total
            WHEN 'mean' THEN g.total/nullif(g.known,0) WHEN 'slowest' THEN g.slowest ELSE g.bad END DESC NULLS LAST,
        g.scope_id,g.database,g.execution_user,g.value,g.timing_type
    LIMIT p_limit;
END $function$;

-- SQL identities ranked by the stored overall statistics of each cluster's current version.
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
        cardinality(r.active_dates),r.p50_ms,r.p95_ms,r.p99_ms,r.max_ms,r.mean_ms,
        (x.sufficiency->'basic'->>'met')::boolean,(x.sufficiency->'p95'->>'met')::boolean,
        (x.sufficiency->'p99'->>'met')::boolean,mpp_view_identity_token(r.scope_id,r.database,r.execution_user),r.rows
    FROM (SELECT g.scope_id,g.database,g.execution_user,g.fingerprint_value,g.timing_type,s.build_id,s.included_count,
            s.active_dates,s.active_week_starts,s.p50_ms,s.p95_ms,s.p99_ms,s.max_ms,s.mean_ms,count(*) OVER () rows
        FROM mpp_statistic s JOIN mpp_baseline_group g USING(group_id)
        WHERE s.partition_id=ANY(partitions) AND s.build_id=ANY(builds) AND s.layer='overall'
            AND s.included_count>0 AND s.included_count>=coalesce(p_min_samples,0)
            AND (wanted IS NULL OR g.timing_type=ANY(wanted))
            AND (p_database IS NULL OR g.database=p_database)
            AND (p_user IS NULL OR g.execution_user=p_user)
        ORDER BY CASE p_order WHEN 'samples' THEN s.included_count WHEN 'p50' THEN s.p50_ms WHEN 'p95' THEN s.p95_ms
                WHEN 'p99' THEN s.p99_ms WHEN 'max' THEN s.max_ms ELSE s.mean_ms END DESC NULLS LAST,s.group_id
        LIMIT p_limit) r
    JOIN build b ON b.build_id=r.build_id JOIN config_snapshot c USING(config_id)
    CROSS JOIN LATERAL (SELECT mpp_statistic_sufficiency(c.statistics_version,c.thresholds,'overall',
        r.included_count,r.active_dates,r.active_week_starts) sufficiency) x
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
