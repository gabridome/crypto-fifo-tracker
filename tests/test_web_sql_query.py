"""Test for web/app.py run_sql_query route — comment handling."""

import os
import sys
import tempfile

import pytest

# Ensure project root is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


@pytest.fixture
def client_with_sql_dir(monkeypatch, db_path):
    """Flask test client with SQL_DIR pointing to a tmp dir we control."""
    tmp_sql = tempfile.mkdtemp()
    monkeypatch.setenv('FIFO_DB', db_path)
    # Re-import app so DATABASE_PATH picks up the env var
    if 'web.app' in sys.modules:
        del sys.modules['web.app']
    from web import app as app_module
    app_module.SQL_DIR = tmp_sql
    app_module.app.config['TESTING'] = True
    return app_module.app.test_client(), tmp_sql


def _write_query(sql_dir, name, content):
    path = os.path.join(sql_dir, name)
    with open(path, 'w') as f:
        f.write(content)
    return path


class TestSqlQueryComments:

    def test_query_with_leading_comments_is_accepted(self, client_with_sql_dir):
        """Una query con commenti `--` prima del SELECT deve essere accettata."""
        client, sql_dir = client_with_sql_dir
        _write_query(sql_dir, 'q.sql',
                     "-- This is a comment\n"
                     "-- Another comment line\n"
                     "SELECT 1 AS one;\n")
        r = client.get('/reports/query/q.sql')
        # Status 200 (success) or 500 (DB issue), but NOT 403 (forbidden)
        assert r.status_code != 403, f"Query bocciata come non-SELECT: {r.get_json()}"

    def test_query_starting_with_select_is_accepted(self, client_with_sql_dir):
        """Una query che inizia direttamente con SELECT deve essere accettata."""
        client, sql_dir = client_with_sql_dir
        _write_query(sql_dir, 'q.sql', "SELECT 1 AS one;\n")
        r = client.get('/reports/query/q.sql')
        assert r.status_code != 403

    def test_query_with_forbidden_keyword_is_blocked(self, client_with_sql_dir):
        """Una query con DELETE/DROP deve essere bloccata."""
        client, sql_dir = client_with_sql_dir
        _write_query(sql_dir, 'q.sql', "DELETE FROM transactions;\n")
        r = client.get('/reports/query/q.sql')
        assert r.status_code == 403

    def test_query_with_only_comments_no_select_is_blocked(self, client_with_sql_dir):
        """Solo commenti senza SELECT/WITH deve essere bloccata."""
        client, sql_dir = client_with_sql_dir
        _write_query(sql_dir, 'q.sql', "-- just a comment\n-- nothing else\n")
        r = client.get('/reports/query/q.sql')
        assert r.status_code == 403

    def test_query_with_cte_is_accepted(self, client_with_sql_dir):
        """Una query CTE (WITH ...) deve essere accettata."""
        client, sql_dir = client_with_sql_dir
        _write_query(sql_dir, 'q.sql',
                     "-- comment\n"
                     "WITH x AS (SELECT 1) SELECT * FROM x;\n")
        r = client.get('/reports/query/q.sql')
        assert r.status_code != 403
