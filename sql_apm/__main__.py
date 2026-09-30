"""Product command dispatcher."""
import argparse
from sql_apm.cli.ingest import main as ingest
from sql_apm.cli.training import main as training
from sql_apm.cli.statistics import main as statistics


def main():
    parser = argparse.ArgumentParser(prog='python -m sql_apm')
    parser.add_argument('command', choices=['import','training','statistics'])
    args, remaining = parser.parse_known_args()
    return {'import': ingest, 'training': training, 'statistics': statistics}[args.command](remaining)


if __name__ == '__main__':
    raise SystemExit(main())
