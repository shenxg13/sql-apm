"""Product command dispatcher."""
import argparse
from sql_apm.cli.ingest import main as ingest
from sql_apm.cli.training import main as training


def main():
    parser = argparse.ArgumentParser(prog='python -m sql_apm')
    parser.add_argument('command', choices=['import','training'])
    args, remaining = parser.parse_known_args()
    return (ingest if args.command == 'import' else training)(remaining)


if __name__ == '__main__':
    raise SystemExit(main())
