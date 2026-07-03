import logging
from contextlib import contextmanager

import psycopg2
import psycopg2.pool
import psycopg2.extras
from psycopg2.extras import RealDictCursor, Json

from config import DATABASE_URL

log = logging.getLogger(__name__)

_pool: psycopg2.pool.ThreadedConnectionPool | None = None


def get_pool() -> psycopg2.pool.ThreadedConnectionPool:
    global _pool
    if _pool is None:
        if not DATABASE_URL:
            raise RuntimeError('DATABASE_URL not set in .env')
        _pool = psycopg2.pool.ThreadedConnectionPool(1, 10, dsn=DATABASE_URL)
        # Log host/db without the password
        safe = DATABASE_URL.split('@')[-1] if '@' in DATABASE_URL else DATABASE_URL
        log.info('Postgres pool ready (%s)', safe)
    return _pool


@contextmanager
def cursor():
    """Context manager: yields a RealDictCursor, commits on exit, rolls back on error."""
    pool = get_pool()
    conn = pool.getconn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            yield cur
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


# ── Convenience helpers ───────────────────────────────────────────────────────

def fetchall(sql: str, params=None) -> list[dict]:
    with cursor() as cur:
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


def fetchone(sql: str, params=None) -> dict | None:
    with cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
        return dict(row) if row else None


def execute(sql: str, params=None) -> None:
    with cursor() as cur:
        cur.execute(sql, params)


def insert_returning(sql: str, params=None) -> dict | None:
    """INSERT … RETURNING — returns the first returned row as a dict."""
    with cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
        return dict(row) if row else None
