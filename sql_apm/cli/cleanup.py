"""Explicit retention command. Preview is the default and has no write path."""
import argparse
import json
import os
import signal

from sql_apm.cli.statistics import interrupted
from sql_apm.ingestion.config import IngestionError
from sql_apm.ingestion.importer import emit
from sql_apm.storage.cleanup import CleanupStore, preview
from sql_apm.storage.ingestion import connect
from sql_apm.training.config import TrainingError, load_retention


def main(argv=None):
    parser = argparse.ArgumentParser(description='预览或清理按集群过期的版本结果')
    parser.add_argument('--cluster', required=True)
    parser.add_argument('--training-config', required=True)
    parser.add_argument('--schema', default='sql_apm')
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args(argv)
    db = None
    previous = signal.signal(signal.SIGTERM, interrupted)
    try:
        months = load_retention(args.training_config,args.cluster)
        db = connect(os.environ.get('SQL_APM_DSN',''),args.schema)
        if args.execute:
            result = CleanupStore(db).execute(args.cluster,months)
        else:
            db.set_session(readonly=True)
            result = dict(preview(db,args.cluster,months),state='preview')
        emit(**json.loads(json.dumps(result,default=str)))
        return 1 if result['state']=='failed' else 0
    except TrainingError as error:
        emit(state='failed',reason=str(error))
        return 1
    except IngestionError as error:
        emit(state='failed',reason=str(error))
        return 1
    except KeyboardInterrupt:
        emit(state='interrupted',reason='operator_interrupt')
        return 130
    except Exception:
        emit(state='failed',reason='cleanup_failed')
        return 1
    finally:
        signal.signal(signal.SIGTERM,previous)
        if db is not None:
            db.close()
