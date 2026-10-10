"""1.7.0 empty upgrade and explicit rejection of populated legacy databases."""
from contextlib import contextmanager
import hashlib
from pathlib import Path
import shutil

from database.fixture import statements


@contextmanager
def legacy_target(v, root, runner):
    """Keep all earlier migration regressions aimed at their frozen 1.6 target.

    These checks exercise released migrations, not a supported data upgrade to
    1.7. The new rejection checks below exercise the current product entry.
    """
    target=v.directory/'legacy-target'
    shutil.copytree(root/'sql_apm/storage',target/'sql_apm/storage')
    fixtures=Path(__file__).parent/'legacy_160'
    for path in fixtures.iterdir():
        dest=target/('scripts/db' if path.suffix=='.sh' else 'sql_apm/storage')/path.name
        dest.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(path,dest)
    shutil.copyfile(root/'sql_apm/storage/versions/1.6.0.sql',target/'sql_apm/storage/schema.sql')
    (target/'scripts/db/initialize.sh').chmod(0o755)
    original_init,original_sql=v.init,v.sql
    def old_values(text):
        if text is None:return None
        for new,old in [('mpp-csv/1','hashdata-csv/1'),('mpp-mapping/1','hashdata-3.13.13/1'),
                        ('mpp-csv-reader/1','hashdata-csv-reader/1'),("'mpp'","'hashdata'")]:
            text=text.replace(new,old)
        return text
    def run(args,env=None,sql=None,ok=True):
        return runner(args,env,old_values(sql),ok)
    def init(mode='all',names=None,ok=True,root=None):
        return original_init(mode,names,ok,root or target)
    def sql(statement,admin=False,database=None,ok=True):
        return original_sql(old_values(statement),admin,database,ok)
    v.init,v.sql=init,sql
    try:yield target,run
    finally:v.init,v.sql=original_init,original_sql


def verify_naming_migration(v,root,runner):
    v.init('bootstrap')
    for version in ('1.7.0','1.6.0','1.0.0'):
        old=(root/'sql_apm/storage/versions'/ (version+'.sql')).read_bytes()
        sha=hashlib.sha256(old).hexdigest()
        v.sql('CREATE SCHEMA sql_apm;'+old.decode()+
              "INSERT INTO schema_version(version,script_sha256) VALUES ('"+version+"','"+sha+"');")
        receipt=v.sql("SELECT row_to_json(s) FROM schema_version s")
        v.init('upgrade');v.init('check')
        assert v.sql("SELECT version FROM schema_version ORDER BY string_to_array(version,'.')::int[] DESC LIMIT 1")=='1.11.0'
        assert v.sql("SELECT row_to_json(s) FROM schema_version s WHERE version='"+version+"'")==receipt
        before=v.sql('SELECT jsonb_agg(s ORDER BY version) FROM schema_version s')
        for mode in ('schema','all','upgrade','check'):v.init(mode)
        assert v.sql('SELECT jsonb_agg(s ORDER BY version) FROM schema_version s')==before
        v.require(True,'empty '+version+' -> 1.11.0 and every rerun preserve receipts')
        v.sql('DROP SCHEMA sql_apm CASCADE')
    old=(root/'sql_apm/storage/versions/1.6.0.sql').read_bytes()
    fixture=statements().replace('mpp-csv/1','hashdata-csv/1').replace("'mpp'","'hashdata'")
    v.sql('CREATE SCHEMA sql_apm;'+old.decode()+
          "INSERT INTO schema_version(version,script_sha256) VALUES ('1.6.0','"+hashlib.sha256(old).hexdigest()+"');"+fixture)
    tables=v.sql("SELECT relname FROM pg_class WHERE relnamespace='sql_apm'::regnamespace AND relkind IN ('r','p') ORDER BY relname").splitlines()
    def state():
        return {t:v.sql('SELECT coalesce(jsonb_agg(to_jsonb(s) ORDER BY to_jsonb(s)::text),\'[]\') FROM "'+t+'" s') for t in tables}
    before=state()
    failure=v.init('upgrade',ok=False)
    assert 'mpp_naming_requires_empty_schema' in failure.stderr
    assert state()==before
    assert v.sql("SELECT count(*) FROM pg_namespace WHERE nspname LIKE '_apm_expected_%' OR nspname LIKE '_apm_legacy_%'")=='0'
    v.require(True,'populated 1.6.0 refuses 1.7.0; all rows and receipts unchanged; no scratch schema')
