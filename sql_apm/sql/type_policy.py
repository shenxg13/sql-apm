"""Bounded PG 9.4 type facts; no aliases, casts, domains or catalog resolution.

Facts: PostgreSQL REL9_4_26 src/include/catalog/{pg_type,pg_range}.h.
Constraints: https://www.postgresql.org/docs/9.4/extend-type-system.html
Only the explicit built-in subset below is recognized. A leading underscore
alone is never evidence that an unknown type is an array.
"""

NORMALIZABLE_CASTS = frozenset('int2 int4 int8 float4 float8 numeric text varchar '
                             'bpchar bytea bit varbit date time timetz timestamp '
                             'timestamptz interval'.split())
RANGE_ELEMENTS = {'int4range': 'int4', 'int8range': 'int8', 'numrange': 'numeric',
                  'tsrange': 'timestamp', 'tstzrange': 'timestamptz', 'daterange': 'date'}
NON_ARRAY_TYPES = NORMALIZABLE_CASTS | frozenset(
    'bool oid regproc regprocedure regoper regoperator regclass regtype regconfig '
    'regdictionary json jsonb uuid'.split()) | frozenset(RANGE_ELEMENTS)
ARRAY_ELEMENTS = {'_' + name: name for name in NON_ARRAY_TYPES}
KNOWN_TYPES = NON_ARRAY_TYPES | frozenset(ARRAY_ELEMENTS)
POLYMORPHIC_TYPES = frozenset(('anyelement', 'anyarray', 'anynonarray', 'anyenum', 'anyrange'))


def signature_compatibility(actual, expected):
    """True: compatible candidate; False: contradiction; None: needs resolver.

    Null/absent types retain action-consensus selection. Complete literal
    signature equality also supports inventory audits using pseudo-type names;
    those are declarations, not proof of a resolved runtime call.
    """
    if actual is None or actual == expected:
        return True
    bindings = {'element': set(), 'array': set(), 'range': set()}
    unresolved = False
    for source, target in zip(actual, expected):
        if source is None:
            continue
        if target not in POLYMORPHIC_TYPES and target != 'any':
            if source != target:
                return False
            continue
        if source not in KNOWN_TYPES:
            unresolved = True
            continue
        if target == 'anyarray':
            if source not in ARRAY_ELEMENTS:
                return False
            bindings['array'].add(source)
            bindings['element'].add(ARRAY_ELEMENTS[source])
        elif target == 'anyrange':
            if source not in RANGE_ELEMENTS:
                return False
            bindings['range'].add(source)
            bindings['element'].add(RANGE_ELEMENTS[source])
        elif target == 'anyenum':
            # No user-defined enum catalog is available. None of this explicit
            # built-in subset is an enum; unknown names were deferred above.
            return False
        elif target in ('anyelement', 'anynonarray'):
            if target == 'anynonarray' and source in ARRAY_ELEMENTS:
                return False
            bindings['element'].add(source)
        # "any" is unconstrained, not a member of the linked polymorphic family.
    if any(len(values) > 1 for values in bindings.values()):
        return False
    return None if unresolved else True
