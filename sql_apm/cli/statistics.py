"""Calculate a sealed snapshot under the shared cluster task lease."""
import argparse
import os
import signal

from sql_apm.ingestion.importer import emit
from sql_apm.ingestion.config import IngestionError
from sql_apm.storage.statistics import StatisticsStore, StatisticsError


def interrupted(signum, frame):
    raise KeyboardInterrupt


def main(argv=None):
    parser = argparse.ArgumentParser(description='引用已封存快照创建构建并计算五层统计')
    parser.add_argument('--schema', default='sql_apm')
    parser.add_argument('--cluster', required=True)
    parser.add_argument('--input', required=True)
    parser.add_argument('--config-id', required=True)
    parser.add_argument('--retry-of')
    args = parser.parse_args(argv)
    store = None
    previous = signal.signal(signal.SIGTERM,interrupted)
    try:
        store = StatisticsStore(os.environ.get('SQL_APM_DSN',''),args.schema)
        result = store.calculate(args.cluster,args.input,args.config_id,args.retry_of,
                                 progress=lambda row: emit(**row))
        emit(phase='statistics',**result)
        return 0
    except (StatisticsError, IngestionError) as error:
        emit(state='failed',reason=str(error))
        return 1
    except KeyboardInterrupt:
        emit(state='interrupted',reason='operator_interrupt')
        return 130
    except Exception:
        emit(state='failed',reason='statistics_failed')
        return 1
    finally:
        signal.signal(signal.SIGTERM,previous)
        if store:
            store.close()
