"""Preserve populated 1.4 results and leaf identity during the additive upgrade."""
import hashlib
import json
from database.fixture import statements

V140_SHA='21b044742a664aa11b156db7a18d687a9bded94661fe2dcce6e59c6a3b96dc61'


def verify_observation_migration(v, root, runner):
    legacy=(root/'sql_apm/storage/versions/1.4.0.sql').read_bytes()
    v.require(hashlib.sha256(legacy).hexdigest()==V140_SHA,'published 1.4.0 DDL bytes frozen')
    for names in [('sql_apm',)*3,('obs_db','obs_store','obs_role')]:
        database,schema,role=names
        def sql(statement,ok=True):
            return runner([v.pg_bin/'psql','-X','-w','-Atq','-v','ON_ERROR_STOP=1',
                '-h',v.directory/'socket','-p','55473','-U',role,'-d',database],v.env,
                'SET search_path="'+schema+'",pg_catalog;\n'+statement,ok=ok).stdout.strip()
        v.init('bootstrap',names=names)
        sql('CREATE SCHEMA "'+schema+'";'+legacy.decode()+
            "INSERT INTO schema_version VALUES ('1.4.0','"+V140_SHA+"',current_timestamp);"+statements(include_coverage=True))
        sql("SELECT mpp_ensure_result_partition('CL1','2026-11-01')")
        relations=sql("SELECT relname FROM pg_class WHERE relnamespace=current_schema()::regnamespace AND relkind IN ('r','p') ORDER BY relname").splitlines()
        old_objects=sql("SELECT string_agg(oid::text,',') FROM pg_class WHERE relnamespace=current_schema()::regnamespace AND relname NOT LIKE 'mpp_build_coverage%'")
        def state():
            rows={name:sql('SELECT coalesce(jsonb_agg((to_jsonb(t)-ARRAY[\'stage_seconds\']-CASE WHEN to_jsonb(t) ? \'mode\' THEN ARRAY[\'started_at\',\'finished_at\'] ELSE ARRAY[]::text[] END) ORDER BY (to_jsonb(t)-ARRAY[\'stage_seconds\']-CASE WHEN to_jsonb(t) ? \'mode\' THEN ARRAY[\'started_at\',\'finished_at\'] ELSE ARRAY[]::text[] END)::text),\'[]\') FROM "'+name+'" t') for name in relations if name!='schema_version' and not name.startswith('mpp_build_coverage')}
            objects=sql('SELECT jsonb_agg(jsonb_build_array(oid,relfilenode,relname) ORDER BY oid) FROM pg_class WHERE oid IN ('+old_objects+')')
            return rows,objects
        before=state()
        receipt=sql('SELECT jsonb_agg(v) FROM schema_version v')
        v.init('schema',names=names,ok=False)
        assert state()==before and sql('SELECT jsonb_agg(v) FROM schema_version v')==receipt
        v.init('upgrade',names=names);v.init('check',names=names)
        assert state()==before
        assert sql("SELECT count(*) FROM mpp_statistic")!='0'
        assert sql("SELECT count(*) FROM pg_inherits WHERE inhparent='mpp_observation_statistic'::regclass")=='2'
        versions=json.loads(sql('SELECT jsonb_object_agg(version,to_jsonb(v)) FROM schema_version v'))
        assert set(versions)=={'1.4.0','1.5.0','1.6.0'}
        assert versions['1.4.0']==json.loads(receipt)[0]
        for mode in ['all','check','upgrade']:v.init(mode,names=names)
        assert state()==before
        assert json.loads(sql('SELECT jsonb_object_agg(version,to_jsonb(v)) FROM schema_version v'))==versions
        v.require(True,schema+': populated 1.4 results, all existing OIDs/files, receipts and two months preserved on upgrade/rerun')
        leaf=sql("SELECT inhrelid::regclass::text FROM pg_inherits WHERE inhparent='mpp_observation_statistic'::regclass LIMIT 1")
        sql('ALTER TABLE '+leaf+' SET (fillfactor=80)')
        assert 'incompatible object' in v.init('check',names=names,ok=False).stderr
        sql('ALTER TABLE '+leaf+' RESET (fillfactor)')
        v.init('check',names=names)
        v.require(True,schema+': observation leaf catalog drift detected')
