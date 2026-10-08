SELECT set_config('apm.project_role', :'project_role', false),
       set_config('apm.project_database', :'project_database', false);
DO $block$
DECLARE r record; d record; role_name text := current_setting('apm.project_role');
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
