BEGIN;
SET LOCAL client_min_messages = warning;
SELECT set_config('apm.schema', :'project_schema', true),
       set_config('apm.role', :'project_role', true),
       set_config('apm.sha256', :'script_sha256', true),
       set_config('apm.check_only', :'check_only', true),
       set_config('apm.require_complete', :'check_only', true),
       set_config('apm.legacy_sha256', :'legacy_sha256', true);
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
