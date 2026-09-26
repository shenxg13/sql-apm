-- Explicit, single-step migration. Stop writers and use a maintenance window.
\ir prepare.sql
SET LOCAL lock_timeout = '5s';
\ir schema.sql
SET LOCAL search_path = pg_catalog;
\ir catalog.sql
CREATE TEMP TABLE expected_structure ON COMMIT DROP AS
    SELECT * FROM pg_temp.structure(current_setting('apm.expected'));
CREATE TEMP TABLE target_structure ON COMMIT DROP AS TABLE expected_structure;
DO $block$
BEGIN
    IF to_regclass(format('%I.schema_version',current_setting('apm.schema'))) IS NULL THEN
        RAISE EXCEPTION 'migration requires an initialized 1.0.0 or 1.1.0 schema';
    END IF;
END $block$;
SELECT EXISTS (SELECT FROM :"project_schema".schema_version WHERE version='1.0.0')
   AND NOT EXISTS (SELECT FROM :"project_schema".schema_version WHERE version='1.1.0') AS upgrade_needed \gset
\if :upgrade_needed
    SELECT set_config('apm.target',current_setting('apm.expected'),true),
           set_config('apm.expected','_apm_legacy_'||pg_backend_pid(),true);
    DO $block$
    BEGIN
        EXECUTE format('CREATE SCHEMA %I',current_setting('apm.expected'));
    END $block$;
    SELECT set_config('search_path',quote_ident(current_setting('apm.expected'))||',pg_catalog',true);
    \ir versions/1.0.0.sql
    SET LOCAL search_path = pg_catalog;
    TRUNCATE pg_temp.expected_structure;
    INSERT INTO pg_temp.expected_structure SELECT * FROM pg_temp.structure(current_setting('apm.expected'));
    \ir check_structure.sql
    DO $block$
    DECLARE valid boolean;
    BEGIN
        EXECUTE format('SELECT count(*)=1 AND bool_and(version=%L AND script_sha256=%L) FROM %I.schema_version',
            '1.0.0',current_setting('apm.legacy_sha256'),current_setting('apm.schema')) INTO valid;
        IF NOT valid THEN RAISE EXCEPTION 'incompatible legacy version or script checksum'; END IF;
        EXECUTE format('DROP SCHEMA %I CASCADE',current_setting('apm.expected'));
    END $block$;
    SELECT set_config('apm.expected',current_setting('apm.target'),true);
    TRUNCATE pg_temp.expected_structure;
    INSERT INTO pg_temp.expected_structure SELECT * FROM pg_temp.target_structure;
    \ir migrations/1.0.0-to-1.1.0.sql
    \ir check_structure.sql
    INSERT INTO :"project_schema".schema_version (version,script_sha256)
        VALUES ('1.1.0',current_setting('apm.sha256'));
\else
    \ir check_structure.sql
\endif
\ir check_version.sql
DO $block$
BEGIN
    EXECUTE format('DROP SCHEMA %I CASCADE',current_setting('apm.expected'));
END $block$;
COMMIT;
