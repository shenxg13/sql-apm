"""Populated 1.9 -> 1.11 preserves every old column; fold is database generated."""
import hashlib
from database.fixture import statements


def verify_search_migration(v, root):
    v.init('bootstrap')
    old = (root / 'sql_apm/storage/versions/1.9.0.sql').read_bytes()
    v.sql('CREATE SCHEMA sql_apm;' + old.decode() +
          "INSERT INTO schema_version(version,script_sha256) VALUES ('1.9.0','" +
          hashlib.sha256(old).hexdigest() + "');" + statements())
    tables = v.sql("SELECT relname FROM pg_class WHERE relnamespace='sql_apm'::regnamespace "
                   "AND relkind IN ('r','p') AND NOT relispartition ORDER BY relname").splitlines()
    def state():
        return {t: v.sql("SELECT coalesce(jsonb_agg((to_jsonb(s)-'search_text') "
                          "ORDER BY (to_jsonb(s)-'search_text')::text),'[]') FROM \"" + t + '" s' +
                          (" WHERE version='1.9.0'" if t == 'schema_version' else '')) for t in tables}
    before = state()
    v.init('upgrade'); v.init('check')
    assert before == state()
    assert v.sql("SELECT bool_and(search_text=mpp_search_fold(text)) FROM mpp_sql_text") == 't'
    assert v.sql("SELECT count(*) FROM schema_version WHERE version='1.10.0'") == '1'
    assert v.sql("SELECT relname FROM pg_class WHERE relnamespace='sql_apm'::regnamespace "
                 "AND relkind IN ('r','p') AND NOT relispartition ORDER BY relname").splitlines() == tables
    for mode in ('schema', 'all', 'upgrade', 'check'):
        v.init(mode)
    assert before == state()
    v.require(True, 'populated 1.9 -> 1.11: old values, tables and receipts preserved; generated text complete; reruns safe')
