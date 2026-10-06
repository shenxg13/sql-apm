"""Product command dispatcher."""
import argparse
from sql_apm.cli.ingest import main as ingest
from sql_apm.cli.training import main as training
from sql_apm.cli.statistics import main as statistics
from sql_apm.cli.workflow import main as workflow
from sql_apm.cli.cleanup import main as cleanup


def main():
    parser = argparse.ArgumentParser(prog='python -m sql_apm')
    parser.add_argument('command', choices=['import','training','statistics','full','rebuild','status','history','cleanup'])
    parser.add_argument('arguments', nargs=argparse.REMAINDER, help=argparse.SUPPRESS)
    args = parser.parse_args()
    remaining = args.arguments
    if args.command in ('full','rebuild','status','history'):
        return workflow(args.command,remaining)
    return {'import': ingest, 'training': training, 'statistics': statistics, 'cleanup': cleanup}[args.command](remaining)


if __name__ == '__main__':
    raise SystemExit(main())
