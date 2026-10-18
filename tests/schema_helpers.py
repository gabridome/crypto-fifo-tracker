"""Small sqlite3 introspection helpers shared by the schema/migration tests.

Not a conftest.py fixture module on purpose: these are plain, stateless
functions, not fixtures, and conftest.py is meant to stay the place pytest
looks for fixtures. They also need to be importable as `tests.schema_helpers`
regardless of pytest's import mode — `from conftest import ...` only works
under the default `prepend` import mode (where pytest puts a test file's own
directory on sys.path); it breaks collection outright under
`--import-mode=importlib`, which pytest's own docs recommend for new
projects and which nothing in this repo's config currently rules out.
"""
import sqlite3


def _columns(db_path, table):
    conn = sqlite3.connect(db_path)
    try:
        return [r[1] for r in conn.execute(f'PRAGMA table_info({table})')]
    finally:
        conn.close()


def _index_names(db_path, table):
    conn = sqlite3.connect(db_path)
    try:
        return [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=?", (table,))]
    finally:
        conn.close()


def _index_columns(db_path, index_name):
    """Column names covered by an index, via PRAGMA index_info — not just its name."""
    conn = sqlite3.connect(db_path)
    try:
        return [r[2] for r in conn.execute(f'PRAGMA index_info({index_name})')]
    finally:
        conn.close()
