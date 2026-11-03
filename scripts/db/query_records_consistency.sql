-- Read-only oracle: derive every value from original facts, never the refresh
-- function. Each result is a symmetric row difference; all must be zero.
-- CASE text_keys
WITH expected AS (SELECT sql_id FROM mpp_sql_text), actual AS (SELECT sql_id FROM mpp_query_text_key)
SELECT count(*) FROM ((TABLE expected EXCEPT TABLE actual) UNION ALL (TABLE actual EXCEPT TABLE expected)) d;
-- CASE memberships
WITH expected AS (SELECT normalization_id,profile,sql_id,value FROM mpp_fingerprint WHERE state='reliable'),
actual AS (SELECT n.normalization_id,s.profile,k.sql_id,s.fingerprint_value
    FROM mpp_query_membership m JOIN mpp_query_text_key k USING(sql_key)
    JOIN mpp_normalization n ON n.query_key=m.rule_key JOIN mpp_query_structure s USING(structure_key))
SELECT count(*) FROM ((TABLE expected EXCEPT TABLE actual) UNION ALL (TABLE actual EXCEPT TABLE expected)) d;
-- CASE structures
WITH expected AS (SELECT normalization_id,profile,value,count(*) n,min(sql_id) example
    FROM mpp_fingerprint WHERE state='reliable' GROUP BY normalization_id,profile,value),
actual AS (SELECT n.normalization_id,s.profile,s.fingerprint_value,s.text_count,s.example_sql_id
    FROM mpp_query_structure s JOIN mpp_normalization n ON n.query_key=s.rule_key)
SELECT count(*) FROM ((TABLE expected EXCEPT TABLE actual) UNION ALL (TABLE actual EXCEPT TABLE expected)) d;
-- CASE identities
WITH expected AS (SELECT DISTINCT f.normalization_id,f.profile,f.value,o.scope_id,o.database,o.execution_user
    FROM mpp_occurrence o JOIN mpp_fingerprint f USING(sql_id) WHERE f.state='reliable'),
actual AS (SELECT n.normalization_id,s.profile,s.fingerprint_value,c.scope_id,i.database,i.execution_user
    FROM mpp_query_identity i JOIN mpp_query_structure s USING(structure_key)
    JOIN mpp_normalization n ON n.query_key=i.rule_key JOIN scope c ON c.query_key=i.scope_key)
SELECT count(*) FROM ((TABLE expected EXCEPT TABLE actual) UNION ALL (TABLE actual EXCEPT TABLE expected)) d;
-- CASE records
WITH expected AS (SELECT f.normalization_id,f.profile,f.value,o.scope_id,o.database,o.execution_user,
    o.analysis_id,o.occurrence_id,o.sql_id,o.end_at,o.estimated_start_at,o.duration_ms,
    o.timing_type,o.outcome,o.unit,o.request_shape
    FROM mpp_occurrence o JOIN mpp_fingerprint f USING(sql_id) WHERE f.state='reliable'),
actual AS (SELECT n.normalization_id,s.profile,s.fingerprint_value,c.scope_id,i.database,i.execution_user,
    a.analysis_id,r.occurrence_id,k.sql_id,r.end_at,r.estimated_start_at,r.duration_ms,
    (ARRAY['request','execute_first','execute_fetch','parse','bind'])[r.timing_type],
    (ARRAY['success','failed','cancelled','timed_out','unknown'])[r.outcome+1],
    (ARRAY['request','call'])[r.unit+1],(ARRAY['single','batch','unknown'])[r.request_shape+1]
    FROM mpp_query_record r JOIN mpp_normalization n ON n.query_key=r.rule_key
    JOIN scope c ON c.query_key=r.scope_key JOIN analysis a ON a.query_key=r.analysis_key
    JOIN mpp_query_identity i USING(identity_key) JOIN mpp_query_structure s USING(structure_key)
    JOIN mpp_query_text_key k USING(sql_key))
SELECT count(*) FROM ((TABLE expected EXCEPT ALL TABLE actual) UNION ALL (TABLE actual EXCEPT ALL TABLE expected)) d;
-- CASE identity_totals
WITH expected AS (SELECT f.normalization_id,f.profile,f.value,o.scope_id,o.database,o.execution_user,o.timing_type,
    count(*) n,count(*) FILTER(WHERE o.outcome='success') success,min(o.end_at) first_at,max(o.end_at) last_at
    FROM mpp_occurrence o JOIN mpp_fingerprint f USING(sql_id) WHERE f.state='reliable'
    GROUP BY f.normalization_id,f.profile,f.value,o.scope_id,o.database,o.execution_user,o.timing_type),
actual AS (SELECT n.normalization_id,s.profile,s.fingerprint_value,c.scope_id,i.database,i.execution_user,
    (ARRAY['request','execute_first','execute_fetch','parse','bind'])[t.timing_type],
    t.record_count,t.success_count,t.first_at,t.last_at
    FROM mpp_query_identity_total t JOIN mpp_query_identity i USING(identity_key)
    JOIN mpp_query_structure s USING(structure_key) JOIN mpp_normalization n ON n.query_key=i.rule_key
    JOIN scope c ON c.query_key=i.scope_key)
SELECT count(*) FROM ((TABLE expected EXCEPT ALL TABLE actual) UNION ALL (TABLE actual EXCEPT ALL TABLE expected)) d;
-- CASE text_totals
WITH expected AS (SELECT f.normalization_id,f.profile,f.value,o.scope_id,o.database,o.execution_user,o.sql_id,
    count(*) n,min(o.end_at) first_at,max(o.end_at) last_at
    FROM mpp_occurrence o JOIN mpp_fingerprint f USING(sql_id) WHERE f.state='reliable'
    GROUP BY f.normalization_id,f.profile,f.value,o.scope_id,o.database,o.execution_user,o.sql_id),
actual AS (SELECT n.normalization_id,s.profile,s.fingerprint_value,c.scope_id,i.database,i.execution_user,k.sql_id,
    t.record_count,t.first_at,t.last_at
    FROM mpp_query_text_total t JOIN mpp_query_identity i USING(identity_key)
    JOIN mpp_query_structure s USING(structure_key) JOIN mpp_normalization n ON n.query_key=t.rule_key
    JOIN scope c ON c.query_key=i.scope_key JOIN mpp_query_text_key k USING(sql_key))
SELECT count(*) FROM ((TABLE expected EXCEPT ALL TABLE actual) UNION ALL (TABLE actual EXCEPT ALL TABLE expected)) d;
-- CASE dangling
SELECT (SELECT count(*) FROM mpp_query_record r WHERE
    NOT EXISTS(SELECT FROM mpp_normalization n WHERE n.query_key=r.rule_key)
    OR NOT EXISTS(SELECT FROM scope s WHERE s.query_key=r.scope_key)
    OR NOT EXISTS(SELECT FROM analysis a WHERE a.query_key=r.analysis_key)
    OR NOT EXISTS(SELECT FROM mpp_query_text_key k WHERE k.sql_key=r.sql_key)
    OR NOT EXISTS(SELECT FROM mpp_query_identity i WHERE i.identity_key=r.identity_key AND i.rule_key=r.rule_key AND i.scope_key=r.scope_key))
    +(SELECT count(*) FROM mpp_query_identity i WHERE NOT EXISTS(SELECT FROM mpp_query_structure s WHERE s.structure_key=i.structure_key AND s.rule_key=i.rule_key))
    +(SELECT count(*) FROM mpp_query_membership m WHERE NOT EXISTS(SELECT FROM mpp_query_structure s WHERE s.structure_key=m.structure_key AND s.rule_key=m.rule_key))
    +(SELECT count(*) FROM mpp_query_pending);
