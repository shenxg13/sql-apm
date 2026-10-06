"""Explicit full/rebuild operations; stages reuse one cluster-locked connection."""
from sql_apm.ingestion.config import IngestionError, identity
from sql_apm.ingestion.importer import Importer
from sql_apm.storage.ingestion import connect
from sql_apm.storage.tasks import Task
from sql_apm.storage.training import TrainingStore
from sql_apm.storage.statistics import StatisticsStore
from sql_apm.storage.publication import PublicationStore
from sql_apm.storage.cleanup import overdue_count


def run(dsn, schema, config, ingestion=None, retry_of=None, workers=4, progress=None, retention_months=2):
    db = connect(dsn,schema)
    try:
        with Task(db,config['scope_id'],'full' if ingestion else 'rebuild') as task:
            imported=None
            if ingestion:
                importer=Importer(dsn,schema,workers,progress or (lambda **row:None),db=db)
                try:
                    imported=importer.run(ingestion,task)
                finally:
                    importer.close()
                if imported['state']!='complete':
                    raise IngestionError('batch_incomplete')
            with db,db.cursor() as cur:
                cur.execute('''SELECT b.batch_id,EXISTS (
                    SELECT FROM batch_entry e JOIN source_file f USING(file_id)
                    JOIN import_attempt a ON a.attempt_id=e.final_attempt_id
                    WHERE e.batch_id=b.batch_id AND a.state IN ('succeeded','duplicate_skipped')
                        AND (f.last_log_at IS NULL OR f.last_log_at >= %s::timestamptz))
                    FROM import_batch b WHERE b.scope_id=%s AND b.state='complete'
                        AND b.files_confirmed_complete ORDER BY b.batch_id''',
                    (config['window_start'],config['scope_id']))
                completed=cur.fetchall()
                batches=[batch for batch,selected in completed if selected]
                fallback=bool(completed) and not batches
                if fallback:
                    batches=[batch for batch,_ in completed]
                selection=dict(completed_batches=len(completed),selected_batches=len(batches),
                    excluded_batches=len(completed)-len(batches),window_fallback=fallback)
            training=TrainingStore(dsn,schema,db=db)
            frozen=training.snapshot(config,batches,task=task)
            if progress:
                progress(phase='snapshot_finished',task_id=task.task_id,input_id=frozen['input_id'],config_id=frozen['config_id'],**selection)
            statistics=StatisticsStore(dsn,schema,db=db)
            built=statistics.calculate(config['scope_id'],frozen['input_id'],frozen['config_id'],retry_of,
                progress=(lambda row:progress(**row)) if progress else None,task=task)
            publication=PublicationStore(db,task)
            checks=publication.check(built['build_id'])
            if progress:
                progress(phase='checks_finished',task_id=task.task_id,build_id=built['build_id'],checks=checks)
            published=publication.publish(built['build_id'])
            result=dict(task_id=task.task_id,scope='scope:'+identity(config['scope_id']),cutoff_date=config['cutoff_date'],
                window_start=config['window_start'],window_end=config['window_end'],snapshot=frozen,
                build=built,checks=checks,publication=published)
            if imported:
                result['import']=imported
            if ingestion:
                result['expired_result_months']=overdue_count(db,config['scope_id'],retention_months)
        result.update(state='failed' if task.failure else 'succeeded',stage_seconds=task.seconds)
        return result
    finally:
        db.close()
