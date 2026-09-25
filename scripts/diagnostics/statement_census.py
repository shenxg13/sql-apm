#!/usr/bin/env python3
"""Repository entrypoint for read-only category evidence collection."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from sql_apm.diagnostics.statement_census import main


if __name__ == '__main__':
    main()
