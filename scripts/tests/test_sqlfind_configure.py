"""Developer-only login configuration checks; the setup tool is not packaged yet."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
import sys

ROOT = Path(__file__).resolve().parents[2]


class ConfigurationTests(unittest.TestCase):
    def test_install_is_idempotent_and_uninstall_restores_profile_bytes(self):
        spec = importlib.util.spec_from_file_location('sqlfind_configure', ROOT / 'scripts/sqlfind/configure.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            app = home / 'app'
            (app / '.venv/bin').mkdir(parents=True)
            (app / '.venv/bin/python').symlink_to(sys.executable)
            (app / 'sqlfind').write_text('# test entry\n')
            profile = home / '.bash_profile'
            before = b'# custom\r\nexport PGUSER=sql_apm\nexport PGPASSFILE=/private/existing'
            profile.write_bytes(before)
            profile.chmod(0o640)
            password = home / 'passfile'
            password.write_text('test:1:db:reader:SECRET')
            password.chmod(0o600)
            values = dict(host='/socket', port=1, dbname='db', user='reader', passfile=str(password), schema='sql_apm')
            changed = module.install(home, app, values)
            self.assertTrue(all(p.is_relative_to(home) for p in changed))
            self.assertEqual(module.install(home, app, values), [])
            self.assertEqual(profile.stat().st_mode & 0o777, 0o640)
            for path in (home / '.sqlfind').iterdir():
                if path.is_file():
                    self.assertNotIn('SECRET', path.read_text())
            module.uninstall(home)
            self.assertEqual(profile.read_bytes(), before)
            self.assertFalse((home / '.sqlfind').exists())
            self.assertEqual(password.read_text(), 'test:1:db:reader:SECRET')
            profile.unlink()
            module.install(home, app, values)
            module.uninstall(home)
            self.assertFalse(profile.exists())


if __name__ == '__main__':
    unittest.main()
