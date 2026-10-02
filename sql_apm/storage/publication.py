"""Checks consume saved formal results; one transaction switches the pointer."""
import uuid

from sql_apm.storage.statistics import StatisticsError

CHECKS = ('batch_complete','rules_consistent','results_complete','results_saved',
          'counts_consistent','values_consistent')


class PublicationStore:
    def __init__(self, db, task):
        if task.db is not db:
            raise StatisticsError('task_connection_mismatch')
        self.db, self.task = db, task

    def check(self, build_id):
        self.task.set_stage('check')
        with self.db, self.db.cursor() as cur:
            cur.execute('SELECT state,results_saved,input_id,config_id,partition_id FROM build WHERE build_id=%s AND scope_id=%s',
                        (build_id,self.task.scope))
            build = cur.fetchone()
            if not build:
                raise StatisticsError('build_not_found')
            outcomes = {}
            if build[0] != 'calculated':
                outcomes = {name:('not_run','calculated_build_required') for name in CHECKS}
            else:
                _, saved, input_id, config_id, partition = build
                cur.execute('''SELECT EXISTS (SELECT FROM input_batch WHERE input_id=%s)
                    AND NOT EXISTS (SELECT FROM input_batch i JOIN import_batch b USING(batch_id)
                        WHERE i.input_id=%s AND (b.state<>'complete' OR NOT b.files_confirmed_complete))
                    AND NOT EXISTS (SELECT FROM input_batch i JOIN batch_entry e USING(batch_id)
                        LEFT JOIN import_attempt a ON a.attempt_id=e.final_attempt_id
                        WHERE i.input_id=%s AND (a.state IS NULL OR a.state NOT IN ('succeeded','duplicate_skipped')))''',
                    (input_id,input_id,input_id))
                passed = {'batch_complete':cur.fetchone()[0], 'results_saved':saved}
                cur.execute('''SELECT b.normalization_id=c.normalization_id AND b.profile=c.profile
                    AND c.statistics_version='baseline-formulas/1' AND t.decision_version='training-decision/1'
                    AND r.normalization_id=c.normalization_id
                    AND NOT EXISTS (SELECT FROM mpp_build_group bg JOIN mpp_baseline_group g USING(group_id)
                        JOIN mpp_fingerprint f USING(fingerprint_id) WHERE bg.build_id=b.build_id AND
                        (g.scope_id<>b.scope_id OR g.normalization_id<>b.normalization_id OR g.profile<>b.profile
                         OR f.normalization_id<>b.normalization_id OR f.state<>'reliable' OR f.value<>g.fingerprint_value))
                    FROM build b JOIN config_snapshot c USING(config_id)
                    JOIN training_config t USING(config_id) JOIN mpp_training_rule r USING(rule_id)
                    WHERE b.build_id=%s''',(build_id,))
                row=cur.fetchone();passed['rules_consistent']=bool(row and row[0])
                # Reuse the versioned physical CHECK predicates, including all
                # NULL reasons and numeric finiteness. Do not trust constraints
                # as evidence: test them against every saved row again.
                cur.execute("SELECT pg_get_expr(conbin,conrelid) FROM pg_constraint WHERE conrelid='mpp_statistic'::regclass AND contype='c' ORDER BY conname")
                predicates=' AND '.join('('+r[0]+') IS NOT FALSE' for r in cur)
                cur.execute('DROP TABLE IF EXISTS pg_temp.publication_groups')
                cur.execute('''CREATE TEMP TABLE publication_groups ON COMMIT DROP AS
                    SELECT group_id,layer,count(*) rows,sum(included_count) included,sum(excluded_count) excluded,
                        bool_and('''+predicates+''') valid
                    FROM mpp_statistic WHERE build_id=%s AND partition_id=%s GROUP BY group_id,layer''',
                    (build_id,partition))
                cur.execute('CREATE INDEX ON publication_groups(group_id,layer)')
                cur.execute('ANALYZE publication_groups')
                cur.execute('''WITH actual AS (SELECT layer,sum(rows) rows,count(*) groups
                        FROM publication_groups GROUP BY layer),
                    receipts AS (SELECT * FROM mpp_build_layer_count WHERE build_id=%s AND kind='formal'),
                    required_groups AS MATERIALIZED (SELECT group_id FROM mpp_build_group
                        WHERE build_id=%s AND partition_id=%s),
                    actual_groups AS (SELECT group_id,count(*) layers FROM publication_groups GROUP BY group_id)
                    SELECT (SELECT count(*)=5 FROM receipts)
                    AND NOT EXISTS (SELECT FROM receipts r LEFT JOIN actual a USING(layer)
                        WHERE r.row_count<>coalesce(a.rows,0) OR r.group_count<>coalesce(a.groups,0))
                    AND NOT EXISTS (SELECT FROM required_groups g FULL JOIN actual_groups s USING(group_id)
                        WHERE g.group_id IS NULL OR s.group_id IS NULL OR s.layers<>5)
                    AND (SELECT count(*) FROM required_groups)=
                        (SELECT coalesce((diagnostics->>'groups')::bigint,-1) FROM build WHERE build_id=%s)''',
                    (build_id,build_id,partition,build_id))
                passed['results_complete']=cur.fetchone()[0]
                cur.execute('''SELECT NOT EXISTS (SELECT FROM publication_groups GROUP BY group_id
                        HAVING count(*)<>5 OR min(included)<>max(included) OR min(excluded)<>max(excluded))
                    AND (SELECT count(*)=5 FROM mpp_build_timing_coverage WHERE build_id=%s)
                    AND NOT EXISTS (SELECT FROM mpp_build_timing_coverage t LEFT JOIN (
                        SELECT g.timing_type,sum(s.included) i,sum(s.excluded) e
                        FROM publication_groups s JOIN mpp_baseline_group g USING(group_id)
                        WHERE s.layer='overall' GROUP BY g.timing_type) x USING(timing_type)
                        WHERE t.build_id=%s AND (t.included_count<>coalesce(x.i,0) OR t.excluded_count<>coalesce(x.e,0)))''',
                    (build_id,build_id))
                passed['counts_consistent']=cur.fetchone()[0]
                cur.execute('SELECT coalesce(bool_and(valid),true) FROM publication_groups')
                passed['values_consistent']=cur.fetchone()[0]
                outcomes={name:('passed',None) if passed[name] else ('failed',name+'_failed') for name in CHECKS}
            for name,(state,reason) in outcomes.items():
                cur.execute('''INSERT INTO build_check VALUES (%s,%s,%s,%s)
                    ON CONFLICT (build_id,name) DO UPDATE SET state=excluded.state,reason=excluded.reason''',
                    (build_id,name,state,reason))
        return {name:dict(state=value[0],reason=value[1]) for name,value in outcomes.items()}

    def _record(self, cur, build_id, result, reason):
        cur.execute('SELECT build_id FROM current_version WHERE scope_id=%s',(self.task.scope,))
        current=cur.fetchone()
        publication='PUB:'+uuid.uuid4().hex
        cur.execute('''INSERT INTO publication VALUES (%s,%s,%s,%s,%s,%s,clock_timestamp()) RETURNING at''',
                    (publication,self.task.scope,build_id,current[0] if current else None,result,reason))
        at=cur.fetchone()[0]
        self.task.link(cur,'publication',publication)
        return dict(publication_id=publication,build_id=build_id,previous_build_id=current[0] if current else None,
                    result=result,reason=reason,at=at.isoformat())

    def publish(self, build_id, fault=None):
        self.task.set_stage('publish')
        try:
            with self.db, self.db.cursor() as cur:
                cur.execute('''SELECT b.state,b.results_saved,
                    (SELECT count(*)=6 AND bool_and(state='passed') FROM build_check c WHERE c.build_id=b.build_id),
                    (SELECT coalesce(sum(included_count),0)>0 FROM mpp_build_timing_coverage t WHERE t.build_id=b.build_id)
                    FROM build b WHERE b.build_id=%s AND b.scope_id=%s FOR UPDATE''',(build_id,self.task.scope))
                row=cur.fetchone()
                if not row:
                    raise StatisticsError('build_not_found')
                result,reason=('published',None)
                if row[:3] != ('calculated',True,True):
                    result,reason='check_failed','publication_checks_failed'
                elif not row[3]:
                    result,reason='no_samples','no_valid_samples'
                value=self._record(cur,build_id,result,reason)
                if result=='published':
                    cur.execute('''INSERT INTO current_version (scope_id,build_id,last_success_at,publication_id)
                        VALUES (%s,%s,%s,%s) ON CONFLICT (scope_id) DO UPDATE SET
                        build_id=excluded.build_id,last_success_at=excluded.last_success_at,publication_id=excluded.publication_id''',
                        (self.task.scope,build_id,value['at'],value['publication_id']))
                if fault:
                    fault()
                self.task.finish(cur,'failed' if result=='check_failed' else 'succeeded',
                                 reason if result=='check_failed' else None)
            if result=='check_failed':
                self.task.failure='publication_checks_failed'
            return value
        except (KeyboardInterrupt,SystemExit):
            raise
        except Exception:
            # A dead session cannot publish or reconnect. Next admission records
            # interruption. On a live session, rollback preserves the old pointer.
            self.db.rollback()
            with self.db,self.db.cursor() as cur:
                value=self._record(cur,build_id,'publish_failed','publication_write_failed')
                self.task.finish(cur,'failed','publication_write_failed')
            self.task.failure='publication_write_failed'
            return value


def version_status(db, scope, history=False, limit=20):
    with db, db.cursor() as cur:
        fields='''p.publication_id,p.build_id,p.previous_build_id,p.result,p.reason,p.at,
            c.cutoff_date,c.window_start,c.window_end,
            coalesce((SELECT included_count=0 FROM mpp_build_timing_coverage t
                WHERE t.build_id=p.build_id AND t.timing_type='request'),true) request_baseline_missing'''
        joins='publication p JOIN build b USING(build_id) JOIN config_snapshot c USING(config_id)'
        def rows():
            names=[d[0] for d in cur.description]
            return [dict(zip(names,row)) for row in cur]
        if history:
            cur.execute('SELECT '+fields+' FROM '+joins+" WHERE p.scope_id=%s AND p.result='published' ORDER BY p.at DESC,p.publication_id LIMIT %s",(scope,limit))
            return dict(versions=rows())
        cur.execute('SELECT '+fields+' FROM current_version v JOIN '+joins+' ON v.publication_id=p.publication_id WHERE v.scope_id=%s',(scope,))
        current=rows()
        cur.execute('''SELECT task_id,mode,state,stage,reason,started_at,finished_at,stage_seconds,
            ARRAY(SELECT batch_id FROM task_batch a WHERE a.task_id=t.task_id ORDER BY batch_id) batch_ids,
            ARRAY(SELECT build_id FROM task_build a WHERE a.task_id=t.task_id ORDER BY build_id) build_ids,
            ARRAY(SELECT publication_id FROM task_publication a WHERE a.task_id=t.task_id ORDER BY publication_id) publication_ids
            FROM task t WHERE scope_id=%s ORDER BY started_at DESC,task_id LIMIT %s''',(scope,limit))
        tasks=rows()
        cur.execute("""SELECT * FROM (
            SELECT publication_id,build_id,result,reason,at FROM publication
                WHERE scope_id=%s AND result<>'published'
            UNION ALL
            SELECT NULL,(SELECT build_id FROM task_build b WHERE b.task_id=t.task_id ORDER BY build_id DESC LIMIT 1),
                t.state,t.reason,coalesce(t.finished_at,t.started_at) FROM task t
                WHERE t.scope_id=%s AND t.mode IN ('full','rebuild') AND t.state IN ('failed','interrupted')
                AND NOT EXISTS (SELECT FROM task_publication p WHERE p.task_id=t.task_id)
            ) failed ORDER BY at DESC,publication_id NULLS LAST LIMIT 1""",(scope,scope))
        unpublished=rows()
        return dict(current=current[0] if current else None,baseline_state='available' if current else 'no_baseline',
                    tasks=tasks,last_unpublished=unpublished[0] if unpublished else None)
