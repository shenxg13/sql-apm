"""Run installed entrypoints and record their actual driver calls, discarding output."""
import io
import inspect
import json
import os
from pathlib import Path
import signal
import sys
import time

import psycopg2
from psycopg2 import extensions

from .core import encoded, shape, save


class Sink(io.TextIOBase):
    def __init__(self):
        self.bytes = 0
    def write(self, text):
        self.bytes += len(text.encode('utf-8', 'replace'))
        return len(text)
    def flush(self):
        pass
    def isatty(self):
        return True


def run(request, output):
    # Product modules must not have been imported from the source checkout.
    sys.path.insert(0, request['app'])
    original_connect = psycopg2.connect
    records, live = [], set()

    class CursorMixin:
        def execute(self, statement, params=None):
            started = time.monotonic()
            record = dict(parameter_shape=shape(params), state='ok')
            try:
                return super().execute(statement, params)
            except psycopg2.errors.QueryCanceled:
                record['state'] = 'timeout'
                raise
            except psycopg2.Error as error:
                record.update(state='error',sqlstate=error.pgcode)
                raise
            finally:
                record.update(ms=round((time.monotonic()-started)*1000,3),rows=max(0,self.rowcount))
                record['sql'] = self.query.decode(self.connection.encoding) if self.query else ''
                record['sites']=[dict(file=str(Path(f.filename).relative_to(request['app'])),line=f.lineno,function=f.function)
                                 for f in inspect.stack() if Path(f.filename).is_relative_to(request['app'])]
                record['backend_pid']=self.connection.get_backend_pid()
                records.append(record)

    class Connection(extensions.connection):
        def cursor(self, *args, **kwargs):
            base = kwargs.get('cursor_factory', extensions.cursor)
            kwargs['cursor_factory'] = type('RecordedCursor', (CursorMixin,base), {})
            return super().cursor(*args,**kwargs)
        def close(self):
            try:
                return super().close()
            finally:
                live.discard(self)

    def connect(dsn='', *args, **kwargs):
        if live:
            raise RuntimeError('concurrent_product_connection')
        # Preserve the user's account and its timeout. Add only a read-only default.
        opts = kwargs.pop('options',None)
        if opts is None:
            opts=extensions.parse_dsn(dsn).get('options','')
        kwargs.update(connection_factory=Connection, options=opts+' -c default_transaction_read_only=on',
                      application_name='sql-apm-perfcheck-cli')
        db = original_connect(dsn,*args,**kwargs)
        live.add(db)
        return db

    psycopg2.connect = connect
    sink, before_out, before_err, before_in = Sink(), sys.stdout, sys.stderr, sys.stdin
    sys.stdout = sys.stderr = sink
    sys.stdin = Sink()
    started, code, reason = time.monotonic(), 0, None
    previous = signal.signal(signal.SIGINT, lambda *args: (_ for _ in ()).throw(SystemExit(130)))
    try:
        kind = request['kind']
        if kind == 'sqlfind':
            from sql_apm.cli.sqlfind import main
            code = main(request['argv'])
        elif kind == 'interactive':
            from sql_apm.cli import sqlfind
            answers = iter(request['answers'])
            sqlfind.terminal_input = lambda *args,**kwargs: next(answers, 'q').encode()
            code = sqlfind.main(request['argv'])
        elif kind == 'search':
            from sql_apm.cli.search import main
            code = main(request['argv'])
        elif kind == 'daily':
            from sql_apm.cli.daily import main
            code = main(['status'])
        else:
            raise ValueError('unknown_command_kind')
    except SystemExit as error:
        code = error.code if isinstance(error.code,int) else 1
    except BaseException as error:
        code,reason=1,type(error).__name__
    finally:
        signal.signal(signal.SIGINT,previous)
        for db in list(live):
            db.close()
        psycopg2.connect = original_connect
        sys.stdout,sys.stderr,sys.stdin=before_out,before_err,before_in
        save(output,dict(ms=round((time.monotonic()-started)*1000,3),exit_code=code,
                         output_bytes=sink.bytes,reason=reason,statements=records))
    return code


if __name__ == '__main__':
    request = json.loads(Path(sys.argv[1]).read_text())
    raise SystemExit(run(request,Path(sys.argv[2])))
