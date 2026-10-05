"""Observation-only projection of the build's already materialized decisions."""
from collections import Counter
import itertools

from sql_apm.baseline.statistics import calculate_group

SQL_REASONS = ('sql_uncertain', 'sql_incomplete', 'sql_encoding_invalid', 'fingerprint_failed')


def calculate_observations(db, build_id, partition, start, end, writer_factory, columns):
    """Runs inside the formal result transaction; no second eligibility derivation.

    A missing identity or start cannot be assigned to a bucket. Such events keep
    their reasons in build diagnostics. Unknown timing is an explicit observation
    dimension; it can contain exclusions only.
    """
    with db.cursor() as cur:
        cur.execute('''CREATE TEMP TABLE observation_candidates ON COMMIT DROP AS
            SELECT d.*,o.scope_id,o.database,o.execution_user,o.sql_state
            FROM statistics_decisions d JOIN mpp_occurrence o USING(analysis_id,occurrence_id)
            WHERE d.reason_codes && %s OR 'sql_missing'=ANY(d.reason_codes)''', (list(SQL_REASONS),))
        cur.execute('ANALYZE observation_candidates')
        cur.execute('SELECT sql_state,count(*) FROM observation_candidates GROUP BY 1 ORDER BY 1')
        sql_states = cur.fetchall()
        cur.execute('''CREATE TEMP TABLE observation_events ON COMMIT DROP AS
            WITH linked AS (
                SELECT c.*,a.rule_id,a.result_id,r.value,r.state AS approximate_state,
                    ARRAY(SELECT code FROM unnest(c.reason_codes) code WHERE NOT code=ANY(%s)) AS exclusions
                FROM observation_candidates c
                LEFT JOIN mpp_occurrence_approximate a USING(analysis_id,occurrence_id)
                LEFT JOIN mpp_approximate_result r USING(result_id,rule_id)
            )
            SELECT *, CASE
                WHEN in_window IS FALSE THEN 'outside_window'
                WHEN sql_state='missing' THEN 'sql_missing'
                WHEN approximate_state IS DISTINCT FROM 'available' THEN 'approximate_unavailable'
                WHEN database IS NULL OR execution_user IS NULL THEN 'identity_missing'
                WHEN estimated_start_at IS NULL THEN 'start_unknown'
                ELSE 'group' END AS observation_scope,
                CASE WHEN state='unresolved' AND cardinality(exclusions)=0 THEN true ELSE false END AS observed,
                CASE WHEN approximate_state='available' AND sql_state<>'missing'
                        AND in_window IS TRUE AND database IS NOT NULL AND execution_user IS NOT NULL
                     THEN 'OG:'||encode(sha256(convert_to(jsonb_build_array(scope_id,'mpp-csv/1',
                          database,execution_user,rule_id,value,coalesce(timing_type,'unknown'))::text,'UTF8')),'hex') END AS observation_group_id
            FROM linked''', (list(SQL_REASONS),))
        cur.execute('ANALYZE observation_events')
        summary = dict(observation_only=True, sql_states=sql_states)
        # Counts are executions, even if historical results under multiple rule
        # versions exist. Group totals remain explicitly separated by rule.
        for label, query in {
            'scopes': '''SELECT observation_scope,count(DISTINCT (analysis_id,occurrence_id))
                FROM observation_events GROUP BY 1 ORDER BY 1''',
            'ungrouped_reasons': '''SELECT observation_scope,code,count(DISTINCT (analysis_id,occurrence_id))
                FROM observation_events CROSS JOIN LATERAL unnest(reason_codes) code
                WHERE observation_scope NOT IN ('group','outside_window') GROUP BY 1,2 ORDER BY 1,2''',
        }.items():
            cur.execute(query)
            summary[label] = cur.fetchall()
        cur.execute('''INSERT INTO mpp_observation_group
            SELECT DISTINCT ON (observation_group_id) observation_group_id,scope_id,'mpp-csv/1',
                database,execution_user,rule_id,result_id,value,coalesce(timing_type,'unknown')
            FROM observation_events WHERE observation_scope='group'
            ORDER BY observation_group_id,result_id ON CONFLICT (group_id) DO NOTHING''')
        cur.execute('''INSERT INTO mpp_build_observation_group
            SELECT %s,%s,observation_group_id FROM observation_events
            WHERE observation_scope='group' GROUP BY observation_group_id''', (partition, build_id))
        summary['groups'] = cur.rowcount
    writer = writer_factory(db, 'mpp_observation_statistic', columns)
    layers, rules = Counter(), {}
    with db.cursor(name='observation_groups') as stream:
        stream.itersize = 4000
        stream.execute('''SELECT observation_group_id,rule_id,estimated_start_at,duration_ms,observed,exclusions
            FROM observation_events WHERE observation_scope='group'
            ORDER BY observation_group_id,estimated_start_at,duration_ms,observed,exclusions''')
        for gid, items in itertools.groupby(stream, key=lambda row: row[0]):
            events = list(items)
            rule = rules.setdefault(events[0][1], dict(groups=0, included=0, excluded=0, reasons=Counter()))
            rule['groups'] += 1
            computed, _ = calculate_group((row[2:] for row in events), start, end)
            for row in computed:
                writer.add(dict(row, build_id=build_id, group_id=gid, partition_id=partition))
                layers[row['layer']] += 1
                if row['layer'] == 'overall':
                    rule['included'] += row['included_count']
                    rule['excluded'] += row['excluded_count']
                    rule['reasons'].update(row['exclusions_by_reason'])
    writer.flush()
    summary.update(layers=dict(layers), rules=rules)
    return summary
