"""Structural PG AST helpers; does not import optional parser dependencies."""
POSITION_KEYS = frozenset(('location', 'stmt_location', 'stmt_len'))


def pg_clean(value):
    """Copy the complete tree and remove only source positions, without recursion."""
    root = [None]
    pending = [(root, 0, value)]
    while pending:
        parent, key, node = pending.pop()
        if isinstance(node, dict):
            copy = {}
            parent[key] = copy
            for name, child in node.items():
                if name not in POSITION_KEYS:
                    pending.append((copy, name, child))
        elif isinstance(node, list):
            copy = [None] * len(node)
            parent[key] = copy
            pending.extend((copy, index, child) for index, child in enumerate(node))
        else:
            parent[key] = node
    return root[0]
