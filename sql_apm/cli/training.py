"""Public training diagnostics expose counts, fixed reasons and opaque IDs."""
import argparse
import os

from sql_apm.ingestion.config import IngestionError
from sql_apm.ingestion.importer import emit
from sql_apm.storage.training import TrainingStore
from sql_apm.training.config import TrainingError, load_config


def main(argv=None):
    parser = argparse.ArgumentParser(description='固定训练快照或汇总按需判定')
    parser.add_argument('--schema', default='sql_apm')
    sub = parser.add_subparsers(dest='action', required=True)
    snapshot = sub.add_parser('snapshot')
    snapshot.add_argument('--config', required=True)
    snapshot.add_argument('--cluster', required=True)
    snapshot.add_argument('--batch', action='append', required=True)
    snapshot.add_argument('--analysis', action='append', default=[])
    summary = sub.add_parser('summary')
    summary.add_argument('--input', required=True)
    summary.add_argument('--config-id', required=True)
    summary.add_argument('--groups', action='store_true')
    args = parser.parse_args(argv)
    store = None
    try:
        config = load_config(args.config, args.cluster) if args.action == 'snapshot' else None
        store = TrainingStore(os.environ.get('SQL_APM_DSN', ''), args.schema)
        if args.action == 'snapshot':
            result = store.snapshot(config, args.batch, args.analysis)
        else:
            result = store.summary(
                args.input, args.config_id,
                group_sink=(lambda value: emit(phase='group_summary', **value)) if args.groups else None)
        emit(phase=args.action, state='complete', **result)
        return 0
    except (TrainingError, IngestionError) as error:
        emit(state='failed', reason=str(error))
        return 1
    except KeyboardInterrupt:
        emit(state='interrupted', reason='operator_interrupt')
        return 130
    except Exception:
        emit(state='failed', reason='training_failed')
        return 1
    finally:
        if store:
            store.close()
