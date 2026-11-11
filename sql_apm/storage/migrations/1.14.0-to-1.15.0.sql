SELECT set_config('search_path',quote_ident(current_setting('apm.schema'))||',pg_catalog',true);
CREATE OR REPLACE FUNCTION mpp_query_merge_totals() RETURNS void LANGUAGE plpgsql AS $function$
DECLARE changed record;
BEGIN
    CREATE TEMP TABLE query_identity_delta ON COMMIT DROP AS
        SELECT identity_key,timing_type,sum(n)::bigint n,sum(success)::bigint success,
            min(first_at) FILTER(WHERE n>0) first_at,max(last_at) FILTER(WHERE n>0) last_at,
            min(first_at) FILTER(WHERE n<0) removed_first,max(last_at) FILTER(WHERE n<0) removed_last
        FROM (SELECT identity_key,timing_type,count(*) n,count(*) FILTER(WHERE outcome=0) success,
                min(end_at) first_at,max(end_at) last_at FROM query_added GROUP BY identity_key,timing_type
            UNION ALL SELECT identity_key,timing_type,-count(*),-count(*) FILTER(WHERE outcome=0),
                min(end_at),max(end_at) FROM query_removed GROUP BY identity_key,timing_type) x GROUP BY identity_key,timing_type;
    CREATE TEMP TABLE query_identity_totals ON COMMIT DROP AS
        SELECT d.identity_key,d.timing_type,coalesce(t.record_count,0)+d.n record_count,
            coalesce(t.success_count,0)+d.success success_count,
            CASE WHEN d.removed_first=t.first_at THEN (SELECT min(r.end_at) FROM mpp_query_record r
                WHERE r.identity_key=d.identity_key AND r.timing_type=d.timing_type)
                ELSE least(t.first_at,d.first_at) END first_at,
            CASE WHEN d.removed_last=t.last_at THEN (SELECT max(r.end_at) FROM mpp_query_record r
                WHERE r.identity_key=d.identity_key AND r.timing_type=d.timing_type)
                ELSE greatest(t.last_at,d.last_at) END last_at
        FROM query_identity_delta d LEFT JOIN LATERAL (SELECT t.* FROM mpp_query_identity_total t
            WHERE t.identity_key=d.identity_key AND t.timing_type=d.timing_type OFFSET 0) t ON true;
    FOR changed IN SELECT identity_key,timing_type FROM query_identity_totals WHERE record_count=0 LOOP
        DELETE FROM mpp_query_identity_total t WHERE t.identity_key=changed.identity_key AND t.timing_type=changed.timing_type;
    END LOOP;
    INSERT INTO mpp_query_identity_total SELECT * FROM query_identity_totals WHERE record_count>0
        ON CONFLICT (identity_key,timing_type) DO UPDATE SET record_count=excluded.record_count,
            success_count=excluded.success_count, first_at=excluded.first_at,last_at=excluded.last_at;
    DROP TABLE query_identity_delta,query_identity_totals;
    CREATE TEMP TABLE query_text_delta ON COMMIT DROP AS
        SELECT rule_key,sql_key,identity_key,sum(n)::bigint n,
            min(first_at) FILTER(WHERE n>0) first_at,max(last_at) FILTER(WHERE n>0) last_at,
            min(first_at) FILTER(WHERE n<0) removed_first,max(last_at) FILTER(WHERE n<0) removed_last
        FROM (SELECT rule_key,sql_key,identity_key,count(*) n,
                min(end_at) first_at,max(end_at) last_at FROM query_added GROUP BY rule_key,sql_key,identity_key
            UNION ALL SELECT rule_key,sql_key,identity_key,-count(*),
                min(end_at),max(end_at) FROM query_removed GROUP BY rule_key,sql_key,identity_key) x GROUP BY rule_key,sql_key,identity_key;
    CREATE TEMP TABLE query_text_totals ON COMMIT DROP AS
        SELECT d.rule_key,d.sql_key,d.identity_key,coalesce(t.record_count,0)+d.n record_count,

            CASE WHEN d.removed_first=t.first_at THEN (SELECT min(r.end_at) FROM mpp_query_record r
                WHERE r.rule_key=d.rule_key AND r.sql_key=d.sql_key AND r.identity_key=d.identity_key)
                ELSE least(t.first_at,d.first_at) END first_at,
            CASE WHEN d.removed_last=t.last_at THEN (SELECT max(r.end_at) FROM mpp_query_record r
                WHERE r.rule_key=d.rule_key AND r.sql_key=d.sql_key AND r.identity_key=d.identity_key)
                ELSE greatest(t.last_at,d.last_at) END last_at
        FROM query_text_delta d LEFT JOIN LATERAL (SELECT t.* FROM mpp_query_text_total t
            WHERE t.rule_key=d.rule_key AND t.sql_key=d.sql_key AND t.identity_key=d.identity_key OFFSET 0) t ON true;
    FOR changed IN SELECT rule_key,sql_key,identity_key FROM query_text_totals WHERE record_count=0 LOOP
        DELETE FROM mpp_query_text_total t WHERE t.rule_key=changed.rule_key
            AND t.sql_key=changed.sql_key AND t.identity_key=changed.identity_key;
    END LOOP;
    INSERT INTO mpp_query_text_total SELECT * FROM query_text_totals WHERE record_count>0
        ON CONFLICT (rule_key,sql_key,identity_key) DO UPDATE SET record_count=excluded.record_count,
             first_at=excluded.first_at,last_at=excluded.last_at;
    DROP TABLE query_text_delta,query_text_totals;
END $function$;

CREATE OR REPLACE FUNCTION mpp_query_refresh() RETURNS void LANGUAGE plpgsql AS $function$
DECLARE rebuilding boolean; records_changed boolean; changed record;
BEGIN
    IF to_regclass('pg_temp.mpp_query_changed_text') IS NULL THEN RETURN; END IF;
    -- File transactions take the lock only here, after their last SqlWriter call.
    -- VOLATILE READ COMMITTED commands below see prior lock owners' commits.
    PERFORM pg_advisory_xact_lock(1835101,1);
    SELECT EXISTS(SELECT FROM pg_temp.mpp_query_rebuild_flag) INTO rebuilding;
    IF rebuilding THEN
        INSERT INTO pg_temp.mpp_query_changed_text
            SELECT sql_id FROM mpp_sql_text UNION SELECT sql_id FROM mpp_query_text_key ON CONFLICT DO NOTHING;
        INSERT INTO pg_temp.mpp_query_changed_fingerprint
            SELECT normalization_id,sql_id,n.query_key FROM mpp_fingerprint JOIN mpp_normalization n USING(normalization_id)
            UNION SELECT n.normalization_id,k.sql_id,m.rule_key FROM mpp_query_membership m
                JOIN mpp_normalization n ON n.query_key=m.rule_key JOIN mpp_query_text_key k USING(sql_key)
            ON CONFLICT DO NOTHING;
    END IF;
    INSERT INTO mpp_query_text_key(sql_id)
        SELECT t.sql_id FROM pg_temp.mpp_query_changed_text c JOIN mpp_sql_text t USING(sql_id)
        ON CONFLICT DO NOTHING;
    -- OFFSET 0 keeps each history probe parameterized by a captured key. Temp
    -- table estimates must not turn an empty/new batch into a history hash scan.
    CREATE TEMP TABLE query_old_members (LIKE mpp_query_membership) ON COMMIT DROP;
    IF rebuilding THEN
        INSERT INTO query_old_members SELECT * FROM mpp_query_membership;
    ELSE
        INSERT INTO query_old_members SELECT m.* FROM pg_temp.mpp_query_changed_fingerprint c
        CROSS JOIN LATERAL (SELECT k.sql_key FROM mpp_query_text_key k WHERE k.sql_id=c.sql_id OFFSET 0) k
        CROSS JOIN LATERAL (SELECT m.* FROM mpp_query_membership m
            WHERE m.rule_key=c.rule_key AND m.sql_key=k.sql_key OFFSET 0) m;
    END IF;
    CREATE TEMP TABLE query_new_fingerprints ON COMMIT DROP AS
        SELECT n.query_key rule_key,k.sql_key,f.profile,f.value FROM pg_temp.mpp_query_changed_fingerprint c
        CROSS JOIN LATERAL (SELECT f.* FROM mpp_fingerprint f
            WHERE f.normalization_id=c.normalization_id AND f.sql_id=c.sql_id OFFSET 0) f
        JOIN mpp_normalization n ON n.normalization_id=c.normalization_id
        CROSS JOIN LATERAL (SELECT k.sql_key FROM mpp_query_text_key k WHERE k.sql_id=c.sql_id OFFSET 0) k
        WHERE f.state='reliable';
    INSERT INTO mpp_query_structure(rule_key,profile,fingerprint_value)
        SELECT DISTINCT rule_key,profile,value FROM query_new_fingerprints ON CONFLICT DO NOTHING;
    FOR changed IN SELECT * FROM query_old_members LOOP
        DELETE FROM mpp_query_membership m WHERE m.rule_key=changed.rule_key AND m.sql_key=changed.sql_key;
    END LOOP;
    INSERT INTO mpp_query_membership
        SELECT f.rule_key,f.sql_key,s.structure_key FROM query_new_fingerprints f
        CROSS JOIN LATERAL (SELECT s.structure_key FROM mpp_query_structure s
            WHERE s.rule_key=f.rule_key AND s.profile=f.profile AND s.fingerprint_value=f.value OFFSET 0) s;
    -- Only altered structures are recounted; ordinary inserts update in batches.
    CREATE TEMP TABLE query_member_delta ON COMMIT DROP AS
        SELECT structure_key,sum(delta)::bigint delta,min(sql_id) added_min,
            min(sql_id) FILTER(WHERE delta<0) removed_min
        FROM (SELECT m.structure_key,-1 delta,k.sql_id FROM query_old_members m
                CROSS JOIN LATERAL (SELECT k.sql_id FROM mpp_query_text_key k WHERE k.sql_key=m.sql_key OFFSET 0) k
            UNION ALL SELECT s.structure_key,1,k.sql_id FROM query_new_fingerprints f
                CROSS JOIN LATERAL (SELECT s.structure_key FROM mpp_query_structure s
                    WHERE s.rule_key=f.rule_key AND s.profile=f.profile AND s.fingerprint_value=f.value OFFSET 0) s
                CROSS JOIN LATERAL (SELECT k.sql_id FROM mpp_query_text_key k WHERE k.sql_key=f.sql_key OFFSET 0) k) x GROUP BY structure_key;
    FOR changed IN SELECT * FROM query_member_delta LOOP
        UPDATE mpp_query_structure s SET text_count=s.text_count+changed.delta,
        example_sql_id=CASE WHEN changed.removed_min=s.example_sql_id THEN
            (SELECT min(k.sql_id) FROM mpp_query_membership m
                CROSS JOIN LATERAL (SELECT k.sql_id FROM mpp_query_text_key k WHERE k.sql_key=m.sql_key OFFSET 0) k
                WHERE m.structure_key=s.structure_key)
            ELSE least(s.example_sql_id,changed.added_min) END
        WHERE s.structure_key=changed.structure_key;
    END LOOP;

    -- Fingerprint changes find existing events through the original sql_id index;
    -- new file events use their complete primary key, never a history rescan.
    CREATE TEMP TABLE query_source_events ON COMMIT DROP AS
        SELECT n.query_key rule_key,o.analysis_id,o.occurrence_id,o.sql_id,o.scope_id,o.database,o.execution_user,o.end_at,o.estimated_start_at,o.duration_ms,o.timing_type,o.outcome,o.unit,o.request_shape FROM pg_temp.mpp_query_changed_event c
            CROSS JOIN mpp_normalization n
            CROSS JOIN LATERAL (SELECT o.* FROM mpp_occurrence o
                WHERE o.analysis_id=c.analysis_id AND o.occurrence_id=c.occurrence_id OFFSET 0) o
            CROSS JOIN LATERAL (SELECT k.sql_key FROM mpp_query_text_key k WHERE k.sql_id=o.sql_id OFFSET 0) k
            CROSS JOIN LATERAL (SELECT m.rule_key FROM mpp_query_membership m
                WHERE m.rule_key=n.query_key AND m.sql_key=k.sql_key OFFSET 0) m
        UNION
        SELECT n.query_key,o.analysis_id,o.occurrence_id,o.sql_id,o.scope_id,o.database,o.execution_user,o.end_at,o.estimated_start_at,o.duration_ms,o.timing_type,o.outcome,o.unit,o.request_shape FROM pg_temp.mpp_query_changed_fingerprint c
            JOIN mpp_normalization n USING(normalization_id)
            CROSS JOIN LATERAL (SELECT o.* FROM mpp_occurrence o WHERE o.sql_id=c.sql_id OFFSET 0) o;
    INSERT INTO mpp_query_identity(structure_key,rule_key,scope_key,database,execution_user)
        SELECT DISTINCT m.structure_key,m.rule_key,s.query_key,o.database,o.execution_user
        FROM query_source_events o
        CROSS JOIN LATERAL (SELECT k.sql_key FROM mpp_query_text_key k WHERE k.sql_id=o.sql_id OFFSET 0) k
        CROSS JOIN LATERAL (SELECT m.* FROM mpp_query_membership m
            WHERE m.rule_key=o.rule_key AND m.sql_key=k.sql_key OFFSET 0) m
        JOIN scope s USING(scope_id) ON CONFLICT DO NOTHING;
    CREATE TEMP TABLE query_new_records (LIKE mpp_query_record) ON COMMIT DROP;
    INSERT INTO query_new_records
        SELECT o.rule_key,s.query_key,i.identity_key,a.query_key,k.sql_key,o.occurrence_id,
            o.end_at,o.estimated_start_at,o.duration_ms,
            coalesce(array_position(ARRAY['request','execute_first','execute_fetch','parse','bind'],o.timing_type),0)::smallint,
            (array_position(ARRAY['success','failed','cancelled','timed_out','unknown'],o.outcome)-1)::smallint,
            (array_position(ARRAY['request','call'],o.unit)-1)::smallint,
            (array_position(ARRAY['single','batch','unknown'],o.request_shape)-1)::smallint
        FROM query_source_events o JOIN scope s USING(scope_id) JOIN analysis a USING(analysis_id)
        CROSS JOIN LATERAL (SELECT k.sql_key FROM mpp_query_text_key k WHERE k.sql_id=o.sql_id OFFSET 0) k
        CROSS JOIN LATERAL (SELECT m.* FROM mpp_query_membership m
            WHERE m.rule_key=o.rule_key AND m.sql_key=k.sql_key OFFSET 0) m
        -- Four disjoint NULL cases keep the full existing unique index usable;
        -- IS NOT DISTINCT FROM would leave names as filters after its prefix.
        CROSS JOIN LATERAL (
            SELECT i.identity_key FROM mpp_query_identity i
            WHERE i.structure_key=m.structure_key AND i.scope_key=s.query_key
                AND i.database=o.database AND i.execution_user=o.execution_user
            UNION ALL SELECT i.identity_key FROM mpp_query_identity i
            WHERE i.structure_key=m.structure_key AND i.scope_key=s.query_key
                AND o.database IS NULL AND i.database IS NULL AND i.execution_user=o.execution_user
            UNION ALL SELECT i.identity_key FROM mpp_query_identity i
            WHERE i.structure_key=m.structure_key AND i.scope_key=s.query_key
                AND i.database=o.database AND o.execution_user IS NULL AND i.execution_user IS NULL
            UNION ALL SELECT i.identity_key FROM mpp_query_identity i
            WHERE i.structure_key=m.structure_key AND i.scope_key=s.query_key
                AND o.database IS NULL AND i.database IS NULL
                AND o.execution_user IS NULL AND i.execution_user IS NULL
            OFFSET 0) i;
    CREATE TEMP TABLE query_old_records (LIKE mpp_query_record) ON COMMIT DROP;
    IF rebuilding THEN
        INSERT INTO query_old_records SELECT * FROM mpp_query_record;
    ELSE
        INSERT INTO query_old_records
            SELECT r.* FROM pg_temp.mpp_query_changed_event c
                CROSS JOIN mpp_normalization n
                CROSS JOIN LATERAL (SELECT r.* FROM mpp_query_record r
                    WHERE r.rule_key=n.query_key AND r.analysis_key=c.analysis_key AND r.occurrence_id=c.occurrence_id OFFSET 0) r
            UNION
            SELECT r.* FROM pg_temp.mpp_query_changed_fingerprint c
                CROSS JOIN LATERAL (SELECT k.sql_key FROM mpp_query_text_key k WHERE k.sql_id=c.sql_id OFFSET 0) k
                CROSS JOIN LATERAL (SELECT r.* FROM mpp_query_record r
                    WHERE r.rule_key=c.rule_key AND r.sql_key=k.sql_key OFFSET 0) r;
    END IF;
    IF NOT EXISTS(SELECT FROM query_old_records) THEN
        -- New-file/first-rule writes and empty-table backfills have no previous projection. The
        -- source UNION and unique memberships/identities already make each
        -- desired event key unique; sorting it again and copying it is wasted.
        CREATE TEMP TABLE query_removed (LIKE mpp_query_record) ON COMMIT DROP;
        ALTER TABLE query_new_records RENAME TO query_added;
    ELSE
        CREATE TEMP TABLE query_removed ON COMMIT DROP AS TABLE query_old_records EXCEPT TABLE query_new_records;
        CREATE TEMP TABLE query_added ON COMMIT DROP AS TABLE query_new_records EXCEPT TABLE query_old_records;
        DROP TABLE query_new_records;
    END IF;
    SELECT EXISTS(SELECT FROM query_removed) OR EXISTS(SELECT FROM query_added) INTO records_changed;
    IF records_changed THEN
        FOR changed IN SELECT rule_key,analysis_key,occurrence_id FROM query_removed LOOP
            DELETE FROM mpp_query_record r WHERE r.rule_key=changed.rule_key
                AND r.analysis_key=changed.analysis_key AND r.occurrence_id=changed.occurrence_id;
        END LOOP;
        INSERT INTO mpp_query_record SELECT * FROM query_added;
        PERFORM mpp_query_merge_totals();
        FOR changed IN SELECT DISTINCT identity_key FROM query_removed LOOP
            DELETE FROM mpp_query_identity i WHERE i.identity_key=changed.identity_key
                AND NOT EXISTS(SELECT FROM mpp_query_identity_total t WHERE t.identity_key=i.identity_key);
        END LOOP;
        UPDATE mpp_query_state SET revision=revision+1;
    END IF;
    FOR changed IN SELECT structure_key FROM query_member_delta LOOP
        DELETE FROM mpp_query_structure s WHERE s.structure_key=changed.structure_key AND s.text_count=0;
    END LOOP;
    FOR changed IN SELECT c.sql_id FROM pg_temp.mpp_query_changed_text c
        WHERE NOT EXISTS(SELECT FROM mpp_sql_text t WHERE t.sql_id=c.sql_id)
    LOOP
        DELETE FROM mpp_query_text_key k WHERE k.sql_id=changed.sql_id;
    END LOOP;
    DELETE FROM mpp_query_pending WHERE transaction_id=pg_current_xact_id();
    TRUNCATE pg_temp.mpp_query_changed_text,pg_temp.mpp_query_changed_fingerprint,
        pg_temp.mpp_query_changed_event,pg_temp.mpp_query_rebuild_flag;
    DROP TABLE query_old_members,query_new_fingerprints,query_member_delta,query_source_events,
        query_old_records,query_removed,query_added;
END $function$;

SET LOCAL search_path=pg_catalog;
