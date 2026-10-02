"""1.2.0 -> 1.3.0 preserves populated data; new functions are catalog checked."""
import hashlib
import json
from database.fixture import statements


def verify_training_migration(v, root, runner):
    old=(root/'sql_apm/storage/versions/1.2.0.sql').read_bytes()
    for names in [('sql_apm',)*3,('training_db','training_store','training_role')]:
        database,schema,role=names
        def sql(statement,ok=True):
            return runner([v.pg_bin/'psql','-X','-w','-Atq','-v','ON_ERROR_STOP=1','-h',v.directory/'socket','-p','55473','-U',role,'-d',database],v.env,
                'SET search_path="'+schema+'",pg_catalog;\n'+statement,ok=ok).stdout.strip()
        v.init('bootstrap',names=names)
        sql('CREATE SCHEMA "'+schema+'";\n'+old.decode()+"INSERT INTO schema_version VALUES('1.2.0','"+hashlib.sha256(old).hexdigest()+"',current_timestamp);"+statements(legacy=True, results=False))
        tables=sql("SELECT relname FROM pg_class WHERE relnamespace='"+schema+"'::regnamespace AND relkind IN ('r','p') AND NOT relispartition ORDER BY relname").splitlines()
        oids=sql("SELECT string_agg(c.oid::text,',') FROM pg_class c WHERE c.relname NOT IN ('mpp_statistic','mpp_build_coverage') AND c.oid NOT IN (SELECT indexrelid FROM pg_index WHERE indrelid IN ('mpp_statistic'::regclass,'mpp_build_coverage'::regclass)) AND relnamespace='"+schema+"'::regnamespace")
        def state():
            return ({t:sql('SELECT coalesce(jsonb_agg((to_jsonb(t)-ARRAY[\'partition_id\',\'diagnostics\',\'stage_seconds\']-CASE WHEN to_jsonb(t) ? \'task_id\' AND to_jsonb(t) ? \'mode\' THEN ARRAY[\'started_at\',\'finished_at\'] ELSE ARRAY[]::text[] END) ORDER BY (to_jsonb(t)-ARRAY[\'partition_id\',\'diagnostics\',\'stage_seconds\']-CASE WHEN to_jsonb(t) ? \'task_id\' AND to_jsonb(t) ? \'mode\' THEN ARRAY[\'started_at\',\'finished_at\'] ELSE ARRAY[]::text[] END)::text),\'[]\') FROM "'+t+'" t') for t in tables if t not in ('schema_version','mpp_statistic','mpp_build_coverage')},
                    sql('SELECT oid,relfilenode FROM pg_class WHERE oid IN ('+oids+') ORDER BY oid'))
        before=state()
        v.init('schema',names=names,ok=False)
        v.init('upgrade',names=names)
        v.require(state()==before,schema+': direct 1.2.0 migration preserves all original rows and relation files')
        receipts=sql('SELECT jsonb_object_agg(version,to_jsonb(s)) FROM schema_version s')
        v.require(set(json.loads(receipts))=={'1.2.0','1.3.0','1.4.0','1.5.0','1.6.0'},schema+': both receipts retained')
        for mode in ['all','schema','check','upgrade']:v.init(mode,names=names)
        v.require(state()==before and sql('SELECT jsonb_object_agg(version,to_jsonb(s)) FROM schema_version s')==receipts,schema+': all repeat modes preserve data and receipts')
        definition=sql("SELECT pg_get_functiondef('training_version(text)'::regprocedure)")
        sql("CREATE OR REPLACE FUNCTION training_version(version text) RETURNS boolean LANGUAGE sql IMMUTABLE AS 'SELECT true'")
        v.require('incompatible object' in v.init('check',names=names,ok=False).stderr,schema+': changed rule function rejected by catalog')
        sql(definition)
        sql('ALTER TABLE input_snapshot DISABLE TRIGGER training_immutable')
        v.require('triggers' in v.init('check',names=names,ok=False).stderr,schema+': disabled snapshot guard rejected')
        sql('ALTER TABLE input_snapshot ENABLE TRIGGER training_immutable')
        # Parse-time binding: invocation needs no project search path.
        v.require(sql('SET search_path=pg_catalog; SELECT count(*) FROM "'+schema+'".mpp_training_decisions(\'missing\',\'missing\')')=='0',schema+': parsed SQL body retains correct schema bindings')
        v.init('check',names=names)
    print('TRAINING MIGRATION CHECKS:',v.completed)
