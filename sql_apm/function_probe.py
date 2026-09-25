"""Bounded lexical call candidates for diagnostics, never a SQL parser.

Strings/comments are skipped; uncertain SQL and unsupported forms are counted.
Names and SQL must not be included in public sample reports.
"""
import re
from dataclasses import dataclass


class ProbeError(ValueError):
    pass


@dataclass(frozen=True)
class Token:
    text: str
    kind: str
    offset: int


def tokens(sql):
    if '\x00' in sql or any('\udc80' <= char <= '\udcff' for char in sql):
        raise ProbeError('invalid_encoding_or_nul')
    result, pos, size = [], 0, len(sql)
    while pos < size:
        char = sql[pos]
        if char.isspace():
            pos += 1
            continue
        if sql.startswith('--', pos):
            end = sql.find('\n', pos + 2)
            pos = size if end < 0 else end + 1
            continue
        if sql.startswith('/*', pos):
            level, pos = 1, pos + 2
            while pos < size and level:
                if sql.startswith('/*', pos):
                    level, pos = level + 1, pos + 2
                elif sql.startswith('*/', pos):
                    level, pos = level - 1, pos + 2
                else:
                    pos += 1
            if level:
                raise ProbeError('unclosed_comment')
            continue
        if char in "'\"" or (char in 'eE' and sql[pos:pos+2].lower() == "e'"):
            start, escaped = pos, char in 'eE'
            if escaped:
                pos += 1
            quote, pos = sql[pos], pos + 1
            while pos < size:
                if sql[pos] == '\\' and quote == "'":
                    if not escaped:
                        raise ProbeError('ambiguous_string_escape')
                    pos += 2
                elif sql[pos] == quote:
                    if pos + 1 < size and sql[pos + 1] == quote:
                        pos += 2
                    else:
                        pos += 1
                        break
                else:
                    pos += 1
            else:
                raise ProbeError('unclosed_quote')
            result.append(Token(sql[start:pos] if quote == '"' else '', 'identifier' if quote == '"' else 'value', start))
            continue
        dollar = re.match(r'\$(?:[A-Za-z_][A-Za-z_0-9]*)?\$', sql[pos:]) if char == '$' else None
        if dollar:
            end = sql.find(dollar[0], pos + len(dollar[0]))
            if end < 0:
                raise ProbeError('unclosed_dollar_quote')
            result.append(Token('', 'value', pos))
            pos = end + len(dollar[0])
            continue
        word = re.match(r'[A-Za-z_][A-Za-z_0-9$]*', sql[pos:])
        if word:
            result.append(Token(word[0], 'identifier', pos))
            pos += len(word[0])
            continue
        if '\udc80' <= char <= '\udcff' or ord(char) == 0:
            raise ProbeError('invalid_encoding_or_nul')
        if ord(char) > 127:
            raise ProbeError('unsupported_unquoted_identifier')
        operator = next((op for op in ('::', '=>', ':=') if sql.startswith(op, pos)), None)
        result.append(Token(operator or char, 'symbol', pos))
        pos += len(operator) if operator else 1
        if len(result) > 100000:
            raise ProbeError('token_limit')
    return result


# These are syntactic constructs, not ordinary function-call candidates.
EXCLUDED = set('select from where having in exists any all some not and or as on using values into '
               'over filter within group order by distinct case when then else end cast extract trim '
               'coalesce nullif greatest least row array grouping sets rollup cube with recursive '
               'partition limit offset union intersect except'.split())


def call_candidates(sql):
    ts = tokens(sql)
    stack, pairs = [], {}
    for i, token in enumerate(ts):
        if token.text in ('(', '['):
            stack.append(i)
            if len(stack) > 100:
                raise ProbeError('nesting_limit')
        elif token.text in (')', ']'):
            if not stack or ts[stack[-1]].text != ( '(' if token.text == ')' else '['):
                raise ProbeError('unbalanced_bracket')
            pairs[stack.pop()] = i
    if stack:
        raise ProbeError('unbalanced_bracket')
    calls, excluded = [], 0
    # DDL and procedural bodies cannot be reliably diagnosed as expression calls.
    if any(t.kind == 'identifier' and t.text.lower() in ('create', 'alter', 'drop', 'do') for t in ts):
        raise ProbeError('ddl_or_procedural_sql')
    for i in range(1, len(ts)):
        if ts[i].text != '(' or ts[i-1].kind != 'identifier':
            continue
        name = ts[i-1].text
        if name.lower() in EXCLUDED:
            excluded += 1
            continue
        schema = None
        if i >= 3 and ts[i-2].text == '.' and ts[i-3].kind == 'identifier':
            schema = ts[i-3].text
            if i >= 4 and ts[i-4].text == '.':
                excluded += 1
                continue
        # CAST type modifiers, table column lists and FROM aliases are uncertain.
        before = i - 4 if schema is not None else i - 2
        if before >= 0 and ts[before].text.lower() in ('as', 'into', 'update', 'table', '::'):
            excluded += 1
            continue
        end, arity, cursor, form = pairs[i], 0 if pairs[i] == i+1 else 1, i+1, 'positional'
        while cursor < end:
            text = ts[cursor].text.lower()
            if text in ('=>', ':=', 'variadic', 'distinct', 'order', 'from', 'for', 'in', 'placing', '*'):
                form = 'special'
            if text == ',':
                arity += 1
            if ts[cursor].text in ('(', '['):
                cursor = pairs[cursor] + 1
            else:
                cursor += 1
        if arity > 10000:
            raise ProbeError('argument_limit')
        calls.append(dict(name=name, schema=schema, arity=arity, form=form, offset=ts[i-1].offset))
    return calls, excluded
