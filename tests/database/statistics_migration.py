"""1.3 -> 1.4 precondition, partition routing and current catalog protection."""
import hashlib
from database.fixture import statements


def verify_statistics_migration(v, root, runner):
    legacy=(root/'sql_apm/storage/versions/1.3.0.sql').read_bytes()
    for names in [('sql_apm',)*3,('stats_db','stats_store','stats_role')]:
        database,schema,role=names
        def sql(statement,ok=True):
            return runner([v.pg_bin/'psql','-X','-w','-Atq','-v','ON_ERROR_STOP=1',
                '-h',v.directory/'socket','-p','55473','-U',role,'-d',database],v.env,
                'SET search_path="'+schema+'",pg_catalog;\n'+statement,ok=ok).stdout.strip()
        v.init('bootstrap',names=names)
        sql('CREATE SCHEMA "'+schema+'";'+legacy.decode()+
            "INSERT INTO schema_version VALUES ('1.3.0','"+hashlib.sha256(legacy).hexdigest()+"',current_timestamp);"+statements(legacy=True))
        before=sql('SELECT jsonb_agg(s) FROM mpp_statistic s; SELECT jsonb_agg(c) FROM mpp_build_coverage c; SELECT jsonb_agg(v) FROM schema_version v')
        v.require('statistics_migration_requires_empty_results' in v.init('upgrade',names=names,ok=False).stderr,
                  schema+': populated statistics and coverage reject migration')
        v.require(sql('SELECT jsonb_agg(s) FROM mpp_statistic s; SELECT jsonb_agg(c) FROM mpp_build_coverage c; SELECT jsonb_agg(v) FROM schema_version v')==before,
                  schema+': refused migration preserves rows and version')
        # Explicit synthetic fixture cleanup belongs to this test, never migration.
        sql('DELETE FROM mpp_statistic')
        v.require('statistics_migration_requires_empty_results' in v.init('upgrade',names=names,ok=False).stderr,
                  schema+': coverage alone also blocks migration')
        sql('DELETE FROM mpp_build_coverage')
        original=sql('SELECT jsonb_agg(o) FROM mpp_occurrence o; SELECT jsonb_agg(s) FROM mpp_sql_text s')
        v.init('upgrade',names=names);v.init('check',names=names)
        v.require(sql('SELECT max(version) FROM schema_version')=='1.5.0' and
                  sql('SELECT jsonb_agg(o) FROM mpp_occurrence o; SELECT jsonb_agg(s) FROM mpp_sql_text s')==original,
                  schema+': direct upgrade retains execution and SQL evidence')
        for month,bid in [('2026-10-01','V1'),('2026-11-01','V2')]:
            sql("SELECT mpp_ensure_result_partition('CL1','"+month+"')")
            if bid=='V1':
                sql("UPDATE build SET partition_id=(SELECT partition_id FROM mpp_result_partition WHERE build_month='"+month+"') WHERE build_id='V1'")
            else:
                sql("INSERT INTO build SELECT 'V2',scope_id,input_id,config_id,normalization_id,profile,NULL,'running','2026-11-01',NULL,false,(SELECT partition_id FROM mpp_result_partition WHERE build_month='"+month+"'),'{}' FROM build WHERE build_id='V1'")
            sql("INSERT INTO mpp_build_group SELECT partition_id,build_id,'G1' FROM build WHERE build_id='"+bid+"'; "
                "INSERT INTO mpp_build_coverage SELECT build_id,'G1',partition_id,'overall','[]','[null]' FROM build WHERE build_id='"+bid+"'")
        v.require(sql('SELECT count(DISTINCT tableoid) FROM mpp_build_coverage')=='2',schema+': new month routes to separate provisioned leaf')
        sql("INSERT INTO mpp_normalization SELECT 'N2',algorithm_version,parser_version,dictionary_schema_version,'synthetic-next',dictionary_digest_algorithm,'synthetic-next',rules_ref FROM mpp_normalization WHERE normalization_id='N1'; "
            "INSERT INTO mpp_fingerprint SELECT 'F2',sql_id,'N2',profile,state,value,reason FROM mpp_fingerprint WHERE fingerprint_id=(SELECT fingerprint_id FROM mpp_baseline_group WHERE group_id='G1'); "
            "INSERT INTO mpp_baseline_group SELECT 'G2',scope_id,profile,'N2',database,execution_user,'F2',fingerprint_value,timing_type FROM mpp_baseline_group WHERE group_id='G1'")
        sql("INSERT INTO mpp_build_group SELECT partition_id,build_id,'G2' FROM build WHERE build_id='V1'",ok=False)
        v.require(True,schema+': result relation rejects a group under another normalization')
        sql("INSERT INTO scope VALUES ('CL2','hashdata','hashdata-csv/1','1.0.0'); "
            "INSERT INTO mpp_baseline_group SELECT 'G3','CL2',profile,normalization_id,database,execution_user,fingerprint_id,fingerprint_value,timing_type FROM mpp_baseline_group WHERE group_id='G1'")
        sql("INSERT INTO mpp_build_group SELECT partition_id,build_id,'G3' FROM build WHERE build_id='V1'",ok=False)
        v.require(True,schema+': result relation rejects a group under another cluster')
        sql("INSERT INTO build SELECT 'BAD_MONTH',scope_id,input_id,config_id,normalization_id,profile,NULL,'running','2026-12-01',NULL,false,partition_id,'{}' FROM build WHERE build_id='V1'",ok=False)
        v.require(True,schema+': mismatched construction month is rejected')

        v.require(sql("SELECT count(*) FROM pg_class WHERE relnamespace='"+schema+"'::regnamespace AND relkind='p'")=='3',schema+': three partitioned parents, no layer tables')
        v.require(sql("SELECT count(*) FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid WHERE c.relnamespace=current_schema()::regnamespace AND c.relname LIKE 'mpp_statistic%' AND a.attname='sufficiency' AND NOT a.attisdropped")=='0',schema+': parent and leaves omit sufficiency')
        sql('ALTER FUNCTION mpp_statistic_sufficiency(text,jsonb,text,bigint,date[],date[]) STABLE')
        drift=v.init('check',names=names,ok=False).stderr
        v.require('incompatible object' in drift and 'mpp_statistic_sufficiency' in drift,schema+': derived sufficiency function drift detected')
        sql('ALTER FUNCTION mpp_statistic_sufficiency(text,jsonb,text,bigint,date[],date[]) IMMUTABLE')
        for mode in ['all','check','upgrade']:v.init(mode,names=names)
        child=sql('SELECT tableoid::regclass::text FROM mpp_build_coverage LIMIT 1')
        sql('ALTER TABLE '+child+' ADD COLUMN drift integer',ok=False)
        # Parent and leaves are included in catalog validation (not wildcard ignored).
        sql('ALTER TABLE '+child+' SET (fillfactor=80)')
        v.require('incompatible object' in v.init('check',names=names,ok=False).stderr,schema+': leaf drift detected')
        sql('ALTER TABLE '+child+' RESET (fillfactor)')
        v.init('check',names=names)
