-- Caller verified the frozen 1.6.0 catalog and rejected every populated schema.
-- Replace only profile checks; never rewrite stored identifiers or historical hashes.
SELECT set_config('search_path',quote_ident(current_setting('apm.schema'))||',pg_catalog',true);
DO $block$
DECLARE item record;
BEGIN
    FOR item IN SELECT c.relname,k.conname,pg_get_constraintdef(k.oid) AS definition
        FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid
        WHERE c.relnamespace=current_setting('apm.schema')::regnamespace
          AND k.contype='c' AND pg_get_constraintdef(k.oid) LIKE '%hashdata-csv/1%'
    LOOP
        EXECUTE format('ALTER TABLE %I DROP CONSTRAINT %I',item.relname,item.conname);
        EXECUTE format('ALTER TABLE %I ADD CONSTRAINT %I %s',item.relname,item.conname,
            replace(item.definition,'hashdata-csv/1','mpp-csv/1'));
    END LOOP;
END $block$;
\ir ../schema.sql
SET LOCAL search_path = pg_catalog;
