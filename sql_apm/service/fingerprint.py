"""Loopback-only fingerprint service: a complete SQL in, the exact-search result out.

It computes the structure fingerprint with the rules installed at start-up and
answers exactly like ``search exact``; the database is only read. The log carries
the time, input length, result class and duration, never SQL text.
"""
import argparse
import base64
import binascii
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import os
import re
import sys
import threading
import time

import psycopg2

from sql_apm.cli.search import exact
from sql_apm.ingestion.config import IngestionError, identity
from sql_apm.sql.normalization import MAX_BYTES, Normalizer
from sql_apm.storage.ingestion import connect

# base64 of MAX_BYTES + 1 bytes plus the small JSON envelope; larger bodies are
# answered as over the input limit without being read.
MAX_BODY = (MAX_BYTES + 3) // 3 * 4 + 4096


LINE_BREAK = re.compile('\r\n?')
FORMS = (('lf', '\n'), ('crlf', '\r\n'), ('cr', '\r'))


def same_but_breaks(cur, rules, fingerprint, plain):
    """A stored text of this structure that is `plain` once its line breaks are LF."""
    # Only texts whose size allows them to differ in line breaks only are compared.
    size, breaks = len(plain.encode('utf-8')), plain.count('\n')
    cur.execute("""SELECT t.sql_id FROM mpp_fingerprint f JOIN mpp_sql_text t USING (sql_id)
        WHERE f.normalization_id=%s AND f.profile='mpp-csv/1' AND f.state='reliable' AND f.value=%s
          AND octet_length(t.text) BETWEEN %s AND %s
          AND regexp_replace(t.text,E'\\r\\n?',E'\\n','g')=%s
        ORDER BY t.sql_id LIMIT 1""", (rules, fingerprint, size, size + breaks, plain))
    row = cur.fetchone()
    return row[0] if row else None


def same_text_id(cur, rules, fingerprint, text):
    """The stored original text that is the input character for character.

    A browser's input box keeps one line-break form for the whole text, so CR LF,
    a lone CR and LF count as the same line break here; every other character is
    compared as it is. A text that is identical byte for byte wins. With a
    structure the text is looked for within that structure, so the answer never
    names a text of another structure.
    """
    cur.execute('SELECT mpp_query_text_id(%s)', (text,))
    found = cur.fetchone()[0]
    if found or ('\r' not in text and '\n' not in text):
        return found
    plain = LINE_BREAK.sub('\n', text)
    if fingerprint:
        return same_but_breaks(cur, rules, fingerprint, plain)
    # Without a usable structure only the three uniform forms can be found by content.
    for candidate in dict.fromkeys((plain, plain.replace('\n', '\r\n'), plain.replace('\n', '\r'))):
        cur.execute('SELECT mpp_query_text_id(%s)', (candidate,))
        found = cur.fetchone()[0]
        if found:
            return found
    return None


def other_form(cur, normalizer, rules, fingerprint, text):
    """The input in another line-break form, when a stored text proves that form.

    String constants and quoted names that the rules keep in the structure carry
    their line breaks into the fingerprint, so the same statement is another
    structure in another form, and the browser can only send one form. The input
    is tried with every line break as LF, as CR LF and as a lone CR; a form counts
    only when a stored text of its structure is the input character for character
    apart from the line-break forms. Returns (form, bytes, sql_id) or None.
    """
    plain = LINE_BREAK.sub('\n', text)
    if '\n' not in plain:
        return None
    for form, mark in FORMS:
        other = plain.replace('\n', mark)
        if other == text:
            continue
        raw = other.encode('utf-8')
        value = normalizer.normalize(raw)['fingerprint']
        if value['state'] != 'reliable' or value['value'] == fingerprint:
            continue
        found = same_but_breaks(cur, rules, value['value'], plain)
        if found:
            return form, raw, found
    return None


def log(length, outcome, started):
    print(json.dumps(dict(at=time.strftime('%Y-%m-%dT%H:%M:%S%z'), bytes=length, result=outcome,
                          ms=round((time.monotonic() - started) * 1000, 1))), flush=True)


class Service:
    def __init__(self, dsn, schema):
        self.dsn, self.schema = dsn, schema
        self.normalizer = Normalizer()
        self.rules = dict(self.normalizer.context, normalization_id='N:' + identity(self.normalizer.context))
        # One request at a time: the normalizer is not shared between threads.
        self.lock = threading.Lock()

    def exact(self, raw, filters):
        with self.lock:
            db = connect(self.dsn, self.schema)
            try:
                db.set_session(readonly=True, isolation_level='REPEATABLE READ')
                with db, db.cursor() as cur:
                    result = exact(cur, self.normalizer, raw, filters)
                    stored = form = None
                    if len(raw) <= MAX_BYTES:
                        try:
                            rules, text = self.rules['normalization_id'], raw.decode('utf-8')
                            stored = same_text_id(cur, rules, result.get('fingerprint'), text)
                            if stored is None:
                                other = other_form(cur, self.normalizer, rules, result.get('fingerprint'), text)
                                if other:
                                    # The answer is the command line's for the form the stored text has.
                                    form, changed, stored = other
                                    result = exact(cur, self.normalizer, changed, filters)
                        except (UnicodeError, ValueError):
                            stored = form = None
            finally:
                db.close()
        result.update(exact_sql_id=stored, line_breaks=form, rules=self.rules,
                      input=dict(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest()))
        return result


def handler(service):
    class Handler(BaseHTTPRequestHandler):
        server_version, sys_version, timeout = 'sql-apm-fingerprint', '', 30

        def log_message(self, *args):  # the default line would be the only unstructured output
            pass

        def reply(self, status, document):
            body = json.dumps(document, ensure_ascii=False, default=str).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path != '/v1/health':
                return self.reply(404, dict(state='failed', reason='not_found'))
            self.reply(200, dict(state='ok', rules=service.rules, max_bytes=MAX_BYTES))

        def do_POST(self):
            started, length = time.monotonic(), 0
            if self.path != '/v1/exact':
                return self.reply(404, dict(state='failed', reason='not_found'))
            try:
                size = int(self.headers.get('Content-Length', ''))
                if size < 0:
                    raise ValueError
            except ValueError:
                log(0, 'invalid_request', started)
                return self.reply(400, dict(state='failed', reason='invalid_request'))
            try:
                if size > MAX_BODY:
                    # Same answer as the command line for an oversized input; the body is not read.
                    self.close_connection = True
                    raw, filters = b'\0' * (MAX_BYTES + 1), [None, None, None]
                    length = size
                else:
                    document = json.loads(self.rfile.read(size))
                    encoded = document['sql_b64']
                    if not isinstance(encoded, str) or set(document) - {'sql_b64', 'cluster', 'database', 'user'}:
                        raise ValueError
                    padded = encoded.replace('-', '+').replace('_', '/')
                    raw = base64.b64decode(padded + '=' * (-len(padded) % 4), validate=True)
                    filters = [document.get(key) for key in ('cluster', 'database', 'user')]
                    if any(value is not None and (not isinstance(value, str) or not value) for value in filters):
                        raise ValueError
                    length = len(raw)
            except (ValueError, KeyError, TypeError, binascii.Error):
                log(length, 'invalid_request', started)
                return self.reply(400, dict(state='failed', reason='invalid_request'))
            try:
                result = service.exact(raw, filters)
            except (psycopg2.Error, IngestionError):
                log(length, 'database_unavailable', started)
                return self.reply(503, dict(state='failed', reason='database_unavailable'))
            except Exception:
                log(length, 'internal_error', started)
                return self.reply(500, dict(state='failed', reason='internal_error'))
            log(length, result['state'], started)
            self.reply(200, result)
    return Handler


def main(argv=None):
    parser = argparse.ArgumentParser(prog='python -m sql_apm fingerprint-service', description='完整 SQL 的结构指纹服务（只监听本机）')
    parser.add_argument('--host', default='127.0.0.1', help='只接受回环地址')
    parser.add_argument('--port', type=int, default=3001)
    parser.add_argument('--schema', default='sql_apm')
    args = parser.parse_args(argv)
    try:
        loopback = ipaddress.ip_address(args.host).is_loopback
    except ValueError:
        loopback = False
    if not loopback:
        parser.error('--host must be a loopback address')
    service = Service(os.environ.get('SQL_APM_DSN', ''), args.schema)
    server = ThreadingHTTPServer((args.host, args.port), handler(service))
    server.daemon_threads = True
    print(json.dumps(dict(state='listening', host=args.host, port=server.server_address[1],
                          normalization_id=service.rules['normalization_id'])), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
