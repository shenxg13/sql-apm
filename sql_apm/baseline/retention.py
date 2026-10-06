"""Database-independent calendar policy for version-result retention."""


def expired(month, current, months):
    """Integer month arithmetic handles cross-year and arbitrarily large N."""
    return month.year * 12 + month.month < current.year * 12 + current.month - months
