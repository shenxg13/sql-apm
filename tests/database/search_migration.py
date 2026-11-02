"""Populated 1.9 -> 1.14 preserves every old column; fold is database generated."""
import hashlib
from database.fixture import statements

# 1.12.0 adds the daily-run records; every earlier table is kept as it was.
DAILY = ['mpp_daily_cluster', 'mpp_daily_day', 'mpp_daily_file', 'mpp_daily_problem', 'mpp_daily_run']
SUMMARIES = ['mpp_sql_text_count', 'mpp_fingerprint_count', 'mpp_occurrence_name']

QUERY = ['mpp_query_text_key', 'mpp_query_structure', 'mpp_query_identity', 'mpp_query_membership', 'mpp_query_record', 'mpp_query_identity_total', 'mpp_query_text_total', 'mpp_query_state', 'mpp_query_pending']

def verify_search_migration(v, root):
    v.init('bootstrap')
    old = (root / 'sql_apm/storage/versions/1.9.0.sql').read_bytes()
    v.sql('CREATE SCHEMA sql_apm;' + old.decode() +
          "INSERT INTO schema_version(version,script_sha256) VALUES ('1.9.0','" +
          hashlib.sha256(old).hexdigest() + "');" + statements())
    tables = v.sql("SELECT relname FROM pg_class WHERE relnamespace='sql_apm'::regnamespace "
                   "AND relkind IN ('r','p') AND NOT relispartition ORDER BY relname").splitlines()
    def state():
        return {t: v.sql("SELECT coalesce(jsonb_agg((to_jsonb(s)-ARRAY['search_text','query_key']) "
                          "ORDER BY (to_jsonb(s)-ARRAY['search_text','query_key'])::text),'[]') FROM \"" + t + '" s' +
                          (" WHERE version='1.9.0'" if t == 'schema_version' else '')) for t in tables}
    before = state()
    v.init('upgrade'); v.init('check')
    assert before == state()
    assert v.sql("SELECT bool_and(search_text=mpp_search_fold(text)) FROM mpp_sql_text") == 't'
    assert v.sql("SELECT count(*) FROM schema_version WHERE version='1.10.0'") == '1'
    assert v.sql("SELECT relname FROM pg_class WHERE relnamespace='sql_apm'::regnamespace "
                 "AND relkind IN ('r','p') AND NOT relispartition ORDER BY relname").splitlines() == sorted(tables + DAILY + SUMMARIES + QUERY)
    for mode in ('schema', 'all', 'upgrade', 'check'):
        v.init(mode)
    assert before == state()
    v.require(True, 'populated 1.9 -> 1.14: old values, tables and receipts preserved; generated text complete; reruns safe')
