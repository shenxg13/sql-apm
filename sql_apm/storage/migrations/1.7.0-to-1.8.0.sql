-- File bounds are derived import metadata. Existing files remain unknown.
SELECT set_config('search_path',quote_ident(current_setting('apm.schema'))||',pg_catalog',true);
ALTER TABLE source_file
    ADD COLUMN first_log_at timestamptz,
    ADD COLUMN last_log_at timestamptz,
    ADD CONSTRAINT source_file_log_time_bounds CHECK (
        (first_log_at IS NULL AND last_log_at IS NULL) OR
        (first_log_at IS NOT NULL AND last_log_at IS NOT NULL AND first_log_at <= last_log_at));
SET LOCAL search_path = pg_catalog;
