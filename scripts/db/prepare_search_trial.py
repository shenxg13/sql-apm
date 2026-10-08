#!/usr/bin/env python3
"""Local-only trial examples and row counts; never print SQL or source identities."""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from psycopg2 import sql
from sql_apm.storage.ingestion import connect
from sql_apm.sql.normalization import Normalizer
from sql_apm.ingestion.config import identity


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['examples','before','after'])
    p.add_argument('--directory',type=Path,required=True)
    args=p.parse_args();directory=args.directory.resolve()
    db=connect(os.environ.get('SQL_APM_DSN',''),'sql_apm');db.set_session(readonly=True)
    try:
        with db,db.cursor() as c:
            if args.action=='examples':
                directory.mkdir(mode=0o700)
                norm='N:'+identity(Normalizer().context)
                c.execute('''SELECT t.text FROM mpp_baseline_group g JOIN current_version v USING(scope_id)
                    JOIN mpp_statistic s ON s.group_id=g.group_id AND s.build_id=v.build_id AND s.layer='overall'
                    JOIN mpp_fingerprint f ON f.fingerprint_id=g.fingerprint_id JOIN mpp_sql_text t USING(sql_id)
                    WHERE g.normalization_id=%s AND s.included_count>0 ORDER BY s.included_count DESC,g.group_id LIMIT 1''',(norm,))
                (directory/'baseline.sql').write_text(c.fetchone()[0])
                c.execute('''SELECT t.text FROM mpp_fingerprint f JOIN mpp_sql_text t USING(sql_id)
                    WHERE f.normalization_id=%s AND f.state='reliable'
                      AND EXISTS(SELECT FROM mpp_occurrence o WHERE o.sql_id=f.sql_id AND o.outcome<>'success')
                      AND NOT EXISTS(SELECT FROM mpp_occurrence o WHERE o.sql_id=f.sql_id AND o.outcome='success')
                      AND NOT EXISTS(SELECT FROM mpp_baseline_group g JOIN current_version v USING(scope_id)
                          JOIN mpp_statistic s ON s.group_id=g.group_id AND s.build_id=v.build_id
                          WHERE g.normalization_id=%s AND g.fingerprint_value=f.value)
                    ORDER BY f.sql_id LIMIT 1''',(norm,norm))
                found=c.fetchone()
                if found:(directory/'records-only.sql').write_text(found[0])
                (directory/'not-seen.sql').write_text('SELECT * FROM sql_apm_issue47_never_seen_trial_20261007')
                (directory/'unreliable.sql').write_text('SELECT ?')
                print(json.dumps(dict(state='prepared',records_only_available=found is not None)))
            else:
                c.execute("SELECT relname FROM pg_class WHERE relnamespace='sql_apm'::regnamespace AND relkind IN ('r','p') AND NOT relispartition ORDER BY relname")
                names=[r[0] for r in c];counts={}
                for name in names:
                    c.execute(sql.SQL('SELECT count(*) FROM {}').format(sql.Identifier(name)))
                    counts[name]=c.fetchone()[0]
                if args.action=='before':
                    (directory/'before-counts.json').write_text(json.dumps(counts));print('{"state":"recorded"}')
                else:
                    equal=counts==json.loads((directory/'before-counts.json').read_text())
                    (directory/'after-counts.json').write_text(json.dumps(counts))
                    print(json.dumps(dict(state='ok' if equal else 'failed',all_business_row_counts_equal=equal)))
                    return 0 if equal else 1
    finally:db.close()
    return 0


if __name__=='__main__':raise SystemExit(main())
