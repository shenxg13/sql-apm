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


def unchanged(files, kept):
    """Names and sizes as they were when the day was imported; names only when no size was kept."""
    if kept is None:
        return True
    if any(size is None for _, size in kept):
        return sorted(name for name, _ in files) == sorted(name for name, _ in kept)
    return sorted(files) == sorted(kept)


def classify(found, today, states, recorded):
    """Sort the days of one receiving directory.

    `states` gives the day batch's state where one exists, `recorded` the names and
    sizes kept from its successful import. Returns the days to import (oldest first),
    the days that are imported and unchanged, and the problems as
    (kind, day, reason, file_count).
    """
    pending, settled, problems = [], [], []
    for day in sorted(set(found['days']) | found['markers']):
        files, marked = found['days'].get(day, []), day in found['markers']
        if marked and day >= today:
            problems.append(('marker_not_before_today', day, None, len(files)))
        elif not marked:
            problems.append(('files_without_marker', day, None, len(files)))
        elif states.get(day) == 'complete':
            if files and not unchanged(files, recorded.get(day)):
                problems.append(('day_failed', day, 'files_changed_after_import', len(files)))
            else:
                settled.append(day)
        elif files:
            pending.append(day)
        else:
            problems.append(('marker_without_files', day, None, 0))
    return pending, settled, problems
