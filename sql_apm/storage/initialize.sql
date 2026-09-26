\ir prepare.sql
\ir schema.sql
SET LOCAL search_path = pg_catalog;
\ir catalog.sql
CREATE TEMP TABLE expected_structure ON COMMIT DROP AS
    SELECT * FROM pg_temp.structure(current_setting('apm.expected'));
\ir check_structure.sql
\ir check_version.sql
DO $block$
BEGIN
    EXECUTE format('DROP SCHEMA %I CASCADE',current_setting('apm.expected'));
    IF current_setting('apm.check_only') <> 'true' AND NOT EXISTS (
        SELECT FROM pg_namespace WHERE nspname=current_setting('apm.schema')) THEN
        EXECUTE format('CREATE SCHEMA %I',current_setting('apm.schema'));
    END IF;
END $block$;
\if :check_only
    ROLLBACK;
\else
    SELECT set_config('search_path', quote_ident(current_setting('apm.schema'))||',pg_catalog', true);
    \ir schema.sql
    INSERT INTO schema_version (version,script_sha256)
        VALUES ('1.1.0',current_setting('apm.sha256')) ON CONFLICT (version) DO NOTHING;
    COMMIT;
\endif
