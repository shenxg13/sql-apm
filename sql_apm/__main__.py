"""Product command dispatcher."""
import argparse
from sql_apm.cli.ingest import main as ingest


def main():
    parser = argparse.ArgumentParser(prog='python -m sql_apm')
    parser.add_argument('command', choices=['import'])
    args, remaining = parser.parse_known_args()
    return ingest(remaining)


if __name__ == '__main__':
    raise SystemExit(main())
