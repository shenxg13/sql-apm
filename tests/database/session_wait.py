"""Observe a killed task owner's backend without acquiring a task or lock."""
import time


def wait_for_backend_exit(db, backend_pid, timeout=10):
    deadline = time.monotonic() + timeout
    while True:
        # End each transaction so pg_stat_activity cannot reuse a stale snapshot.
        with db, db.cursor() as cur:
            cur.execute('SELECT EXISTS (SELECT FROM pg_stat_activity WHERE pid=%s)',
                        (backend_pid,))
            present = cur.fetchone()[0]
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AssertionError('cluster_owner_exit_timeout: backend_pid=' +
                                 str(backend_pid) + '; timeout_seconds=' + str(timeout))
        if not present:
            return
        # Poll pacing only; elapsed time alone never establishes backend exit.
        time.sleep(min(0.01, remaining))
