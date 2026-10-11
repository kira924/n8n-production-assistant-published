"""SQLite for local Compose; PostgreSQL for durable cloud demo state."""
from __future__ import annotations

import hashlib
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path


class Row(dict):
    def __getitem__(self, key):
        return tuple(self.values())[key] if isinstance(key, int) else super().__getitem__(key)


def row_factory(cursor):
    names = [column.name for column in cursor.description or ()]
    return lambda values: Row(zip(names, values))


class EmptyCursor:
    def fetchone(self):
        return None


class PostgresConnection:
    def __init__(self, connection, namespace):
        self.connection = connection
        self.lock_id = int.from_bytes(hashlib.sha256(namespace.encode()).digest()[:4], 'big', signed=True)

    def execute(self, statement, parameters=()):
        if statement.strip().upper().startswith('PRAGMA '):
            return EmptyCursor()
        if statement.strip().upper() == 'BEGIN IMMEDIATE':
            self.connection.execute('BEGIN')
            return self.connection.execute('SELECT pg_advisory_xact_lock(%s)', (self.lock_id,))
        statement = re.sub(r'INTEGER PRIMARY KEY AUTOINCREMENT', 'BIGSERIAL PRIMARY KEY', statement, flags=re.I)
        statement = re.sub(r'\bREAL\b', 'DOUBLE PRECISION', statement, flags=re.I)
        return self.connection.execute(statement.replace('?', '%s'), parameters)

    def executescript(self, statements):
        # Serialize first-boot table creation across container instances.
        self.execute('BEGIN IMMEDIATE')
        for statement in statements.split(';'):
            if statement.strip():
                self.execute(statement)
        self.commit()

    def commit(self):
        self.connection.commit()

    def rollback(self):
        self.connection.rollback()

    def close(self):
        self.connection.close()


@contextmanager
def connect(path: Path, namespace: str, database_url: str | None = None, prefix='production_desk'):
    if not re.fullmatch(r'[a-z][a-z0-9_]{0,40}', prefix) or namespace not in ('gateway', 'erp'):
        raise ValueError('Invalid database namespace')
    if database_url:
        import psycopg
        from psycopg import sql
        schema = prefix + '_' + namespace
        raw = psycopg.connect(database_url, autocommit=True, row_factory=row_factory, connect_timeout=15)
        try:
            # The deployment bootstrap creates these schemas under its startup lock.
            raw.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(schema)))
            connection = PostgresConnection(raw, schema)
        except Exception:
            raw.close()
            raise
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys=ON')
    try:
        yield connection
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def database_errors():
    try:
        import psycopg
        return (sqlite3.Error, psycopg.Error)
    except ImportError:
        return (sqlite3.Error,)
