"""Populated 1.11 -> 1.12 adds the daily-run records and their functions; every existing row is preserved."""
import hashlib
from database.fixture import statements

TABLES = ("SELECT relname FROM pg_class WHERE relnamespace='sql_apm'::regnamespace "
          "AND relkind IN ('r','p') AND NOT relispartition ORDER BY relname")
DAILY = ['mpp_daily_cluster', 'mpp_daily_day', 'mpp_daily_file', 'mpp_daily_problem', 'mpp_daily_run']


def verify_daily_migration(v, root):
    v.init('bootstrap')
    old = (root / 'sql_apm/storage/versions/1.11.0.sql').read_bytes()
    v.sql("SELECT set_config('apm.readonly_role','sql_apm_ro',false); CREATE SCHEMA sql_apm; SET search_path=sql_apm,pg_catalog;" +
          old.decode() + "INSERT INTO schema_version(version,script_sha256) VALUES ('1.11.0','" +
          hashlib.sha256(old).hexdigest() + "');" + statements())
    tables = v.sql(TABLES).splitlines()
    shape = lambda: v.sql("SELECT jsonb_agg(jsonb_build_array(c.relname,a.attname,format_type(a.atttypid,a.atttypmod)) ORDER BY c.relname,a.attnum) "
                          "FROM pg_class c JOIN pg_attribute a ON a.attrelid=c.oid WHERE c.relnamespace='sql_apm'::regnamespace "
                          "AND c.relkind IN ('r','p','i') AND a.attnum>0 AND NOT a.attisdropped AND c.relname NOT LIKE 'mpp\\_daily\\_%'")
    def state():
        return {t: v.sql('SELECT count(*)||\':\'||md5(coalesce(string_agg(to_jsonb(s)::text,\'\' ORDER BY to_jsonb(s)::text),\'\')) FROM "' + t + '" s' +
                         (" WHERE version='1.11.0'" if t == 'schema_version' else '')) for t in tables}
    functions = lambda: set(v.sql("SELECT proname FROM pg_proc WHERE pronamespace='sql_apm'::regnamespace").splitlines())
    before, columns, old_functions = state(), shape(), functions()
    assert int(before['mpp_statistic'].split(':')[0]) > 0 and not set(DAILY) & set(tables)
    v.init('upgrade'); v.init('check')
    assert before == state() and columns == shape() and v.sql(TABLES).splitlines() == sorted(tables + DAILY)
    assert v.sql("SELECT string_agg(version,',' ORDER BY string_to_array(version,'.')::int[]) FROM schema_version") == '1.11.0,1.12.0'
    assert v.sql("SELECT count(*) FROM pg_extension WHERE extname<>'plpgsql'") == '0'
    added = functions() - old_functions
    assert old_functions < functions() and {'mpp_daily_lock_key', 'mpp_daily_runs', 'mpp_daily_problems', 'mpp_daily_clusters',
                                           'mpp_daily_recent', 'mpp_view_daily_last', 'mpp_view_daily_clusters',
                                           'mpp_view_daily_problems', 'mpp_view_daily_recent'} <= added and all(name.startswith(('mpp_daily_', 'mpp_view_daily_')) for name in added)
    assert all(v.sql('SELECT count(*) FROM ' + t) == '0' for t in DAILY)
    reader = lambda sql, ok=True: v.sql('SET ROLE sql_apm_ro; ' + sql, admin=True, database='sql_apm', ok=ok)
    assert reader("SET search_path=sql_apm,pg_catalog; SELECT count(*) FROM mpp_view_daily_recent(5)") == '0'
    reader("INSERT INTO sql_apm.mpp_daily_run (run_id,started_by,state,local_date,stale_after_hours) VALUES ('x','manual','running',current_date,48)", ok=False)
    for mode in ('schema', 'all', 'upgrade', 'check'):
        v.init(mode)
    assert before == state() and v.sql("SELECT count(*) FROM schema_version") == '2'
    v.sql('REVOKE SELECT ON sql_apm.mpp_daily_run FROM sql_apm_ro')
    assert 'incompatible object: mpp_daily_run' in v.init('check', ok=False).stderr
    v.init('schema'); v.init('check')
    v.require(True, 'populated 1.11 -> 1.12: every existing row, table, column and receipt preserved; only the five daily-run tables and their '
                    'functions added; the read-only account reads them and cannot write; a lost grant is detected and restored; reruns safe')
