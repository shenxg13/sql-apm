-- Validated 1.2.0 first; no event or Decision copies and no existing row changes.
ALTER TABLE :"project_schema".mpp_fingerprint
    ADD UNIQUE (fingerprint_id, normalization_id, sql_id);
SELECT set_config('search_path',quote_ident(current_setting('apm.schema'))||',pg_catalog',true);
\ir ../versions/1.3.0.sql
SET LOCAL search_path = pg_catalog;
