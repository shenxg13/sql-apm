"""What a receiving directory holds, by day. Reads names and sizes only; never file contents."""
from datetime import date
import os
import re

LOG = re.compile(r'gpdb-(\d{4}-\d{2}-\d{2})_\d{6}\.csv(?:\.\d+)?')
MARKER = re.compile(r'(\d{4}-\d{2}-\d{2})\.complete')
OTHER_LIMIT = 50


def marker_name(day):
    return day.isoformat() + '.complete'


def batch_id(source_id, day):
    """The same source and day always give the same batch."""
    return 'daily:' + source_id + ':' + day.isoformat()


def _day(pattern, name):
    match = pattern.fullmatch(name)
    if not match:
        return None
    try:
        return date.fromisoformat(match.group(1))
    except ValueError:
        return None


def scan(directory):
    """Regular files directly inside the directory: log files by day, markers, and the rest.

    Subdirectories are not looked into. A symbolic link is never followed: it is listed
    with the files whose names do not fit, so nothing outside the directory is imported
    or deleted through it.
    """
    days, markers, other = {}, set(), []
    with os.scandir(directory) as entries:
        for entry in entries:
            if entry.is_dir(follow_symlinks=False):
                continue
            regular = entry.is_file(follow_symlinks=False)
            marked, logged = _day(MARKER, entry.name), _day(LOG, entry.name)
            if regular and marked:
                markers.add(marked)
            elif regular and logged:
                days.setdefault(logged, []).append((entry.name, entry.stat(follow_symlinks=False).st_size))
            else:
                other.append(entry.name.encode('utf-8', 'backslashreplace').decode('utf-8')[:255])
    return dict(days={day: sorted(files) for day, files in days.items()}, markers=markers, other=sorted(other))


def log_day(name):
    """The day a log file name belongs to; None when the name is not one."""
    return _day(LOG, name)


def classify(found, today, states):
    """Sort the days of one receiving directory.

    `states` gives the day batch's state where one exists. Returns the days to import
    (oldest first), the marked days that are already imported (whether their files are
    still what was imported is for the caller to find out), and the problems as
    (kind, day, reason, file_count).
    """
    pending, imported, problems = [], [], []
    for day in sorted(set(found['days']) | found['markers']):
        files, marked = found['days'].get(day, []), day in found['markers']
        if marked and day >= today:
            problems.append(('marker_not_before_today', day, None, len(files)))
        elif not marked:
            problems.append(('files_without_marker', day, None, len(files)))
        elif states.get(day) == 'complete':
            imported.append(day)
        elif files:
            pending.append(day)
        else:
            problems.append(('marker_without_files', day, None, 0))
    return pending, imported, problems
