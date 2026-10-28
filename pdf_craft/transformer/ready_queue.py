"""Disk-backed PCEX work admission and result joins, independent of delivery."""
import pickle
import sqlite3
from contextlib import closing


class ReadyQueue:
    """No book-sized task/future/DOM registry; only admitted jobs leave SQLite."""

    def __init__(self, path):
        self.path = path
        self.run('CREATE TABLE jobs (id INTEGER PRIMARY KEY, kind TEXT, item INTEGER, dependency INTEGER, payload BLOB, state TEXT DEFAULT "ready", result BLOB)')
        self.run('CREATE TABLE items (id INTEGER PRIMARY KEY, channel TEXT, source BLOB, sealed INTEGER DEFAULT 0, consumed INTEGER DEFAULT 0, notified INTEGER DEFAULT 0)')
        self.run('CREATE INDEX ready_jobs ON jobs(kind,state,id)')
        self.run('CREATE INDEX item_jobs ON jobs(item,state)')
        self.run('CREATE TABLE titles (id INTEGER PRIMARY KEY, page INTEGER, source_order INTEGER, resolved INTEGER DEFAULT 0, value TEXT)')

    def run(self, sql, parameters=()):
        with closing(sqlite3.connect(self.path)) as db:
            with db:
                return db.execute(sql, parameters).fetchall()

    def add(self, kind, payload, *, item=None, dependency=None):
        if dependency is not None:
            self.run('INSERT OR IGNORE INTO titles(id) VALUES (?)', (dependency,))
        return self.run('INSERT INTO jobs(kind,item,dependency,payload) VALUES (?,?,?,?) RETURNING id',
                        (kind, item, dependency, pickle.dumps(payload)))[0][0]

    def item(self, index, source, channel):
        self.run('INSERT INTO items(id,channel,source) VALUES (?,?,?)', (index, channel, pickle.dumps(source)))

    def take(self, kinds):
        for kind in kinds:
            rows = self.run('SELECT id,payload,item FROM jobs WHERE kind=? AND state="ready" '
                            'AND (dependency IS NULL OR EXISTS (SELECT 1 FROM titles WHERE titles.id=dependency AND resolved=1)) ORDER BY id LIMIT 1', (kind,))
            if rows:
                identifier, payload, item = rows[0]
                self.run('UPDATE jobs SET state="active" WHERE id=?', (identifier,))
                return identifier, kind, pickle.loads(payload), item
        return None

    def finish(self, identifier, result):
        self.run('UPDATE jobs SET state="done", result=?, payload=NULL WHERE id=?',
                 (pickle.dumps(result), identifier))

    def result(self, index):
        # Publication, readiness and payloads must share one SQLite snapshot:
        # the scanner can add jobs and seal the item while callbacks read it.
        rows = self.run(
            'SELECT items.source,jobs.kind,jobs.result FROM items '
            'LEFT JOIN jobs ON jobs.item=items.id '
            'WHERE items.id=? AND items.sealed=1 AND NOT EXISTS '
            '(SELECT 1 FROM jobs WHERE item=items.id AND state!="done") ORDER BY jobs.id',
            (index,))
        if not rows:
            return None
        results = [(kind, pickle.loads(data)) for _, kind, data in rows if kind is not None]
        return pickle.loads(rows[0][0]), results

    def discard(self, index, *, notified=False, observing=False):
        if observing:
            field = 'notified' if notified else 'consumed'
            self.run(f'UPDATE items SET {field}=1 WHERE id=?', (index,))
            if not self.run('SELECT 1 FROM items WHERE id=? AND consumed=1 AND notified=1', (index,)):
                return
        self.run('DELETE FROM jobs WHERE item=?', (index,))
        self.run('DELETE FROM items WHERE id=?', (index,))

    def pending(self, kinds=None):
        if kinds is None:
            return bool(self.run('SELECT 1 FROM jobs WHERE state!="done" LIMIT 1'))
        return any(self.run('SELECT 1 FROM jobs WHERE state!="done" AND kind=? LIMIT 1', (kind,)) for kind in kinds)
