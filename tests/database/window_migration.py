"""Populated 1.7 -> 1.8 changes only file bounds and the version receipt."""
import hashlib

from database.fixture import statements


def verify_window_migration(v, root):
    v.init('bootstrap')
    old = (root/'sql_apm/storage/versions/1.7.0.sql').read_bytes()
    v.sql('CREATE SCHEMA sql_apm;' + old.decode() +
          "INSERT INTO schema_version(version,script_sha256) VALUES ('1.7.0','" +
          hashlib.sha256(old).hexdigest() + "');" + statements())
    tables = v.sql("SELECT relname FROM pg_class WHERE relnamespace='sql_apm'::regnamespace "
                   "AND relkind IN ('r','p') AND relname<>'schema_version' ORDER BY relname").splitlines()
    def state():
        return {t:v.sql("SELECT coalesce(jsonb_agg((to_jsonb(s)-ARRAY['first_log_at','last_log_at','cleaned_at','groups_cleaned_at','search_text']) "
                       "ORDER BY (to_jsonb(s)-ARRAY['first_log_at','last_log_at','cleaned_at','groups_cleaned_at','search_text'])::text),'[]') FROM \""+t+'" s') for t in tables}
    before = state()
    receipt = v.sql('SELECT row_to_json(s) FROM schema_version s')
    v.init('upgrade'); v.init('check')
    assert v.sql("SELECT version FROM schema_version ORDER BY string_to_array(version,'.')::int[] DESC LIMIT 1") == '1.11.0'
    assert state() == before
    assert v.sql("SELECT row_to_json(s) FROM schema_version s WHERE version='1.7.0'") == receipt
    assert v.sql('SELECT bool_and(first_log_at IS NULL AND last_log_at IS NULL) FROM source_file') == 't'
    v.require(True, 'populated 1.7 -> 1.11 preserves every row and old receipt; bounds remain NULL')
    for assignment in ("first_log_at='2026-01-01 00:00:00+08'", "last_log_at='2026-01-01 00:00:00+08'",
                       "first_log_at='2026-01-02 00:00:00+08',last_log_at='2026-01-01 00:00:00+08'"):
        v.rejects('UPDATE source_file SET ' + assignment, 'invalid file bounds rejected')
    v.sql("UPDATE source_file SET first_log_at='2026-01-01 00:00:00+08',last_log_at='2026-01-01 00:00:00+08'")
    after = v.sql('SELECT jsonb_agg(s ORDER BY file_id) FROM source_file s')
    receipts = v.sql('SELECT jsonb_agg(s ORDER BY version) FROM schema_version s')
    for mode in ('schema','all','upgrade','check'): v.init(mode)
    assert v.sql('SELECT jsonb_agg(s ORDER BY file_id) FROM source_file s') == after
    assert v.sql('SELECT jsonb_agg(s ORDER BY version) FROM schema_version s') == receipts
    v.require(True, 'bounds constraints and populated same-version reruns preserve all values')
