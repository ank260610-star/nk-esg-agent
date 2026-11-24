"""任务、解析证据和业务写入使用的持久仓储。"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from threading import RLock


def token_identity(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class TaskView:
    """兼容既有任务访问接口，版本写入直接落 SQLite。"""
    permission = 'read-only'

    def __init__(self, store: ParseRepository, task_id: str):
        self.store, self.task_id = store, task_id

    @property
    def revision(self) -> int:
        with self.store.transaction() as db:
            return db.execute('SELECT revision FROM tasks WHERE task_id=?', (self.task_id,)).fetchone()[0]

    @revision.setter
    def revision(self, value: int) -> None:
        with self.store.transaction() as db:
            db.execute('UPDATE tasks SET revision=? WHERE task_id=?', (value, self.task_id))

    @property
    def results(self) -> dict:
        with self.store.transaction() as db:
            raw = db.execute('SELECT results FROM tasks WHERE task_id=?', (self.task_id,)).fetchone()[0]
        return json.loads(raw)


class ParseRepository:
    def __init__(self, path: str | Path = ':memory:'):
        self.lock = RLock()
        if str(path) != ':memory:':
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None, timeout=10)
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS tasks(
                task_id TEXT PRIMARY KEY, revision INTEGER NOT NULL CHECK(revision >= 0), results TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS tokens(identity TEXT PRIMARY KEY, task_id TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS reports(report_id TEXT PRIMARY KEY, path TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS parse_cache(
                hash TEXT, version TEXT, data TEXT NOT NULL, PRIMARY KEY(hash, version));
            CREATE TABLE IF NOT EXISTS task_parses(
                task_id TEXT, report_id TEXT, hash TEXT, version TEXT, summary TEXT NOT NULL, evidence TEXT NOT NULL,
                PRIMARY KEY(task_id, report_id));
            CREATE TABLE IF NOT EXISTS agent_run_claims(task_id TEXT PRIMARY KEY,run_id TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS agent_runs(
                run_id TEXT PRIMARY KEY,task_id TEXT NOT NULL,state TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS agent_tool_calls(
                id INTEGER PRIMARY KEY AUTOINCREMENT, identity TEXT NOT NULL, task_id TEXT NOT NULL,
                tool TEXT NOT NULL, ok INTEGER NOT NULL, revision INTEGER, error_code TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS artifacts(
                artifact_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, metadata TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS business_state(
                task_id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS extraction_attempts(
                attempt_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, identity TEXT NOT NULL,
                key TEXT NOT NULL, request_hash TEXT NOT NULL, status TEXT NOT NULL,
                model_calls INTEGER NOT NULL DEFAULT 0,usage TEXT);
            CREATE UNIQUE INDEX IF NOT EXISTS extraction_active_task
                ON extraction_attempts(task_id) WHERE status='running';
            CREATE TABLE IF NOT EXISTS idempotency(
                identity TEXT, task_id TEXT, tool TEXT, key TEXT, request_hash TEXT NOT NULL, response TEXT NOT NULL,
                PRIMARY KEY(identity, task_id, tool, key));
        ''')
        with self.transaction() as db:
            if 'usage' not in {row[1] for row in db.execute('PRAGMA table_info(extraction_attempts)')}:
                db.execute('ALTER TABLE extraction_attempts ADD COLUMN usage TEXT')

    @contextmanager
    def transaction(self):
        """仅持有短事务；SQLite 唯一约束同时保护不同连接/进程的提交。"""
        with self.lock:
            self.db.execute('BEGIN IMMEDIATE')
            try:
                yield self.db
                self.db.commit()
            except BaseException:
                self.db.rollback()
                raise

    def close(self) -> None:
        with self.lock:
            self.db.close()

    def register_task(self, task_id: str, revision: int, results: dict) -> None:
        if type(revision) is not int or revision < 0:
            raise ValueError('revision must be a nonnegative integer')
        with self.transaction() as db:
            db.execute('INSERT INTO tasks VALUES(?,?,?)', (task_id, revision, json.dumps(results)))

    def issue_agent_token(self, task_id: str, token: str) -> None:
        if not token:
            raise ValueError('token must not be empty')
        with self.transaction() as db:
            if not db.execute('SELECT 1 FROM tasks WHERE task_id=?', (task_id,)).fetchone():
                raise ValueError('task must exist before issuing token')
            db.execute('INSERT OR REPLACE INTO tokens VALUES(?,?)', (token_identity(token), task_id))

    def task_for_agent_token(self, token: str | None) -> str | None:
        with self.transaction() as db:
            row = db.execute('SELECT task_id FROM tokens WHERE identity=?', (token_identity(token or ''),)).fetchone()
        return row[0] if row else None

    def get(self, task_id: str) -> TaskView | None:
        with self.transaction() as db:
            exists = db.execute('SELECT 1 FROM tasks WHERE task_id=?', (task_id,)).fetchone()
        return TaskView(self, task_id) if exists else None

    @property
    def _tasks(self) -> dict[str, TaskView]:
        # 既有首页以该映射选择演示任务，实际任务字段仍从数据库取得。
        with self.transaction() as db:
            return {r[0]: TaskView(self, r[0]) for r in db.execute('SELECT task_id FROM tasks')}

    def permission_for(self, task_id: str) -> str:
        return 'read-only' if self.get(task_id) else 'none'

    def register_report_path(self, report_id: str, path: Path | str) -> None:
        """仅供可信宿主登记；没有模型可调用的文件路径接口。"""
        with self.transaction() as db:
            db.execute('INSERT OR REPLACE INTO reports VALUES(?,?)', (report_id, str(Path(path).resolve())))

    def report_path(self, report_id: str) -> Path | None:
        with self.transaction() as db:
            row = db.execute('SELECT path FROM reports WHERE report_id=?', (report_id,)).fetchone()
        return Path(row[0]) if row else None

    def evidence_for_task(self, task_id: str) -> list[dict]:
        with self.transaction() as db:
            task = db.execute('SELECT results FROM tasks WHERE task_id=?', (task_id,)).fetchone()
            allowed = set(bound_reports(json.loads(task[0]))) if task else set()
            return [e for rid, raw in db.execute('SELECT report_id,evidence FROM task_parses WHERE task_id=?', (task_id,))
                    if rid in allowed for e in json.loads(raw)]


def read_business(db, task_id):
    row = db.execute('SELECT data FROM business_state WHERE task_id=?', (task_id,)).fetchone()
    return json.loads(row[0]) if row else dict(records=[], comparisons=[], checks=[], runs=[], issues=[])


def save_business(db, task_id, data):
    db.execute('INSERT OR REPLACE INTO business_state VALUES(?,?)', (task_id, json.dumps(data)))


def bound_reports(results):
    return results.get('task', results).get('report_ids', [])
