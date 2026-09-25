"""Versioned function policies; no SQL parser, database lookup or fingerprint engine."""
import argparse
import copy
import hashlib
import json
import re
import sys
from pathlib import Path

from .type_policy import NORMALIZABLE_CASTS, signature_compatibility


class DictionaryError(ValueError):
    """Invalid configuration; callers must not use a partial dictionary."""


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise DictionaryError('duplicate JSON key: ' + key)
        result[key] = value
    return result


def read_json(path):
    def invalid_number(value):
        raise DictionaryError('non-finite JSON number: ' + value)
    with Path(path).open(encoding='utf-8') as stream:
        return json.load(stream, object_pairs_hook=_object, parse_constant=invalid_number)


def _require(condition, message):
    if not condition:
        raise DictionaryError(message)


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def validate(data):
    _require(isinstance(data, dict), 'dictionary must be an object')
    required = {'schema_version', 'rules_version', 'profile', 'rules'}
    _require(set(data) == required, 'dictionary fields must be ' + ', '.join(sorted(required)))
    _require(type(data['schema_version']) is int and data['schema_version'] == 1, 'unsupported schema_version')
    _require(isinstance(data['rules_version'], str) and
             re.fullmatch(r'[1-9]\d*\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)', data['rules_version']), 'invalid rules_version')
    _require(data['profile'] == 'hashdata-pg94', 'unsupported profile')
    _require(isinstance(data['rules'], list) and data['rules'], 'rules must be a nonempty list')
    ids, keys = set(), set()
    fields = {'id', 'name', 'schema', 'types', 'defaults', 'variadic', 'kind',
              'allow_unqualified', 'decision', 'arguments', 'rationale', 'sources', 'enabled'}
    for index, rule in enumerate(data['rules']):
        label = 'rules[{}]'.format(index)
        _require(isinstance(rule, dict) and set(rule) == fields, label + ': missing or unknown fields')
        for field in ('id', 'name', 'schema', 'rationale'):
            _require(_text(rule[field]), label + ': invalid ' + field)
        label = rule['id']
        _require(label not in ids, label + ': duplicate rule id')
        ids.add(label)
        _require(isinstance(rule['types'], list) and all(_text(t) for t in rule['types']), label + ': invalid types')
        count = len(rule['types'])
        _require(type(rule['defaults']) is int and 0 <= rule['defaults'] <= count, label + ': invalid defaults')
        _require(rule['variadic'] is None or (_text(rule['variadic']) and count > 0), label + ': invalid variadic')
        _require(rule['kind'] in ('function', 'aggregate', 'window'), label + ': invalid kind')
        _require(type(rule['allow_unqualified']) is bool and type(rule['enabled']) is bool, label + ': invalid flags')
        _require(rule['decision'] in ('normalize', 'preserve', 'pending'), label + ': invalid decision')
        _require(isinstance(rule['sources'], list) and rule['sources'] and
                 all(_text(s) and s.startswith('https://') for s in rule['sources']), label + ': invalid sources')
        key = (rule['schema'], rule['name'], tuple(rule['types']), rule['kind'])
        _require(key not in keys, label + ': duplicate/conflicting signature')
        keys.add(key)
        args = rule['arguments']
        _require(isinstance(args, list) and len(args) == count, label + ': argument count differs from signature')
        positions, actions = set(), []
        for arg in args:
            _require(isinstance(arg, dict) and set(arg) == {'position', 'action', 'role'}, label + ': invalid argument fields')
            position = arg['position']
            _require(type(position) is int and 1 <= position <= count and position not in positions,
                     label + ': argument position duplicate or out of bounds')
            positions.add(position)
            _require(arg['action'] in ('normalize', 'preserve'), label + ': unknown action')
            _require(_text(arg['role']), label + ': missing argument role')
            actions.append(arg['action'])
        _require(positions == set(range(1, count + 1)), label + ': incomplete positions')
        _require((rule['decision'] == 'normalize') == ('normalize' in actions), label + ': decision/actions disagree')
    return data


def digest(data):
    validate(data)
    canonical = copy.deepcopy(data)
    canonical['rules'].sort(key=lambda r: r['id'])
    for rule in canonical['rules']:
        rule['arguments'].sort(key=lambda a: a['position'])
    encoded = json.dumps(canonical, ensure_ascii=False, sort_keys=True,
                         separators=(',', ':'), allow_nan=False).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def identifier(value):
    """One lexical identifier; never accepts a dotted qualified name."""
    if not isinstance(value, str) or not value:
        raise ValueError('identifier must be a nonempty string')
    if re.fullmatch(r'"(?:[^"]|"")+"', value):
        return value[1:-1].replace('""', '"')
    if not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9$]*', value):
        raise ValueError('unsupported identifier spelling')
    return value.lower()


class FunctionDictionary:
    def __init__(self, data):
        self._data = copy.deepcopy(validate(data))
        self.rules_version = self._data['rules_version']
        self.sha256 = digest(self._data)
        self._index = {}
        for rule in self._data['rules']:
            self._index.setdefault((rule['schema'], rule['name']), []).append(rule)

    @classmethod
    def load(cls, path):
        return cls(read_json(path))

    def select(self, name, arity, schema=None, types=None, kind='function',
               form='positional', protected=False):
        if type(arity) is not int or arity < 0 or arity > 10000:
            raise ValueError('invalid arity')
        if types is not None and (not isinstance(types, list) or len(types) != arity or
                                  not all(t is None or _text(t) for t in types)):
            raise ValueError('types must have one canonical type name or null per argument')

        def fallback(reason, ids=()):
            return dict(decision='preserve', reason=reason, actions=['preserve'] * arity,
                        rule_ids=sorted(ids), rules_version=self.rules_version, sha256=self.sha256)

        if protected:
            return fallback('protected_context')
        if form != 'positional':
            return fallback('unsupported_call_form')
        try:
            resolved_name = identifier(name)
            resolved_schema = 'pg_catalog' if schema is None else identifier(schema)
        except ValueError:
            return fallback('unsupported_identifier')
        rules = self._index.get((resolved_schema, resolved_name), [])
        matches = []
        unresolved = []
        for rule in rules:
            if rule['kind'] != kind or (schema is None and not rule['allow_unqualified']):
                continue
            count = len(rule['types'])
            if rule['variadic'] is not None:
                # Expanded, explicit VARIADIC-array and zero-element forms need a
                # parser/type resolver. No parameter normalization for these yet.
                if arity >= count - 1:
                    matches.append((rule, ['preserve'] * arity, False))
                continue
            if not count - rule['defaults'] <= arity <= count:
                continue
            expected = rule['types'][:arity]
            compatible = signature_compatibility(types, expected)
            if compatible is False:
                continue
            if compatible is None:
                unresolved.append(rule['id'])
                continue
            exact = types is not None and all(t is not None for t in types) and types == expected
            actions = [a['action'] for a in sorted(rule['arguments'], key=lambda a: a['position'])][:arity]
            matches.append((rule, actions, exact))
        if any(exact for _, _, exact in matches):
            matches = [m for m in matches if m[2]]
        elif unresolved:
            return fallback('polymorphic_requires_resolver', unresolved)
        if not matches:
            return fallback('no_matching_rule')
        ids = [r['id'] for r, _, _ in matches]
        if any(not r['enabled'] for r, _, _ in matches):
            return fallback('disabled_rule', ids)
        if any(r['decision'] == 'pending' for r, _, _ in matches):
            return fallback('pending_review', ids)
        if any(r['variadic'] is not None for r, _, _ in matches):
            return fallback('variadic_requires_resolver', ids)
        actions = {tuple(a) for _, a, _ in matches}
        if len(actions) != 1:
            return fallback('ambiguous_overload', ids)
        actions = list(next(iter(actions)))
        return dict(decision='normalize' if 'normalize' in actions else 'preserve',
                    reason='matched', actions=actions, rule_ids=sorted(ids),
                    rules_version=self.rules_version, sha256=self.sha256)

    def preview(self, expression, context='ordinary'):
        """Apply policies to artificial expression trees, NOT to SQL strings.

        Unknown nodes and control subtrees are copied. Operators keep their
        constants; recognized nested calls can still apply their own rules.
        """
        if context not in ('ordinary', 'set', 'limit', 'offset'):
            raise ValueError('unsupported context')
        if context != 'ordinary':
            return copy.deepcopy(expression)

        def visit(node, business=False, depth=0):
            if depth > 100:
                raise ValueError('expression nesting exceeds 100')
            if not isinstance(node, dict):
                raise ValueError('expression node must be an object')
            result = copy.deepcopy(node)
            tag = node.get('kind')
            if tag == 'literal' and business:
                if set(node) == {'kind', 'value'} and type(node['value']) in (str, int, float):
                    return {'kind': 'business_value'}
            elif tag == 'parameter' and business:
                if set(node) == {'kind', 'index'} and type(node['index']) is int and node['index'] > 0:
                    return {'kind': 'business_value'}
            elif tag == 'cast':
                # Preserve the entire subtree across identity, structured, bool
                # and unknown conversions, even inside an outer text cast.
                if node.get('type') not in NORMALIZABLE_CASTS:
                    return result
                result['expr'] = visit(node['expr'], business, depth + 1)
            elif tag == 'operator':
                result['args'] = [visit(child, False, depth + 1) for child in node['args']]
            elif tag == 'call':
                choice = self.select(node['name'], len(node['args']), schema=node.get('schema'),
                                     types=node.get('types'), kind=node.get('call_kind', 'function'),
                                     form=node.get('form', 'positional'))
                result['args'] = [visit(arg, True, depth + 1) if action == 'normalize'
                                  else copy.deepcopy(arg)
                                  for arg, action in zip(node['args'], choice['actions'])]
            return result
        return visit(expression)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    command = sub.add_parser('validate')
    command.add_argument('dictionary', type=Path)
    command = sub.add_parser('select')
    command.add_argument('dictionary', type=Path)
    command.add_argument('call', type=Path, help='JSON object with name, arity and optional schema/types/kind/form')
    args = parser.parse_args()
    try:
        dictionary = FunctionDictionary.load(args.dictionary)
        result = {'rules_version': dictionary.rules_version, 'sha256': dictionary.sha256,
                  'rules': len(dictionary._data['rules'])} if args.command == 'validate' else dictionary.select(**read_json(args.call))
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    except (DictionaryError, ValueError, TypeError, OSError) as error:
        print('ERROR: ' + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
