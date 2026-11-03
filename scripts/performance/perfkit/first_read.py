"""One instrumented first execution before ordinary timing; never clear caches."""
from datetime import datetime, timedelta, timezone
import json

from . import dashboards, plans
from .core import Refused, measure, save


def measured(session, sql, **kwargs):
    notice = plans.Notices(set())
    session.db.notices = notice
    record, rows = measure(session, sql, **kwargs)
    record['first_ms'] = record['ms']
    # The last notice is the outer statement. Its inclusive root counters already
    # include nested functions: summing nodes/notices would double-count reads.
    outer = notice.plans[-1].get('Plan') if notice.plans else None
    available = record['state'] == 'ok' and outer is not None and notice.unparsed == 0
    record['first_plan_available'] = int(available)
    if available:
        for target, source in (('first_shared_read_blocks', 'Shared Read Blocks'),
                               ('first_local_read_blocks', 'Local Read Blocks'),
                               ('first_temp_read_blocks', 'Temp Read Blocks')):
            record[target] = outer.get(source, 0)
    return record, rows


def run(args, work, session):
    output = work.public/'first-read.json'
    if output.exists() or (work.public/'timing.json').exists():
        raise Refused('first_read_requires_fresh_work_before_timing')
    selected = json.loads((work.private/'cases.json').read_text())
    if not selected:
        raise Refused('first_read_requires_representative_identity')
    now = datetime.fromisoformat(args.at) if args.at else datetime.now(timezone.utc).replace(microsecond=0)
    if now.tzinfo is None:
        raise Refused('time_requires_timezone')
    documents = dashboards.load(args.dashboards)
    module = plans.enable(session, args.module_dir)
    result = dict(module=module, pages=[])
    save(output, result)
    if module['state'] != 'loaded':
        return
    # Load/configure the verified module as admin, then execute with the same
    # privileges as ordinary timing. This does not change role/server settings.
    session.query('SET ROLE sql_apm_ro')
    trace, replay = [], []
    seen = set()
    start = now-timedelta(days=7)
    try:
        for cluster in sorted({c['scope_id'] for c in selected}):
            for kind in ('executions', 'texts'):
                case = next((c for c in selected if c['scope_id'] == cluster and kind in c['kinds']), None)
                if case is None or case['id'] in seen:
                    continue  # cases.json retains the missing-category reason.
                seen.add(case['id'])
                label = case['id']+'/mpp-detail'
                values = dashboards.overrides(case, case['inputs'][0])
                values['norm'] = case['norm']
                row, _ = dashboards.page(session, documents['mpp-detail'], values, start, now,
                    label, replay, trace, measure_statement=measured)
                result['pages'].append(row)
                save(output, result)
        for cluster in sorted({c['scope_id'] for c in selected})+[None]:
            case = next(c for c in selected if cluster is None or c['scope_id'] == cluster)
            label = (work.alias('cluster', cluster) if cluster is not None else 'all')+'/range-7/mpp-list'
            values = dict(norm=case['norm'], cluster=dashboards.token(cluster) if cluster else '*', limit='100')
            row, _ = dashboards.page(session, documents['mpp-list'], values, start, now,
                label, replay, trace, panel_ids={3}, measure_statement=measured)
            result['pages'].append(row)
            save(output, result)
    finally:
        save(output, result)
        save(work.private/'first-read-trace.json', trace)
        session.query('RESET ROLE')
