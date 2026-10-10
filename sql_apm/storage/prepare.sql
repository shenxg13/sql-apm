BEGIN;
SET LOCAL client_min_messages = warning;
SELECT set_config('apm.schema', :'project_schema', true),
       set_config('apm.role', :'project_role', true),
       set_config('apm.sha256', :'script_sha256', true),
       set_config('apm.check_only', :'check_only', true),
       set_config('apm.require_complete', :'check_only', true),
       set_config('apm.legacy_sha256', :'legacy_sha256', true),
       set_config('apm.v110_sha256', :'v110_sha256', true),
       set_config('apm.v120_sha256', :'v120_sha256', true),
       set_config('apm.v130_sha256', :'v130_sha256', true),
       set_config('apm.v140_sha256', :'v140_sha256', true),
       set_config('apm.v150_sha256', :'v150_sha256', true),
       set_config('apm.v160_sha256', :'v160_sha256', true),
       set_config('apm.v170_sha256', :'v170_sha256', true),
       set_config('apm.v180_sha256', :'v180_sha256', true),
       set_config('apm.v190_sha256', :'v190_sha256', true),
       set_config('apm.v1100_sha256', :'v1100_sha256', true),
       set_config('apm.v1110_sha256', :'v1110_sha256', true),
       set_config('apm.readonly_role', :'readonly_role', true);
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
    -- The administrator creates the read-only account once (bootstrap); the
    -- project role only grants to it and never creates or alters roles.
    SELECT * INTO n FROM pg_roles WHERE rolname=current_setting('apm.readonly_role');
    IF NOT FOUND THEN
        RAISE EXCEPTION 'read-only role % is missing; run bootstrap as administrator first', current_setting('apm.readonly_role');
    END IF;
    IF n.rolsuper OR n.rolcreatedb OR n.rolcreaterole OR n.rolreplication OR n.rolbypassrls
        OR n.oid = r.oid OR EXISTS (SELECT FROM pg_auth_members WHERE member=n.oid) THEN
        RAISE EXCEPTION 'incompatible read-only role attributes/membership';
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
