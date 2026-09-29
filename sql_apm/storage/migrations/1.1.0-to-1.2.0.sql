-- Called only after complete 1.1.0 catalog and historical checksum verification.
-- Failure (including pre-existing approximate values) rolls back every step.
ALTER TABLE :"project_schema".mpp_fingerprint
    ADD CONSTRAINT mpp_fingerprint_not_approximate CHECK (value NOT LIKE 'approx:%');
SELECT set_config('search_path',quote_ident(current_setting('apm.schema'))||',pg_catalog',true);
\ir ../versions/1.2.0.sql
SET LOCAL search_path = pg_catalog;
