"""Explicit commands emit only identifiers, times, counts and fixed reasons."""
import argparse
import json
import os
import signal

from sql_apm.baseline.workflow import run
from sql_apm.cli.statistics import interrupted
from sql_apm.ingestion.config import IngestionError, load_config as import_config
from sql_apm.ingestion.importer import emit
from sql_apm.storage.ingestion import connect
from sql_apm.storage.publication import version_status
from sql_apm.storage.statistics import StatisticsError
from sql_apm.training.config import TrainingError, load_config


def main(command, argv=None):
    parser=argparse.ArgumentParser(description='离线基线完整流程、重建和版本查询')
    parser.add_argument('--schema',default='sql_apm')
    if command in ('full','rebuild'):
        parser.add_argument('--training-config',required=True)
        parser.add_argument('--cutoff-date',required=command=='rebuild')
        parser.add_argument('--days',type=int)
        if command=='full':
            parser.add_argument('--config',required=True)
            parser.add_argument('--source',required=True)
            parser.add_argument('--batch',required=True)
            parser.add_argument('--workers',type=int,choices=range(1,9),default=4)
        else:
            parser.add_argument('--cluster',required=True)
            parser.add_argument('--retry-of')
    else:
        parser.add_argument('--cluster',required=True)
        parser.add_argument('--limit',type=int,default=20,choices=range(1,1001))
    args=parser.parse_args(argv)
    previous=signal.signal(signal.SIGTERM,interrupted)
    db=None
    try:
        dsn=os.environ.get('SQL_APM_DSN','')
        if command in ('status','history'):
            db=connect(dsn,args.schema)
            result=version_status(db,args.cluster,command=='history',args.limit)
        else:
            ingestion=import_config(args.config,args.source,args.batch) if command=='full' else None
            scope=ingestion['scope_id'] if ingestion else args.cluster
            cutoff=args.cutoff_date or max(ingestion['dates'])
            config=load_config(args.training_config,scope,cutoff_date=cutoff,window_days=args.days)
            result=run(dsn,args.schema,config,ingestion,getattr(args,'retry_of',None),getattr(args,'workers',4),emit)
        emit(**json.loads(json.dumps(result,default=str)))
        return 1 if result.get('state')=='failed' else 0
    except (IngestionError,TrainingError,StatisticsError) as error:
        emit(state='failed',reason=str(error))
        return 1
    except KeyboardInterrupt:
        emit(state='interrupted',reason='operator_interrupt')
        return 130
    except Exception:
        emit(state='failed',reason='workflow_failed')
        return 1
    finally:
        signal.signal(signal.SIGTERM,previous)
        if db is not None:
            db.close()
