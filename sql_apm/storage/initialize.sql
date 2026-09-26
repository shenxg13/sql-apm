BEGIN;
SET LOCAL client_min_messages = warning;
SELECT set_config('apm.schema', :'project_schema', true),
       set_config('apm.role', :'project_role', true),
       set_config('apm.sha256', :'script_sha256', true),
       set_config('apm.check_only', :'check_only', true);
DO $block$
DECLARE r record; d record; n record;
BEGIN
    IF current_setting('server_version_num')::integer / 10000 <> 17 THEN
        RAISE EXCEPTION 'requires PostgreSQL 17';
    END IF;
    IF current_user <> current_setting('apm.role') THEN
        RAISE EXCEPTION 'schema phase must log in as project role';
    END IF;
    SELECT * INTO r FROM pg_roles WHERE rolname=current_user;
    IF r.rolsuper OR r.rolcreatedb OR r.rolcreaterole OR r.rolreplication OR r.rolbypassrls
        OR EXISTS (SELECT FROM pg_auth_members WHERE member=r.oid) THEN
        RAISE EXCEPTION 'incompatible project role attributes/membership';
    END IF;
    SELECT * INTO d FROM pg_database WHERE datname=current_database();
    IF d.datdba <> r.oid OR d.encoding <> pg_char_to_encoding('UTF8')
        OR d.datcollate <> 'C' OR d.datctype <> 'C' OR d.datlocprovider <> 'c'
        OR NOT d.datallowconn OR d.datistemplate THEN
        RAISE EXCEPTION 'incompatible project database owner/encoding/locale/flags';
    END IF;
    SELECT * INTO n FROM pg_namespace WHERE nspname=current_setting('apm.schema');
    IF FOUND AND n.nspowner <> r.oid THEN
        RAISE EXCEPTION 'incompatible schema owner: %', n.nspname;
    END IF;
    PERFORM set_config('apm.expected','_apm_expected_'||pg_backend_pid(),true);
    -- No IF NOT EXISTS: never adopt a pre-existing scratch schema.
    EXECUTE format('CREATE SCHEMA %I',current_setting('apm.expected'));
END $block$;
SELECT set_config('search_path', quote_ident(current_setting('apm.expected'))||',pg_catalog', true);
\ir schema.sql
SET LOCAL search_path = pg_catalog;
\ir catalog.sql
CREATE TEMP TABLE expected_structure ON COMMIT DROP AS
    SELECT * FROM pg_temp.structure(current_setting('apm.expected'));
DO $block$
DECLARE obj record; expected jsonb; mismatch text;
BEGIN
    FOR obj IN SELECT * FROM pg_temp.structure(current_setting('apm.schema')) LOOP
        SELECT definition INTO expected FROM pg_temp.expected_structure WHERE object_name=obj.object_name;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'unexpected object in managed schema: %', obj.object_name;
        END IF;
        IF obj.definition IS DISTINCT FROM expected THEN
            SELECT string_agg(e.key, ', ' ORDER BY e.key) INTO mismatch
            FROM jsonb_each(expected) e WHERE e.value IS DISTINCT FROM obj.definition->e.key;
            RAISE EXCEPTION 'incompatible object: %, differing catalog fields: %', obj.object_name, mismatch;
        END IF;
    END LOOP;
    IF EXISTS (SELECT FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
               WHERE n.nspname=current_setting('apm.schema'))
        OR EXISTS (SELECT FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace
                   WHERE n.nspname=current_setting('apm.schema') AND t.typtype IN ('d','e','r','m')) THEN
        RAISE EXCEPTION 'unexpected function or custom type in managed schema';
    END IF;
    IF current_setting('apm.check_only')='true' AND EXISTS (
        SELECT object_name FROM pg_temp.expected_structure
        EXCEPT SELECT object_name FROM pg_temp.structure(current_setting('apm.schema'))) THEN
        RAISE EXCEPTION 'missing objects in check-only mode';
    END IF;
    IF to_regclass(format('%I.schema_version',current_setting('apm.schema'))) IS NOT NULL THEN
        EXECUTE format('SELECT EXISTS (SELECT FROM %I.schema_version WHERE version<>%L OR script_sha256<>%L)',
            current_setting('apm.schema'),'1.0.0',current_setting('apm.sha256')) INTO obj;
        IF obj.exists THEN RAISE EXCEPTION 'incompatible structure version or script checksum'; END IF;
    END IF;
    EXECUTE format('DROP SCHEMA %I CASCADE',current_setting('apm.expected'));
    IF current_setting('apm.check_only') <> 'true' AND NOT EXISTS (
        SELECT FROM pg_namespace WHERE nspname=current_setting('apm.schema')) THEN
        EXECUTE format('CREATE SCHEMA %I',current_setting('apm.schema'));
    END IF;
END $block$;
\if :check_only
    DO $block$
    DECLARE ok boolean;
    BEGIN
        EXECUTE format('SELECT count(*)=1 FROM %I.schema_version WHERE version=%L AND script_sha256=%L',
            current_setting('apm.schema'),'1.0.0',current_setting('apm.sha256')) INTO ok;
        IF NOT ok THEN RAISE EXCEPTION 'missing structure version'; END IF;
    END $block$;
    ROLLBACK;
\else
    SELECT set_config('search_path', quote_ident(current_setting('apm.schema'))||',pg_catalog', true);
    \ir schema.sql
    INSERT INTO schema_version (version,script_sha256)
        VALUES ('1.0.0',current_setting('apm.sha256')) ON CONFLICT (version) DO NOTHING;
    COMMIT;
\endif
