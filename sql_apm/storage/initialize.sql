\ir prepare.sql
\ir schema.sql
\ir expected_partitions.sql
SET LOCAL search_path = pg_catalog;
\ir catalog.sql
CREATE TEMP TABLE expected_structure ON COMMIT DROP AS
    SELECT * FROM pg_temp.structure(current_setting('apm.expected'));
\if :check_only
\else
    -- The read-only grants are part of the expected structure, and a compatible
    -- schema created before them has none. Grant first, then compare; a failed
    -- comparison rolls this back with everything else in the transaction.
    DO $block$
    DECLARE item record; reader text := current_setting('apm.readonly_role');
    BEGIN
        IF EXISTS (SELECT FROM pg_namespace WHERE nspname=current_setting('apm.schema')) THEN
            EXECUTE format('GRANT USAGE ON SCHEMA %I TO %I',current_setting('apm.schema'),reader);
            FOR item IN SELECT c.relname FROM pg_class c
                WHERE c.relnamespace=current_setting('apm.schema')::regnamespace
                  AND c.relkind IN ('r','p') AND NOT c.relispartition
                  -- A table with another owner is left for the comparison to name.
                  AND pg_get_userbyid(c.relowner)=current_user ORDER BY c.relname LOOP
                EXECUTE format('GRANT SELECT ON TABLE %I.%I TO %I',current_setting('apm.schema'),item.relname,reader);
            END LOOP;
        END IF;
    END $block$;
\endif
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
        VALUES ('1.11.0',current_setting('apm.sha256')) ON CONFLICT (version) DO NOTHING;
    COMMIT;
\endif
