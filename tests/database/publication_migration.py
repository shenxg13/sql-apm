"""Populated 1.5.0 -> 1.6.0: exact coverage and both statistic families survive."""
import hashlib
import json

from database.fixture import statements
from database.approximate import rule_sql,insert_sql
from sql_apm.sql.approximate import fingerprint


def verify_publication_migration(v,root,runner):
    old=(root/'sql_apm/storage/versions/1.5.0.sql').read_bytes()
    sha='50e468eebd148c1853fa9cf9bbefd97d92bd877cf000e4b6cabe6903e392c743'
    assert hashlib.sha256(old).hexdigest()==sha
    v.init('bootstrap')
    v.sql('CREATE SCHEMA sql_apm;'+old.decode()+"INSERT INTO schema_version VALUES ('1.5.0','"+sha+"',current_timestamp);"+statements(include_coverage=True))
    raw=b'SELECT 1 FROM'
    approx=fingerprint(raw,structural_reason='base_parser_rejected')
    v.sql(rule_sql(approx)+insert_sql('MIGRATION_APPROX',raw,approx))
    v.sql("""INSERT INTO mpp_observation_group SELECT 'OG:migration','CL1','mpp-csv/1','synthetic','synthetic',rule_id,result_id,value,'request'
        FROM mpp_approximate_result WHERE result_id='MIGRATION_APPROX';
        INSERT INTO mpp_build_observation_group SELECT partition_id,build_id,'OG:migration' FROM build WHERE build_id='V1';
        INSERT INTO mpp_observation_statistic SELECT (jsonb_populate_record(NULL::mpp_observation_statistic,
            to_jsonb(s)||'{"group_id":"OG:migration"}'::jsonb)).* FROM mpp_statistic s;""")
    original=v.sql("SELECT jsonb_agg(to_jsonb(s) ORDER BY layer) FROM mpp_statistic s; SELECT jsonb_agg(to_jsonb(s) ORDER BY layer) FROM mpp_observation_statistic s")
    expected=json.loads(v.sql("SELECT jsonb_agg(to_jsonb(c)-'partition_id'-'build_id' ORDER BY group_id,layer) FROM mpp_build_coverage c"))
    v.init('upgrade');v.init('check')
    actual=json.loads(v.sql("SELECT jsonb_agg(to_jsonb(c) ORDER BY group_id,layer) FROM mpp_coverage('V1') c"))
    assert actual==expected
    observed=json.loads(v.sql("SELECT jsonb_agg(to_jsonb(c)||'{\"group_id\":\"G1\"}'::jsonb ORDER BY group_id,layer) FROM mpp_coverage('V1',true) c"))
    assert observed==expected
    assert v.sql("SELECT to_regclass('mpp_build_coverage') IS NULL")=='t'
    assert v.sql("SELECT jsonb_agg(to_jsonb(s) ORDER BY layer) FROM mpp_statistic s; SELECT jsonb_agg(to_jsonb(s) ORDER BY layer) FROM mpp_observation_statistic s")==original
    assert v.sql("SELECT count(*) FROM mpp_build_layer_count WHERE build_id='V1' AND row_count=1 AND group_count=1")=='10'
    receipts=v.sql('SELECT jsonb_agg(v ORDER BY version) FROM schema_version v')
    for mode in ('all','upgrade','check'):v.init(mode)
    assert v.sql('SELECT jsonb_agg(v ORDER BY version) FROM schema_version v')==receipts
    v.require(True,'populated 1.5 coverage exactly derived; both statistic families unchanged; receipts and reruns verified')
