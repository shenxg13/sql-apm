"""Synthetic retention fixtures and content digests; no production identities."""
import hashlib
from psycopg2 import sql
from psycopg2.extras import Json


def clone_build(db, source, build, month, published=False, saved=True):
    with db, db.cursor() as cur:
        cur.execute('SELECT scope_id FROM build WHERE build_id=%s',(source,));scope=cur.fetchone()[0]
        cur.execute('SELECT mpp_ensure_result_partition(%s,%s::date)',(scope,month));pid=cur.fetchone()[0]
        patch=dict(build_id=build,partition_id=pid,started_at=month+'T00:00:00+08:00',
                   finished_at=month+'T00:00:01+08:00',results_saved=saved,
                   state='calculated' if saved else 'failed',retry_of=None)
        cur.execute('INSERT INTO build SELECT (jsonb_populate_record(NULL::build,to_jsonb(b)||%s::jsonb)).* FROM build b WHERE build_id=%s',
                    (Json(patch),source))
        tables=['build_check','mpp_build_layer_count','mpp_build_timing_coverage']
        if saved:tables+=['mpp_build_group','mpp_build_observation_group','mpp_statistic','mpp_observation_statistic']
        for table in tables:
            cur.execute(sql.SQL('INSERT INTO {} SELECT (jsonb_populate_record(NULL::{},to_jsonb(t)||%s::jsonb)).* FROM {} t WHERE build_id=%s').format(
                sql.Identifier(table),sql.Identifier(table),sql.Identifier(table)),
                (Json(dict(build_id=build,partition_id=pid)),source))
        if published:
            cur.execute("INSERT INTO publication VALUES (%s,%s,%s,%s,'published',NULL,%s::timestamptz)",
                        ('PUB:'+build,scope,build,source,month+'T00:00:02+08:00'))
    return pid


def digest(db, table, omitted=(), where='', params=()):
    """Order-independent rows using sorted per-row SHA256s; bounded server sort.

    Values stay server-side. Only hashes and counts leave the database; even SQL
    text and credential-like test values never reach evidence files.
    """
    h=hashlib.sha256();count=0
    with db, db.cursor(name='retention_digest') as cur:
        cur.itersize=10000
        cur.execute(sql.SQL("SELECT encode(sha256(convert_to((to_jsonb(t)-%s::text[])::text,'UTF8')),'hex') h FROM {} t {} ORDER BY h").format(
            sql.Identifier(table),sql.SQL(where)),(list(omitted),)+tuple(params))
        for (value,) in cur:
            h.update(value.encode('ascii'));count+=1
    return dict(rows=count,sha256=h.hexdigest())


def contents(db, *, cleanup=False):
    with db,db.cursor() as cur:
        cur.execute("SELECT relname FROM pg_class WHERE relnamespace=current_schema()::regnamespace AND relkind IN ('r','p') AND NOT relispartition ORDER BY relname")
        tables=[r[0] for r in cur]
    excluded={'schema_version','mpp_cleanup_month'}
    if cleanup:excluded.update(('task','mpp_statistic','mpp_observation_statistic','mpp_build_group','mpp_build_observation_group'))
    return {table:digest(db,table,('cleaned_at','groups_cleaned_at') if table=='mpp_result_partition' else ())
            for table in tables if table not in excluded}
