SELECT set_config('apm.project_role', :'project_role', false),
       set_config('apm.project_database', :'project_database', false),
       set_config('apm.project_schema', :'project_schema', false),
       set_config('apm.readonly_role', :'readonly_role', false),
       set_config('apm.readonly_timeout', :'readonly_timeout', false);
DO $block$
DECLARE r record; d record; role_name text := current_setting('apm.project_role');
    reader text := current_setting('apm.readonly_role');
BEGIN
    IF current_setting('server_version_num')::integer / 10000 <> 17 THEN
        RAISE EXCEPTION 'requires PostgreSQL 17';
    END IF;
    SELECT * INTO r FROM pg_roles WHERE rolname=role_name;
    IF FOUND THEN
        IF NOT r.rolcanlogin OR r.rolsuper OR r.rolcreatedb OR r.rolcreaterole
            OR r.rolreplication OR r.rolbypassrls
            OR EXISTS (SELECT FROM pg_auth_members WHERE member=r.oid) THEN
            RAISE EXCEPTION 'incompatible project role: %', role_name;
        END IF;
    ELSE
        EXECUTE format('CREATE ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT',role_name);
    END IF;
    -- Read-only account: privileges come only from the schema phase's grants.
    SELECT * INTO r FROM pg_roles WHERE rolname=reader;
    IF FOUND THEN
        IF NOT r.rolcanlogin OR r.rolsuper OR r.rolcreatedb OR r.rolcreaterole
            OR r.rolreplication OR r.rolbypassrls
            OR EXISTS (SELECT FROM pg_auth_members WHERE member=r.oid)
            OR EXISTS (SELECT FROM pg_database WHERE datdba=r.oid) THEN
            RAISE EXCEPTION 'incompatible read-only role: %', reader;
        END IF;
    ELSE
        EXECUTE format('CREATE ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT',reader);
    END IF;
    -- Session defaults only: a session may change them, privileges are the guard.
    EXECUTE format('ALTER ROLE %I SET statement_timeout=%L',reader,current_setting('apm.readonly_timeout'));
    EXECUTE format('ALTER ROLE %I SET default_transaction_read_only=on',reader);
    SELECT * INTO d FROM pg_database WHERE datname=current_setting('apm.project_database');
    IF FOUND AND (pg_get_userbyid(d.datdba) <> role_name OR d.encoding <> pg_char_to_encoding('UTF8')
        OR d.datcollate <> 'C' OR d.datctype <> 'C' OR d.datlocprovider <> 'c'
        OR NOT d.datallowconn OR d.datistemplate) THEN
        RAISE EXCEPTION 'incompatible project database: % (owner/encoding/locale/flags)',d.datname;
    END IF;
END $block$;
SELECT format('CREATE DATABASE %I OWNER %I TEMPLATE template0 ENCODING %L LOCALE_PROVIDER libc LC_COLLATE %L LC_CTYPE %L',
              :'project_database', :'project_role', 'UTF8', 'C', 'C')
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname=:'project_database')
\gexec
-- The owner keeps TEMPORARY; other logins cannot create even session tables.
SELECT format('REVOKE TEMPORARY ON DATABASE %I FROM PUBLIC', :'project_database')
\gexec
SELECT format('ALTER ROLE %I IN DATABASE %I SET search_path=%I,pg_catalog',
              :'readonly_role', :'project_database', :'project_schema')
\gexec
