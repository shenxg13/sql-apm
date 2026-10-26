#!/usr/bin/env python3
"""Upgrade admission and recovery regression without touching a real installation."""
import copy
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts/deployment'))
import upgrade
from verify_package import digest


def package(path, version):
    path.mkdir()
    files = {'requirements.txt': 'same dependencies\n', 'VERSION': version+'\n',
             'PROGRAM_COMMIT': ('a' if version=='v1.1.0' else 'b')*40+'\n'}
    for dashboard in ('mpp-list','mpp-detail','mpp-search','mpp-status'):
        files['grafana/dashboards/'+dashboard+'.json'] = json.dumps(dict(version=version))
    for name, text in files.items():
        target = path/name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    release = dict(version=version,kind='release',commit=files['PROGRAM_COMMIT'].strip(),
                   files={name:digest(path/name) for name in files})
    (path/'RELEASE.json').write_text(json.dumps(release))
    (path/'SHA256SUMS').write_text(''.join(digest(p)+'  '+str(p.relative_to(path))+'\n'
        for p in sorted(path.rglob('*')) if p.is_file()))


def verification(path, commit):
    path.mkdir()
    (path/'PROGRAM_COMMIT').write_text(commit+'\n')
    (path/'SHA256SUMS').write_text(digest(path/'PROGRAM_COMMIT')+'  PROGRAM_COMMIT\n')


def snapshot(path):
    return {str(p.relative_to(path)):p.read_bytes() for p in path.rglob('*') if p.is_file()}


class Fixture(upgrade.Upgrade):
    def __init__(self, *args):
        super().__init__(*args)
        self.version='1.12.0'
        self.busy=False
        self.migration_failure=False
        self.external_busy=False
        self.operations=[]
        self.service_states={unit:dict(LoadState='loaded',ActiveState='active' if 'grafana' in unit or 'fingerprint' in unit
                                      else 'inactive',UnitFileState='enabled' if 'grafana' in unit else 'disabled')
                             for unit in upgrade.SERVICES}

    def services(self):
        return copy.deepcopy(self.service_states)

    def command(self, name, args):
        self.operations.append(name)
        if name.startswith('stop-'):
            self.service_states[args[-1]]['ActiveState']='inactive'
        if name.startswith('start-'):
            self.service_states[args[-1]]['ActiveState']='active'
        return ''

    def query(self, sql):
        if 'SELECT version' in sql:
            return [(self.version,)]
        if 'sum(c.row_count)' in sql:
            return [(500,)]
        if 'pg_stat_activity' in sql:
            return [(1,)] if self.external_busy else []
        if 'FROM task' in sql:
            return [(1,)] if self.busy else []
        return []

    def initialize(self, mode, app):
        self.operations.append('initialize-'+mode)
        if mode=='upgrade':
            if self.migration_failure:
                raise ValueError('injected migration rollback')
            self.version='1.13.0'


class UpgradeTests(unittest.TestCase):
    def test_libpq_environment_cannot_redirect_receipt_connection(self):
        inherited=dict(PGHOSTADDR='192.0.2.1',PGSERVICE='unrelated',
                       PGOPTIONS='-c search_path=unrelated',PGPASSWORD='test-only')
        def connect(**kwargs):
            self.assertFalse(any(k.startswith('PG') for k in os.environ))
            self.assertEqual(kwargs['host'],str(self.root/'socket'))
            self.assertEqual(kwargs['passfile'],str(self.root/'private/pgpass'))
            raise RuntimeError('connection refused')
        with patch.dict(os.environ,inherited),patch('psycopg2.connect',side_effect=connect):
            with self.assertRaisesRegex(RuntimeError,'connection refused'):
                upgrade.Upgrade.query(self.subject,'SELECT 1')
            self.assertEqual({k:os.environ[k] for k in inherited},inherited)

    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base=Path(self.temporary.name)
        self.root=self.base/'installed';self.root.mkdir()
        for directory in ('pgdata/pg_wal','pgdata/pg_tblspc','socket','private','grafana/dashboards/mpp'):
            (self.root/directory).mkdir(parents=True,exist_ok=True)
        (self.root/'pgdata/postmaster.pid').write_text('123\n')
        (self.root/'socket/.s.PGSQL.5432.lock').write_text('123\n')
        (self.root/'runtime-manifest.json').write_text('{}')
        package(self.root/'app','v1.1.0')
        (self.root/'app/.venv').mkdir()
        (self.root/'app/.venv/retained').write_text('runtime bytes')
        verification(self.root/'verification','a'*40)
        package(self.base/'candidate','v1.2.0')
        verification(self.base/'verification','b'*40)
        self.subject=Fixture(self.root,self.base/'candidate',self.base/'verification')
        self.addCleanup(patch.stopall)
        patch.object(upgrade,'verify_installed',return_value={}).start()
        patch.object(upgrade,'digest',side_effect=lambda p:upgrade.RUNTIME_MANIFEST_SHA if Path(p).name=='runtime-manifest.json' else digest(p)).start()
        self.disk=patch.object(upgrade.shutil,'disk_usage',return_value=shutil._ntuple_diskusage(100_000_000_000,0,100_000_000_000)).start()

    def test_check_is_read_only_and_accounts_for_retained_builds(self):
        before=snapshot(self.root)
        result=self.subject.preflight()
        self.assertGreaterEqual(result['required_free_bytes'],6_000_000_000)
        self.assertEqual(snapshot(self.root),before)
        self.assertEqual(self.subject.operations,['sudo-access'])
        self.assertGreater(upgrade.space_required(100_000_000,0),6_000_000_000)

    def test_preflight_rejections_do_not_modify_installation(self):
        cases=('busy','disk','version','checksum','runtime','socket','dependencies','verification','unresolved')
        for case in cases:
            with self.subTest(case=case):
                self.subject.busy=case=='busy'
                self.subject.version='1.11.0' if case=='version' else '1.12.0'
                self.disk.return_value=shutil._ntuple_diskusage(100_000_000_000,0,1 if case=='disk' else 100_000_000_000)
                changes=[]
                if case=='checksum':changes=[(self.root/'app/requirements.txt','corrupt')]
                if case=='socket':changes=[(self.root/'socket/.s.PGSQL.5432.lock','999\n')]
                if case=='verification':changes=[(self.base/'verification/PROGRAM_COMMIT','c'*40)]
                if case=='dependencies':changes=[(self.base/'candidate/requirements.txt','changed')]
                originals={p:p.read_text() for p,_ in changes}
                for p,value in changes:p.write_text(value)
                unresolved=self.root/'upgrades/previous/state.json'
                if case=='unresolved':
                    unresolved.parent.mkdir(parents=True)
                    unresolved.write_text('{"phase":"committed"}')
                before=snapshot(self.root)
                with patch.object(upgrade,'verify_installed',side_effect=ValueError('runtime mismatch') if case=='runtime' else None):
                    with self.assertRaises(ValueError):self.subject.preflight()
                self.assertEqual(snapshot(self.root),before)
                for p,value in originals.items():p.write_text(value)
                if case=='unresolved':shutil.rmtree(self.root/'upgrades')

    def test_migration_failure_restores_old_program_and_services(self):
        before=snapshot(self.root/'app');states=self.subject.services()
        result=self.subject.preflight();self.subject.migration_failure=True
        with self.assertRaisesRegex(ValueError,'rollback'):self.subject.apply(result)
        self.assertEqual(snapshot(self.root/'app'),before)
        self.assertEqual(self.subject.services(),states)
        self.assertEqual(self.subject.state['phase'],'rolled-back')
        self.assertEqual(self.subject.version,'1.12.0')

    def test_success_preserves_runtime_and_original_service_states(self):
        states=self.subject.services()
        self.subject.apply(self.subject.preflight())
        self.assertEqual(self.subject.state['phase'],'complete')
        self.assertEqual((self.root/'app/VERSION').read_text(),'v1.2.0\n')
        self.assertEqual((self.root/'app/.venv/retained').read_text(),'runtime bytes')
        self.assertEqual(self.subject.services(),states)
        for path in (self.root/'app/grafana/dashboards').glob('*.json'):
            self.assertEqual(path.read_bytes(),(self.root/'grafana/dashboards/mpp'/path.name).read_bytes())

    def test_daily_starting_after_preflight_is_not_killed(self):
        obj=self.subject
        obj.service_states['sql-apm-daily.timer']['ActiveState']='active'
        result=obj.preflight();command=obj.command
        def race(name,args):
            value=command(name,args)
            if name=='stop-sql-apm-daily.timer':obj.busy=True
            return value
        with patch.object(obj,'command',side_effect=race), self.assertRaisesRegex(ValueError,'task is running'):
            obj.apply(result)
        self.assertNotIn('stop-sql-apm-daily.service',obj.operations)
        self.assertNotIn('initialize-upgrade',obj.operations)
        self.assertEqual(obj.version,'1.12.0')
        obj.busy=False
        obj.recover()
        self.assertEqual(obj.service_states['sql-apm-daily.timer']['ActiveState'],'active')
        self.assertEqual(obj.state['phase'],'rolled-back')

    def test_resume_each_interrupted_directory_rename(self):
        for position in range(1,6):
            with self.subTest(rename=position):
                # Reset each subcase using an independent installation.
                fresh=UpgradeTests('test_check_is_read_only_and_accounts_for_retained_builds')
                fresh.setUp()
                try:
                    obj=fresh.subject;result=obj.preflight();original=Path.rename;calls=[]
                    def interrupted(path,target):
                        value=original(path,target);calls.append(path)
                        if len(calls)==position:raise OSError('injected interruption')
                        return value
                    with patch.object(Path,'rename',interrupted),self.assertRaises(OSError):obj.apply(result)
                    self.assertEqual(obj.version,'1.13.0')
                    self.assertNotEqual(obj.state['phase'],'rolled-back')
                    obj.recover()
                    self.assertEqual(obj.state['phase'],'complete')
                    self.assertEqual((obj.root/'app/.venv/retained').read_text(),'runtime bytes')
                finally:fresh.doCleanups()
                # Nested cleanup stops our patches as well; restore only for the next setup.

    def test_resume_refuses_when_detached_migration_still_runs(self):
        self.subject.external_busy=True
        with self.assertRaisesRegex(ValueError,'busy'):self.subject.recover()
        self.assertFalse(any(name.startswith('start-') for name in self.subject.operations))


if __name__=='__main__':unittest.main()
