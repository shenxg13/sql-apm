"""Let a stop signal reach a run that is waiting inside a database statement.

A Python signal handler runs only when the interpreter has control, and a statement
that waits (a long calculation, a lock) keeps it away until the statement ends. The
signal's arrival is therefore also read by a thread, which cancels the statements of
the run's step connections until the run has left; the handler then raises as usual.
A killed process is not helped by this: that is what the connection check is for.
"""
import os
import signal
import threading
import weakref

from sql_apm.storage import ingestion


class Interrupter:
    def __init__(self, signals=(signal.SIGTERM, signal.SIGINT), pause=0.5):
        self.signals, self.pause = signals, pause
        self.connections = weakref.WeakSet()
        self.stopping, self.done, self.raised = threading.Event(), threading.Event(), False

    def __enter__(self):
        self.reader, self.writer = os.pipe()
        os.set_blocking(self.writer, False)
        self.handlers = {number: signal.signal(number, self._handle) for number in self.signals}
        self.wakeup = signal.set_wakeup_fd(self.writer, warn_on_full_buffer=False)
        ingestion.WATCHERS.append(self.connections)
        self.thread = threading.Thread(target=self._watch, name='stop-signal', daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.done.set()
        ingestion.WATCHERS.remove(self.connections)
        signal.set_wakeup_fd(self.wakeup)
        for number, handler in self.handlers.items():
            signal.signal(number, handler)
        os.close(self.writer)
        self.thread.join(2)
        os.close(self.reader)

    def stop_requested(self):
        return self.stopping.is_set()

    def leave_alone(self, connection):
        """The connection that records the run is never cancelled: the abort must be written."""
        self.connections.discard(connection)

    def _handle(self, number, frame):
        self.stopping.set()
        if not self.raised:      # once: a second signal must not cut the recording of the abort short
            self.raised = True
            raise KeyboardInterrupt

    def _watch(self):
        wanted = {int(number) for number in self.signals}
        while True:
            try:
                received = os.read(self.reader, 64)
            except OSError:
                return
            if not received:
                return
            if wanted.intersection(received):
                break
        self.stopping.set()
        # Again and again: after the first statement is cancelled, the step's own tidying up
        # may wait behind the same lock.
        while True:
            for connection in list(self.connections):
                try:
                    connection.cancel()
                except Exception:
                    pass
            if self.done.wait(self.pause):
                return
