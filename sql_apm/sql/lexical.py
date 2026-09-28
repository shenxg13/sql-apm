"""Conservative SQL lexical boundaries and category labels, not a full parser."""
import re

WORD = re.compile(r'[A-Za-z_\u0080-\uffff][A-Za-z_0-9$\u0080-\uffff]*')
SPACE = re.compile(r'\s+')
BAD = re.compile('[\udc80-\udcff\x00]')
TAG = re.compile(r'\$(?:[A-Za-z_][A-Za-z_0-9]*)?\$')
QUOTED = re.compile(r"'(?:[^'\\]|''|\\.)*'", re.S)
IDENT = re.compile(r'"(?:[^"]|"")*"')
COMMANDS = set(('SELECT WITH INSERT UPDATE DELETE TRUNCATE VALUES COPY EXPLAIN ANALYZE ANALYSE '
                'VACUUM SET RESET SHOW BEGIN START COMMIT END ROLLBACK ABORT SAVEPOINT '
                'RELEASE DISCARD PREPARE EXECUTE DEALLOCATE DECLARE FETCH MOVE CLOSE '
                'GRANT REVOKE COMMENT DO CALL CHECKPOINT REINDEX CLUSTER LOCK LISTEN '
                'UNLISTEN NOTIFY LOAD REFRESH').split())
OBJECTS = set('TABLE INDEX VIEW MATERIALIZED SEQUENCE SCHEMA DATABASE ROLE USER FUNCTION '
              'TYPE DOMAIN TRIGGER RULE EXTENSION LANGUAGE TABLESPACE EXTERNAL RESOURCE '
              'AGGREGATE OPERATOR CAST PROTOCOL SERVER FOREIGN'.split())


def category(words):
    first = words[0]
    if first in ('CREATE', 'ALTER', 'DROP'):
        rest = list(words[1:])
        while rest and rest[0] in ('OR', 'REPLACE', 'UNIQUE', 'TEMP', 'TEMPORARY', 'UNLOGGED', 'GLOBAL', 'LOCAL', 'READABLE', 'WRITABLE'):
            rest.pop(0)
        obj = rest[0] if rest and rest[0] in OBJECTS else 'OTHER'
        return first + ' ' + obj
    if first == 'SET' and words[1:4] == ['SESSION', 'CHARACTERISTICS', 'AS']:
        return 'SET TRANSACTION'
    if first == 'SET' and len(words) > 1:
        rest = words[1:]
        if rest[0] in ('LOCAL', 'SESSION'):
            rest = rest[1:]
        if rest and rest[0] in ('ROLE', 'AUTHORIZATION', 'TRANSACTION', 'CONSTRAINTS'):
            return 'SET ' + rest[0]
    if len(words) > 1 and ((first in ('COMMIT', 'ROLLBACK') and words[1] == 'PREPARED')
                           or (first == 'PREPARE' and words[1] == 'TRANSACTION')):
        return first + ' ' + words[1]
    if first == 'START' and len(words) > 1 and words[1] == 'TRANSACTION':
        return 'START TRANSACTION'
    return first if first in COMMANDS else 'UNKNOWN'


def diagnose(sql):
    """Conservative lexical split. No syntax validity or execution claim."""
    if BAD.search(sql):
        return (), ('invalid_encoding_or_nul',)
    pos, size, words, stack, sequence = 0, len(sql), [], [], []
    while pos < size:
        match = SPACE.match(sql, pos)
        if match:
            pos = match.end()
            continue
        if sql.startswith('--', pos):
            end = sql.find('\n', pos + 2)
            pos = size if end < 0 else end + 1
            continue
        if sql.startswith('/*', pos):
            depth, pos = 1, pos + 2
            while pos < size and depth:
                if sql.startswith('/*', pos):
                    depth, pos = depth + 1, pos + 2
                elif sql.startswith('*/', pos):
                    depth, pos = depth - 1, pos + 2
                else:
                    pos += 1
            if depth:
                return (), ('unclosed_comment',)
            continue
        escaped = sql[pos:pos+2].lower() == "e'"
        if sql[pos] == "'" or escaped:
            match = QUOTED.match(sql, pos + int(escaped))
            if not match:
                return (), ('unclosed_string',)
            if not escaped and '\\' in match[0]:
                return (), ('ambiguous_string_escape',)
            if len(words) < 8:
                words.append('?')
            pos = match.end()
            continue
        if sql[pos] == '"':
            match = IDENT.match(sql, pos)
            if not match:
                return (), ('unclosed_identifier',)
            if len(words) < 8:
                words.append('?')
            pos = match.end()
            continue
        match = TAG.match(sql, pos)
        if match:
            end = sql.find(match[0], match.end())
            if end < 0:
                return (), ('unclosed_dollar_quote',)
            if len(words) < 8:
                words.append('?')
            pos = end + len(match[0])
            continue
        match = WORD.match(sql, pos)
        if match:
            if len(words) < 8:
                words.append(match[0].upper())
            pos = match.end()
            continue
        char = sql[pos]
        if char in '([':
            stack.append(char)
        elif char in ')]':
            if not stack or stack.pop() != ('(' if char == ')' else '['):
                return (), ('unbalanced_bracket',)
        if char == ';' and not stack:
            if words:
                sequence.append(category(words))
                words = []
        elif len(words) < 8:
            words.append('?')
        pos += 1
    if stack:
        return (), ('unbalanced_bracket',)
    if words:
        sequence.append(category(words))
    return tuple(sequence), ()
