"""Snapshot persistence and text caches. Eligibility lives solely in PostgreSQL."""
import time
import uuid

from psycopg2.extras import Json, execute_values

from sql_apm.ingestion.config import identity
from sql_apm.storage.ingestion import connect
from sql_apm.sql.normalization import Normalizer
from sql_apm.training.categories import RULES, classify
from sql_apm.training.config import DECISION_VERSION, TrainingError


class TrainingStore:
    def __init__(self, dsn='', schema='sql_apm', normalizer=None):
        self.engine = normalizer or Normalizer()
        self.db = connect(dsn, schema)
        self.context = self.engine.context
        self.normalization_id = 'N:' + identity(self.context)

    def close(self):
        self.db.close()

    def _select(self, scope, batches, analyses):
        if not batches or len(set(batches)) != len(batches) or len(set(analyses)) != len(analyses):
            raise TrainingError('invalid_input_selection')
        with self.db, self.db.cursor() as cur:
            cur.execute('SELECT scope_id FROM scope WHERE scope_id=%s', (scope,))
            if not cur.fetchone():
                raise TrainingError('unknown_cluster')
            cur.execute('SELECT batch_id,state,files_confirmed_complete FROM import_batch WHERE batch_id=ANY(%s) AND scope_id=%s ORDER BY batch_id', (batches, scope))
            rows = cur.fetchall()
            if len(rows) != len(batches) or any(r[1:] != ('complete', True) for r in rows):
                raise TrainingError('completed_batches_required')
            cur.execute('''SELECT DISTINCT f.file_id,f.source_id,f.checksum_algorithm,f.checksum_value,f.byte_count,a.state
                FROM batch_entry b JOIN source_file f USING(file_id)
                LEFT JOIN import_attempt a ON a.attempt_id=b.final_attempt_id
                WHERE b.batch_id=ANY(%s) ORDER BY f.file_id''', (batches,))
            files = cur.fetchall()
            if not files or any(f[-1] not in ('succeeded','duplicate_skipped') for f in files):
                raise TrainingError('successful_files_required')
            cur.execute('SELECT count(DISTINCT batch_id) FROM batch_entry WHERE batch_id=ANY(%s)',(batches,))
            if cur.fetchone()[0] != len(batches):
                raise TrainingError('successful_files_required')
            # Even duplicate files must map to exactly one explicitly chosen interpretation.
            file_ids = sorted({f[0] for f in files})
            cur.execute('''SELECT af.file_id,a.analysis_id,a.mapping_version,a.parser_version,a.association_version,a.evidence_manifest
                FROM analysis_file af JOIN analysis a USING(analysis_id)
                WHERE af.file_id=ANY(%s) AND a.scope_id=%s AND (%s OR a.analysis_id=ANY(%s))
                ORDER BY af.file_id,a.analysis_id''', (file_ids, scope, not analyses, analyses))
            choices = {}
            for row in cur:
                choices.setdefault(row[0], []).append(row)
            if set(choices) != set(file_ids) or any(len(v) != 1 for v in choices.values()):
                raise TrainingError('unique_file_analysis_required')
            selected = [choices[f][0] for f in file_ids]
            if analyses and set(analyses) != {r[1] for r in selected}:
                raise TrainingError('unused_analysis_selection')
            cur.execute('''SELECT s.source_id,s.scope_id,s.mapping_ref,s.declared_build,s.timezone,s.declaration_evidence
                FROM source s WHERE s.source_id=ANY(%s) ORDER BY s.source_id''', (sorted({f[1] for f in files}),))
            mappings = [dict(zip(('source_id','scope_id','mapping_ref','declared_build','timezone','declaration_evidence'),r)) for r in cur]
            cur.execute('SELECT batch_id,declared_date FROM batch_date WHERE batch_id=ANY(%s) ORDER BY 1,2', (batches,))
            dates = [[b,d.isoformat()] for b,d in cur]
        return dict(scope_id=scope,batches=sorted(batches),dates=dates,
                    files=[list(f) for f in sorted({r[:-1] for r in files})],analyses=[list(r) for r in selected],source_mappings=mappings)

    def _rule(self, config):
        templates = []
        for item in sorted(config['templates'],key=lambda t:t['id']):
            fp = self.engine.normalize(item['sql'])['fingerprint']
            if fp['state'] != 'reliable':
                raise TrainingError('template_not_reliably_normalized')
            templates.append(dict(item,fingerprint=fp['value']))
        snapshot = self.engine.rule_snapshot()
        rule_id = 'TR:' + identity(RULES,templates,self.context,snapshot)
        with self.db, self.db.cursor() as cur:
            c = self.context
            cur.execute('INSERT INTO mpp_normalization VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                        (self.normalization_id,c['algorithm_version'],c['parser_version'],c['dictionary_schema_version'],
                         c['dictionary_rules_version'],'sha256',c['dictionary_digest'],c['rules_ref']))
            cur.execute('INSERT INTO mpp_training_rule VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                        (rule_id,self.normalization_id,Json(RULES),Json(templates),Json(snapshot)))
        return rule_id,templates

    def _prepare(self, manifest, rule_id, templates):
        """One rule owner; short commits, bounded batches and restartable cache."""
        started, added = time.monotonic(), 0
        lock = int(identity(rule_id)[:15],16)
        with self.db, self.db.cursor() as cur:
            cur.execute('SELECT pg_try_advisory_lock(%s)', (lock,))
            if not cur.fetchone()[0]:
                raise TrainingError('training_rule_busy')
        try:
            with self.db, self.db.cursor() as cur:
                cur.execute('CREATE TEMP TABLE training_selection(file_id text PRIMARY KEY,analysis_id text) ON COMMIT PRESERVE ROWS')
                execute_values(cur,'INSERT INTO training_selection VALUES %s', [(r[0],r[1]) for r in manifest['analyses']])
                cur.execute('''CREATE TEMP TABLE training_needed ON COMMIT PRESERVE ROWS AS
                    SELECT DISTINCT o.sql_id FROM training_selection m
                    JOIN evidence_record e USING(file_id)
                    JOIN mpp_occurrence o ON o.analysis_id=m.analysis_id AND o.anchor_ref=e.record_id
                    WHERE o.sql_id IS NOT NULL''')
                cur.execute('ALTER TABLE training_needed ADD PRIMARY KEY(sql_id)')
                cur.execute('ANALYZE training_needed')
            after = ''
            while True:
                with self.db, self.db.cursor() as cur:
                    cur.execute('''SELECT s.sql_id,s.text,f.fingerprint_id,f.state,f.value,f.reason
                        FROM training_needed n JOIN mpp_sql_text s USING(sql_id)
                        LEFT JOIN mpp_training_sql c ON c.sql_id=s.sql_id AND c.rule_id=%s
                        LEFT JOIN mpp_fingerprint f ON f.sql_id=s.sql_id AND f.normalization_id=%s AND f.profile='hashdata-csv/1'
                        WHERE s.sql_id>%s AND c.sql_id IS NULL ORDER BY s.sql_id LIMIT 1000''', (rule_id,self.normalization_id,after))
                    rows = cur.fetchall()
                if not rows:
                    break
                values, fingerprints = [], []
                for sid, text, fid, state, value, reason in rows:
                    if fid is None:
                        result = self.engine.normalize(text)['fingerprint']
                        state, value, reason = (result[k] for k in ('state','value','reason'))
                        fid = 'F:' + identity(sid,self.normalization_id)
                        fingerprints.append((fid,sid,self.normalization_id,'hashdata-csv/1',state,value,reason))
                    category = classify(text,grammar_verified=(state == 'reliable'))
                    matches = [t['id'] for t in templates if state == 'reliable' and t['fingerprint']==value]
                    values.append((sid,rule_id,self.normalization_id,fid,category['kind'],category['categories'],matches))
                with self.db, self.db.cursor() as cur:
                    if fingerprints:
                        execute_values(cur,'INSERT INTO mpp_fingerprint VALUES %s ON CONFLICT DO NOTHING',fingerprints)
                    execute_values(cur,'INSERT INTO mpp_training_sql VALUES %s ON CONFLICT DO NOTHING',values)
                added += len(values)
                after = rows[-1][0]
            return dict(added_sql_results=added,seconds=round(time.monotonic()-started,3))
        finally:
            self.db.rollback()
            with self.db, self.db.cursor() as cur:
                cur.execute('DROP TABLE IF EXISTS pg_temp.training_needed,pg_temp.training_selection')
                cur.execute('SELECT pg_advisory_unlock(%s)',(lock,))

    def snapshot(self, config, batches, analyses=()):
        manifest = self._select(config['scope_id'],list(batches),list(analyses))
        rule_id, templates = self._rule(config)
        cache = self._prepare(manifest,rule_id,templates)
        # Recheck only file-level metadata, not millions of events, after cache work.
        if manifest != self._select(config['scope_id'],list(batches),list(analyses)):
            raise TrainingError('input_changed_during_snapshot')
        input_id, config_id = 'IS:'+uuid.uuid4().hex,'CS:'+uuid.uuid4().hex
        scope = config['scope_id']
        blacklist = dict(category_ref=RULES['version'],template_ref=rule_id,
                         category_rules=RULES,template_rules=templates)
        with self.db, self.db.cursor() as cur:
            cur.execute('INSERT INTO input_snapshot VALUES (%s,%s,%s,%s,current_timestamp)',
                        (input_id,scope,'immutable_manifest','sha256:'+identity(manifest)))
            execute_values(cur,'INSERT INTO input_batch VALUES %s',[(input_id,b,scope) for b in manifest['batches']])
            execute_values(cur,'INSERT INTO input_file VALUES %s',[(input_id,f,scope) for f in sorted({r[0] for r in manifest['files']})])
            execute_values(cur,'INSERT INTO input_analysis VALUES %s',[(input_id,a,scope) for a in sorted({r[1] for r in manifest['analyses']})])
            execute_values(cur,'INSERT INTO input_file_analysis VALUES %s',[(input_id,r[0],r[1]) for r in manifest['analyses']])
            cur.execute('INSERT INTO input_manifest VALUES (%s,%s)',(input_id,Json(manifest)))
            cur.execute('INSERT INTO config_snapshot VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                        (config_id,scope,self.normalization_id,'hashdata-csv/1',Json([m['mapping_ref'] for m in manifest['source_mappings']]),
                         config['cutoff_date'],config['window_days'],config['window_start'],config['window_end'],Json(blacklist),
                         Json(config['exclusions']),Json(config['thresholds']),'baseline-formulas/1'))
            cur.execute('INSERT INTO training_config VALUES (%s,%s,%s,%s)',
                        (config_id,rule_id,DECISION_VERSION,Json(manifest['source_mappings'])))
        return dict(input_id=input_id,config_id=config_id,rule_id=rule_id,cache=cache)

    def _check_pair(self, input_id, config_id):
        with self.db.cursor() as cur:
            cur.execute('''SELECT training_version(t.decision_version) FROM input_snapshot i
                JOIN input_manifest m USING(input_id) JOIN config_snapshot c USING(scope_id)
                JOIN training_config t USING(config_id) WHERE i.input_id=%s AND c.config_id=%s''',(input_id, config_id))
            if not cur.fetchone():
                raise TrainingError('snapshot_pair_not_found')

    def decision(self, input_id, config_id, analysis_id, occurrence_id):
        with self.db, self.db.cursor() as cur:
            self._check_pair(input_id, config_id)
            cur.execute('SELECT * FROM mpp_training_decisions(%s,%s,%s,%s)',(input_id,config_id,analysis_id,occurrence_id))
            row = cur.fetchone()
            if not row:
                raise TrainingError('occurrence_not_in_snapshot')
            return dict(zip((d[0] for d in cur.description),row))

    def summary(self, input_id, config_id, *, group_sink=None):
        """Session TEMP table avoids repeated full derivation; never permanent rows.

        A sink streams group summaries instead of holding them all in Python RAM.
        SQL/database/user names are deliberately absent from every projection.
        """
        started = time.monotonic()
        with self.db, self.db.cursor() as cur:
            self._check_pair(input_id, config_id)
            cur.execute('''CREATE TEMP TABLE training_summary_rows ON COMMIT DROP AS
                SELECT scope_id,group_id,timing_type,state,count_scope,reason_codes,category_kind,categories,template_ids,interval_ids
                FROM mpp_training_decisions(%s,%s)''',(input_id, config_id))
            derived = round(time.monotonic()-started,3)
            result = dict(input_id=input_id,config_id=config_id,derive_seconds=derived)
            for label,query in {
                'states':'SELECT state,count(*) FROM training_summary_rows GROUP BY 1 ORDER BY 1',
                'timings':"SELECT coalesce(timing_type,'unknown'),state,count(*) FROM training_summary_rows GROUP BY 1,2 ORDER BY 1,2",
                'count_scopes':'SELECT count_scope,state,count(*) FROM training_summary_rows GROUP BY 1,2 ORDER BY 1,2',
                'reasons':'SELECT r,state,count(*) FROM training_summary_rows CROSS JOIN LATERAL unnest(reason_codes) r GROUP BY 1,2 ORDER BY 1,2',
                'categories':'SELECT c,category_kind,count(*) FROM training_summary_rows CROSS JOIN LATERAL unnest(categories) c GROUP BY 1,2 ORDER BY 1,2',
                'templates':'SELECT t,count(*) FROM training_summary_rows CROSS JOIN LATERAL unnest(template_ids) t GROUP BY 1 ORDER BY 1',
                'intervals':'SELECT i,count(*) FROM training_summary_rows CROSS JOIN LATERAL unnest(interval_ids) i GROUP BY 1 ORDER BY 1',
            }.items():
                cur.execute(query)
                rows = cur.fetchall()
                # Configured labels could contain identity or SQL: expose only hashes.
                if label in ('templates','intervals'):
                    rows=[('rule:'+identity(r[0]),r[1]) for r in rows]
                result[label]=rows
            cur.execute('SELECT scope_id,count(*) FROM training_summary_rows GROUP BY 1 ORDER BY 1')
            result['clusters']=[('scope:'+identity(s),n) for s,n in cur]
            if group_sink:
                with self.db.cursor(name='training_group_summary') as stream:
                    stream.itersize=1000
                    stream.execute('''SELECT scope_id,group_id,timing_type,state,count_scope,reason,count(*)
                        FROM training_summary_rows CROSS JOIN LATERAL unnest(ARRAY[NULL::text]||reason_codes) reason
                        GROUP BY 1,2,3,4,5,6 ORDER BY 1,2,3,4,5,6''')
                    for scope, gid, timing, state, count_scope, reason, n in stream:
                        group_sink(dict(scope='scope:'+identity(scope),group_id=gid,timing_type=timing,state=state,count_scope=count_scope,reason=reason,count=n))
            result['total_seconds']=round(time.monotonic()-started,3)
        return result
