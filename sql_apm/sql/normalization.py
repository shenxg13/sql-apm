"""Complete structural normalization with immutable, content-addressed rules.

No catalog, database, bind recovery or executable SQL generation. The AST walker
uses an explicit stack; each supported node declares traversable fields. Unknown
nodes, control subtrees and MPP extension metadata are preserved in full.
"""
import base64
from collections import Counter
import hashlib
from importlib.metadata import version
from pathlib import Path

from . import approximate, mpp_parser
from .function_dictionary import FunctionDictionary
from .structure import dumps, loads
from .type_policy import KNOWN_TYPES, NORMALIZABLE_CASTS

ALGORITHM_VERSION = 'sql-normalization/4'
PARSER_DEPENDENCY_VERSION = '7.18'
PROFILE = 'hashdata-pg94'
MAX_BYTES = approximate.MAX_BYTES
DEFAULT_DICTIONARY = Path(__file__).resolve().parents[2] / 'rules/functions/v1.0.1.json'
# O: ordinary; B: WHERE business expression; D: direct business value;
# F: legacy FILTER business values without IN bucketing; P: fully protected; T: assignment target; I/V/R: INSERT/multiassignment containers.
RULES = {
    'algorithm_version': ALGORITHM_VERSION,
    'marker': 'SQLAPMBusinessValue',
    'where': 'numeric/string/native parameter; preserve null/bool/controls',
    'where_in': {'marker': 'SQLAPMInBucket', 'buckets': ['1', '2-10', '11-100', '>100'],
                 'eligibility': 'query/update WHERE including ON CONFLICT DO UPDATE WHERE; every normalized IN/NOT IN element is a bare SQLAPMBusinessValue; FILTER keeps v3 element lists; nested query WHERE has its own context; conflict inference predicates stay protected',
                 'hint_exception': 'preserve anchor identity; same-bucket lists may remain distinct when Hint anchors differ'},
    'writes': 'INSERT VALUES and UPDATE SET direct values only',
    'functions': 'dictionary action consensus; protected arguments are opaque',
    'special_calls': 'preserve non-positional, SQL syntax, variadic and unknown',
    'casts': sorted(NORMALIZABLE_CASTS),
    'other_constants': 'preserve; CASE results, SET, limits, window frames, DDL',
    'extensions': 'preserve typed MPP extension trees',
    'hints': 'exact content and anchor only; exclude global gap; constant-aware local gaps/frame; source-backed PG-folded numeric signs share VALUE; skip source mapping for synthesized constants; retain brackets, operators and batch boundaries',
    'encoding': 'canonical sorted ASCII JSON; arrays retain order; sha256 domain-separated',
}


def _sha(value):
    return hashlib.sha256(dumps(value).encode('ascii')).hexdigest()


def _quote(value):
    # PG AST identifiers have already been decoded/folded. Preserve their case.
    return '"' + value.replace('"', '""') + '"'


def _names(nodes):
    return [n['String']['sval'] for n in nodes]


def _cast_type(node):
    cast = node.get('TypeCast', {}) if isinstance(node, dict) else {}
    typename = cast.get('typeName', {})
    names = _names(typename.get('names', []))
    if not names or len(names) > 2 or (len(names) == 2 and names[0] != 'pg_catalog'):
        return None
    name = names[-1]
    if name not in KNOWN_TYPES:
        return None
    if typename.get('arrayBounds'):
        name = '_' + name
    return name if name in KNOWN_TYPES else None


class Normalizer:
    """One fixed rule context, reusable for many independent SQL inputs.

    Construction validates and snapshots rules, failing closed on bad config or
    dependency drift. Returned artifacts are independent copies. No shared cache
    or mutable per-call state; resource isolation belongs to a batch caller.
    """
    def __init__(self, dictionary=None):
        actual = version('pglast')
        if actual != PARSER_DEPENDENCY_VERSION:
            raise ValueError('unsupported_parser_version')
        selected = dictionary if dictionary is not None else FunctionDictionary.load(DEFAULT_DICTIONARY)
        self._dictionary = FunctionDictionary(selected.snapshot())
        data = self._dictionary.snapshot()
        # Snapshot includes selection inputs, even currently unused/preserved rules.
        self._rules = loads(dumps(RULES))
        self._snapshot = {'normalization': self._rules, 'dictionary': data}
        self._context = {
            'algorithm_version': ALGORITHM_VERSION,
            'parser_version': mpp_parser.VERSION,
            'parser_dependency': 'pglast/' + actual,
            'profile': data['profile'],
            'dictionary_schema_version': data['schema_version'],
            'dictionary_rules_version': data['rules_version'],
            'dictionary_digest': self._dictionary.sha256,
            'rules_digest': _sha(self._rules),
        }
        # Rule order is immaterial, matching the existing dictionary digest.
        data['rules'].sort(key=lambda rule: rule['id'])
        for rule in data['rules']:
            rule['arguments'].sort(key=lambda arg: arg['position'])
        self._context['rules_ref'] = 'sha256:' + _sha(self._snapshot)

    @property
    def context(self):
        return dict(self._context)

    def rule_snapshot(self):
        """Persist this artifact alongside a run; rules_ref verifies its content."""
        return loads(dumps(self._snapshot))

    def _choice(self, call):
        args = call.get('args', [])
        names = _names(call.get('funcname', []))
        if (len(names) not in (1, 2) or call.get('func_variadic') or
                call.get('funcformat') != 'COERCE_EXPLICIT_CALL' or
                any('NamedArgExpr' in arg for arg in args)):
            return None, 'unsupported_call_form'
        types = [_cast_type(arg) for arg in args]
        # An explicit unknown type cannot be treated as an absent type hint.
        if any('TypeCast' in arg and t is None for arg, t in zip(args, types)):
            return None, 'unknown_argument_type'
        kinds = ('function', 'aggregate', 'window') if call.get('over') else ('function', 'aggregate')
        if call.get('agg_order') or call.get('agg_filter') or call.get('agg_within_group') or call.get('agg_star'):
            kinds = ('aggregate',)
        choices = [self._dictionary.select(_quote(names[-1]), len(args),
                   schema=_quote(names[0]) if len(names) == 2 else None,
                   types=types, kind=kind) for kind in kinds]
        candidates = [c for c in choices if c['reason'] != 'no_matching_rule']
        if not candidates:
            return None, 'no_matching_rule'
        if any(c['reason'] != 'matched' for c in candidates):
            return None, next(c['reason'] for c in candidates if c['reason'] != 'matched')
        if len({tuple(c['actions']) for c in candidates}) != 1:
            return None, 'ambiguous_call_kind'
        return candidates[0]['actions'], 'matched'

    def _walk(self, root, counters):
        output = [None]
        tasks = [(root, 'O', output, 0)]
        while tasks:
            node, mode, parent, key = tasks.pop()
            if mode == 'bucket':
                # Post-order: eligibility uses the existing value policy, so
                # casts, expressions, NULL and protected subtrees stay intact.
                if node and all(value == {'SQLAPMBusinessValue': {}} for value in node):
                    size = len(node)
                    bucket = '1' if size == 1 else '2-10' if size <= 10 else '11-100' if size <= 100 else '>100'
                    parent[key] = {'SQLAPMInBucket': bucket}
                    counters['in_lists_bucketed'] += 1
                continue
            if isinstance(node, list):
                result = [None] * len(node)
                parent[key] = result
                if mode == 'N':
                    tasks.append((result, 'bucket', parent, key))
                    mode = 'B'
                tasks.extend((child, mode, result, i) for i, child in enumerate(node))
                continue
            if not isinstance(node, dict):
                parent[key] = node
                continue
            if mode in ('W', 'C'):
                fields = {'ctes': 'O'} if mode == 'W' else {'targetList': 'T', 'whereClause': 'B'}
                result = {}
                parent[key] = result
                tasks.extend((value, fields.get(field, 'P'), result, field)
                             for field, value in node.items())
                continue
            # Only wrapper nodes have executable traversal policy. Plain fields
            # (names/options/type modifiers) never inherit business-value mode.
            tag = next(iter(node)) if len(node) == 1 else None
            body = node.get(tag) if tag is not None else None
            if mode != 'P' and isinstance(body, dict):
                if mode in ('B', 'F', 'D') and (tag == 'ParamRef' or
                        tag == 'A_Const' and bool(set(body) & {'ival', 'fval', 'sval', 'bsval'})):
                    parent[key] = {'SQLAPMBusinessValue': {}}
                    counters['replacements'] += 1
                    continue
                fields = self._fields(tag, body, mode, counters)
                result = {}
                parent[key] = {tag: result}
                for field, value in body.items():
                    field_mode = fields.get(field, 'P')
                    if isinstance(field_mode, list):
                        children = [None] * len(value)
                        result[field] = children
                        tasks.extend((v, m, children, i) for i, (v, m) in enumerate(zip(value, field_mode)))
                    else:
                        tasks.append((value, field_mode, result, field))
                continue
            result = {}
            parent[key] = result
            tasks.extend((value, 'P', result, field) for field, value in node.items())
        return output[0]

    def _fields(self, tag, body, mode, counters):
        expression = mode if mode in ('B', 'F') else 'O'
        if tag == 'TypeCast':
            return {'arg': mode} if _cast_type({tag: body}) in NORMALIZABLE_CASTS else {}
        if tag == 'FuncCall':
            actions, reason = self._choice(body)
            counters['function_' + reason] += 1
            if actions is None:
                return {}
            # FILTER keeps v3 value replacement without inheriting v4 IN buckets.
            # SelectStmt encountered inside it still establishes its own WHERE.
            return {'args': ['D' if a == 'normalize' else 'P' for a in actions],
                    'agg_filter': 'F', 'agg_order': 'O'}
        if tag == 'SelectStmt':
            fields = {k: 'O' for k in ('targetList', 'fromClause', 'withClause', 'larg', 'rarg',
                       'havingClause', 'groupClause', 'sortClause', 'distinctClause')}
            fields.update(withClause='W', whereClause='B', valuesLists='V' if mode == 'I' else 'O')
            # INSERT ... SELECT ... UNION VALUES is a query, not direct VALUES.
            return fields
        if tag == 'InsertStmt':
            return {'selectStmt': 'I', 'withClause': 'W', 'onConflictClause': 'C', 'returningList': 'O'}
        if tag in ('UpdateStmt', 'DeleteStmt'):
            return {'targetList': 'T', 'whereClause': 'B', 'fromClause': 'O', 'usingClause': 'O',
                    'withClause': 'W', 'returningList': 'O'}
        if tag == 'OnConflictClause':
            return {'targetList': 'T', 'whereClause': 'B'}
        if tag == 'ResTarget':
            return {'val': 'D' if mode == 'T' else 'O'}
        if tag == 'List':
            return {'items': 'D' if mode == 'V' else mode}
        if tag == 'MultiAssignRef':
            return {'source': 'R'} if mode == 'D' else {'source': 'O'}
        if tag == 'RowExpr':
            return {'args': 'D' if mode == 'R' else expression}
        if tag == 'A_Expr':
            # NULLIF/SIMILAR are special function forms, not ordinary operators.
            if body.get('kind') in ('AEXPR_NULLIF', 'AEXPR_SIMILAR'):
                return {}
            if mode == 'B' and body.get('kind') == 'AEXPR_IN':
                return {'lexpr': 'B', 'rexpr': 'N'}
            return {'lexpr': expression, 'rexpr': expression}
        if tag == 'BoolExpr':
            return {'args': expression}
        if tag in ('NullTest', 'BooleanTest', 'CollateClause'):
            return {'arg': expression}
        if tag == 'A_Indirection':
            return {'arg': expression}
        if tag == 'CaseExpr':
            return {'arg': expression, 'args': expression, 'defresult': 'O'}
        if tag == 'CaseWhen':
            return {'expr': expression, 'result': 'O'}
        if tag == 'SubLink':
            return {'testexpr': expression, 'subselect': 'O'}
        if tag == 'SortBy':
            return {'node': 'O'}
        if tag == 'JoinExpr':
            return {'larg': 'O', 'rarg': 'O', 'quals': 'O'}
        if tag == 'RangeSubselect':
            return {'subquery': 'O'}
        if tag == 'RangeFunction':
            return {'functions': 'O'}
        if tag == 'WithClause':
            return {'ctes': 'O'}
        if tag == 'CommonTableExpr':
            return {'ctequery': 'O'}
        if tag in ('CopyStmt', 'ExplainStmt', 'CreateTableAsStmt', 'ViewStmt',
                   'PrepareStmt', 'DeclareCursorStmt'):
            return {'query': 'O'}
        return {}

    def normalize(self, sql):
        """Return a reliable structural result, explicit refusal, or fixed error.

        Only parser Unsupported produces an independent approximate result.
        Resource/configuration/internal failures never use a text fallback.
        """
        if not isinstance(sql, (str, bytes)):
            raise TypeError('sql must be str or bytes')
        raw = sql.encode('utf-8', 'surrogatepass') if isinstance(sql, str) else sql
        source = {'byte_length': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(),
                  'bytes_base64': base64.b64encode(raw).decode('ascii') if len(raw) <= MAX_BYTES else None}
        result = {'source': source, 'context': self.context, 'normalized': None,
                  'fingerprint': {'kind': 'structural', 'state': 'normalization_failed',
                                  'value': None, 'reason': None},
                  'approximate': None, 'diagnostics': {}}
        status = result['fingerprint']
        if len(raw) > MAX_BYTES:
            status['reason'] = 'input_size_limit'
            return result
        try:
            try:
                text = raw.decode('utf-8', 'strict')
            except UnicodeError:
                raise mpp_parser.Unsupported('invalid_encoding') from None
            tree = mpp_parser.parse(text)
        except mpp_parser.Unsupported as error:
            reason = str(error)
            status.update(state='unsupported_syntax', reason=reason)
            try:
                result['approximate'] = approximate.fingerprint(raw, structural_reason=reason)
            except Exception:
                result['diagnostics']['approximate_error'] = 'approximate_failed'
            return result
        except Exception:
            status['reason'] = 'parser_failed'
            return result
        try:
            counters = Counter()
            normalized = {'statements': [
                {'base': self._walk(statement['base'], counters),
                 'extensions': loads(dumps(statement['extensions']))}
                for statement in tree['statements']],
                'hints': [{key: loads(dumps(value)) for key, value in hint.items() if key != 'gap'}
                          for hint in tree['hints']]}
            value = _sha({'kind': 'sql-apm-structural', 'context': self._context, 'normalized': normalized})
            result['normalized'] = normalized
            result['diagnostics'] = dict(counters)
            status.update(state='reliable', value='struct:' + ALGORITHM_VERSION + ':' + value)
        except Exception:
            status['reason'] = 'normalization_failed'
        return result
