"""Issue #23 acceptance helpers; frozen v2 oracle, aggregate-only evidence."""
from collections import Counter
from contextlib import contextmanager
import hashlib
import importlib.util
from pathlib import Path
from unittest.mock import patch

from psycopg2.extras import execute_values
from sql_apm.sql.lexical import diagnose
from sql_apm.training.categories import CATEGORIES, RULES, classify
from sql_apm.training.config import validate

ROOT = Path(__file__).resolve().parents[2]
FROZEN = ROOT / 'tests/parser_probe/fixtures/training_categories_v2.py'
BASE_SHA = '5edfa9236fc21e88dac3c9898a5ef7f9853ad2a3'
# Byte-for-byte categories.py at the merged Issue #21 head.
FROZEN_SHA256 = '3a57eadbd9a740a1dae217d79475b4b5160ef28632349bcb71ab2f912d2efff7'


@contextmanager
def category_v2():
    assert hashlib.sha256(FROZEN.read_bytes()).hexdigest() == FROZEN_SHA256
    spec = importlib.util.spec_from_file_location('training_categories_v2', FROZEN)
    old = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(old)
    assert old.CATEGORIES == CATEGORIES
    with patch('sql_apm.storage.training.RULES', old.RULES), patch('sql_apm.storage.training.classify', old.classify):
        yield old


def old_snapshot(store, doc, scope, batches):
    with category_v2():
        return store.snapshot(validate(doc, scope), batches)


def compare(store, previous, current, historical, current_summary):
    """Compare all executions; hold compact projections only in session TEMP tables.

    Rules/configuration references differ by design. Every other field apart from
    category, blacklist evaluation and resulting state must remain identical.
    """
    baseline = store.summary(previous['input_id'], previous['config_id'])
    for field in ('states', 'timings', 'count_scopes', 'reasons', 'categories', 'templates', 'intervals', 'clusters'):
        assert [list(r) for r in baseline[field]] == historical[field], field
    def reason_totals(summary):
        counts = Counter()
        for reason, state, n in summary['reasons']:
            if reason != 'blacklist_category':
                counts[reason] += n
        return counts
    assert reason_totals(baseline) == reason_totals(current_summary)
    result = dict(base_sha=BASE_SHA, frozen_sha256=FROZEN_SHA256,
                  previous_snapshot=previous, previous_summary=baseline,
                  historical_counts_equal=True, other_reason_totals_equal=True)
    with store.db, store.db.cursor() as cur:
        cur.execute('''SELECT s.sql_id,s.text,a.category_kind,a.categories,b.category_kind,b.categories
            FROM mpp_training_sql a JOIN mpp_training_sql b USING(sql_id)
            JOIN mpp_sql_text s USING(sql_id)
            WHERE a.rule_id=%s AND b.rule_id=%s
              AND (a.category_kind,a.categories) IS DISTINCT FROM (b.category_kind,b.categories)''',
            (previous['rule_id'], current['rule_id']))
        changes = []
        with category_v2() as old:
            for sid, text, before_kind, before_categories, after_kind, after_categories in cur:
                sequence, issues = diagnose(text)
                aliases = sorted(set(sequence).intersection(RULES['aliases']))
                assert not issues and aliases
                assert old.classify(text) == dict(kind=before_kind, categories=before_categories)
                assert classify(text) == dict(kind=after_kind, categories=after_categories)
                assert classify(text, grammar_verified=True) == classify(text)
                changes.append((sid, aliases, 'single' if len(sequence) == 1 else 'batch', before_kind, after_kind))
        cur.execute('''CREATE TEMP TABLE alias_changes(sql_id text PRIMARY KEY, aliases text[], unit text,
            before_kind text, after_kind text) ON COMMIT DROP''')
        if changes:
            execute_values(cur, 'INSERT INTO alias_changes VALUES %s', changes)
        result['changed_originals_in_prepared_rules'] = len(changes)
        # Normalize only reference prefixes; SQL payload and identity names are absent.
        for name, frozen in [('alias_before', previous), ('alias_after', current)]:
            cur.execute('''CREATE TEMP TABLE ''' + name + ''' ON COMMIT DROP AS
                SELECT analysis_id,occurrence_id,timing_type,state,reason_codes,category_kind,categories,
                    rule_evaluations->>'blacklist' AS blacklist_evaluation,
                    sha256(convert_to((
                        (to_jsonb(d)-ARRAY['state','reason_codes','reasons','rule_evaluations','category_kind','categories'])
                        || jsonb_build_object(
                            'reason_codes',array_remove(reason_codes,'blacklist_category'),
                            'reasons',(SELECT coalesce(jsonb_agg(
                                jsonb_set(r,'{rule_ref}',to_jsonb(replace(r->>'rule_ref',%s,'CONFIG')))
                                ORDER BY ord),'[]'::jsonb)
                                FROM jsonb_array_elements(reasons) WITH ORDINALITY AS x(r,ord)
                                WHERE r->>'code'<>'blacklist_category'),
                            'rule_evaluations',rule_evaluations-'blacklist')
                    )::text,'UTF8')) AS invariant
                FROM mpp_training_decisions(%s,%s) d''',
                (frozen['config_id'], frozen['input_id'], frozen['config_id']))
            cur.execute('ANALYZE ' + name)
        cur.execute('''SELECT count(*),count(*) FILTER (WHERE
            a.analysis_id IS NULL OR b.analysis_id IS NULL OR a.invariant IS DISTINCT FROM b.invariant OR
            ('blacklist_category'=ANY(a.reason_codes) AND NOT 'blacklist_category'=ANY(b.reason_codes)) OR
            (a.state IS DISTINCT FROM b.state AND NOT
                (a.state IN ('included','unresolved') AND b.state='excluded' AND
                 NOT 'blacklist_category'=ANY(a.reason_codes) AND 'blacklist_category'=ANY(b.reason_codes))))
            FROM alias_before a FULL JOIN alias_after b USING(analysis_id,occurrence_id)''')
        count, violations = cur.fetchone()
        assert violations == 0 and count == sum(n for state, n in current_summary['states'])
        result.update(compared_executions=count, invariant_violations=violations)
        cur.execute('''SELECT count(*) FROM alias_before a JOIN alias_after b USING(analysis_id,occurrence_id)
            JOIN mpp_occurrence o USING(analysis_id,occurrence_id)
            LEFT JOIN alias_changes c USING(sql_id)
            WHERE (a.category_kind,a.categories,a.blacklist_evaluation) IS DISTINCT FROM
                  (b.category_kind,b.categories,b.blacklist_evaluation) AND c.sql_id IS NULL''')
        assert cur.fetchone()[0] == 0
        cur.execute('''CREATE TEMP TABLE alias_hits ON COMMIT DROP AS
            SELECT c.aliases,c.unit,c.before_kind,c.after_kind,b.timing_type,a.state AS old_state,b.state AS new_state
            FROM alias_before a JOIN alias_after b USING(analysis_id,occurrence_id)
            JOIN mpp_occurrence o USING(analysis_id,occurrence_id)
            JOIN alias_changes c USING(sql_id)
            WHERE NOT 'blacklist_category'=ANY(a.reason_codes) AND 'blacklist_category'=ANY(b.reason_codes)''')
        cur.execute("SELECT count(*) FROM alias_hits WHERE after_kind<>'pure' OR before_kind='pure'")
        assert cur.fetchone()[0] == 0
        for key, sql in {
            'new_category_hits': 'SELECT count(*) FROM alias_hits',
            'new_exclusions': "SELECT count(*) FROM alias_hits WHERE old_state<>new_state AND new_state='excluded'",
            'mixed_to_pure': "SELECT count(*) FROM alias_hits WHERE before_kind='mixed'",
        }.items():
            cur.execute(sql)
            result[key] = cur.fetchone()[0]
        for key, sql in {
            'details': '''SELECT aliases,coalesce(timing_type,'unknown'),unit,before_kind,old_state,new_state,count(*)
                FROM alias_hits GROUP BY 1,2,3,4,5,6 ORDER BY 1,2,3,4,5,6''',
            'by_alias': '''SELECT alias,coalesce(timing_type,'unknown'),unit,count(*),
                count(*) FILTER (WHERE old_state<>new_state AND new_state='excluded'),
                count(*) FILTER (WHERE before_kind='mixed')
                FROM alias_hits CROSS JOIN LATERAL unnest(aliases) alias GROUP BY 1,2,3 ORDER BY 1,2,3''',
            'state_transitions': '''SELECT a.state,b.state,count(*) FROM alias_before a
                JOIN alias_after b USING(analysis_id,occurrence_id) GROUP BY 1,2 ORDER BY 1,2''',
        }.items():
            cur.execute(sql)
            result[key] = cur.fetchall()
        old_hits = sum(n for reason, state, n in baseline['reasons'] if reason == 'blacklist_category')
        new_hits = sum(n for reason, state, n in current_summary['reasons'] if reason == 'blacklist_category')
        assert new_hits - old_hits == result['new_category_hits']
    return result
