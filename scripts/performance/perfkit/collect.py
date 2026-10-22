"""Bounded host reads and database catalogs; no external monitoring dependency."""
from datetime import datetime
import json
import os
from pathlib import Path
import platform
import shutil
import socket
import threading
import time

from .core import save,serializable


STATISTICS = {
    'database': 'SELECT * FROM pg_stat_database WHERE datname=current_database()',
    'tables': 'SELECT * FROM pg_stat_user_tables ORDER BY relid',
    'indexes': 'SELECT s.*,pg_relation_size(indexrelid) bytes FROM pg_stat_user_indexes s ORDER BY indexrelid',
    'checkpointer': 'SELECT * FROM pg_stat_checkpointer',
    'bgwriter': 'SELECT * FROM pg_stat_bgwriter',
    'wal': 'SELECT * FROM pg_stat_wal',
    'io': 'SELECT * FROM pg_stat_io ORDER BY backend_type,object,context',
    'table_io': 'SELECT * FROM pg_statio_user_tables ORDER BY relid',
    'index_io': 'SELECT * FROM pg_statio_user_indexes ORDER BY indexrelid',
}


def health(session):
    result = {}
    for name, statement in STATISTICS.items():
        try:
            rows = session.query(statement)
            result[name] = [{k: v for k, v in row.items() if k not in ('datname', 'datid', 'schemaname')}
                            for row in rows]
        except Exception as error:
            # No raw errors: messages can include addresses, roles and query values.
            if not hasattr(error, 'pgcode'):
                raise
            result[name] = {'unavailable': error.pgcode}
    return result


def delta(before, after):
    result = {}
    for name, old in before.items():
        new = after.get(name)
        if not isinstance(old, list) or not isinstance(new, list):
            result[name] = {'unavailable': True}
            continue
        keys = ('relid', 'indexrelid', 'backend_type', 'object', 'context')
        indexed = {tuple(r.get(k) for k in keys): r for r in old}
        changes = []
        for row in new:
            previous = indexed.get(tuple(row.get(k) for k in keys))
            if previous is None:
                continue
            item = {k: row[k] for k in keys if k in row}
            item['stats_reset_unchanged'] = previous.get('stats_reset') == row.get('stats_reset')
            item['values'] = {k: v-previous[k] for k, v in row.items()
                              if k not in keys and isinstance(v, (float, int))
                              and isinstance(previous.get(k), (float, int))}
            changes.append(item)
        result[name] = changes
    return result


def directory_bytes(path):
    total = 0
    try:
        if not Path(path).is_dir():
            return {'unavailable': True}
        for root, dirs, files in os.walk(path, followlinks=False, onerror=lambda e: (_ for _ in ()).throw(e)):
            for name in files:
                file = Path(root) / name
                if not file.is_symlink():
                    total += file.stat().st_size
        return {'bytes': total, 'method': 'logical_file_lengths'}
    except OSError:
        return {'unavailable': True}


def inventory(session, work, pgdata):
    total = session.query('SELECT pg_database_size(current_database()) bytes')[0]
    relations = session.query("""SELECT c.oid,c.relname,c.relkind,c.relispartition,
        c.reltuples::bigint estimated_rows,c.reltoastrelid,
        pg_relation_size(c.oid) heap_bytes,pg_table_size(c.oid) table_bytes,
        pg_indexes_size(c.oid) index_bytes,pg_total_relation_size(c.oid) total_bytes
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname IN ('sql_apm','pg_toast') AND c.relkind IN ('r','p','t','i','I')
        ORDER BY pg_total_relation_size(c.oid) DESC,c.oid""")
    partitions = session.query("""SELECT p.scope_id,p.build_month,p.cleaned_at,c.relname,
        pg_total_relation_size(c.oid) bytes FROM mpp_result_partition p
        JOIN pg_class c ON c.relnamespace='sql_apm'::regnamespace
        AND c.relname IN ('mpp_statistic_p'||p.partition_id,'mpp_observation_statistic_p'||p.partition_id)
        ORDER BY p.scope_id,p.build_month,c.relname""")
    for row in partitions:
        row['cluster'] = work.alias('cluster', row.pop('scope_id'))
    directories = {}
    for name in ('base', 'global', 'pg_wal', 'pg_xact', 'pg_multixact', 'pg_stat', 'pg_logical'):
        directories[name] = directory_bytes(Path(pgdata) / name)
    # Parent table total sizes include toast and indexes; do not add them twice.
    physical = sum(r['total_bytes'] for r in relations if r['relkind'] == 'r')
    return dict(database_bytes=total['bytes'], relations=relations, result_months=partitions,
                directories=directories, application_table_total_bytes=physical,
                residual_bytes=total['bytes']-physical,
                residual_reason='catalogs_other_schemas_and_relation_metadata; WAL_is_cluster_wide')


def settings(session):
    rows = session.query("""SELECT name,setting,unit,boot_val,source,vartype,enumvals,pending_restart
        FROM pg_settings WHERE setting IS DISTINCT FROM boot_val OR name ~
        'work_mem|buffers|parallel|checkpoint|autovacuum|page_cost|io_concurrency|jit|max_connections|wal_size|effective_cache'
        ORDER BY name""")
    for row in rows:
        # String GUCs include connection secrets, addresses and arbitrary options.
        # Keep numeric/bool/enum values; retain only the approved path settings.
        if row['vartype'] == 'string' and row['name'] not in (
                'data_directory', 'config_file', 'hba_file', 'ident_file', 'unix_socket_directories',
                'log_directory', 'timezone', 'TimeZone', 'search_path'):
            row['setting'] = row['boot_val'] = '<redacted>'
        row.pop('enumvals', None)
    build = session.query("SELECT name,setting FROM pg_config WHERE name IN ('VERSION','CONFIGURE','CC','CFLAGS')")
    return {'values': rows, 'build': build}


def read(path):
    try:
        return Path(path).read_text()
    except (OSError, UnicodeError):
        return None


def host_static(pgdata, work):
    for value in (socket.gethostname(), socket.getfqdn()):
        work.secret(value)
    try:
        for item in socket.getaddrinfo(socket.gethostname(), None):
            work.secret(item[4][0])
    except OSError:
        pass
    # Host addresses without invoking network or external commands.
    fib = read('/proc/net/fib_trie') or ''
    lines = fib.splitlines()
    import re
    for i, line in enumerate(lines):
        if '/32 host LOCAL' in line and i:
            found = re.search(r'(\d+\.\d+\.\d+\.\d+)', lines[i-1])
            if found:
                work.secret(found[1])
    for line in (read('/proc/net/if_inet6') or '').splitlines():
        import ipaddress
        work.secret(str(ipaddress.IPv6Address(int(line.split()[0], 16))))
    memory = {line.split(':')[0]: int(line.split()[1])*1024
              for line in (read('/proc/meminfo') or '').splitlines() if line.split()[1].isdigit()}
    cpu = next((line.split(':', 1)[1].strip() for line in (read('/proc/cpuinfo') or '').splitlines()
                if line.startswith('model name')), None)
    try:
        disk = shutil.disk_usage(pgdata)
        storage = dict(total_bytes=disk.total, free_bytes=disk.free)
    except OSError:
        storage = {'unavailable': True}
    mounted = []
    for line in (read('/proc/self/mountinfo') or '').splitlines():
        left, right = line.split(' - ', 1)
        parts = left.split()
        mount = parts[4].replace('\\040', ' ')
        if str(Path(pgdata).resolve()).startswith(mount.rstrip('/')+'/'):
            mounted.append((len(mount), parts[2], right.split()[0]))
    if mounted:
        _, device, filesystem = max(mounted)
        storage.update(filesystem=filesystem, device_type=(read('/sys/dev/block/'+device+'/queue/rotational') or '').strip() or 'unavailable')
    return dict(cpu_model=cpu, logical_cpus=os.cpu_count(), memory_bytes=memory,
                kernel=platform.release(), architecture=platform.machine(), storage=storage,
                transparent_hugepages=read('/sys/kernel/mm/transparent_hugepage/enabled'))


def host_sample():
    result = {'at': datetime.now().astimezone(), 'monotonic': time.monotonic()}
    for filename in ('stat', 'meminfo', 'vmstat', 'loadavg', 'diskstats'):
        raw = read('/proc/' + filename)
        if raw is None:
            result[filename] = {'unavailable': True}
            continue
        if filename == 'diskstats':
            result[filename] = [{'major': int(p[0]), 'minor': int(p[1]), 'counters': list(map(int, p[3:]))}
                                for p in (line.split() for line in raw.splitlines())]
        elif filename == 'loadavg':
            result[filename] = list(map(float, raw.split()[:3]))
        else:
            result[filename] = {p[0].rstrip(':'): [int(x) for x in p[1:] if x.isdigit()]
                                for p in (line.split() for line in raw.splitlines())}
    totals = {'postgres_rss_kib': 0, 'fingerprint_rss_kib': 0, 'unreadable_processes': 0}
    for proc in Path('/proc').glob('[0-9]*'):
        command, status = read(proc/'cmdline'), read(proc/'status')
        if command is None or status is None:
            totals['unreadable_processes'] += 1
            continue
        kind = 'fingerprint' if ('sql_apm.service.fingerprint' in command or 'fingerprint-service' in command) else 'postgres' if 'postgres' in command.split('\0')[0] else None
        if kind:
            rss = next((int(line.split()[1]) for line in status.splitlines() if line.startswith('VmRSS:')), 0)
            totals[kind+'_rss_kib'] += rss
    result['processes'] = totals
    return result


class Sampler:
    def __init__(self, path, journal):
        self.path, self.stop, self.samples = path, threading.Event(), []
        self.journal = journal
        self.error = None

    def __enter__(self):
        self.journal.write_bytes(b'')
        def sample():
            try:
                while True:
                    row=host_sample()
                    self.samples.append(row)
                    with self.journal.open('a',encoding='utf-8') as stream:
                        stream.write(json.dumps(row,default=serializable,separators=(',',':'))+'\n')
                        stream.flush()
                        os.fsync(stream.fileno())
                    if self.stop.wait(5):
                        break
            except Exception as error:
                self.error = error
        self.thread = threading.Thread(target=sample, name='host-sampler')
        self.thread.start()
        return self

    def __exit__(self, *unused):
        self.stop.set()
        self.thread.join()
        if self.error is not None:
            raise self.error
        self.samples.append(host_sample())
        save(self.path, self.samples)
