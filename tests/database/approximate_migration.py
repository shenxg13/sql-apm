"""Populated 1.1.0 upgrade preservation and rollback tests."""
import hashlib
import json
import shutil

from database.fixture import statements

V110_SHA = 'e53ea534d1b110a95ebae2a4889744b557b8333cd9d13d1daf6536a808661eb3'


def verify_approximate_migration(v, root, runner):
    storage = root / 'sql_apm/storage'
    legacy = (storage / 'versions/1.1.0.sql').read_bytes()
    v.require(hashlib.sha256(legacy).hexdigest() == V110_SHA, 'published 1.1.0 DDL byte-for-byte preserved')
    for names in [('sql_apm',) * 3, ('near_db', 'near_store', 'near_role')]:
        database, schema, role = names

        def sql(statement, ok=True, admin=False):
            return runner([v.pg_bin / 'psql', '-X', '-w', '-Atq', '-v', 'ON_ERROR_STOP=1',
                           '-h', v.directory / 'socket', '-p', '55473',
                           '-U', 'apm_test_admin' if admin else role, '-d', database], v.env,
                          'SET search_path="' + schema + '",pg_catalog;\n' + statement, ok=ok).stdout.strip()

        v.init('bootstrap', names=names)
        sql('CREATE SCHEMA "' + schema + '";\n' + legacy.decode() +
            "INSERT INTO schema_version(version,script_sha256) VALUES ('1.1.0','" + V110_SHA + "');\n" + statements(legacy=True, results=False))
        old_tables = sql("SELECT relname FROM pg_class WHERE relnamespace='" + schema + "'::regnamespace AND relkind IN ('r','p') AND NOT relispartition ORDER BY relname").splitlines()
        old_oids = sql("SELECT string_agg(c.oid::text,',') FROM pg_class c WHERE c.relname NOT IN ('mpp_statistic','mpp_build_coverage') AND c.oid NOT IN (SELECT indexrelid FROM pg_index WHERE indrelid IN ('mpp_statistic'::regclass,'mpp_build_coverage'::regclass)) AND relnamespace='" + schema + "'::regnamespace")
        constraint_oids = sql("SELECT string_agg(oid::text,',') FROM pg_constraint WHERE conrelid NOT IN ('mpp_statistic'::regclass,'mpp_build_coverage'::regclass) AND NOT (conrelid='task'::regclass AND conname IN ('task_mode_check','task_stage_check','task_check1')) AND connamespace='" + schema + "'::regnamespace")

        def state():
            rows = {t: sql('SELECT coalesce(jsonb_agg((to_jsonb(t)-ARRAY[\'partition_id\',\'diagnostics\',\'stage_seconds\']-CASE WHEN to_jsonb(t) ? \'task_id\' AND to_jsonb(t) ? \'mode\' THEN ARRAY[\'started_at\',\'finished_at\'] ELSE ARRAY[]::text[] END) ORDER BY (to_jsonb(t)-ARRAY[\'partition_id\',\'diagnostics\',\'stage_seconds\']-CASE WHEN to_jsonb(t) ? \'task_id\' AND to_jsonb(t) ? \'mode\' THEN ARRAY[\'started_at\',\'finished_at\'] ELSE ARRAY[]::text[] END)::text),\'[]\') FROM "' + t + '" t')
                    for t in old_tables if t not in ('schema_version','mpp_statistic','mpp_build_coverage')}
            objects = sql('SELECT oid,relfilenode FROM pg_class WHERE oid IN (' + old_oids + ') ORDER BY oid')
            constraints = sql('SELECT oid,conname FROM pg_constraint WHERE oid IN (' + constraint_oids + ') ORDER BY oid')
            return rows, objects, constraints

        def versions():
            return json.loads(sql('SELECT jsonb_object_agg(version,to_jsonb(v)) FROM schema_version v'))

        before, receipt = state(), versions()
        for mode in ('schema', 'check'):
            v.init(mode, names=names, ok=False)
        v.require(state() == before and versions() == receipt, schema + ': no implicit 1.1.0 upgrade')
        if schema == 'sql_apm':
            for setup, restore, expected in [
                ('ALTER TABLE mpp_occurrence ADD COLUMN drift integer', 'ALTER TABLE mpp_occurrence DROP COLUMN drift', 'incompatible object'),
                ("UPDATE schema_version SET script_sha256=repeat('0',64)", "UPDATE schema_version SET script_sha256='" + V110_SHA + "'", 'checksum'),
                ('CREATE TABLE mpp_approximate_result(id integer)', 'DROP TABLE mpp_approximate_result', 'unexpected object')]:
                sql(setup)
                v.require(expected in v.init('upgrade', names=names, ok=False).stderr, '1.1.0 drift blocks upgrade: ' + expected)
                sql(restore)
            sql('ALTER TABLE mpp_sql_text DISABLE TRIGGER ALL', admin=True)
            v.require('foreign_key_trigger_overrides' in v.init('upgrade', names=names, ok=False).stderr, '1.1.0 FK trigger drift blocks upgrade')
            sql('ALTER TABLE mpp_sql_text ENABLE TRIGGER ALL', admin=True)
            sql("INSERT INTO mpp_normalization SELECT 'BAD_N',algorithm_version,parser_version,dictionary_schema_version,dictionary_rules_version,dictionary_digest_algorithm,dictionary_digest_value,rules_ref FROM mpp_normalization; "
                "INSERT INTO mpp_fingerprint SELECT 'BAD_F',sql_id,'BAD_N',profile,'reliable','approx:legacy',NULL FROM mpp_fingerprint")
            bad = state()
            v.require('mpp_fingerprint_not_approximate' in v.init('upgrade', names=names, ok=False).stderr and state() == bad and versions() == receipt,
                      'pre-existing approximate reliable value rejects migration without deleting it')
            sql("DELETE FROM mpp_fingerprint WHERE fingerprint_id='BAD_F'; DELETE FROM mpp_normalization WHERE normalization_id='BAD_N'")
            copy_root = v.directory / 'near_failure'
            shutil.copytree(storage, copy_root / 'sql_apm/storage')
            (copy_root / 'scripts/db').mkdir(parents=True)
            shutil.copy2(root / 'scripts/db/initialize.sh', copy_root / 'scripts/db/initialize.sh')
            for relative, marker in [('migrations/1.1.0-to-1.2.0.sql', 'SET LOCAL search_path = pg_catalog;'), ('migrate.sql', 'COMMIT;')]:
                path = copy_root / 'sql_apm/storage' / relative
                original = path.read_text()
                path.write_text(original.replace(marker, 'SELECT 1/0;\n' + marker))
                v.require('division by zero' in v.init('upgrade', names=names, root=copy_root, ok=False).stderr,
                          'injected failure after new DDL or receipt: ' + relative)
                v.require(state() == before and versions() == receipt and sql("SELECT count(*) FROM pg_class WHERE relnamespace='sql_apm'::regnamespace AND relname LIKE 'mpp_approximate_%'") == '0',
                          '1.1.0 rollback preserves data/objects/receipts and removes new tables')
                path.write_text(original)
        v.init('upgrade', names=names)
        v.init('check', names=names)
        after = versions()
        v.require(state() == before and set(after) == {'1.1.0','1.2.0','1.3.0','1.4.0','1.5.0','1.6.0'} and after['1.1.0'] == receipt['1.1.0'], schema + ': direct upgrade preserves all 1.1.0 rows/OIDs and receipt')
        for mode in ('upgrade', 'schema', 'all'):
            v.init(mode, names=names)
        v.require(state() == before and versions() == after, schema + ': 1.6.0 reruns preserve all data and timestamps')
        v.require(sql("SELECT count(*) FROM pg_namespace WHERE nspname LIKE '_apm_%'") == '0', schema + ': no scratch schemas remain')
    print('RESULT: ' + str(v.completed) + ' direct approximate migration checks passed', flush=True)
