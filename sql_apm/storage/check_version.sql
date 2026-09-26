-- Current schema is 1.1.0; an upgrade retains the original 1.0.0 receipt.
DO $block$
DECLARE bad boolean; present boolean; total bigint;
BEGIN
    IF to_regclass(format('%I.schema_version',current_setting('apm.schema'))) IS NULL THEN
        IF current_setting('apm.check_only')='true' THEN
            RAISE EXCEPTION 'missing structure version';
        END IF;
        RETURN;
    END IF;
    EXECUTE format('SELECT count(*), bool_or(NOT ((version=%L AND script_sha256=%L) OR (version=%L AND script_sha256=%L))), bool_or(version=%L) FROM %I.schema_version',
        '1.0.0',current_setting('apm.legacy_sha256'),'1.1.0',current_setting('apm.sha256'),
        '1.1.0',current_setting('apm.schema')) INTO total,bad,present;
    IF bad OR (total>0 AND NOT present) THEN
        RAISE EXCEPTION 'incompatible structure version or script checksum';
    END IF;
    IF current_setting('apm.check_only')='true' AND NOT coalesce(present,false) THEN
        RAISE EXCEPTION 'missing structure version';
    END IF;
END $block$;
