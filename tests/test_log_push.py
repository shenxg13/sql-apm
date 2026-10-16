"""Master-side regressions; SSH/SCP stand-ins write only into a synthetic landing.

SQL_APM_TEST_BASH selects an older Bash. SQL_APM_TEST_WORKDIR places fixtures
outside /tmp when running in an isolated mount namespace with a full /tmp.
"""
from datetime import date, timedelta
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'daily/push-logs.sh'


class PushTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=os.environ.get('SQL_APM_TEST_WORKDIR'))
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / 'source'
        self.second = self.root / 'second'
        self.landing = self.root / 'landing'
        binary = self.root / 'bin'
        for path in (self.source, self.second, self.landing, binary):
            path.mkdir()
        self.calls = self.root / 'calls'
        self.calls.write_text('')
        scripts = {
            'su': 'shift 3; exec bash -c "$1"',
            'hostname': 'echo master-test',
            'ssh': 'echo ssh >> "$TEST_CALLS"; exec bash -c "${*: -1}"',
            'scp': 'echo scp >> "$TEST_CALLS"; destination=${*: -1}; cp -p -- "${@: -2:1}" "${destination#*:}"',
        }
        for name, body in scripts.items():
            path = binary / name
            path.write_text('#!/bin/bash\n' + body + '\n')
            path.chmod(0o755)
        self.env = dict(os.environ, PATH=str(binary) + ':' + os.environ['PATH'],
                        TEST_CALLS=str(self.calls), RCLONE_DEST_PATH='task', RCLONE_BUCKET_NAME='bucket',
                        PLAN_ID='synthetic', backHomePath=str(self.landing), appUser='test',
                        uploadServerIp='127.0.0.1', pushDays='7')

    def push(self, paths):
        return subprocess.run([os.environ.get('SQL_APM_TEST_BASH', 'bash'), str(SCRIPT)],
                              env=dict(self.env, backupFilePath=paths), text=True, capture_output=True, timeout=30)

    def test_no_here_document_or_here_string(self):
        # Bash <=5.0 materializes these constructs as local temporary files.
        self.assertNotIn('<<', SCRIPT.read_text())

    def test_multiple_directories_days_and_complete_manifest(self):
        today = date.today()
        expected = {}
        for number, directory in ((1, self.source), (3, self.second), (2, self.source)):
            name = 'gpdb-%s_000000.csv' % (today - timedelta(days=number))
            content = b'x' * (number * 10)
            (directory / name).write_bytes(content)
            expected[name] = content
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns, p.stat().st_mode)
                  for directory in (self.source, self.second) for p in directory.iterdir()}
        done = self.push(' \t' + str(self.source) + '\n\t' + str(self.second) + '\n')
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        target = self.landing / 'bucket/task'
        self.assertEqual({p.name for p in target.iterdir()}, set(expected) | {'push.complete'})
        manifest = (target / 'push.complete').read_text().splitlines()
        self.assertEqual(manifest[:4], ['SQL_APM_PUSH\t1', 'host\tmaster-test', 'today\t' + str(today), 'days\t7'])
        self.assertCountEqual(manifest[4:], ['file\t%s\t%d' % (name, len(data)) for name, data in expected.items()])
        for name, data in expected.items():
            self.assertEqual((target / name).read_bytes(), data)
        summaries = [line.split()[0][5:] for line in done.stdout.splitlines() if line.startswith('date=')]
        self.assertEqual(summaries, sorted(name[5:15] for name in expected))
        self.assertIn('files=3 bytes=60', done.stdout)
        for path, original in before.items():
            self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_mode), original)

    def test_invalid_directory_fails_before_connecting_but_empty_directory_succeeds(self):
        regular = self.root / 'regular-file'
        regular.write_text('not a directory')
        for invalid in (self.root / 'missing', regular):
            for paths in ((invalid, self.source), (self.source, invalid)):
                done = self.push(' '.join(map(str, paths)))
                self.assertNotEqual(done.returncode, 0)
                self.assertIn(str(invalid), done.stdout)
                self.assertEqual(self.calls.read_text(), '')
        done = self.push(str(self.source))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(len((self.landing / 'bucket/task/push.complete').read_text().splitlines()), 4)

    def test_directory_arguments_are_literal_not_globs(self):
        pattern = str(self.root / 's*')  # Would match both existing directories if expanded.
        done = self.push(pattern)
        self.assertNotEqual(done.returncode, 0)
        self.assertIn(pattern, done.stdout)
        self.assertEqual(self.calls.read_text(), '')
        literal = self.root / 'literal*'
        literal.mkdir()
        done = self.push(str(literal))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)


if __name__ == '__main__':
    unittest.main()
