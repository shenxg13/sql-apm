"""Strict CSV boundaries and conservative, file-local Execute association."""
from collections import OrderedDict, deque
import csv
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import io
import re

from sql_apm.ingestion.config import IngestionError, canonical

PARSER_VERSION = 'mpp-csv-reader/1'

BEIJING = timezone(timedelta(hours=8))
DURATION = re.compile(r'^duration:\s*([0-9]+(?:\.[0-9]+)?)\s+ms\b', re.I)
EXECUTE = re.compile(r'^execute( fetch from)?\s+([^:]+):\s*(.*)$', re.I | re.S)
TIMINGS = {'1946': 'request', '2219': 'parse', '2224': 'parse', '2603': 'bind', '2608': 'bind'}


def raw_bytes(text):
    return text.encode('utf-8', 'surrogateescape')


def valid_text(text):
    return '\x00' not in text and not any(0xD800 <= ord(c) <= 0xDFFF for c in text)


def timestamp(text):
    try:
        if text.endswith(' CST'):
            return datetime.fromisoformat(text[:-4]).replace(tzinfo=BEIJING)
        value = datetime.fromisoformat(text)
        if value.tzinfo is None:
            raise ValueError()
        return value.astimezone(BEIJING)
    except (ValueError, OverflowError):
        return None


def session(row):
    # Source identity is supplied by the enclosing importer, file boundaries reset.
    fields = (row[7], row[3], row[9], row[2], row[1])
    return fields if all(fields) and all(valid_text(x) for x in fields) else None


def site(row):
    name = row[27].rsplit('/', 1)[-1]
    if re.fullmatch(r'[a-zA-Z_][a-zA-Z_0-9]*\.(?:c|cc|cpp)', name) and row[28].isdigit():
        return name + ':' + row[28]
    return 'other'


class DigestReader(io.RawIOBase):
    def __init__(self, stream):
        self.stream, self.sha, self.used = stream, hashlib.sha256(), 0

    def readable(self):
        return True

    def readinto(self, buffer):
        size = self.stream.readinto(buffer)
        if size:
            self.sha.update(memoryview(buffer)[:size])
            self.used += size
        return size


class Records:
    def __init__(self, path):
        self.path, self.count, self.first, self.last = path, 0, [], deque(maxlen=32)
        self.sha256 = self.byte_count = None

    def __iter__(self):
        csv.field_size_limit(128 * 1024 * 1024)
        before = self.path.stat()
        previous_line = 0
        try:
            with self.path.open('rb', buffering=0) as raw:
                hashing = DigestReader(raw)
                with io.TextIOWrapper(io.BufferedReader(hashing, 1024 * 1024),
                                      encoding='utf-8', errors='surrogateescape', newline='') as stream:
                    # Python 3.9 csv rejects NUL even inside a bounded quoted field.
                    # DC00 cannot arise from UTF-8+surrogateescape (DC80..DCFF), so
                    # this reversible transport sentinel preserves original bytes.
                    reader = csv.reader((line.replace('\x00', '\udc00') for line in stream), strict=True)
                    for number, row in enumerate(reader, 1):
                        row = [value.replace('\udc00', '\x00') for value in row]
                        if len(row) != 30:
                            raise IngestionError('csv_columns')
                        start, previous_line = previous_line + 1, reader.line_num
                        self.count = number
                        digest = hashlib.sha256(canonical(row).encode('ascii')).hexdigest()
                        edge = (digest, row[0], bool(session(row)))
                        if len(self.first) < 32:
                            self.first.append(edge)
                        self.last.append(edge)
                        yield number, start, reader.line_num, row
                self.sha256, self.byte_count = hashing.sha.hexdigest(), hashing.used
        except (csv.Error, UnicodeError):
            raise IngestionError('csv_boundary') from None
        after = self.path.stat()
        if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
            raise IngestionError('file_changed_during_read')

    def edge_pairs(self):
        pairs = set()
        for edges in (self.first, list(self.last)):
            for left, right in zip(edges, edges[1:]):
                if left[2] and right[2] and left[1] != right[1]:
                    pairs.add(hashlib.sha256((left[0] + right[0]).encode('ascii')).hexdigest())
        return sorted(pairs)


class Interpreter:
    """Consume each anchor once. No temporal-nearest or command-number matching."""
    def __init__(self, max_sessions=100000, profile="mpp-csv/1"):
        if profile != "mpp-csv/1":
            raise ValueError("unsupported_profile")
        self.pending = OrderedDict()
        self.max_sessions = max_sessions

    def interpret(self, row, record_id):
        origin, key = site(row), session(row)
        message, sql = row[18], row[24]
        when = timestamp(row[0])
        duration_match = DURATION.match(message)
        if origin == 'postgres.c:2764':
            match = EXECUTE.match(message)
            if key:
                prior = self.pending.pop(key, None)
                entry = dict(ref=record_id, sql=sql, at=when, ambiguous=prior is not None,
                             kind='execute_fetch' if match and match[1] else 'execute_first')
                entry['valid'] = bool(match and sql and match[3] == sql.lstrip() and when)
                self.pending[key] = entry
                while len(self.pending) > self.max_sessions:
                    self.pending.popitem(last=False)
            return None
        previous = None
        boundary = (row[16] in ('ERROR', 'FATAL', 'PANIC') or
                    origin.startswith('postgres.c:') and
                    (row[28] in TIMINGS or row[28] == '2843' or
                     re.match(r'^(?:statement:|parse\s|bind\s)', message, re.I)))
        if key and boundary:
            previous = self.pending.pop(key, None)
        # Internal optimizer/statistics logs do not end a protocol call.
        if not duration_match and not (origin.startswith('postgres.c:') and (row[28] in TIMINGS or row[28] == '2843')
                                       and message.lower().startswith('duration:')):
            if row[16] == 'ERROR' and row[11] == 'seg-1' and sql.strip():
                outcome = 'failed'
                if row[17] == '57014':
                    if 'canceling statement due to user request' in message:
                        outcome = 'cancelled'
                    elif 'canceling statement due to statement timeout' in message:
                        outcome = 'timed_out'
                return dict(unit='request', timing=None, timing_reason='error_without_timing',
                            outcome=outcome, association='reliable', association_reason=None,
                            end=when, duration=None, start=None, support=None, problem=None)
            return None
        if row[11] != 'seg-1' or row[16] != 'LOG' or origin.split(':')[0] != 'postgres.c':
            return None
        line = row[28]
        if line not in TIMINGS and line != '2843':
            return None
        value = Decimal(duration_match[1]) if duration_match else None
        try:
            start = when - timedelta(microseconds=int(value * 1000)) if when is not None and value is not None else None
            # PG timestamps have microsecond resolution; never silently round.
            if value is not None and value * 1000 != (value * 1000).to_integral_value():
                raise ValueError()
        except (ValueError, OverflowError, InvalidOperation):
            value, start = None, None
        result = dict(unit='request' if line == '1946' else 'call', timing=TIMINGS.get(line),
                      timing_reason=None, outcome='success' if value is not None else 'unknown',
                      association='reliable', association_reason=None, end=when,
                      duration=value, start=start, support=None, problem=None)
        if line == '2843':
            reason = None
            if not key:
                reason = 'session_identity_missing'
            elif previous is None:
                reason = 'execute_start_missing'
            elif previous['ambiguous']:
                reason = 'execute_start_ambiguous'
            elif not previous['valid'] or previous['sql'] != sql:
                reason = 'execute_context_mismatch'
            elif when is None or previous['at'] > when:
                reason = 'execute_order_invalid'
            if reason:
                result.update(timing_reason=reason, association='ambiguous' if reason == 'execute_start_ambiguous' else 'unpaired',
                              association_reason=reason, problem=reason)
            else:
                result.update(timing=previous['kind'], support=previous['ref'])
        return result


def record_metrics(row):
    counts = {'records': 1}
    duration = bool(DURATION.match(row[18]))
    if duration:
        counts['duration_with_sql' if row[24].strip() else 'duration_without_sql'] = 1
        counts['duration:' + site(row)] = 1
    return counts, duration
