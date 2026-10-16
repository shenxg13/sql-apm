"""Reversible login configuration checks, also usable against the delivered app."""
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
import sys

ROOT = Path(os.environ.get('SQL_APM_APP_ROOT', Path(__file__).resolve().parents[2])).resolve()


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
            (app / 'bin').mkdir()
            (app / 'bin/sqlfind').write_text('# test entry\n')
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
            link = home / '.sqlfind/bin/sqlfind'
            self.assertEqual(link.resolve(), app / 'bin/sqlfind')
            saved = {p: p.read_bytes() for p in (home / '.sqlfind').iterdir() if p.is_file()}
            profile_before = profile.read_bytes()
            link.unlink()
            link.symlink_to(app / 'sqlfind')
            self.assertEqual(module.install(home, app, values), [link])
            self.assertEqual(link.resolve(), app / 'bin/sqlfind')
            self.assertEqual(module.install(home, app, values), [])
            self.assertEqual(profile.read_bytes(), profile_before)
            self.assertTrue(all(p.read_bytes() == content for p, content in saved.items()))
            # Do not overwrite an unrelated symlink or a user-created file.
            for target in (app / 'other-command', None):
                link.unlink()
                if target is None:
                    link.write_text('# user file\n')
                else:
                    link.symlink_to(target)
                with self.assertRaises(ValueError):
                    module.install(home, app, values)
                if target is None:
                    self.assertEqual(link.read_text(), '# user file\n')
                else:
                    self.assertEqual(link.readlink(), target)
            link.unlink()
            link.symlink_to(app / 'bin/sqlfind')
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
