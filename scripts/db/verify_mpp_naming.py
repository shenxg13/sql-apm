#!/usr/bin/env python3
"""Synthetic persisted profile checks and sensitivity of the full replay oracle."""
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'tests'),str(ROOT/'scripts/deployment')]
from verify import instance,Verification
from verify_mpp_naming_full import export
from rehearsal import mpp_identifiers
from ingestion.test_reader import row,write_csv,configuration
from sql_apm.ingestion.config import load_config
from sql_apm.training.config import validate
from sql_apm.baseline.workflow import run
from sql_apm.storage.ingestion import connect


def verify(pg_bin):
    with instance(pg_bin) as (directory,env):
        v=Verification(pg_bin,directory,env);v.init()
        path=directory/'input.csv';cfg=directory/'import.json'
        write_csv(path,[row(text='SELECT 1',message='duration: 2 ms'),
                        row(text='SELECT 2',message='duration: 3 ms'),
                        row(text='SELECT * FROM (',message='duration: 5 ms')])
        cfg.write_text(json.dumps(configuration(cfg,[path])))
        dsn='host='+str(directory/'socket')+' port=55473 dbname=sql_apm user=sql_apm'
        result=run(dsn,'sql_apm',validate(dict(version=1,clusters=['C1'],window=dict(cutoff_date='2026-07-31')),'C1'),
                   load_config(cfg,'S1','B1'),workers=1)
        db=connect(dsn,'sql_apm')
        try:
            expected,identifiers=export(db,result,'C1')
            for field,values in identifiers.items():
                target='mpp' if field=='scope.system_kind' else 'mpp-mapping/1' if field.endswith('mapping_version') else (
                    'mpp-csv-reader/1' if field.endswith('parser_version') else
                    '1.0.2' if field.endswith('dictionary_rules_version') else 'mpp-csv/1')
                assert values==[target],field
            with db.cursor() as cur:
                exported=mpp_identifiers(cur)
            assert all(identifiers[key]==value for key,value in exported.items())
            assert expected['formal']['rows']>0 and expected['observation']['rows']>0
            assert expected['publication']=='published' and len(expected['checks'])==6
            v.require(True,'new identifiers persisted in every context with formal and observation groups')
            for kind,table in [('formal','mpp_statistic'),('observation','mpp_observation_statistic')]:
                with db,db.cursor() as cur:
                    cur.execute('UPDATE '+table+" SET excluded_count=excluded_count+1 WHERE layer='overall'")
                changed,_=export(db,result,'C1')
                assert changed[kind]!=expected[kind]
                with db,db.cursor() as cur:
                    cur.execute('UPDATE '+table+" SET excluded_count=excluded_count-1 WHERE layer='overall'")
            restored,_=export(db,result,'C1');assert restored==expected
            v.require(True,'complete metric oracle detects formal and observation changes and exact restoration')
        finally:db.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pg-bin',type=Path,default=Path('/usr/pgsql-17/bin'))
    verify(parser.parse_args().pg_bin)
