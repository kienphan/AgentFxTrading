"""A '%' inside a SQL string literal on the PostgreSQL path (2026-09-24).

psycopg2 formats the query on the client whenever parameters are passed, even an empty tuple:
%s takes the next parameter, %% is a literal %, and any other % is an error. The currency
exposure query in get_portfolio_status (symbol LIKE 'EUR%') therefore raised
"IndexError: tuple index out of range" on every call, and /portfolio/status answered 200 with no
positions and a P&L of 0. The SQLite tests never saw it because sqlite3 leaves '%' alone.
"""
from app.db import PostgresCursorWrapper


class Psycopg2FormattingCursor:
    """Stands in for a psycopg2 cursor: formats the query as psycopg2 does client-side and keeps
    what would go to the server. Python's % operator follows the same %s / %% rules and raises on
    a stray % as psycopg2 does."""

    def __init__(self):
        self.sent = None

    def execute(self, sql, params=None):
        self.sent = sql if params is None else sql % tuple(repr(p) for p in params)


def _execute(sql, params):
    raw = Psycopg2FormattingCursor()
    PostgresCursorWrapper(raw).execute(sql, params)
    return raw.sent


CURRENCY_EXPOSURE_SQL = """
    SELECT CASE WHEN symbol LIKE 'EUR%' THEN 'EUR' ELSE 'OTHER' END AS currency, COUNT(*)
    FROM positions WHERE status = 'open' AND account_id = ? GROUP BY currency
"""


def test_a_percent_in_a_literal_reaches_the_server_as_written_when_params_are_passed():
    sent = _execute(CURRENCY_EXPOSURE_SQL, ("demo-10115236",))
    assert "LIKE 'EUR%'" in sent
    assert "account_id = 'demo-10115236'" in sent


def test_a_percent_in_a_literal_survives_an_empty_params_tuple():
    # get_portfolio_status passes tuple(params) even when no account filter was added
    sent = _execute("SELECT COUNT(*) FROM positions WHERE symbol LIKE 'XAU%'", ())
    assert "LIKE 'XAU%'" in sent


def test_a_query_without_params_is_sent_unescaped():
    # psycopg2 does not format a query without parameters, so a doubled %% would reach the server
    sent = _execute("SELECT COUNT(*) FROM positions WHERE symbol LIKE 'XAU%'", None)
    assert "LIKE 'XAU%'" in sent
