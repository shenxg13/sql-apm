"""Experimental MPP grammar adapter, NOT the product normalization engine.

Grammar evidence: GP6 gram.y at 9a08259bd1836f0cf5ba935e7e0030a5a9c0a54b.
Every removed source span is parsed into a typed extension first. Unsupported
forms raise a fixed diagnostic; no SQL/token-text fallback or partial batch.
"""
from bisect import bisect_right
from dataclasses import dataclass
import hashlib

from pglast import parser

from sql_apm.sql.lexical import diagnose
from sql_apm.sql.pg_ast import pg_clean
from sql_apm.sql.structure import dumps, loads

VERSION = 'mpp-adapter-probe/6'


class Unsupported(ValueError):
    """Arguments are fixed diagnostic codes, never source fragments."""


@dataclass(frozen=True)
class Token:
    raw: str
    name: str
    start: int
    end: int

    @property
    def word(self):
        if self.raw and (self.raw[0].isalpha() or self.raw[0] == '_') and self.name not in ('SCONST',):
            return self.raw.upper()
        return self.raw if len(self.raw) == 1 else ''


def pg_one(sql):
    try:
        stmts = loads(parser.parse_sql_json(sql))['stmts']
    except (parser.ParseError, UnicodeError):
        raise Unsupported('base_parser_rejected') from None
    if len(stmts) != 1:
        raise Unsupported('expected_one_statement')
    return pg_clean(stmts[0]['stmt'])


def expr(sql):
    tree = pg_one('SELECT ' + sql)
    select = tree.get('SelectStmt', {})
    targets = select.get('targetList', [])
    allowed = {'targetList', 'limitOption', 'op'}
    if len(targets) != 1 or set(select) - allowed:
        raise Unsupported('unsupported_extension_expression')
    target = targets[0]['ResTarget']
    if set(target) != {'val'}:
        raise Unsupported('unexpected_extension_alias')
    return target['val']


def identifier(raw):
    value = expr(raw).get('ColumnRef', {}).get('fields', [])
    if len(value) != 1 or set(value[0]) != {'String'}:
        raise Unsupported('expected_identifier')
    return value[0]['String']['sval']


class Cursor:
    def __init__(self, sql, tokens, index=0):
        self.sql, self.tokens, self.i = sql, tokens, index

    def at(self, *words):
        return [t.word for t in self.tokens[self.i:self.i + len(words)]] == list(words)

    def take(self, *words):
        if self.at(*words):
            self.i += len(words)
            return True
        return False

    def need(self, *words):
        if not self.take(*words):
            raise Unsupported('unexpected_extension_syntax')

    def token(self):
        if self.i >= len(self.tokens):
            raise Unsupported('incomplete_extension')
        token = self.tokens[self.i]
        self.i += 1
        return token

    def done(self):
        if self.i != len(self.tokens):
            raise Unsupported('unconsumed_extension')

    def literal(self, kinds=('SCONST',)):
        token = self.token()
        if token.name not in kinds:
            raise Unsupported('expected_literal')
        value = expr(token.raw)
        if set(value) != {'A_Const'}:
            raise Unsupported('expected_constant')
        return value

    def number(self):
        value = self.literal(('ICONST',))
        number = value['A_Const'].get('ival', {}).get('ival', 0)
        if type(number) is not int:
            raise Unsupported('unsupported_integer')
        return number

    def name(self):
        return identifier(self.token().raw)

    def group(self):
        self.need('(')
        start, level = self.i, 1
        while self.i < len(self.tokens):
            token = self.token()
            if token.word == '(':
                level += 1
            elif token.word == ')':
                level -= 1
                if not level:
                    return self.tokens[start:self.i - 1]
        raise Unsupported('unclosed_extension_group')


def fragment(sql, tokens):
    if not tokens:
        raise Unsupported('empty_extension_expression')
    return sql[tokens[0].start:tokens[-1].end]


def mask(sql, spans):
    result = list(sql)
    for start, end in spans:
        result[start:end] = ['\n' if c == '\n' else ' ' for c in sql[start:end]]
    return ''.join(result)


def top_level(tokens):
    depth = 0
    for i, token in enumerate(tokens):
        if token.word in (')', ']'):
            depth -= 1
        if depth == 0:
            yield i
        if token.word in ('(', '['):
            depth += 1


def distribution(cursor):
    cursor.need('DISTRIBUTED')
    if cursor.take('RANDOMLY'):
        return {'kind': 'random'}
    if cursor.take('REPLICATED'):
        return {'kind': 'replicated'}
    cursor.need('BY')
    members = cursor.group()
    if not members:
        raise Unsupported('empty_distribution_keys')
    # Let PG's index grammar validate column names and optional opclasses.
    # Accept only plain key columns; expression/order/collation forms are not
    # part of the corresponding GP6 distributed_by_elem production.
    tree = pg_one('CREATE INDEX probe_index ON probe_table (' + fragment(cursor.sql, members) + ')')
    params = tree['IndexStmt']['indexParams']
    seen = set()
    for node in params:
        member = node['IndexElem']
        if (member.get('name') is None or member.get('expr') is not None
                or member.get('collation') or member.get('opclassopts')
                or member.get('ordering') != 'SORTBY_DEFAULT'
                or member.get('nulls_ordering') != 'SORTBY_NULLS_DEFAULT'):
            raise Unsupported('unsupported_distribution_key')
        if member['name'] in seen:
            raise Unsupported('duplicate_distribution_key')
        seen.add(member['name'])
    return {'kind': 'hash', 'keys': params}


def external_options(cursor):
    """GP external OPTIONS: comma-separated label + string pairs."""
    group = Cursor(cursor.sql, cursor.group())
    result = []
    while group.i < len(group.tokens):
        result.append({'name': group.name(), 'value': group.literal()})
        if group.i < len(group.tokens):
            group.need(',')
            if group.i == len(group.tokens):
                raise Unsupported('trailing_option_comma')
    return result


def row_option_replacements(tokens):
    # GP6 def_arg explicitly maps the bare ROW token to String("row").
    # Only a complete option RHS is rewritten, never a query or literal.
    return [(t.start, t.end, "'row'") for i, t in enumerate(tokens)
            if t.word == 'ROW' and i > 0 and tokens[i - 1].word == '='
            and (i + 1 == len(tokens) or tokens[i + 1].word == ',')
            and i in set(top_level(tokens))]


def replace_spans(sql, spans):
    for start, end, text in sorted(spans, reverse=True):
        sql = sql[:start] + text + sql[end:]
    return sql


def pg_options(sql, tokens):
    start = tokens[0].start if tokens else 0
    text = fragment(sql, tokens)
    spans = [(a - start, b - start, value) for a, b, value in row_option_replacements(tokens)]
    tree = pg_one('CREATE TABLE probe_table () WITH (' + replace_spans(text, spans) + ')')
    return tree['CreateStmt'].get('options', [])


def custom_format_options(sql, tokens):
    # GP format_def_item: ColLabel '=' def_arg or '(' columnList ')'.
    result, start = [], 0
    ends = [i for i in top_level(tokens) if tokens[i].word == ','] + [len(tokens)]
    for end in ends:
        item = tokens[start:end]
        start = end + 1
        if len(item) < 3 or item[1].word != '=':
            raise Unsupported('invalid_custom_format_definition')
        # AS accepts ColLabel, including reserved words such as SELECT.
        label = pg_one('SELECT 1 AS ' + item[0].raw)['SelectStmt']['targetList'][0]['ResTarget']['name']
        values = item[2:]
        if values[0].word == '(':
            group = Cursor(sql, values)
            members = Cursor(sql, group.group())
            group.done()
            columns = [members.name()]
            while members.take(','):
                columns.append(members.name())
            members.done()
            value = {'column_list': columns}
        else:
            # Parse full def_arg (qualified identifiers, signed numerics,
            # strings, etc.) into PG nodes; retain its concrete value/type.
            options = pg_options(sql, item)
            if len(options) != 1 or options[0]['DefElem'].get('defnamespace'):
                raise Unsupported('invalid_custom_format_definition')
            value = options[0]['DefElem']['arg']
        result.append({'kind': 'definition', 'name': label, 'value': value})
    return result


def format_options(cursor):
    group = Cursor(cursor.sql, cursor.group())
    result = []
    if not group.tokens:
        return result
    if len(group.tokens) > 1 and group.tokens[1].word == '=':
        return custom_format_options(cursor.sql, group.tokens)
    while group.i < len(group.tokens):
        option = group.token().word
        if option in ('DELIMITER', 'NULL', 'QUOTE', 'ESCAPE', 'NEWLINE'):
            group.take('AS')
            value = group.literal()
        elif option in ('HEADER', 'CSV'):
            value = True
        elif option == 'FILL':
            group.need('MISSING', 'FIELDS')
            option, value = 'FILL MISSING FIELDS', True
        elif option == 'FORCE':
            if group.take('NOT', 'NULL'):
                option = 'FORCE NOT NULL'
            else:
                group.need('QUOTE')
                option = 'FORCE QUOTE'
            if group.take('*'):
                if option != 'FORCE QUOTE':
                    raise Unsupported('invalid_format_star')
                value = {'star': True}
            else:
                value = [group.name()]
                while group.take(','):
                    value.append(group.name())
        else:
            raise Unsupported('unsupported_format_option')
        result.append({'kind': 'legacy', 'name': option, 'value': value})
    return result


def external_create(sql, tokens):
    cursor = Cursor(sql, tokens)
    cursor.need('CREATE')
    mode = 'default'
    if cursor.at('READABLE') or cursor.at('WRITABLE'):
        mode = cursor.token().word.lower()
    cursor.need('EXTERNAL')
    web = cursor.take('WEB')
    prefix_end = cursor.i
    # Remaining TEMP modifiers and the table/column declaration use PG grammar.
    while not cursor.at('TABLE'):
        if cursor.token().word not in ('TEMP', 'TEMPORARY', 'LOCAL', 'GLOBAL'):
            raise Unsupported('unsupported_external_prefix')
    cursor.need('TABLE')
    while not cursor.at('('):
        cursor.token()
    cursor.group()
    column_end = cursor.tokens[cursor.i - 1].end
    base = pg_one(mask(sql[:column_end], [(tokens[1].start, tokens[prefix_end - 1].end)]))
    if set(base) != {'CreateStmt'}:
        raise Unsupported('external_definition_not_table')
    for column in base['CreateStmt'].get('tableElts', []):
        if 'ColumnDef' in column:
            if column['ColumnDef'].get('constraints') or column['ColumnDef'].get('collClause'):
                raise Unsupported('external_column_constraints')
        elif set(column) != {'TableLikeClause'}:
            raise Unsupported('unsupported_external_column')
    ext = {'kind': 'external_create', 'mode': mode, 'web': web}
    if cursor.take('LOCATION'):
        cursor.need('(')
        values = [cursor.literal()]
        while cursor.take(','):
            values.append(cursor.literal())
        cursor.need(')')
        ext['source'] = {'kind': 'location', 'values': values}
    elif cursor.take('EXECUTE'):
        if not web:
            raise Unsupported('execute_requires_web')
        ext['source'] = {'kind': 'execute', 'command': cursor.literal()}
    else:
        raise Unsupported('external_source_missing')
    execution = []
    while cursor.take('ON'):
        if cursor.take('ALL'):
            execution.append({'kind': 'all'})
        elif cursor.take('MASTER'):
            execution.append({'kind': 'master'})
        elif cursor.take('HOST'):
            host = cursor.literal() if cursor.i < len(tokens) and tokens[cursor.i].name == 'SCONST' else None
            execution.append({'kind': 'host', 'value': host})
        elif cursor.take('SEGMENT'):
            execution.append({'kind': 'segment', 'value': cursor.number()})
        else:
            execution.append({'kind': 'count', 'value': cursor.number()})
    if mode == 'writable' and ext['source']['kind'] == 'execute' and execution:
        raise Unsupported('writable_execute_on_clause')
    ext['execution'] = execution
    cursor.need('FORMAT')
    ext['format'] = cursor.literal()
    ext['format_options'] = format_options(cursor) if cursor.at('(') else None
    ext['options'] = external_options(cursor) if cursor.take('OPTIONS') else None
    encodings = []
    while cursor.take('ENCODING'):
        cursor.take('=')
        encodings.append(cursor.literal(('SCONST', 'ICONST')))
    ext['encodings'] = encodings
    log = None
    if cursor.take('LOG', 'ERRORS'):
        log = 'persistent' if cursor.take('PERSISTENTLY') else 'ordinary'
        if cursor.at('INTO'):
            raise Unsupported('error_table_session_dependency')
    if cursor.take('SEGMENT', 'REJECT', 'LIMIT'):
        limit = cursor.number()
        unit = 'ROWS'
        explicit_unit = False
        if cursor.at('ROWS') or cursor.at('PERCENT'):
            unit, explicit_unit = cursor.token().word, True
        if (unit == 'ROWS' and limit < 2) or (unit == 'PERCENT' and not 1 <= limit <= 100):
            raise Unsupported('invalid_reject_limit')
        if mode == 'writable':
            raise Unsupported('writable_error_handling')
        ext['errors'] = {'log': log, 'limit': limit, 'unit': unit, 'explicit_unit': explicit_unit}
    elif log:
        raise Unsupported('log_errors_without_limit')
    else:
        ext['errors'] = None
    ext['distribution'] = distribution(cursor) if cursor.at('DISTRIBUTED') else None
    cursor.done()
    return {'base': base, 'extensions': [ext]}


def analyze_rootpartition(sql, tokens):
    cursor = Cursor(sql, tokens, 1)
    cursor.take('VERBOSE')
    if not cursor.take('ROOTPARTITION'):
        return None
    start = tokens[cursor.i - 1].start
    all_relations = cursor.take('ALL')
    end = tokens[cursor.i - 1].end
    if not all_relations:
        cursor.name()
        while cursor.take('.'):
            cursor.name()
        if cursor.at('('):
            names = Cursor(sql, cursor.group())
            names.name()
            while names.take(','):
                names.name()
            names.done()
    cursor.done()
    base = pg_one(mask(sql, [(start, end)]))
    if set(base) != {'VacuumStmt'} or (not all_relations and len(base['VacuumStmt'].get('rels', [])) != 1):
        raise Unsupported('invalid_rootpartition_target')
    return {'base': base, 'extensions': [{'kind': 'analyze_rootpartition', 'all': all_relations}]}


def partition_values(sql, tokens):
    """GP tab_part_val: constants, casts and unary minus; never general SQL."""
    ends = [i for i in top_level(tokens) if tokens[i].word == ','] + [len(tokens)]
    values, start = [], 0
    for end in ends:
        node = expr(fragment(sql, tokens[start:end]))
        values.append(node)
        start = end + 1
        current = node
        while True:
            if set(current) == {'A_Const'}:
                break
            if set(current) == {'TypeCast'}:
                current = current['TypeCast']['arg']
                continue
            if set(current) == {'A_Expr'}:
                action = current['A_Expr']
                if (action.get('kind') == 'AEXPR_OP' and 'lexpr' not in action
                        and action.get('name') == [{'String': {'sval': '-'}}]):
                    current = action['rexpr']
                    continue
            raise Unsupported('unsupported_partition_boundary_expression')
    return values


def truncate_partition_action(sql, tokens):
    cursor = Cursor(sql, tokens)
    cursor.need('TRUNCATE')
    if cursor.take('DEFAULT'):
        cursor.need('PARTITION')
        target = {'kind': 'default'}
    else:
        cursor.need('PARTITION')
        if cursor.take('FOR'):
            group = Cursor(sql, cursor.group())
            if (len(group.tokens) > 1 and group.tokens[0].name == 'IDENT'
                    and identifier(group.tokens[0].raw) == 'rank' and group.tokens[1].word == '('):
                group.token()
                values = partition_values(sql, group.group())
                group.done()
                if (len(values) != 1 or set(values[0]) != {'A_Const'}
                        or not ({'ival', 'fval'} & set(values[0]['A_Const']))):
                    raise Unsupported('invalid_partition_rank')
                target = {'kind': 'rank', 'value': values[0]}
            else:
                target = {'kind': 'values', 'values': partition_values(sql, group.tokens)}
        else:
            target = {'kind': 'name', 'name': cursor.name()}
    behavior = 'restrict'
    if cursor.at('CASCADE') or cursor.at('RESTRICT'):
        behavior = cursor.token().word.lower()
    cursor.done()
    return {'kind': 'alter_truncate_partition', 'target': target, 'behavior': behavior}


def range_partition(sql, tokens, index):
    """Adapt GP range lists; retain the native PG path for a bare header."""
    cursor = Cursor(sql, tokens, index)
    cursor.need('PARTITION', 'BY')
    method = cursor.token().word
    key_tokens = cursor.group()
    if not cursor.at('('):
        return None  # Preserve existing PG partition headers and their syntax.
    keys = Cursor(sql, key_tokens)
    columns = [keys.name()]
    while keys.take(','):
        columns.append(keys.name())
    keys.done()
    if method != 'RANGE':
        raise Unsupported('unsupported_partition_specification')
    members = cursor.group()
    cursor.done()
    ends = [i for i in top_level(members) if members[i].word == ','] + [len(members)]
    parts, start, names = [], 0, set()
    for end in ends:
        member = Cursor(sql, members[start:end])
        start = end + 1
        default = member.take('DEFAULT')
        named = member.take('PARTITION')
        if default and not named:
            raise Unsupported('invalid_default_partition')
        name = member.name() if named else None
        bounds = {'start': None, 'end': None, 'every': None}
        for keyword in ('START', 'END', 'EVERY'):
            if member.take(keyword):
                if default or (keyword == 'EVERY' and not any(bounds.values())):
                    raise Unsupported('invalid_partition_boundary')
                values = partition_values(sql, member.group())
                if len(values) != len(columns):
                    raise Unsupported('partition_boundary_arity')
                if keyword == 'EVERY':
                    bounds['every'] = values
                else:
                    inclusive = keyword == 'START'
                    if member.at('INCLUSIVE') or member.at('EXCLUSIVE'):
                        inclusive = member.token().word == 'INCLUSIVE'
                    bounds[keyword.lower()] = {'values': values, 'inclusive': inclusive}
        if not named and not any(bounds.values()):
            raise Unsupported('missing_partition_definition')
        options = pg_options(sql, member.group()) if member.take('WITH') else None
        tablespace = member.name() if member.take('TABLESPACE') else None
        member.done()
        if name is not None:
            if name in names:
                raise Unsupported('duplicate_partition_name')
            names.add(name)
        if default and any(part['default'] for part in parts):
            raise Unsupported('duplicate_default_partition')
        parts.append(dict(name=name, default=default, bounds=bounds, options=options, tablespace=tablespace))
    return {'kind': 'range_partition', 'keys': columns, 'partitions': parts}


def alter_distribution_action(sql, tokens):
    cursor = Cursor(sql, tokens)
    cursor.need('SET')
    options = pg_options(sql, cursor.group()) if cursor.take('WITH') else None
    policy = distribution(cursor) if cursor.at('DISTRIBUTED') else None
    if options is None and policy is None:
        raise Unsupported('missing_alter_distribution')
    cursor.done()
    return {'kind': 'alter_distribution', 'options': options, 'policy': policy}


def alter_table_actions(sql, tokens):
    """Preserve every action in source order, with one shared validated target.

    MPP actions temporarily become PG helper actions only for validating the
    complete statement. Index-based replacement and one-to-one node checks
    ensure ordinary actions (even identical to the helper) are never removed.
    For an adapted ALTER, extensions is the entire ordered action sequence.
    """
    cursor = Cursor(sql, tokens)
    cursor.need('ALTER', 'TABLE')
    if cursor.take('IF'):
        cursor.need('EXISTS')
    only = cursor.take('ONLY')
    if only and cursor.at('('):
        cursor.group()
    else:
        cursor.token()
        while cursor.take('.'):
            cursor.token()
        if not only:
            cursor.take('*')
    if cursor.i == len(tokens):
        raise Unsupported('missing_alter_action')
    # Token consumption only finds the boundary. PG validates every target
    # token (ONLY, catalog/schema, quoting, IF EXISTS, and inheritance).
    header = sql[:tokens[cursor.i].start]
    helper = 'SET (prototype_key=true)'
    header_tree = pg_one(header + helper)
    if set(header_tree) != {'AlterTableStmt'}:
        raise Unsupported('unsupported_alter_target')
    expected = header_tree['AlterTableStmt']
    helper_commands = expected.pop('cmds')
    if expected.get('objtype') != 'OBJECT_TABLE' or len(helper_commands) != 1:
        raise Unsupported('unsupported_alter_target')

    tail = tokens[cursor.i:]
    stops = [i for i in top_level(tail) if tail[i].word == ','] + [len(tail)]
    parts, start = [], 0
    for stop in stops:
        if stop == start:
            raise Unsupported('empty_alter_action')
        parts.append(tail[start:stop])
        start = stop + 1
    adapted, spans = {}, []
    for index, part in enumerate(parts):
        if [t.word for t in part[:2]] in (['SET', 'WITH'], ['SET', 'DISTRIBUTED']):
            adapted[index] = alter_distribution_action(sql, part)
            spans.append((part[0].start, part[-1].end, helper))
        elif [t.word for t in part[:2]] in (['TRUNCATE', 'PARTITION'], ['TRUNCATE', 'DEFAULT']):
            adapted[index] = truncate_partition_action(sql, part)
            spans.append((part[0].start, part[-1].end, helper))
    if not adapted:
        # A lookalike phrase inside an ordinary action must stay on PG's path.
        return {'base': pg_one(sql), 'extensions': []}
    parsed = pg_one(replace_spans(sql, spans))
    if set(parsed) != {'AlterTableStmt'}:
        raise Unsupported('unsupported_alter_action_form')
    base = parsed['AlterTableStmt']
    commands = base.pop('cmds')
    if base != expected or len(commands) != len(parts):
        raise Unsupported('alter_action_structure_mismatch')
    actions = []
    for index, command in enumerate(commands):
        if index in adapted:
            if command != helper_commands[0]:
                raise Unsupported('alter_action_structure_mismatch')
            actions.append(adapted[index])
        else:
            actions.append({'kind': 'postgres_alter', 'command': command})
    return {'base': {'AlterTableStmt': base}, 'extensions': actions}


def create_row_compat(sql, tokens):
    cursor = Cursor(sql, tokens)
    if not cursor.take('CREATE'):
        return sql
    while cursor.i < len(tokens) and cursor.tokens[cursor.i].word in ('TEMP', 'TEMPORARY', 'LOCAL', 'GLOBAL', 'UNLOGGED'):
        cursor.i += 1
    if not cursor.take('TABLE'):
        return sql
    spans = []
    for i in top_level(tokens):
        if tokens[i].word == 'AS':
            break  # The CTAS query starts here; never rewrite its expressions.
        if (tokens[i].word in ('WITH', 'WITHOUT') and i + 1 < len(tokens)
                and tokens[i + 1].word == 'OIDS'):
            value = 'true' if tokens[i].word == 'WITH' else 'false'
            spans.append((tokens[i].start, tokens[i + 1].end, 'WITH (oids=' + value + ')'))
        if tokens[i].word == 'WITH' and i + 1 < len(tokens) and tokens[i + 1].word == '(':
            members = Cursor(sql, tokens, i + 1).group()
            spans.extend(row_option_replacements(members))
    return replace_spans(sql, spans)


def statement(sql, tokens):
    if not tokens:
        raise Unsupported('empty_statement')
    words = [t.word for t in tokens]
    if words[:2] == ['ALTER', 'TABLE']:
        commands = [i for i in top_level(tokens) if words[i:i + 2] in
                    (['SET', 'WITH'], ['SET', 'DISTRIBUTED'],
                     ['TRUNCATE', 'PARTITION'], ['TRUNCATE', 'DEFAULT'])]
        if commands:
            return alter_table_actions(sql, tokens)
    if words[0] in ('ANALYZE', 'ANALYSE'):
        adapted = analyze_rootpartition(sql, tokens)
        if adapted is not None:
            return adapted
    if words[0] == 'CREATE' and (words[1:2] == ['EXTERNAL'] or
                               words[1:3] in (['READABLE', 'EXTERNAL'], ['WRITABLE', 'EXTERNAL'])):
        return external_create(sql, tokens)
    if words[:2] == ['DROP', 'EXTERNAL']:
        end = 3 if words[2:3] == ['WEB'] else 2
        if words[end:end + 1] != ['TABLE']:
            raise Unsupported('unsupported_external_drop')
        base = pg_one(mask(sql, [(tokens[1].start, tokens[end - 1].end)]))
        if set(base) != {'DropStmt'} or base['DropStmt']['removeType'] != 'OBJECT_TABLE':
            raise Unsupported('unsupported_external_drop')
        return {'base': base, 'extensions': [{'kind': 'external_drop', 'web': end == 3}]}
    extensions, spans = [], []
    indices = list(top_level(tokens))
    distro = [i for i in indices if words[i] == 'DISTRIBUTED' and
              words[i + 1:i + 2] in (['BY'], ['RANDOMLY'], ['REPLICATED'])]
    if distro:
        if words[0] != 'CREATE' or len(distro) != 1:
            raise Unsupported('unsupported_distribution_statement')
        cursor = Cursor(sql, tokens, distro[0])
        policy = distribution(cursor)
        if cursor.i != len(tokens) and not cursor.at('PARTITION', 'BY'):
            cursor.done()
        spans.append((tokens[distro[0]].start, tokens[cursor.i - 1].end))
        extensions.append({'kind': 'distribution', 'policy': policy})
    partitions = [i for i in indices if words[i:i + 2] == ['PARTITION', 'BY']]
    if words[0] == 'CREATE' and partitions:
        if len(partitions) != 1:
            raise Unsupported('multiple_partition_clauses')
        partition = range_partition(sql, tokens, partitions[0])
        if partition is not None:
            if distro and distro[0] > partitions[0]:
                raise Unsupported('invalid_partition_position')
            extensions.append(partition)
            spans.append((tokens[partitions[0]].start, tokens[-1].end))
    if words[0] == 'COPY':
        on = [i for i in indices if words[i:i + 2] == ['ON', 'SEGMENT']]
        directions = [i for i in indices if words[i] in ('TO', 'FROM')]
        if on:
            if len(on) != 1 or len(directions) != 1 or on[0] <= directions[0] + 1:
                raise Unsupported('invalid_copy_on_segment')
            start, end = tokens[on[0]].start, tokens[on[0] + 1].end
            # GP6 ON SEGMENT is one legacy copy_opt_item. Validate its exact
            # position with another legacy-only option before removing it.
            # This rejects placement inside PROGRAM/file syntax or beside a
            # parenthesized generic option list; masking alone accepts both.
            checked = pg_one(replace_spans(sql, [(start, end, 'FREEZE')]))
            if set(checked) != {'CopyStmt'}:
                raise Unsupported('invalid_copy_on_segment')
            spans.append((start, end))
            extensions.append({'kind': 'copy_on_segment', 'enabled': True})
    base = pg_one(create_row_compat(mask(sql, spans), tokens))
    if distro and set(base) not in ({'CreateStmt'}, {'CreateTableAsStmt'}):
        raise Unsupported('distribution_requires_create_table')
    if any(e['kind'] == 'range_partition' for e in extensions) and set(base) != {'CreateStmt'}:
        raise Unsupported('partition_requires_create_table')
    if any(e['kind'] == 'copy_on_segment' for e in extensions) and set(base) != {'CopyStmt'}:
        raise Unsupported('copy_extension_requires_copy')
    return {'base': base, 'extensions': extensions}



def _anchor_hints(hints, tokens, ranges):
    # Full PG ASTs discard redundant parentheses. A gap alone (even scoped to
    # a statement) can therefore identify different grammar positions. Bind
    # each gap to the complete ordered scanner-kind frame, including brackets.
    # The frame supplements the full AST; it is never a SQL-text fingerprint.
    # Value kinds share a slot: the AST/normalizer owns value protection, while
    # an otherwise identical business literal/parameter stays interchangeable.
    values = frozenset(('ICONST', 'FCONST', 'SCONST', 'BCONST', 'XCONST', 'PARAM'))
    ends = [t.end for t in tokens]
    starts = [a for a, _ in ranges]
    frames = {}

    def frame(key, begin, end):
        if key not in frames:
            kinds = ['VALUE' if t.name in values else t.name for t in tokens[begin:end]]
            frames[key] = hashlib.sha256(dumps(kinds).encode('ascii')).hexdigest()
        return frames[key]

    for hint in hints:
        offset = hint.pop('start')
        gap = bisect_right(ends, offset)
        # Newline-separated string fragments can form one scanner token.
        if gap < len(tokens) and tokens[gap].start < offset < tokens[gap].end:
            raise Unsupported('hint_inside_combined_token')
        hint['gap'] = gap
        index = bisect_right(starts, gap) - 1
        if index >= 0 and gap <= ranges[index][1]:
            begin, end = ranges[index]
            hint['anchor'] = dict(kind='statement', statement_index=index,
                                  token_gap=gap - begin, syntax_sha256=frame(index, begin, end))
        else:
            # Empty statements and trailing batch comments have no statement
            # owner. Keep their separator position and full batch frame.
            hint['anchor'] = dict(kind='batch_boundary', statement_index=index + 1,
                                  token_gap=gap, syntax_sha256=frame('batch', 0, len(tokens)))


def parse(sql):
    """Return complete derived structure or raise Unsupported (no partial AST)."""
    if not isinstance(sql, str) or not sql.strip():
        raise Unsupported('sql_missing_or_empty')
    try:
        sql.encode('utf-8', 'strict')
    except UnicodeError:
        raise Unsupported('invalid_encoding') from None
    if '\x00' in sql:
        raise Unsupported('nul_input')
    _, issues = diagnose(sql)
    if issues:
        raise Unsupported('lexical_' + issues[0])
    try:
        scanned = parser.scan(sql)
    except parser.ParseError:
        raise Unsupported('scanner_rejected') from None
    hints, comments = [], []
    for t in scanned:
        raw = sql[t.start:t.end + 1]
        if t.name in ('C_COMMENT', 'SQL_COMMENT'):
            comments.append((t.start, t.end + 1))
            if raw.startswith(('/*+', '--+')):
                if '/*' in raw[3:] or '*/' in raw[3:-2]:
                    raise Unsupported('ambiguous_nested_hint')
                hints.append({'start': t.start, 'kind': t.name, 'raw': raw})
    # pglast 7.18 rejects comments between lookahead keywords (NOT IN,
    # NULLS FIRST). Blanking lexer-confirmed comments preserves offsets and
    # newlines; Hint contents/anchors have already been saved above.
    sql = mask(sql, comments)
    try:
        scanned = parser.scan(sql)
    except parser.ParseError:
        raise Unsupported('scanner_rejected') from None
    tokens = [Token(sql[t.start:t.end + 1], t.name, t.start, t.end + 1) for t in scanned]
    # Dollar quoted procedural bodies are one token; semicolons inside do not split.
    ranges, start = [], 0
    for i in top_level(tokens):
        if tokens[i].word == ';':
            if i > start:
                ranges.append((start, i))
            start = i + 1
    if start < len(tokens):
        ranges.append((start, len(tokens)))
    if not ranges:
        raise Unsupported('sql_missing_or_empty')
    if hints:
        _anchor_hints(hints, tokens, ranges)
    result = []
    for start, stop in ranges:
        group = tokens[start:stop]
        begin, end = group[0].start, group[-1].end
        local = [Token(t.raw, t.name, t.start - begin, t.end - begin) for t in group]
        result.append(statement(sql[begin:end], local))
    return {'version': VERSION, 'statements': result, 'hints': hints}
