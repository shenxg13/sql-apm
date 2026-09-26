-- Called only by migrate.sql after complete 1.0.0 catalog/version verification.
-- All renames and the version receipt are committed in the caller's transaction.
ALTER TABLE :"project_schema".sql_text RENAME TO mpp_sql_text;
ALTER TABLE :"project_schema".sql_text_evidence RENAME TO mpp_sql_text_evidence;
ALTER TABLE :"project_schema".occurrence RENAME TO mpp_occurrence;
ALTER TABLE :"project_schema".occurrence_evidence RENAME TO mpp_occurrence_evidence;
ALTER TABLE :"project_schema".normalization RENAME TO mpp_normalization;
ALTER TABLE :"project_schema".fingerprint RENAME TO mpp_fingerprint;
ALTER TABLE :"project_schema".baseline_group RENAME TO mpp_baseline_group;
ALTER TABLE :"project_schema".input_occurrence RENAME TO mpp_input_occurrence;
ALTER TABLE :"project_schema".decision RENAME TO mpp_decision;
ALTER TABLE :"project_schema".decision_reason RENAME TO mpp_decision_reason;
ALTER TABLE :"project_schema".decision_reason_evidence RENAME TO mpp_decision_reason_evidence;
ALTER TABLE :"project_schema".build_timing_coverage RENAME TO mpp_build_timing_coverage;
ALTER TABLE :"project_schema".build_coverage RENAME TO mpp_build_coverage;
ALTER TABLE :"project_schema".statistic RENAME TO mpp_statistic;
ALTER INDEX :"project_schema".sql_text_content_idx RENAME TO mpp_sql_text_content_idx;
ALTER INDEX :"project_schema".occurrence_scope_time_idx RENAME TO mpp_occurrence_scope_time_idx;
ALTER INDEX :"project_schema".occurrence_sql_time_idx RENAME TO mpp_occurrence_sql_time_idx;
ALTER INDEX :"project_schema".fingerprint_value_idx RENAME TO mpp_fingerprint_value_idx;
ALTER INDEX :"project_schema".decision_group_idx RENAME TO mpp_decision_group_idx;
ALTER INDEX :"project_schema".statistic_group_build_idx RENAME TO mpp_statistic_group_build_idx;

-- PostgreSQL-generated constraint names can truncate different column portions
-- after a table prefix changes. Match definitions to the freshly constructed
-- target catalog instead of guessing names; refuse ambiguous matches.
SET LOCAL search_path = pg_catalog;
DO $block$
DECLARE expected record; names text[];
BEGIN
    FOR expected IN
        SELECT t.relname, k.conname, k.contype,
               replace(pg_get_constraintdef(k.oid,true),quote_ident(n.nspname)||'.','@.') AS definition
        FROM pg_constraint k JOIN pg_class t ON t.oid=k.conrelid
        JOIN pg_namespace n ON n.oid=t.relnamespace
        WHERE n.nspname=current_setting('apm.expected')
        ORDER BY t.relname,k.conname
    LOOP
        SELECT array_agg(k.conname::text) INTO names
        FROM pg_constraint k JOIN pg_class t ON t.oid=k.conrelid
        JOIN pg_namespace n ON n.oid=t.relnamespace
        WHERE n.nspname=current_setting('apm.schema') AND t.relname=expected.relname
          AND k.contype=expected.contype
          AND replace(pg_get_constraintdef(k.oid,true),quote_ident(n.nspname)||'.','@.')=expected.definition;
        IF coalesce(cardinality(names),0)<>1 THEN
            RAISE EXCEPTION 'ambiguous or missing constraint: %.%',expected.relname,expected.conname;
        END IF;
        IF names[1]<>expected.conname THEN
            EXECUTE format('ALTER TABLE %I.%I RENAME CONSTRAINT %I TO %I',
                current_setting('apm.schema'),expected.relname,names[1],expected.conname);
        END IF;
    END LOOP;
END $block$;
