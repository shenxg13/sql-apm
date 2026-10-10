"""Populated 1.8 -> 1.12 preserves all old values and creates only metadata."""
import hashlib
from database.fixture import statements


def verify_retention_migration(v,root):
    v.init('bootstrap')
    old=(root/'sql_apm/storage/versions/1.8.0.sql').read_bytes()
    v.sql('CREATE SCHEMA sql_apm;'+old.decode()+
          "INSERT INTO schema_version(version,script_sha256) VALUES ('1.8.0','"+hashlib.sha256(old).hexdigest()+"');"+statements())
    tables=v.sql("SELECT relname FROM pg_class WHERE relnamespace='sql_apm'::regnamespace AND relkind IN ('r','p') ORDER BY relname").splitlines()
    def state():
        return {t:v.sql("SELECT coalesce(jsonb_agg((to_jsonb(t)-ARRAY['cleaned_at','groups_cleaned_at','search_text']) ORDER BY (to_jsonb(t)-ARRAY['cleaned_at','groups_cleaned_at','search_text'])::text),'[]') FROM \""+t+'" t'+
            (" WHERE version='1.8.0'" if t=='schema_version' else '')) for t in tables}
    before=state();v.init('upgrade');v.init('check')
    assert state()==before
    assert v.sql('SELECT bool_and(cleaned_at IS NULL AND groups_cleaned_at IS NULL) FROM mpp_result_partition')=='t'
    assert v.sql("SELECT version FROM schema_version ORDER BY string_to_array(version,'.')::int[] DESC LIMIT 1")=='1.12.0'
    for mode in ('schema','all','upgrade','check'):v.init(mode)
    assert state()==before
    v.require(True,'populated 1.8 -> 1.12 and reruns preserve every original row and receipt; all months uncleaned')
