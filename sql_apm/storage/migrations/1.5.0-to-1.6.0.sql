-- Caller has verified the frozen 1.5.0 catalog. Stop all writers for migration.
SELECT set_config('search_path',quote_ident(current_setting('apm.schema'))||',pg_catalog',true);
ALTER TABLE task DROP CONSTRAINT task_mode_check,
    DROP CONSTRAINT task_stage_check, DROP CONSTRAINT task_check1,
    ADD CHECK (mode IN ('full','import_only','rebuild','snapshot','statistics')),
    ADD CHECK (stage IN ('import','snapshot','build','check','publish','none')),
    ADD CONSTRAINT task_check1 CHECK (busy_task_id IS NULL OR state='busy_rejected'),
    ADD COLUMN started_at timestamptz NOT NULL DEFAULT current_timestamp,
    ADD COLUMN finished_at timestamptz,
    ADD COLUMN stage_seconds jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(stage_seconds)='object');
-- Existing rows predate task timestamps; do not invent their historical end time.
DROP TABLE mpp_build_coverage;
\ir ../schema.sql
INSERT INTO mpp_build_layer_count
SELECT b.build_id,k.kind,l.layer,coalesce(s.rows,0),coalesce(s.groups,0)
FROM build b CROSS JOIN (VALUES ('formal'),('observation')) k(kind)
CROSS JOIN (VALUES ('overall'),('day'),('week'),('weekday'),('hour')) l(layer)
LEFT JOIN (
    SELECT build_id,'formal'::text kind,layer,count(*) rows,count(DISTINCT group_id) groups
    FROM mpp_statistic GROUP BY build_id,layer
    UNION ALL
    SELECT build_id,'observation',layer,count(*),count(DISTINCT group_id)
    FROM mpp_observation_statistic GROUP BY build_id,layer
) s USING(build_id,kind,layer)
WHERE b.state='calculated' AND b.results_saved;
SET LOCAL search_path = pg_catalog;
