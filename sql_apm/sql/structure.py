"""Stack-safe JSON operations for derived SQL structures, not fingerprints.

Keep the standard encoder/decoder fast path and identical canonical bytes.
Deep structures fall back to explicit stacks; the process recursion limit is
never changed. Structures use string object keys and JSON scalar values.
"""
import json


def loads(text):
    try:
        return json.loads(text)
    except RecursionError:
        return _loads_stack(text)


def _loads_stack(text):
    decoder = json.JSONDecoder()
    root, tasks, pos = [None], [], 0
    tasks.append(('value', root, 0))
    while tasks:
        kind, parent, key = tasks.pop()
        pos = json.decoder.WHITESPACE.match(text, pos).end()
        char = text[pos:pos + 1]
        if kind == 'value':
            if char in ('{', '['):
                node = {} if char == '{' else []
                parent[key] = node
                tasks.append(('object_first' if char == '{' else 'array_first', node, None))
                pos += 1
            else:
                parent[key], pos = decoder.raw_decode(text, pos)
        elif kind in ('object_first', 'object_key'):
            if char == '}' and kind == 'object_first':
                pos += 1
                continue
            if char != '"':
                raise ValueError('invalid structural JSON object key')
            name, pos = decoder.raw_decode(text, pos)
            pos = json.decoder.WHITESPACE.match(text, pos).end()
            if text[pos:pos + 1] != ':':
                raise ValueError('invalid structural JSON colon')
            pos += 1
            tasks.extend((('object_after', parent, None), ('value', parent, name)))
        elif kind in ('array_first', 'array_value'):
            if char == ']' and kind == 'array_first':
                pos += 1
                continue
            parent.append(None)
            tasks.extend((('array_after', parent, None), ('value', parent, len(parent) - 1)))
        else:
            closing = '}' if kind == 'object_after' else ']'
            if char == closing:
                pos += 1
            elif char == ',':
                pos += 1
                tasks.append(('object_key' if kind == 'object_after' else 'array_value', parent, None))
            else:
                raise ValueError('invalid structural JSON separator')
    if json.decoder.WHITESPACE.match(text, pos).end() != len(text):
        raise ValueError('trailing structural JSON content')
    return root[0]


def dumps(value):
    """Identical to json.dumps(sort_keys=True, ensure_ascii=True, separators=(',', ':'))."""
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(',', ':'))
    except RecursionError:
        return ''.join(iterencode(value))


def iterencode(value):
    # Iterators keep auxiliary space proportional to depth, not array width.
    active = set()
    tasks = [('value', value)]
    while tasks:
        kind, item = tasks.pop()
        if kind == 'text':
            yield item
        elif kind in ('object', 'array'):
            iterator, identity, first = item
            member = next(iterator, None)
            if member is None:
                yield '}' if kind == 'object' else ']'
                active.remove(identity)
                continue
            if not first:
                yield ','
            tasks.append((kind, (iterator, identity, False)))
            if kind == 'object':
                key, child = member
                if not isinstance(key, str):
                    raise TypeError('structural JSON requires string keys')
                yield json.dumps(key, ensure_ascii=True)
                yield ':'
            else:
                _, child = member
            tasks.append(('value', child))
        elif isinstance(item, (dict, list, tuple)):
            identity = id(item)
            if identity in active:
                raise ValueError('circular structural JSON')
            active.add(identity)
            if isinstance(item, dict):
                yield '{'
                tasks.append(('object', (iter(sorted(item.items())), identity, True)))
            else:
                yield '['
                tasks.append(('array', (iter(enumerate(item)), identity, True)))
        else:
            yield json.dumps(item, ensure_ascii=True, separators=(',', ':'))
