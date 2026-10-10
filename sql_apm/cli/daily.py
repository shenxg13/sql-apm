"""Daily run and its status. Output holds only reason codes, counts, dates, file names and identifiers."""
import argparse
import os
import signal

import psycopg2

from sql_apm.cli.statistics import interrupted
from sql_apm.daily.config import DailyError, load_config
from sql_apm.daily.interrupt import Interrupter
from sql_apm.daily.run import DailyRun
from sql_apm.ingestion.config import IngestionError
from sql_apm.ingestion.importer import emit
from sql_apm.storage.daily import status
from sql_apm.storage.ingestion import connect
from sql_apm.training.config import TrainingError


def main(argv=None):
    parser = argparse.ArgumentParser(prog='python -m sql_apm daily',
                                     description='每日运行：导入有齐全标记的日期、按间隔构建发布、清理；以及查询运行状态')
    actions = parser.add_subparsers(dest='action', required=True)
    run = actions.add_parser('run', help='执行一次每日运行后退出')
    run.add_argument('--config', required=True)
    run.add_argument('--trigger', choices=['manual', 'timer'], default='manual')
    view = actions.add_parser('status', help='最近的运行和当前待处理的问题（只读）')
    view.add_argument('--limit', type=int, default=10, choices=range(1, 201), metavar='1-200')
    for child in (run, view):
        child.add_argument('--schema', default='sql_apm')
    args = parser.parse_args(argv)
    dsn = os.environ.get('SQL_APM_DSN', '')
    previous = signal.signal(signal.SIGTERM, interrupted)
    db = stop = None
    try:
        if args.action == 'status':
            db = connect(dsn, args.schema)
            db.set_session(readonly=True)
            emit(**status(db, args.limit))
            return 0
        # Configuration is checked whole before the first connection: a bad value writes nothing.
        config = load_config(args.config)
        # A stop signal must end the run even while it waits inside a statement.
        with Interrupter() as stop:
            return DailyRun(dsn, args.schema, config, args.trigger, emit, interrupter=stop).execute()
    except (DailyError, IngestionError, TrainingError) as error:
        emit(state='failed', reason=str(error))
        return 1
    except KeyboardInterrupt:
        emit(state='interrupted', reason='operator_interrupt')
        return 130
    except psycopg2.OperationalError:
        if stop is not None and stop.stop_requested():
            emit(state='interrupted', reason='operator_interrupt')
            return 130
        # Nothing could be recorded: the database was not reachable.
        emit(state='failed', reason='database_unavailable')
        return 1
    except Exception:
        if stop is not None and stop.stop_requested():
            emit(state='interrupted', reason='operator_interrupt')
            return 130
        emit(state='failed', reason='daily_failed')
        return 1
    finally:
        signal.signal(signal.SIGTERM, previous)
        if db is not None:
            db.close()
