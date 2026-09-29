"""Only counts, fixed reasons and opaque identifiers are emitted."""
import argparse
import os

from sql_apm.ingestion.config import IngestionError, load_config
from sql_apm.ingestion.importer import Importer, emit


def main(argv=None):
    parser = argparse.ArgumentParser(description='导入已确认完整的 HashData CSV 批次')
    parser.add_argument('--config', required=True)
    parser.add_argument('--source', required=True)
    parser.add_argument('--batch', required=True)
    parser.add_argument('--schema', default='sql_apm')
    parser.add_argument('--workers', type=int, choices=range(1, 9), default=4)
    args = parser.parse_args(argv)
    importer = None
    try:
        config = load_config(args.config, args.source, args.batch)
        importer = Importer(os.environ.get('SQL_APM_DSN', ''), args.schema, args.workers)
        result = importer.run(config)
        emit(phase='batch_finished', **result)
        return 0 if result['state'] == 'complete' else 1
    except IngestionError as error:
        emit(state='failed', reason=str(error))
        return 1
    except KeyboardInterrupt:
        emit(state='interrupted', reason='operator_interrupt')
        return 130
    except Exception:
        emit(state='failed', reason='ingestion_failed')
        return 1
    finally:
        if importer:
            importer.close()
