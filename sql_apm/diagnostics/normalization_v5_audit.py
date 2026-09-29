"""Independent v4 -> v5 structure projection, for diagnostics only.

The frozen result is the input. Optional branch restoration calls ONLY a
version-checked frozen v4 walker on independently wrapped SelectStmt bodies. Explicit reachability prevents entering protected controls.
Dictionary selection and cast classification are unchanged shared policies; the
v5 walker, _fields and bucket implementation are never invoked.
"""
from collections import Counter

from sql_apm.sql.normalization import _cast_type
from sql_apm.sql.structure import dumps, loads
from sql_apm.sql.type_policy import NORMALIZABLE_CASTS


def project_v5(tree, engine, select=True, join=True, sets=False, restore=False):
    if restore and (not sets or engine.context['algorithm_version'] != 'sql-normalization/4'):
        raise ValueError('frozen_v4_required_for_branch_restoration')
    result = loads(dumps(tree))
    counts = Counter()
    # Mutate only this private copy. Each task supplies a node and its new scope.
    stack = [(statement['base'], 'scope') for statement in result['statements']]
    while stack:
        node, scope = stack.pop()
        if scope == 'fold':
            items = node.get('items')
            if isinstance(items, list) and items and all(
                    item == {'SQLAPMBusinessValue': {}} for item in items):
                length = len(items)
                label = next(label for maximum, label in (
                    (1, '1'), (10, '2-10'), (100, '11-100'), (float('inf'), '>100'))
                    if length <= maximum)
                node['items'] = {'SQLAPMInBucket': label}
                counts['in_lists_bucketed'] += 1
            continue
        if isinstance(node, list):
            stack.extend((child, scope) for child in node)
            continue
        if not isinstance(node, dict):
            continue
        if scope in ('with', 'conflict'):
            allowed = {'ctes': 'scope'} if scope == 'with' else {
                'targetList': 'scope', 'whereClause': 'scope'}
            stack.extend((node[field], mode) for field, mode in allowed.items() if field in node)
            continue
        if scope == 'branch':
            if restore:
                restored = engine._walk({'SelectStmt': node}, Counter())['SelectStmt']
                counts['set_branches_visited'] += 1
                node.clear()
                node.update(restored)
            tag, body, scope = 'SelectStmt', node, 'scope'
        else:
            if len(node) != 1:
                continue
            tag, body = next(iter(node.items()))
        if not isinstance(body, dict):
            continue
        if scope == 'business' and (tag == 'ParamRef' or tag == 'A_Const' and
                set(body) & {'ival', 'fval', 'sval', 'bsval'}):
            node.clear()
            node['SQLAPMBusinessValue'] = {}
            counts['replacements'] += 1
            continue
        fields = {}
        if tag == 'SelectStmt':
            fields = {field: 'scope' for field in (
                'fromClause', 'havingClause', 'groupClause',
                'sortClause', 'distinctClause', 'valuesLists', 'whereClause')}
            fields.update(targetList='target' if select else 'scope', withClause='with')
            if sets:
                fields.update(larg='branch', rarg='branch')
        elif tag == 'JoinExpr':
            fields = dict(larg='scope', rarg='scope', quals='business' if join else 'scope')
        elif tag == 'ResTarget':
            fields = {'val': 'business' if scope == 'target' else 'scope'}
        elif tag in ('InsertStmt', 'UpdateStmt', 'DeleteStmt', 'OnConflictClause'):
            fields = {field: 'scope' for field in (
                'selectStmt', 'targetList', 'whereClause', 'fromClause', 'usingClause', 'returningList')}
            fields.update(withClause='with', onConflictClause='conflict')
        elif tag == 'TypeCast':
            if _cast_type(node) in NORMALIZABLE_CASTS:
                fields = {'arg': scope}
        elif tag == 'FuncCall':
            actions, _ = engine._choice(body)
            if actions is not None:
                stack.extend((arg, 'scope') for arg, action in zip(body.get('args', []), actions)
                             if action == 'normalize')
                fields = dict(agg_filter='scope', agg_order='scope')
        elif tag == 'A_Expr':
            if body.get('kind') not in ('AEXPR_NULLIF', 'AEXPR_SIMILAR'):
                fields = dict(lexpr=scope, rexpr=scope)
                container = body.get('rexpr', {}).get('List')
                if scope == 'business' and body.get('kind') == 'AEXPR_IN' and isinstance(container, dict):
                    stack.append((container, 'fold'))
        elif tag == 'CaseExpr':
            fields = dict(arg=scope, args=scope, defresult='scope')
        elif tag == 'CaseWhen':
            fields = dict(expr=scope, result='scope')
        elif tag == 'SubLink':
            fields = dict(testexpr=scope, subselect='scope')
        elif tag in ('List', 'RowExpr', 'BoolExpr'):
            fields = {'items' if tag == 'List' else 'args': scope}
        elif tag in ('NullTest', 'BooleanTest', 'CollateClause', 'A_Indirection'):
            fields = {'arg': scope}
        elif tag == 'MultiAssignRef':
            fields = {'source': 'scope'}
        elif tag == 'SortBy':
            fields = {'node': 'scope'}
        elif tag == 'RangeSubselect':
            fields = {'subquery': 'scope'}
        elif tag == 'RangeFunction':
            fields = {'functions': 'scope'}
        elif tag == 'WithClause':
            fields = {'ctes': 'scope'}
        elif tag == 'CommonTableExpr':
            fields = {'ctequery': 'scope'}
        elif tag in ('CopyStmt', 'ExplainStmt', 'CreateTableAsStmt', 'ViewStmt',
                     'PrepareStmt', 'DeclareCursorStmt'):
            fields = {'query': 'scope'}
        stack.extend((body[field], mode) for field, mode in fields.items() if field in body)
    return result, dict(counts)
