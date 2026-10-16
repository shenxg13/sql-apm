#!/usr/bin/env python3
"""Receive completed platform pushes. Only names/metadata and manifests are read.

Linux directory descriptors and RENAME_NOREPLACE keep operations inside the opened
directories and prevent overwrites. No log contents or database connections.
"""
import argparse
from contextlib import contextmanager
import ctypes
from datetime import date, timedelta
import errno
import fcntl
import os
from pathlib import Path
import re
import stat
import time

MARKER = 'push.complete'
TEMP = MARKER + '.tmp'
LOG = re.compile(r'gpdb-(\d{4}-\d{2}-\d{2})_\d{6}\.csv(?:\.\d+)?', re.ASCII)
WORD = re.compile(r'[A-Za-z0-9._-]+', re.ASCII)
DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
LIBC = ctypes.CDLL(None, use_errno=True)


class Invalid(ValueError):
    pass


def say(**fields):
    # Escape whitespace in paths; each message remains a single key=value line.
    print('intake-logs: ' + ' '.join('%s=%s' % (k, str(v).encode('unicode_escape').decode('ascii').replace(' ', '\\x20'))
                                   for k, v in fields.items()), flush=True)


def log_day(name):
    found = LOG.fullmatch(name)
    if found:
        try:
            return date.fromisoformat(found[1])
        except ValueError:
            pass
    return None


def entries(fd):
    return {name: os.stat(name, dir_fd=fd, follow_symlinks=False) for name in os.listdir(fd)}


def regular(info):
    return info is not None and stat.S_ISREG(info.st_mode)


def matches(info, size):
    return regular(info) and info.st_size == size


@contextmanager
def descend(root, parts):
    fd = os.dup(root)
    try:
        for name in parts:
            child = os.open(name, DIRECTORY, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


def read_marker(fd):
    child = os.open(MARKER, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    with os.fdopen(child, 'rb') as stream:
        if not regular(os.fstat(stream.fileno())):
            raise Invalid('marker_not_regular')
        raw = stream.read(8 * 1024 * 1024 + 1)
    if len(raw) > 8 * 1024 * 1024:
        raise Invalid('marker_too_large')
    return raw.decode('ascii')


def manifest(text):
    lines = [line.split('\t') for line in text.splitlines()]
    if len(lines) < 4 or lines[0] != ['SQL_APM_PUSH', '1']:
        raise Invalid('invalid_format')
    if any(len(line) != 2 or line[0] != key for line, key in zip(lines[1:4], ('host', 'today', 'days'))):
        raise Invalid('invalid_header')
    host, day, days = (line[1] for line in lines[1:4])
    if not WORD.fullmatch(host) or not re.fullmatch(r'[1-9][0-9]{0,2}', days) or int(days) > 366:
        raise Invalid('invalid_header')
    try:
        today = date.fromisoformat(day)
        first = today - timedelta(days=int(days))
    except ValueError as error:
        raise Invalid('invalid_date') from error
    if today.isoformat() != day:
        raise Invalid('invalid_date')
    files = {}
    for line in lines[4:]:
        if len(line) != 3 or line[0] != 'file' or not re.fullmatch(r'0|[1-9][0-9]{0,18}', line[2]):
            raise Invalid('invalid_file_entry')
        name, size = line[1:]
        day = log_day(name)
        if day is None or not first <= day < today or name in files:
            raise Invalid('invalid_file_entry')
        files[name] = int(size)
    return host, today, files


def config(path):
    landing, expiry, sources = None, 24, {}
    seen_expiry = False
    names = set()
    for number, raw in enumerate(path.read_text().splitlines(), 1):
        words = raw.split()
        if not words or words[0].startswith('#'):
            continue
        if words[0] == 'landing' and len(words) == 2 and landing is None:
            landing = Path(words[1])
        elif words[0] == 'expire_hours' and len(words) == 2 and not seen_expiry:
            if not re.fullmatch(r'[1-9][0-9]{0,3}', words[1]):
                raise Invalid('expire_hours_must_be_1_to_9999')
            expiry, seen_expiry = int(words[1]), True
        elif words[0] == 'source' and len(words) == 4:
            _, name, host, inbox = words
            if not WORD.fullmatch(name) or not WORD.fullmatch(host) or name in names or host in sources:
                raise Invalid('duplicate_or_invalid_source')
            names.add(name)
            sources[host] = (name, Path(inbox))
        else:
            raise Invalid('invalid_config_line_%d' % number)
    if landing is None or not sources:
        raise Invalid('landing_and_sources_required')
    paths = [landing] + [value[1] for value in sources.values()]
    if any(not p.is_absolute() or not p.is_dir() or not os.access(p, os.W_OK | os.X_OK) for p in paths):
        raise Invalid('directories_must_exist_and_be_writable_absolute_paths')
    paths = [p.resolve(strict=True) for p in paths]
    for i, left in enumerate(paths):
        for right in paths[i + 1:]:
            if left == right or left in right.parents or right in left.parents:
                raise Invalid('directories_overlap')
            if left.stat().st_dev != right.stat().st_dev:
                raise Invalid('directories_on_different_filesystems')
    resolved = {host: (sources[host][0], path) for host, path in zip(sources, paths[1:])}
    return paths[0], resolved, expiry * 3600


def find_pushes(root):
    """Walk real directories only. A formal marker makes its directory one push."""
    found = []
    failed = False

    def walk(fd, parts):
        nonlocal failed
        listing = entries(fd)
        subdirs = [name for name, info in listing.items() if stat.S_ISDIR(info.st_mode)]
        if MARKER in listing or (not subdirs and (parts or listing)):
            text, parsed, error = '', None, 'missing_marker'
            if MARKER in listing:
                try:
                    text = read_marker(fd)
                    parsed = manifest(text)
                    error = None
                except (OSError, ValueError) as exc:
                    error = str(exc) if isinstance(exc, Invalid) else 'unreadable_marker'
            # Even a malformed marker that names an unknown host is retained.
            hosts = re.findall(r'^host\t([^\r\n]+)$', text, re.MULTILINE)
            found.append((parts, parsed, error, hosts))
            return
        for name, info in sorted(listing.items()):
            if stat.S_ISLNK(info.st_mode):
                say(push='/'.join(parts + (name,)), state='failed', reason='symlink_not_followed')
                failed = True
        for name in sorted(subdirs):
            try:
                with descend(fd, (name,)) as child:
                    if os.fstat(child).st_dev != os.fstat(root).st_dev:
                        raise Invalid('nested_filesystem')
                    walk(child, parts + (name,))
            except (OSError, Invalid) as exc:
                say(push='/'.join(parts + (name,)), state='failed', reason=type(exc).__name__)
                failed = True

    walk(root, ())
    found.sort(key=lambda item: (item[1][1] if item[1] else date.min, item[0]))
    return found, failed


def validate(files, pushed, received):
    for name, size in files.items():
        if name in pushed:
            if not matches(pushed[name], size):
                raise Invalid('file_size_or_type_mismatch:' + name)
        elif not matches(received.get(name), size):
            # A successful rename removes its source. This is the sole recovery
            # exception to "every manifest file is in the push directory".
            raise Invalid('missing_file:' + name)
    if any(LOG.fullmatch(name) and name not in files for name in pushed):
        raise Invalid('extra_log_file')


def rename_new(src, dst, name):
    rename = LIBC.renameat2
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    encoded = os.fsencode(name)
    if rename(src, encoded, dst, encoded, 1) != 0:  # RENAME_NOREPLACE
        number = ctypes.get_errno()
        raise OSError(number, os.strerror(number), name)


def receive(push, inbox, files, today, label, source):
    all_done = True
    for day in sorted({log_day(name) for name in files}):
        selected = {name: size for name, size in files.items() if log_day(name) == day}
        try:
            if day >= today:
                raise Invalid('date_not_before_today')
            existing = entries(inbox)
            marker = day.isoformat() + '.complete'
            if marker in existing:
                if not regular(existing[marker]):
                    raise Invalid('local_marker_not_regular')
                differing = sum(name in existing and existing[name].st_size != size for name, size in selected.items())
                say(push=label, source=source, date=day, state='already_complete', size_differences=differing)
                continue
            for name, info in existing.items():
                if log_day(name) == day and (name not in selected or not matches(info, selected[name])):
                    raise Invalid('local_day_conflict:' + name)
            # Check the whole day before its first move. renameat2 also refuses a
            # destination that appears between this check and the actual rename.
            for name in sorted(selected):
                if name not in existing:
                    info = os.stat(name, dir_fd=push, follow_symlinks=False)
                    if not matches(info, selected[name]):
                        raise Invalid('file_changed:' + name)
                    rename_new(push, inbox, name)
            current = entries(inbox)
            current_day = {name: info for name, info in current.items() if log_day(name) == day}
            if current_day.keys() != selected.keys() or any(not matches(current_day[name], size) for name, size in selected.items()):
                raise Invalid('local_day_changed')
            # Empty marker: exclusive creation is already atomic; no temp in inbox.
            fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=inbox)
            os.close(fd)
            say(push=label, source=source, date=day, state='complete', files=len(selected), bytes=sum(selected.values()))
        except (OSError, Invalid) as exc:
            say(push=label, source=source, date=day, state='failed', reason=str(exc))
            all_done = False
    return all_done


def clean(root, parts, push):
    listing = entries(push)
    # Never recursive-delete: any unrelated object (including a symlink, nested
    # directory or special file) retains the entire directory for the operator.
    if any(not regular(info) or not (LOG.fullmatch(name) or name in (MARKER, TEMP)) for name, info in listing.items()):
        raise Invalid('cleanup_unexpected_content')
    for name in sorted(listing, key=lambda name: name == MARKER):
        os.unlink(name, dir_fd=push)
    # Parents are removed only if empty. The landing directory is never removed.
    while parts:
        with descend(root, parts[:-1]) as parent:
            try:
                os.rmdir(parts[-1], dir_fd=parent)
            except OSError as exc:
                if exc.errno in (errno.ENOTEMPTY, errno.EEXIST):
                    break
                raise
        parts = parts[:-1]


def intake(root, sources, expiry, today=None, now=None):
    today = today or date.today()
    now = time.time() if now is None else now
    pushes, failed = find_pushes(root)
    for parts, parsed, error, hosts in pushes:
        label = '/'.join(parts) or '.'
        try:
            with descend(root, parts) as push:
                if any(host not in sources for host in hosts):
                    say(push=label, state='failed', reason='unknown_host')
                    failed = True
                    continue
                pushed = entries(push)
                # ctime is local arrival/change time; scp -p preserves mtime, not
                # ctime. Use the newest file, or directory ctime for an empty leaf.
                arrival = max((info.st_ctime for info in pushed.values()), default=os.fstat(push).st_ctime)
                done = False
                if parsed:
                    host, _, files = parsed
                    if host not in sources:
                        raise Invalid('unknown_host')
                    source, receiving = sources[host]
                    inbox = os.open(receiving, DIRECTORY)
                    try:
                        try:
                            validate(files, pushed, entries(inbox))
                            done = receive(push, inbox, files, today, label, source)
                            if not done:
                                error = 'dates_not_received'
                        except Invalid as exc:
                            error = str(exc)
                    finally:
                        os.close(inbox)
                if error:
                    say(push=label, state='failed', reason=error)
                    failed = True
                if done or now - arrival > expiry:
                    clean(root, parts, push)
                    say(push=label, state='removed', reason='processed' if done else 'expired')
                else:
                    say(push=label, state='retained')
        except (OSError, Invalid) as exc:
            say(push=label, state='failed', reason=str(exc))
            failed = True
    return int(failed)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, type=Path)
    args = parser.parse_args()
    try:
        landing, sources, expiry = config(args.config)
        if not hasattr(LIBC, 'renameat2'):
            raise Invalid('linux_renameat2_required')
    except (OSError, ValueError) as exc:
        say(state='failed', reason=str(exc))
        return 2
    try:
        root = os.open(landing, DIRECTORY)
        try:
            try:
                fcntl.flock(root, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                say(state='failed', reason='another_intake_is_running')
                return 1
            return intake(root, sources, expiry)
        finally:
            os.close(root)
    except (OSError, ValueError) as exc:
        say(state='failed', reason=str(exc))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
