"""Populated 1.10 -> 1.12: 1.11 adds only functions and read-only grants, 1.12 the daily-run records; every row is preserved."""
import hashlib
from database.fixture import statements

TABLES = ("SELECT relname FROM pg_class WHERE relnamespace='sql_apm'::regnamespace "
          "AND relkind IN ('r','p') AND NOT relispartition ORDER BY relname")
DAILY = ['mpp_daily_cluster', 'mpp_daily_day', 'mpp_daily_file', 'mpp_daily_problem', 'mpp_daily_run']


def verify_views_migration(v, root):
    v.init('bootstrap')
    old = (root / 'sql_apm/storage/versions/1.10.0.sql').read_bytes()
    v.sql('CREATE SCHEMA sql_apm;' + old.decode() +
          "INSERT INTO schema_version(version,script_sha256) VALUES ('1.10.0','" +
          hashlib.sha256(old).hexdigest() + "');" + statements())
    tables = v.sql(TABLES).splitlines()
    shape = lambda: v.sql("SELECT jsonb_agg(jsonb_build_array(c.relname,a.attname,format_type(a.atttypid,a.atttypmod)) ORDER BY c.relname,a.attnum) "
                          "FROM pg_class c JOIN pg_attribute a ON a.attrelid=c.oid WHERE c.relnamespace='sql_apm'::regnamespace "
                          "AND c.relkind IN ('r','p','i') AND a.attnum>0 AND NOT a.attisdropped AND c.relname NOT LIKE 'mpp\\_daily\\_%'")
    def state():
        return {t: v.sql('SELECT coalesce(jsonb_agg(to_jsonb(s) ORDER BY to_jsonb(s)::text),\'[]\') FROM "' + t + '" s' +
                         (" WHERE version='1.10.0'" if t == 'schema_version' else '')) for t in tables}
    before, columns = state(), shape()
    old_functions = set(v.sql("SELECT proname FROM pg_proc WHERE pronamespace='sql_apm'::regnamespace").splitlines())
    reader = lambda sql, ok=True: v.sql('SET ROLE sql_apm_ro; ' + sql, admin=True, database='sql_apm', ok=ok)
    # Before the upgrade the account exists but has been granted nothing.
    reader('SELECT count(*) FROM sql_apm.scope', ok=False)
    v.init('upgrade'); v.init('check')
    assert before == state() and columns == shape() and v.sql(TABLES).splitlines() == sorted(tables + DAILY)
    assert v.sql("SELECT count(*) FROM schema_version WHERE version='1.11.0'") == '1'
    assert v.sql("SELECT count(*) FROM pg_extension WHERE extname<>'plpgsql'") == '0'
    new_functions = set(v.sql("SELECT proname FROM pg_proc WHERE pronamespace='sql_apm'::regnamespace").splitlines())
    assert old_functions < new_functions and {'mpp_search_passage', 'mpp_view_search', 'mpp_view_records'} <= new_functions - old_functions
    assert v.sql("SELECT count(*) FROM pg_proc WHERE pronamespace='sql_apm'::regnamespace AND proname IN ('mpp_query_fuzzy','mpp_query_search')") == '2'
    # The account created by the administrator before the upgrade is usable right after it.
    assert reader("SELECT count(*) FROM sql_apm.scope") == '1'
    assert reader("SELECT sql_apm.mpp_view_decode(sql_apm.mpp_view_encode('x\"y'))") == 'x"y'
    assert reader("SET search_path=sql_apm,pg_catalog; SELECT count(*) FROM mpp_view_search('N1','words','select')") == '1'
    reader('DELETE FROM sql_apm.scope', ok=False)
    for mode in ('schema', 'all', 'upgrade', 'check'):
        v.init(mode)
    assert before == state()
    v.sql('REVOKE SELECT ON sql_apm.scope FROM sql_apm_ro')
    assert 'incompatible object: scope' in v.init('check', ok=False).stderr
    v.init('schema'); v.init('check')
    v.require(True, 'populated 1.10 -> 1.12: rows, tables, columns and receipts preserved; 1.11 adds only functions and read-only grants; '
                    'the read-only account works after the upgrade; a lost grant is detected and restored; reruns safe')
