-- Unpublished 1.4.0 target derives logical sufficiency; no per-row JSONB copy.
-- Empty result tables are a precondition; no implicit data loss.
SELECT set_config('search_path',quote_ident(current_setting('apm.schema'))||',pg_catalog',true);
LOCK TABLE mpp_statistic,mpp_build_coverage IN ACCESS EXCLUSIVE MODE;
DO $block$
BEGIN
    IF EXISTS (SELECT FROM mpp_statistic) OR EXISTS (SELECT FROM mpp_build_coverage) THEN
        RAISE EXCEPTION 'statistics_migration_requires_empty_results';
    END IF;
END $block$;
DROP TABLE mpp_statistic,mpp_build_coverage;
CREATE TABLE mpp_result_partition (
    partition_id bigint PRIMARY KEY,
    scope_id text NOT NULL REFERENCES scope,
    build_month date NOT NULL CHECK (extract(day FROM build_month) = 1),
    UNIQUE (scope_id, build_month),
    UNIQUE (partition_id, scope_id)
);
ALTER TABLE build ADD COLUMN partition_id bigint,
    ADD FOREIGN KEY (partition_id, scope_id) REFERENCES mpp_result_partition (partition_id, scope_id),
    ADD UNIQUE (partition_id, build_id),
    ADD COLUMN diagnostics jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(diagnostics) = 'object');
\ir ../schema.sql
SET LOCAL search_path = pg_catalog;
