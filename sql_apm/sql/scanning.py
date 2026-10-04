"""Character-position scanner compatible with the pinned pglast 7.18 scanner.

PostgreSQL treats every high-bit byte as an identifier letter. Replacing each
Unicode character with one ASCII ``q`` therefore preserves lexical boundaries,
including quoted contents and comments, while avoiding pglast's quadratic
byte-to-character displacement search. Only scan metadata uses the mask: all
token contents and subsequent parsing must use the original SQL.
"""
import re

from pglast import parser


_NON_ASCII = re.compile(r'[^\x00-\x7f]')
_DOLLAR_TAG = re.compile(r'\$[A-Za-z_\x80-\U0010ffff][A-Za-z_0-9\x80-\U0010ffff]*\$')
_SURROGATE = re.compile(r'[\ud800-\udfff]')


def fallback_reason(sql):
    """Conservative preflight, also used by the equivalence audit's counters."""
    if _SURROGATE.search(sql):
        return 'invalid_encoding'
    if '\x00' in sql:
        return 'nul_input'
    # Distinct non-ASCII tags could collapse to one delimiter. Checking even
    # tags inside comments/strings is intentionally conservative.
    if any(not match[0].isascii() for match in _DOLLAR_TAG.finditer(sql)):
        return 'non_ascii_dollar_tag'
    return None


def scan(sql):
    """Return pglast Tokens, including inclusive character ends and keyword kind.

    Errors use the original scanner, retaining its exception and source position.
    ASCII inputs take the original fast path without allocating a masked copy.
    """
    if sql.isascii() or fallback_reason(sql):
        return parser.scan(sql)
    # q cannot create x'...' / E'...' / U&'...' string introducers or
    # 0x... / 0o... / 0b... numeric prefixes. x would NOT preserve boundaries.
    masked = _NON_ASCII.sub('q', sql)
    try:
        tokens = parser.scan(masked)
    except parser.ParseError:
        return parser.scan(sql)
    # Keywords are ASCII only: e.g. uni中ue masks to unique but remains IDENT.
    # No pglast lookahead keyword contains q, so the placeholder cannot create
    # NOT_LA, NULLS_LA, WITH_LA, or WITHOUT_LA and affect the following token.
    return [token._replace(name='IDENT', kind='NO_KEYWORD')
            if token.kind != 'NO_KEYWORD' and not sql[token.start:token.end + 1].isascii()
            else token for token in tokens]
