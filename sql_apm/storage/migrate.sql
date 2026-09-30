-- Verified sequential migrations, one transaction; stop writers first.
\ir prepare.sql
SET LOCAL lock_timeout = '5s';
\ir schema.sql
\ir expected_partitions.sql
SET LOCAL search_path = pg_catalog;
\ir catalog.sql
CREATE TEMP TABLE expected_structure ON COMMIT DROP AS
    SELECT * FROM pg_temp.structure(current_setting('apm.expected'));
CREATE TEMP TABLE target_structure ON COMMIT DROP AS TABLE expected_structure;
DO $block$
DECLARE valid boolean;
BEGIN
    IF to_regclass(format('%I.schema_version',current_setting('apm.schema'))) IS NULL THEN
        RAISE EXCEPTION 'migration requires an initialized 1.0.0, 1.1.0, 1.2.0 1.3.0 or 1.4.0 schema';
    END IF;
    EXECUTE format('SELECT count(*)>0 AND bool_and((version=%L AND script_sha256=%L) OR (version=%L AND script_sha256=%L) OR (version=%L AND script_sha256=%L) OR (version=%L AND script_sha256=%L) OR (version=%L AND script_sha256=%L)) FROM %I.schema_version',
        '1.0.0',current_setting('apm.legacy_sha256'),'1.1.0',current_setting('apm.v110_sha256'),
        '1.2.0',current_setting('apm.v120_sha256'),'1.3.0',current_setting('apm.v130_sha256'),'1.4.0',current_setting('apm.sha256'),current_setting('apm.schema')) INTO valid;
    IF NOT valid THEN RAISE EXCEPTION 'incompatible legacy version or script checksum'; END IF;
END $block$;
SELECT set_config('apm.target',current_setting('apm.expected'),true);
SELECT (SELECT max(version) FROM :"project_schema".schema_version)='1.0.0' AS step_needed \gset
\if :step_needed
    SELECT set_config('apm.expected','_apm_legacy_'||pg_backend_pid(),true);
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
    -- The rename migration resolves generated constraint names against 1.1.0.
    DO $block$
    BEGIN
        EXECUTE format('DROP SCHEMA %I CASCADE',current_setting('apm.expected'));
        EXECUTE format('CREATE SCHEMA %I',current_setting('apm.expected'));
    END $block$;
    SELECT set_config('search_path',quote_ident(current_setting('apm.expected'))||',pg_catalog',true);
    \ir versions/1.1.0.sql
    SET LOCAL search_path = pg_catalog;
    \ir migrations/1.0.0-to-1.1.0.sql
    INSERT INTO :"project_schema".schema_version (version,script_sha256)
        VALUES ('1.1.0',current_setting('apm.v110_sha256'));
    DO $block$
    BEGIN
        EXECUTE format('DROP SCHEMA %I CASCADE',current_setting('apm.expected'));
    END $block$;
\endif
SELECT (SELECT max(version) FROM :"project_schema".schema_version)='1.1.0' AS step_needed \gset
\if :step_needed
    SELECT set_config('apm.expected','_apm_legacy_'||pg_backend_pid(),true);
    DO $block$
    BEGIN
        EXECUTE format('CREATE SCHEMA %I',current_setting('apm.expected'));
    END $block$;
    SELECT set_config('search_path',quote_ident(current_setting('apm.expected'))||',pg_catalog',true);
    \ir versions/1.1.0.sql
    SET LOCAL search_path = pg_catalog;
    TRUNCATE pg_temp.expected_structure;
    INSERT INTO pg_temp.expected_structure SELECT * FROM pg_temp.structure(current_setting('apm.expected'));
    \ir check_structure.sql
    \ir migrations/1.1.0-to-1.2.0.sql
    INSERT INTO :"project_schema".schema_version (version,script_sha256)
        VALUES ('1.2.0',current_setting('apm.v120_sha256'));
    DO $block$
    BEGIN
        EXECUTE format('DROP SCHEMA %I CASCADE',current_setting('apm.expected'));
    END $block$;
\endif
SELECT (SELECT max(version) FROM :"project_schema".schema_version)='1.2.0' AS step_needed \gset
\if :step_needed
    SELECT set_config('apm.expected','_apm_legacy_'||pg_backend_pid(),true);
    DO $block$
    BEGIN
        EXECUTE format('CREATE SCHEMA %I',current_setting('apm.expected'));
    END $block$;
    SELECT set_config('search_path',quote_ident(current_setting('apm.expected'))||',pg_catalog',true);
    \ir versions/1.2.0.sql
    SET LOCAL search_path = pg_catalog;
    TRUNCATE pg_temp.expected_structure;
    INSERT INTO pg_temp.expected_structure SELECT * FROM pg_temp.structure(current_setting('apm.expected'));
    \ir check_structure.sql
    \ir migrations/1.2.0-to-1.3.0.sql
    INSERT INTO :"project_schema".schema_version (version,script_sha256)
        VALUES ('1.3.0',current_setting('apm.v130_sha256'));
    DO $block$
    BEGIN
        EXECUTE format('DROP SCHEMA %I CASCADE',current_setting('apm.expected'));
    END $block$;
\endif
SELECT (SELECT max(version) FROM :"project_schema".schema_version)='1.3.0' AS step_needed \gset
\if :step_needed
    SELECT set_config('apm.expected','_apm_legacy_'||pg_backend_pid(),true);
    DO $block$
    BEGIN
        EXECUTE format('CREATE SCHEMA %I',current_setting('apm.expected'));
    END $block$;
    SELECT set_config('search_path',quote_ident(current_setting('apm.expected'))||',pg_catalog',true);
    \ir versions/1.3.0.sql
    SET LOCAL search_path = pg_catalog;
    TRUNCATE pg_temp.expected_structure;
    INSERT INTO pg_temp.expected_structure SELECT * FROM pg_temp.structure(current_setting('apm.expected'));
    \ir check_structure.sql
    \ir migrations/1.3.0-to-1.4.0.sql
    INSERT INTO :"project_schema".schema_version (version,script_sha256)
        VALUES ('1.4.0',current_setting('apm.sha256'));
    DO $block$
    BEGIN
        EXECUTE format('DROP SCHEMA %I CASCADE',current_setting('apm.expected'));
    END $block$;
\endif
SELECT set_config('apm.expected',current_setting('apm.target'),true);
TRUNCATE pg_temp.expected_structure;
INSERT INTO pg_temp.expected_structure SELECT * FROM pg_temp.target_structure;
\ir check_structure.sql
\ir check_version.sql
DO $block$
BEGIN
    EXECUTE format('DROP SCHEMA %I CASCADE',current_setting('apm.expected'));
END $block$;
COMMIT;
