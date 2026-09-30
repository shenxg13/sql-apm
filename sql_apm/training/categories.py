"""Conservative product categories, gated by complete MPP grammar recognition."""
from sql_apm.sql.lexical import diagnose

VERSION = 'statement-categories/2'
CATEGORIES = ('SET', 'BEGIN', 'COMMIT', 'VACUUM', 'ANALYZE', 'CREATE INDEX', 'ALTER TABLE')
RULES = dict(version=VERSION, categories=list(CATEGORIES),
             boundary='complete_top_level_statements_only',
             set='runtime_parameters_including_SESSION_LOCAL_TIME_ZONE_NAMES_SCHEMA_SEED',
             begin='BEGIN_WORK_TRANSACTION_and_modes', commit='COMMIT_WORK_TRANSACTION',
             vacuum='valid_VACUUM_options_and_targets', analyze='ANALYZE_not_ANALYSE',
             create_index='CREATE_INDEX_and_CREATE_UNIQUE_INDEX', alter_table='valid_ALTER_TABLE_actions',
             deferred=['END', 'START TRANSACTION', 'ANALYSE', 'COMMIT PREPARED',
                       'SET ROLE', 'SET AUTHORIZATION', 'SET TRANSACTION',
                       'SET SESSION CHARACTERISTICS AS TRANSACTION', 'SET CONSTRAINTS'],
             deferred_parameter_names=['role', 'session_authorization', 'authorization',
                                       'session characteristics', 'transaction_isolation',
                                       'transaction_read_only', 'transaction_deferrable',
                                       'default_transaction_isolation', 'default_transaction_read_only',
                                       'default_transaction_deferrable'],
             batch='exclude_only_when_every_statement_is_blacklisted')


def classify(text, *, grammar_verified=False):
    """The trusted fast path is only for a reliable fingerprint of this exact text.

    Lexical labels preserve the spellings PG folds (ANALYSE, END, START). A
    complete grammar parse is required before any label may exclude a sample.
    """
    sequence, issues = diagnose(text)
    if issues or not sequence:
        return dict(kind='unknown', categories=[])
    tree = None
    if not grammar_verified or 'SET' in sequence:
        from sql_apm.sql.mpp_parser import parse, Unsupported
        try:
            tree = parse(text)
        except (Unsupported, ValueError):
            return dict(kind='unknown', categories=[])
    if tree is not None:
        # Parsed names cover identity/transaction parameters and prefixed SESSION
        # CHARACTERISTICS forms without changing the shared diagnostic lexer.
        sequence = list(sequence)
        if len(sequence) != len(tree['statements']):
            return dict(kind='unknown', categories=[])
        for i, statement in enumerate(tree['statements']):
            variable = statement['base'].get('VariableSetStmt', {})
            if sequence[i] == 'SET' and variable.get('name', '').lower() in RULES['deferred_parameter_names']:
                sequence[i] = 'SET DEFERRED'
    matches = sorted(set(sequence).intersection(CATEGORIES))
    kind = ('pure' if all(c in CATEGORIES for c in sequence) else
            'mixed' if matches else 'none')
    return dict(kind=kind, categories=matches)
