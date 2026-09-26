-- Compare the project catalog with pg_temp.expected_structure. No changes.
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
    IF current_setting('apm.require_complete')='true' AND EXISTS (
        SELECT object_name FROM pg_temp.expected_structure
        EXCEPT SELECT object_name FROM pg_temp.structure(current_setting('apm.schema'))) THEN
        RAISE EXCEPTION 'missing objects in check-only mode';
    END IF;
END $block$;
