"""Versioned, observation-only fingerprints of SQL that lacks reliable structure.

No SQL repair, execution, I/O, AST fingerprint or training eligibility. The
caller retains the input; the result also preserves its exact bytes as base64.
Only simple WHERE predicates and direct UPDATE SET values are parameterized.
"""
import base64
from dataclasses import dataclass
import hashlib
import json
import re

VERSION = 'sql-approximate/2'
PROFILE = 'mpp-csv/1'
MAX_BYTES = 512 * 1024
_ASCII_LOWER = str.maketrans('ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')
_WORD = re.compile(r'[A-Za-z_\u0080-\uffff][A-Za-z_0-9$\u0080-\uffff]*')
_NUMBER = re.compile(r'(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?')
_TAG = re.compile(r'\$(?:[A-Za-z_][A-Za-z_0-9]*)?\$')
_PARAM = re.compile(r'\$[1-9][0-9]*')
_OPERATORS = '+-*/<>=~!@#%^&|`?:'
_COMMANDS = frozenset('select with insert update delete copy create alter drop truncate analyze analyse vacuum explain set reset show begin start commit end rollback abort savepoint release discard prepare execute deallocate declare fetch move close grant revoke comment do call checkpoint reindex cluster lock listen unlisten notify load refresh values'.split())
_END_CLAUSES = frozenset('group order having window limit offset fetch returning union intersect except for'.split())
_RULES = {
    'version': VERSION, 'representation': 'typed-token-sequence/1',
    'business_values': ['where_direct_comparison', 'where_direct_in_list', 'update_direct_set'],
    'literals': ['ordinary_string_without_backslash', 'decimal_number', 'native_parameter'],
    'opaque_policy': 'retain_exact_remainder_at_uncertain_lexical_boundary',
    'identifiers': 'ascii_fold_unquoted_only', 'comments': 'drop_closed_ordinary_keep_hint',
    'context': 'preserve_calls_casts_expressions_and_unknown_positions',
    'hint_context': 'ignore_hint_tokens_for_syntax_keep_output_positions',
    'eof_value': 'preserve_unterminated_numeric_or_parameter_token',
    'eligibility': 'observation_only',
}


def _canonical(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':'))


RULES_DIGEST = hashlib.sha256(_canonical(_RULES).encode('ascii')).hexdigest()


@dataclass(frozen=True)
class _Token:
    kind: str
    text: str
    start: int
    end: int

    @property
    def word(self):
        return self.text.translate(_ASCII_LOWER) if self.kind == 'word' else self.text


def _quoted_end(sql, pos, quote, escaped=False):
    i = pos + 1
    while i < len(sql):
        if sql[i] == quote:
            if i + 1 < len(sql) and sql[i + 1] == quote:
                i += 2
            else:
                return i + 1
        elif escaped and sql[i] == '\\':
            i += 2
        else:
            i += 1
    return None


def _invalid(text):
    return any(c == '\x00' or 0xD800 <= ord(c) <= 0xDFFF for c in text)


def _scan(sql):
    tokens, issues, stack = [], [], []
    i, size = 0, len(sql)

    def add(kind, start, end):
        tokens.append(_Token(kind, sql[start:end], start, end))

    def opaque(reason, start):
        issues.append(reason)
        add('opaque', start, size)

    while i < size:
        ch = sql[i]
        if ch in ' \t\r\n\f\v':
            i += 1
            continue
        if ch == '\x00' or 0xD800 <= ord(ch) <= 0xDFFF:
            opaque('invalid_encoding_or_nul', i)
            break
        if sql.startswith('--', i):
            end = sql.find('\n', i + 2)
            end = size if end < 0 else end
            if _invalid(sql[i:end]):
                opaque('invalid_encoding_or_nul', i)
                break
            if sql[i + 2:end].lstrip().startswith('+'):
                add('hint', i, end)
            i = end
            continue
        if sql.startswith('/*', i):
            start, depth = i, 1
            i += 2
            while i < size and depth:
                if sql.startswith('/*', i):
                    depth, i = depth + 1, i + 2
                elif sql.startswith('*/', i):
                    depth, i = depth - 1, i + 2
                else:
                    i += 1
            if depth:
                opaque('unclosed_comment', start)
                break
            if _invalid(sql[start:i]):
                opaque('invalid_encoding_or_nul', start)
                break
            if sql[start + 2:i - 2].lstrip().startswith('+'):
                add('hint', start, i)
            continue
        prefix = sql[i:i + 2].lower()
        if sql[i:i + 3].lower() in ("u&'", 'u&"'):
            opaque('unicode_escape_context', i)
            break
        if ch in "'\"" or prefix in ("e'", "b'", "x'", "n'"):
            start = i
            prefixed = ch not in "'\""
            quote_at = i + int(prefixed)
            quote = sql[quote_at]
            end = _quoted_end(sql, quote_at, quote, escaped=prefix == "e'")
            if end is None:
                opaque('unclosed_identifier' if quote == '"' else 'unclosed_string', start)
                break
            if _invalid(sql[start:end]):
                opaque('invalid_encoding_or_nul', start)
                break
            if quote == "'" and prefix != "e'" and '\\' in sql[quote_at:end]:
                opaque('ambiguous_string_escape', start)
                break
            kind = 'identifier' if quote == '"' else ('protected_literal' if prefixed else 'string')
            add(kind, start, end)
            i = end
            continue
        tag = _TAG.match(sql, i)
        if tag:
            end = sql.find(tag[0], tag.end())
            if end < 0:
                opaque('unclosed_dollar_quote', i)
                break
            end += len(tag[0])
            add('protected_literal', i, end)
            i = end
            continue
        match = _PARAM.match(sql, i)
        if match:
            add('parameter', i, match.end())
            i = match.end()
            continue
        match = _NUMBER.match(sql, i)
        if match:
            end = match.end()
            # Do not split a partial exponent, extended numeric syntax or word.
            if end < size and (sql[end].isalnum() or sql[end] in '_$'):
                opaque('ambiguous_numeric_boundary', i)
                break
            add('number', i, end)
            i = end
            continue
        match = _WORD.match(sql, i)
        if match:
            # Surrogateescape bytes must not hide inside a Unicode identifier.
            if any(0xD800 <= ord(c) <= 0xDFFF for c in match[0]):
                opaque('invalid_encoding_or_nul', i)
                break
            add('word', i, match.end())
            i = match.end()
            continue
        if ch in '([':
            stack.append(ch)
        elif ch in ')]':
            if not stack or stack[-1] != ('(' if ch == ')' else '['):
                opaque('mismatched_bracket', i)
                break
            stack.pop()
        if ch in _OPERATORS:
            end = i + 1
            while end < size and sql[end] in _OPERATORS:
                if sql.startswith('--', end) or sql.startswith('/*', end):
                    break
                end += 1
            add('operator', i, end)
        elif ch in '(),.;[]':
            end = i + 1
            add('punctuation', i, end)
        else:
            opaque('unknown_character', i)
            break
        i = end
    if stack:
        issues.append('unclosed_bracket')
    return tokens, sorted(set(issues)), list(stack)


def _protected_positions(tokens):
    # Protect entire calls (including nested subqueries) and explicitly cast
    # parenthesized expressions. Range counters avoid quadratic nested walks.
    stack, ranges = [], [0] * (len(tokens) + 1)
    grouping_words = frozenset(('where', 'and', 'or', 'not', 'in', 'exists', 'select', 'as', 'from'))

    def protect(start, end):
        ranges[start] += 1
        ranges[end] -= 1

    for i, token in enumerate(tokens):
        if token.word == '(':
            previous = tokens[i - 1] if i else None
            call = previous is not None and (
                previous.kind == 'identifier' or previous.word == ')' or
                (previous.kind == 'word' and previous.word not in grouping_words))
            stack.append((i, call))
        elif token.word == ')' and stack:
            start, call = stack.pop()
            cast = i + 1 < len(tokens) and tokens[i + 1].word == '::'
            if call or cast:
                protect(start, i + 1)
    for start, call in stack:
        if call:
            protect(start, len(tokens))
    active, protected = 0, set()
    for i, delta in enumerate(ranges[:-1]):
        active += delta
        if active:
            protected.add(i)
    return protected


def _business_positions(tokens, sql):
    """Conservative token patterns, never a tolerant SQL grammar or AST."""
    # Hints remain in the output stream but do not interrupt grammar neighbors
    # (function + opening bracket, closing bracket + cast, direct predicates).
    # Run every context check in the same syntax view, then map selections back.
    syntax_indices = [i for i, token in enumerate(tokens) if token.kind != 'hint']
    tokens = [tokens[i] for i in syntax_indices]
    words = [t.word for t in tokens]
    modes, query, stack = [], None, []
    mode = None
    for i, token in enumerate(tokens):
        word = words[i]
        if word == ';':
            query, mode, stack = None, None, []
        elif word == '(':
            inherit = mode if i and words[i - 1] in ('where', 'and', 'or', 'not', '(') else None
            stack.append((query, mode))
            query, mode = (query, inherit) if inherit else (None, None)
        elif word == ')':
            query, mode = stack.pop() if stack else (None, None)
        elif token.kind == 'word':
            if word in ('select', 'update', 'delete'):
                query, mode = word, None
            elif word == 'where' and query:
                mode = 'where'
            elif word == 'set' and query == 'update':
                mode = 'assignment'
            elif word in _END_CLAUSES or word in ('from', 'join', 'on', 'using'):
                mode = None
        modes.append(mode)

    def column_before(index):
        i = index - 1
        if i < 0 or tokens[i].kind not in ('word', 'identifier'):
            return False
        i -= 1
        while i >= 1 and words[i] == '.' and tokens[i - 1].kind in ('word', 'identifier'):
            i -= 2
        # A direct column starts a predicate/assignment, not a cast or expression.
        return i >= 0 and words[i] in ('where', 'and', 'or', 'not', '(', 'set', ',')

    def literal(index):
        if index >= len(tokens) or tokens[index].kind not in ('number', 'string', 'parameter'):
            return False
        t = tokens[index]
        if t.kind != 'string' and t.end == len(sql):
            return False
        if index and tokens[index - 1].end == t.start and tokens[index - 1].kind in ('word', 'number', 'parameter', 'protected_literal', 'string'):
            return False
        return True

    selected = set()
    for i, word in enumerate(words):
        if word not in ('=', '<>', '!=', '<', '>', '<=', '>=', 'in'):
            continue
        if modes[i] not in ('where', 'assignment') or not column_before(i):
            continue
        if word in ('=', '<>', '!=', '<', '>', '<=', '>='):
            j = i + 1
            if literal(j) and (j + 1 == len(tokens) or words[j + 1] in (')', ';', 'and', 'or', ',', 'returning') or words[j + 1] in _END_CLAUSES):
                selected.add(j)
        elif word == 'in' and modes[i] == 'where' and words[i + 1:i + 2] == ['(']:
            j, found = i + 2, []
            while literal(j):
                found.append(j)
                if j + 1 == len(tokens) or words[j + 1] == ')':
                    selected.update(found)
                    break
                if words[j + 1] != ',':
                    break
                j += 2
                if j == len(tokens) or tokens[j].kind == 'opaque':
                    selected.update(found)
                    break
    return {syntax_indices[i] for i in selected - _protected_positions(tokens)}


def _raw(sql):
    if isinstance(sql, bytes):
        return sql
    if isinstance(sql, str):
        return sql.encode('utf-8', 'surrogateescape')
    raise TypeError('SQL input must be str or bytes')


def fingerprint(sql, *, structural_reason, profile=PROFILE):
    """Approximate a known structural failure; never returns a reliable value.

    ``structural_reason`` is a fixed caller diagnostic, not a server message.
    The caller must preserve structural failure and keep this result out of
    normal baseline groups. Prefer ``analyze`` to obtain the parser diagnostic.
    """
    if profile != PROFILE:
        raise ValueError('unsupported source profile')
    if not isinstance(structural_reason, str) or not re.fullmatch(r'[a-z_]{1,80}', structural_reason):
        raise ValueError('structural_reason must be a fixed diagnostic code')
    raw = _raw(sql)
    result = dict(kind='approximate', state='unavailable', value=None, reason=None,
                  algorithm_version=VERSION, profile=profile, rules_digest=RULES_DIGEST,
                  rules_ref='builtin:' + VERSION, rules=json.loads(_canonical(_RULES)),
                  structural_reason=structural_reason, observation_only=True,
                  source=dict(byte_length=len(raw), sha256=hashlib.sha256(raw).hexdigest(),
                              bytes_base64=base64.b64encode(raw).decode('ascii') if len(raw) <= MAX_BYTES else None),
                  normalized=None, diagnostics=[], replacements=0)
    if len(raw) > MAX_BYTES:
        result['reason'] = 'input_size_limit'
        return result
    sql = raw.decode('utf-8', 'surrogateescape')
    tokens, issues, opened = _scan(sql)
    result['diagnostics'] = issues
    if not tokens or not any(t.kind == 'word' and t.word in _COMMANDS for t in tokens):
        result['reason'] = 'no_sql_tokens'
        return result
    selected = _business_positions(tokens, sql)
    sequence = []
    for i, token in enumerate(tokens):
        if i in selected:
            sequence.append(['business_value'])
        else:
            sequence.append([token.kind, token.word])
    normalized = dict(tokens=sequence, lexical_issues=issues, open_brackets=opened,
                      structural_reason=structural_reason, completeness='unverified')
    envelope = dict(kind='approximate', algorithm_version=VERSION, profile=profile,
                    rules_digest=RULES_DIGEST, normalized=normalized)
    value = hashlib.sha256(_canonical(envelope).encode('ascii')).hexdigest()
    result.update(state='available', value='approx:' + VERSION + ':' + value,
                  normalized=normalized, replacements=len(selected))
    return result


def analyze(sql, *, profile=PROFILE):
    """Run the existing MPP parser and approximate only its explicit refusals.

    A parsed AST is not yet a normalized structural fingerprint. Neither parser
    success nor an approximate result is exposed as product ``reliable`` here.
    Unexpected parser failures do not silently fall back to a lexical success.
    """
    from sql_apm.sql.mpp_parser import parse, Unsupported, VERSION as parser_version
    raw = _raw(sql)
    if profile != PROFILE:
        raise ValueError('unsupported source profile')
    result = dict(parser_version=parser_version, parser_state=None, parser_reason=None,
                  structure_fingerprint=None, approximate=None)
    if len(raw) > MAX_BYTES:
        result.update(parser_state='not_attempted', parser_reason='input_size_limit')
        return result
    try:
        parse(raw.decode('utf-8', 'surrogateescape'))
    except Unsupported as exc:
        reason = str(exc)
        result.update(parser_state='unsupported', parser_reason=reason)
        try:
            result['approximate'] = fingerprint(raw, structural_reason=reason, profile=profile)
        except Exception:
            result['approximate'] = dict(kind='approximate', state='failed', value=None,
                                         reason='approximation_exception', observation_only=True)
    except Exception:
        result.update(parser_state='failed', parser_reason='parser_exception')
    else:
        result['parser_state'] = 'parsed'
    return result
