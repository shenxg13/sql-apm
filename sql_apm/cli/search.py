"""JSON adapter for the database query API; SQL text is never executed."""
import argparse
import json
import os
from pathlib import Path

import psycopg2
from pglast.parser import ParseError
from psycopg2.extras import Json

from sql_apm.ingestion.config import IngestionError, identity
from sql_apm.sql.mpp_parser import Token, top_level
from sql_apm.sql.normalization import MAX_BYTES, Normalizer
from sql_apm.sql.scanning import scan
from sql_apm.storage.ingestion import connect


class SearchError(ValueError):
    pass


class Parser(argparse.ArgumentParser):
    def error(self, message):
        # argparse's message can contain user SQL; use a fixed public reason.
        raise SearchError('invalid_arguments')

    def print_help(self, file=None):
        print(json.dumps({'help': self.format_help()}, ensure_ascii=False), file=file)


def statement_inputs(raw):
    """Use the adapter's lexical boundaries; preserve comments/hints in slices."""
    text = raw.decode('utf-8') if isinstance(raw, bytes) else raw
    tokens = [Token(text[t.start:t.end + 1], t.name, t.start, t.end + 1)
              for t in scan(text) if t.name not in ('C_COMMENT', 'SQL_COMMENT')]
    start, result = 0, []
    for i in top_level(tokens):
        if tokens[i].word == ';':
            piece = text[start:tokens[i].end]
            if any(t.start >= start and t.word != ';' for t in tokens[:i]):
                result.append(piece)
            start = tokens[i].end
    if any(t.start >= start for t in tokens):
        result.append(text[start:])
    return result


def query(cur, name, values):
    # name is selected solely from literal names below; values always bound.
    cur.execute('SELECT ' + name + '(' + ','.join(['%s'] * len(values)) + ')', values)
    return cur.fetchone()[0]


def exact(cur, normalizer, raw, filters):
    normalized = normalizer.normalize(raw)
    fp, near = normalized['fingerprint'], normalized['approximate']
    norm = 'N:' + identity(normalizer.context)
    result = query(cur, 'mpp_query_exact', [norm, fp['value'],
        near['value'] if near and near['state'] == 'available' else None] + filters + [fp['reason']])
    if result['state'] in ('not_seen', 'unreliable_fingerprint') and fp['reason'] != 'input_size_limit':
        try:
            pieces = statement_inputs(raw)
        except (ValueError, UnicodeError, ParseError):
            pieces = []
        if len(pieces) > 1:
            hints = []
            for number, piece in enumerate(pieces[:20], 1):
                item = normalizer.normalize(piece)['fingerprint']
                hit = query(cur, 'mpp_query_exact', [norm, item['value'], None] + filters + [item['reason']])
                hints.append(dict(statement=number, fingerprint=item['value'], state=hit['state'], hits=hit['hits']))
            result.update(statement_hints=hints, hints_are_batch_match=False,
                          hints_truncated=len(pieces) > 20, statement_count=len(pieces))
    return result


def main(argv=None):
    parser = Parser(description='SQL 检索、基线与执行历史（JSON）')
    parser.add_argument('--schema', default='sql_apm')
    subs = parser.add_subparsers(dest='action', required=True, parser_class=Parser)
    fuzzy = subs.add_parser('find', help='按词或整段的文本检索（主入口）；完整指纹值直查')
    fuzzy.add_argument('input')
    fuzzy.add_argument('--mode', choices=['words', 'passage'], default='words',
                       help='words：只按空白切词，每个词都要出现，引号是普通字符；passage：整个输入连续出现')
    fuzzy.add_argument('--order', choices=['count', 'recent'], default='count')
    precise = subs.add_parser('exact', help='完整 SQL 或完整批次的结构检索')
    source = precise.add_mutually_exclusive_group(required=True)
    source.add_argument('--sql')
    source.add_argument('--file', type=Path)
    baseline = subs.add_parser('baseline')
    baseline.add_argument('--layer', choices=['overall', 'day', 'week', 'weekday', 'hour'], default='overall')
    history = subs.add_parser('executions')
    history.add_argument('--limit', type=int, default=100)
    history.add_argument('--cursor', help='上一页 JSON 的 next_cursor')
    history.add_argument('--bucket', choices=['hour', 'day'])
    versions = subs.add_parser('versions')
    raw = subs.add_parser('text', help='按需读取一份原文')
    raw.add_argument('--sql-id', required=True)
    for child in (fuzzy, precise, baseline, history, versions):
        child.add_argument('--cluster', required=child in (baseline, history, versions))
        if child is not versions:
            child.add_argument('--database', required=child in (baseline, history))
            child.add_argument('--user', required=child in (baseline, history))
    for child in (baseline, history):
        child.add_argument('--fingerprint', required=True)
        child.add_argument('--version', help='history 或 search versions 输出的 build_id')
    for child in (fuzzy, history):
        child.add_argument('--start', help='含时区的起始时间（包含）')
        child.add_argument('--end', help='含时区的结束时间（不包含）')
    db = None
    try:
        args = parser.parse_args(argv)
        normalizer = Normalizer()
        norm = 'N:' + identity(normalizer.context)
        # All query calls share a read-only, consistent snapshot and short transaction.
        db = connect(os.environ.get('SQL_APM_DSN', ''), args.schema)
        db.set_session(readonly=True, isolation_level='REPEATABLE READ')
        with db, db.cursor() as cur:
            if args.action in ('find', 'exact'):
                filters = [args.cluster, args.database, args.user]
                if args.action == 'find':
                    result = query(cur, 'mpp_query_search', [norm, args.input] + filters + [args.start, args.end, args.order, args.mode])
                else:
                    if args.file:
                        with args.file.open('rb') as stream:
                            value = stream.read(MAX_BYTES + 1)
                    else:
                        value = args.sql
                    result = exact(cur, normalizer, value, filters)
            elif args.action == 'baseline':
                result = query(cur, 'mpp_query_baseline', [norm, args.cluster, args.database, args.user,
                               args.fingerprint, args.version, args.layer])
            elif args.action == 'executions':
                try:
                    cursor = Json(json.loads(args.cursor)) if args.cursor else None
                except ValueError:
                    raise SearchError('invalid_page_cursor') from None
                result = query(cur, 'mpp_query_history', [norm, args.fingerprint, args.cluster, args.database,
                    args.user, args.start, args.end, args.limit, cursor, args.version, args.bucket])
            elif args.action == 'versions':
                cur.execute("SELECT coalesce(jsonb_agg(to_jsonb(v) ORDER BY published_at DESC,build_id),'[]') FROM mpp_query_versions(%s,%s) v", (norm, args.cluster))
                result = dict(state='ok', versions=cur.fetchone()[0])
            else:
                cur.execute('SELECT to_jsonb(t) FROM mpp_query_text(%s) t', (args.sql_id,))
                found = cur.fetchone()
                result = dict(state='ok', **found[0]) if found else dict(state='not_seen')
            result['rules'] = dict(normalizer.context, normalization_id=norm)
        print(json.dumps(result, ensure_ascii=False, default=str))
        return 0
    except (SearchError, IngestionError) as error:
        result = dict(state='failed', reason=str(error))
    except OSError:
        result = dict(state='failed', reason='input_unavailable')
    except psycopg2.Error as error:
        reason = getattr(error.diag, 'message_primary', None)
        allowed = {'empty_search_input', 'too_many_search_terms', 'invalid_search_order', 'invalid_search_mode', 'invalid_time_range',
                   'results_cleaned', 'no_current_baseline', 'published_version_not_found',
                   'normalization_version_mismatch', 'invalid_baseline_layer', 'invalid_time_bucket',
                   'invalid_page_size', 'invalid_page_cursor', 'invalid_training_records', 'occurrence_not_found'}
        result = dict(state='failed', reason=reason if reason in allowed else 'query_failed')
    except (ValueError, TypeError):
        result = dict(state='failed', reason='invalid_input')
    finally:
        if db is not None:
            db.close()
    print(json.dumps(result, ensure_ascii=False))
    return 1
