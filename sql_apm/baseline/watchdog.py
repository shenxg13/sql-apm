"""Private build watchdog: a closed parent pipe means the owning process exited."""
import os
import sys
import time

from sql_apm.storage.statistics import StatisticsStore


def main():
    schema, build_id = sys.argv[1:]
    sys.stdin.buffer.read()
    for attempt in range(6):
        store = None
        try:
            store = StatisticsStore(os.environ.get('SQL_APM_DSN', ''),schema)
            store._failure(build_id,'interrupted','worker_disconnected')
            return
        except Exception:
            time.sleep(min(attempt+1,5))
        finally:
            if store:
                store.close()


if __name__ == '__main__':
    main()
