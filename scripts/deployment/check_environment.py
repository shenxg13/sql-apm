#!/usr/bin/env python3
"""Offline interpreter smoke checks, without reading any real input or database."""
import bz2
import ctypes
from decimal import Decimal
import gzip
import hashlib
import importlib
import json
import lzma
import multiprocessing
import platform
import sqlite3
import ssl
import sys
import uuid
import zlib
from datetime import datetime
from zoneinfo import ZoneInfo


def child(queue):
    queue.put('spawn-ok')


def main():
    assert sys.version_info[:3] == (3, 9, 5), 'requires exact Python 3.9.5'
    assert sys.prefix != sys.base_prefix, 'requires project venv'
    modules = ['ssl', '_ssl', 'hashlib', '_hashlib', 'bz2', 'lzma', 'zlib', 'sqlite3',
               'ctypes', 'readline', 'decimal', 'uuid', 'zoneinfo', 'multiprocessing',
               'pglast', 'psycopg2']
    for name in modules:
        importlib.import_module(name)
    data = 'SQL APM 离线验证'.encode()
    for module in (bz2, gzip, lzma, zlib):
        assert module.decompress(module.compress(data)) == data
    assert hashlib.sha256(b'abc').hexdigest() == 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad'
    assert Decimal('0.1') + Decimal('0.2') == Decimal('0.3')
    assert json.loads(json.dumps({'中文': '值'})) == {'中文': '值'}
    with sqlite3.connect(':memory:') as db:
        db.execute('CREATE TABLE test (value TEXT)')
        db.execute('INSERT INTO test VALUES (?)', ('中文',))
        assert db.execute('SELECT value FROM test').fetchone()[0] == '中文'
    assert datetime(2026, 1, 1, tzinfo=ZoneInfo('Asia/Shanghai')).utcoffset().total_seconds() == 28800
    assert uuid.uuid4().version == 4
    assert ctypes.CDLL(None).getpid() > 0
    context = multiprocessing.get_context('spawn')
    queue = context.Queue()
    process = context.Process(target=child, args=(queue,))
    process.start()
    try:
        assert queue.get(timeout=15) == 'spawn-ok'
        process.join(15)
        assert process.exitcode == 0
    finally:
        if process.is_alive():
            process.terminate()
            process.join()
        queue.close()
    print(json.dumps(dict(passed=True, python=platform.python_version(),
                         executable=sys.executable, base_prefix=sys.base_prefix,
                         modules=modules, openssl=ssl.OPENSSL_VERSION,
                         sqlite=sqlite3.sqlite_version, network_checks=False)))


if __name__ == '__main__':
    main()
