"""Deterministic synthetic logs for the dashboard checks; no real SQL, names or hosts."""
from datetime import datetime, timedelta, timezone
import json
import random

from ingestion.test_reader import row, write_csv

TZ = timezone(timedelta(hours=8))
FIRST_DAY = datetime(2026, 6, 26, tzinfo=TZ)
DAYS = 28
CUTOFF = '2026-07-23'
BUSY = "SELECT o.id, o.amount FROM orders o WHERE o.status = {status} AND o.created_at >= '{day}'"
EXTENDED = 'SELECT p.id, p.name, p.price FROM products p WHERE p.category_id = $1'
SPARSE = "SELECT c.name FROM customers c WHERE c.region = 'north'"
MEDIUM = 'SELECT count(*) FROM audit_log WHERE level = 2'
FAILING = 'SELECT a FROM failure_only'
QUOTED = 'SELECT "Id", "a"."b" FROM "Order Items" WHERE "Id" = 7'
BATCH = 'SELECT 1 AS one; SELECT 2 AS two'
SPECIAL = "SELECT '中文 备注', E'tab\\tline\\n', '$name', \"quoted col\" FROM special_chars WHERE note = 'it''s'"
LONG = 'SELECT id FROM big_list WHERE id IN (' + ', '.join(str(1000000 + n) for n in range(7600)) + ')'
EXCLUSION = dict(id='E1', cluster='C1', start='2026-07-10T02:00:00+08:00', end='2026-07-10T03:00:00+08:00',
                 reason='synthetic maintenance')


def stamp(moment):
    return moment.strftime('%Y-%m-%d %H:%M:%S.%f') + ' CST'


class Session:
    """One connection: a fresh command number for every statement."""
    def __init__(self, number, user, database):
        self.fields = {'1': user, '2': database, '3': 'p' + str(1000 + number),
                       '7': '2026-06-25 20:00:00 CST', '9': 'con' + str(number)}
        self.command = 0

    def line(self, moment, code, message, text, **extra):
        return moment, row(code, message, text, **dict(self.fields, **{'0': stamp(moment), '10': 'cmd' + str(self.command)}, **extra))

    def request(self, moment, text, duration=None, failure=None):
        self.command += 1
        if failure == 'failed':
            return [self.line(moment, '1946', 'synthetic failure', text, **{'16': 'ERROR', '17': '22012'})]
        if failure == 'cancelled':
            return [self.line(moment, '1946', 'canceling statement due to user request', text, **{'16': 'ERROR', '17': '57014'})]
        if failure == 'timed_out':
            return [self.line(moment, '1946', 'canceling statement due to statement timeout', text, **{'16': 'ERROR', '17': '57014'})]
        return [self.line(moment, '1946', 'duration: %.3f ms' % duration, text)]

    def extended(self, moment, text, duration, fetch=None):
        self.command += 1
        step = timedelta(milliseconds=1)
        lines = [self.line(moment, '2219', 'duration: 0.210 ms', text),
                 self.line(moment + step, '2603', 'duration: 0.340 ms', text),
                 self.line(moment + 2 * step, '2764', 'execute p: ' + text, text),
                 self.line(moment + 2 * step + timedelta(milliseconds=duration), '2843', 'duration: %.3f ms' % duration, text)]
        if fetch is not None:
            later = moment + timedelta(milliseconds=duration + 20)
            lines += [self.line(later, '2764', 'execute fetch from p: ' + text, text),
                      self.line(later + timedelta(milliseconds=fetch), '2843', 'duration: %.3f ms' % fetch, text)]
        return lines

    def orphan(self, moment, text):
        self.command += 1
        return [self.line(moment, '2843', 'duration: 5.000 ms', text)]


def events():
    rng = random.Random(51)
    lines = []
    app, report = Session(1, 'app_user', 'shop'), Session(2, 'report_user', 'shop')
    other, extended = Session(3, 'app_user', 'crm'), Session(4, 'app_user', 'shop')
    days = [(FIRST_DAY + timedelta(days=n)) for n in range(DAYS)]
    literal_days = [(FIRST_DAY + timedelta(days=n)).strftime('%Y-%m-%d') for n in range(16)]
    for index, day in enumerate(days):
        for hour in range(8, 20):
            busy = 90 if hour in (10, 11, 14, 15) else 45
            for _ in range(busy):
                moment = day + timedelta(hours=hour, seconds=rng.uniform(0, 3600))
                status = rng.choice((1, 1, 2, 2, 3, 4, 5))
                text = BUSY.format(status=status, day=rng.choice(literal_days))
                duration = rng.lognormvariate(4.8, 0.35) * (2.8 if status == 3 else 1)
                if index >= DAYS - 2 and 14 <= hour < 18:
                    duration *= 3
                draw = rng.random()
                failure = 'failed' if draw < 0.004 else 'cancelled' if draw < 0.006 else 'timed_out' if draw < 0.007 else None
                session = report if rng.random() < 0.05 else app
                lines += session.request(moment, text, duration, failure)
        for _ in range(87 if index < DAYS - 1 else 91):
            moment = day + timedelta(hours=9, seconds=rng.uniform(0, 36000))
            lines += extended.extended(moment, EXTENDED, rng.lognormvariate(3.2, 0.3),
                                       rng.lognormvariate(2.0, 0.2) if rng.random() < 0.17 else None)
        lines += other.request(day + timedelta(hours=11, minutes=index), SPARSE, rng.lognormvariate(5.5, 0.2))
        if index >= DAYS - 12:
            for _ in range(26):
                lines += other.request(day + timedelta(hours=13, seconds=rng.uniform(0, 7200)), MEDIUM, rng.lognormvariate(4.0, 0.5))
        lines += app.request(day + timedelta(hours=12, minutes=3), FAILING, failure='failed')
        lines += app.request(day + timedelta(hours=12, minutes=7), QUOTED, rng.lognormvariate(3.0, 0.2))
        lines += app.request(day + timedelta(hours=12, minutes=9), BATCH, rng.lognormvariate(3.0, 0.2))
        lines += app.request(day + timedelta(hours=12, minutes=11), SPECIAL, rng.lognormvariate(3.0, 0.2))
        if index % 6 == 0:
            lines += app.request(day + timedelta(hours=12, minutes=13), LONG, rng.lognormvariate(6.0, 0.2))
            lines += extended.orphan(day + timedelta(hours=12, minutes=15), EXTENDED)
    # Sixty one-off structures, to show the cap of fifty candidates and the total.
    for n in range(60):
        lines += other.request(days[-1] + timedelta(hours=7, minutes=n), 'SELECT v FROM wide_%02d' % n, rng.lognormvariate(3.0, 0.2))
    # Executions inside the excluded hour, for the exclusion reasons shown on the page.
    for n in range(40):
        lines += app.request(datetime(2026, 7, 10, 2, 5, n, tzinfo=TZ), BUSY.format(status=1, day=literal_days[0]), 100 + n)
    lines.sort(key=lambda item: item[0])
    return [values for _, values in lines]


def second_cluster():
    rng = random.Random(52)
    session, lines = Session(9, 'app_user', 'shop'), []
    for index in range(10):
        day = FIRST_DAY + timedelta(days=DAYS - 10 + index)
        for _ in range(30):
            lines += session.request(day + timedelta(hours=10, seconds=rng.uniform(0, 3600)),
                                     BUSY.format(status=2, day='2026-06-26'), rng.lognormvariate(4.8, 0.3))
        lines += session.request(day + timedelta(hours=11), SPARSE, rng.lognormvariate(5.5, 0.2))
    lines.sort(key=lambda item: item[0])
    return [values for _, values in lines]


def write(directory):
    """Write both clusters' files and the import configuration; return its path."""
    first, second, path = directory / 'c1.csv', directory / 'c2.csv', directory / 'import.json'
    write_csv(first, events())
    write_csv(second, second_cluster())
    declaration = dict(build='HashData Warehouse 3.13.13', timezone='UTC+08:00', declaration='synthetic')
    dates = [(FIRST_DAY + timedelta(days=n)).strftime('%Y-%m-%d') for n in range(DAYS)]
    path.write_text(json.dumps({'version': 1, 'clusters': ['C1', 'C2'],
        'sources': {'S1': dict(declaration, cluster='C1'), 'S2': dict(declaration, cluster='C2')},
        'batches': {'B1': {'source': 'S1', 'files_confirmed_complete': True, 'dates': dates,
                           'files': [{'path': str(first), 'closed_and_copied': True}]},
                    'B2': {'source': 'S2', 'files_confirmed_complete': True, 'dates': dates[-10:],
                           'files': [{'path': str(second), 'closed_and_copied': True}]}}}))
    return path


def training(cutoff=CUTOFF, days=DAYS):
    return dict(version=1, clusters=['C1', 'C2'], window=dict(cutoff_date=cutoff, days=days), exclusions=[EXCLUSION])


def load(dsn, directory, schema='sql_apm'):
    """Import both clusters and publish: an earlier and the current version for C1, one for C2."""
    from sql_apm.baseline.workflow import run
    from sql_apm.ingestion.config import load_config
    from sql_apm.training.config import validate
    path = write(directory)
    older = run(dsn, schema, validate(training('2026-07-16', 21), 'C1'), load_config(path, 'S1', 'B1'), workers=1)
    current = run(dsn, schema, validate(training(), 'C1'), workers=1)
    second = run(dsn, schema, validate(training(), 'C2'), load_config(path, 'S2', 'B2'), workers=1)
    return dict(older=older['build']['build_id'], current=current['build']['build_id'],
                second=second['build']['build_id'])
