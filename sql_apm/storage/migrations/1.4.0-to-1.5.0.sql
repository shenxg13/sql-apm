-- Add observation results without rewriting existing statistics or partitions.
SELECT set_config('search_path',quote_ident(current_setting('apm.schema'))||',pg_catalog',true);
ALTER TABLE mpp_approximate_result ADD UNIQUE (result_id, rule_id, value);
\ir ../versions/1.5.0.sql
DO $block$
DECLARE row record;
BEGIN
    FOR row IN SELECT * FROM mpp_result_partition ORDER BY partition_id LOOP
        PERFORM mpp_ensure_result_partition(row.scope_id,row.build_month);
    END LOOP;
END $block$;
SET LOCAL search_path = pg_catalog;
