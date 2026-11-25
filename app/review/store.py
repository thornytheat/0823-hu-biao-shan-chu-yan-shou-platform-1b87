import json
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path

from app.business import RULE_STALE_CODE, DomainIssue

SCHEMA = '''CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY, report TEXT NOT NULL, created TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING', revision INTEGER NOT NULL DEFAULT 0,
    note TEXT NOT NULL DEFAULT '', reviewed TEXT
);
CREATE TABLE IF NOT EXISTS rule_state (
    version TEXT PRIMARY KEY,
    active INTEGER NOT NULL DEFAULT 1,
    updated TEXT NOT NULL DEFAULT ''
);'''


class Store:
    def __init__(self, path):
        self.path = Path(path)

    @contextmanager
    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=15)) as db, db:
            db.row_factory = sqlite3.Row
            db.executescript(SCHEMA)
            yield db

    def register(self, run_id, report):
        with self.connect() as db:
            db.execute('INSERT INTO runs(id,report,created) VALUES (?,?,?) ON CONFLICT(id) DO NOTHING',
                       (run_id, json.dumps(report, ensure_ascii=False), report['manifest']['created']))
        return self.load(run_id)

    def load(self, run_id):
        with self.connect() as db:
            row = db.execute('SELECT * FROM runs WHERE id=?',(run_id,)).fetchone()
        if row is None:
            raise DomainIssue('RUN_NOT_FOUND')
        result = dict(row)
        result['report'] = json.loads(result['report'])
        return result

    def list(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute('SELECT id,created,status,revision,note FROM runs ORDER BY created DESC,id LIMIT 50')]

    def set_rule_active(self, version, active, updated=''):
        with self.connect() as db:
            db.execute(
                'INSERT INTO rule_state(version,active,updated) VALUES (?,?,?) '
                'ON CONFLICT(version) DO UPDATE SET active=excluded.active,updated=excluded.updated',
                (str(version), 1 if active else 0, updated),
            )
        return self.rule_is_active(version)

    def activate_rule(self, version, updated=''):
        return self.set_rule_active(version, True, updated)

    def enable_rule(self, version, updated=''):
        return self.activate_rule(version, updated)

    def deactivate_rule(self, version, updated=''):
        return self.set_rule_active(version, False, updated)

    def disable_rule(self, version, updated=''):
        return self.deactivate_rule(version, updated)

    def rule_is_active(self, version):
        with self.connect() as db:
            row = db.execute('SELECT active FROM rule_state WHERE version=?', (str(version),)).fetchone()
        # Absence means a known/default-active rule has never been toggled.
        return True if row is None else bool(row['active'])

    def is_rule_active(self, version):
        return self.rule_is_active(version)

    def inactive_rules(self, versions):
        versions = sorted({str(version) for version in versions or []})
        if not versions:
            return []
        with self.connect() as db:
            marks = ','.join('?' for _ in versions)
            rows = db.execute(
                f'SELECT version FROM rule_state WHERE active=0 AND version IN ({marks})',
                versions,
            ).fetchall()
        return [row['version'] for row in rows]

    def revise(self, run_id, revision, status, note, now, used_rules=None):
        used_rules = sorted({str(rule) for rule in used_rules or []})
        with self.connect() as db:
            # Take the write lock before validating so a concurrent rule
            # deactivation cannot commit between validation and the update.
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT revision,status,report FROM runs WHERE id=?', (run_id,)).fetchone()
            if row is None:
                raise DomainIssue('RUN_NOT_FOUND')
            if row['revision'] != revision:
                raise DomainIssue('STALE_REVIEW')
            if row['status'] == 'APPROVED':
                raise DomainIssue('RUN_FINALIZED')
            if used_rules:
                marks = ','.join('?' for _ in used_rules)
                inactive = db.execute(
                    f'SELECT version FROM rule_state WHERE active=0 AND version IN ({marks})',
                    used_rules,
                ).fetchall()
                if inactive:
                    raise DomainIssue(RULE_STALE_CODE)
            cursor = db.execute(
                'UPDATE runs SET status=?,note=?,reviewed=?,revision=revision+1 WHERE id=? AND revision=?',
                (status, note, now, run_id, revision),
            )
            if cursor.rowcount != 1:
                raise DomainIssue('STALE_REVIEW')
        return self.load(run_id)
