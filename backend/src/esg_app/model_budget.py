"""Shared CNY admission ledger. Integer micro-CNY; unresolved requests retain reservations."""
import sqlite3
import uuid
from pathlib import Path


class BudgetBlocked(RuntimeError):
    pass


class ModelBudget:
    def __init__(self, path, *, cap_micro=0, checkpoint_micro=5_000_000):
        if type(cap_micro) is not int or not 0 <= cap_micro <= 10_000_000:
            raise ValueError('Cap must be an integer from zero to 10 CNY')
        if type(checkpoint_micro) is not int or not 0 < checkpoint_micro <= 10_000_000:
            raise ValueError('Invalid checkpoint')
        self.path = str(Path(path).resolve())
        self.cap = cap_micro
        self.checkpoint = checkpoint_micro
        with sqlite3.connect(self.path) as db:
            db.execute('CREATE TABLE IF NOT EXISTS budget_hold (reason TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS budget_requests (request_id TEXT PRIMARY KEY, model_role TEXT, reserve_micro INTEGER, charge_micro INTEGER, settled INTEGER)')

    def reserve(self, role, upper_bound_micro):
        if role not in ('sdk', 'extractor') or type(upper_bound_micro) is not int or upper_bound_micro <= 0:
            raise ValueError('Positive integer bound and known model role required')
        with sqlite3.connect(self.path, timeout=10) as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM budget_hold LIMIT 1').fetchone():
                raise BudgetBlocked('Budget is paused for reconciliation')
            committed = db.execute('SELECT COALESCE(SUM(CASE WHEN settled=1 THEN charge_micro ELSE reserve_micro END),0) FROM budget_requests').fetchone()[0]
            limit = min(self.cap, self.checkpoint)
            if committed + upper_bound_micro > limit:
                raise BudgetBlocked('Combined budget checkpoint/cap would be exceeded; request not admitted')
            rid = uuid.uuid4().hex
            db.execute('INSERT INTO budget_requests VALUES(?,?,?,NULL,0)', (rid, role, upper_bound_micro))
        return rid

    def settle(self, request_id, actual_micro):
        if type(actual_micro) is not int or actual_micro < 0:
            raise ValueError('Non-negative integer charge required')
        overrun = False
        with sqlite3.connect(self.path, timeout=10) as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT reserve_micro,charge_micro,settled FROM budget_requests WHERE request_id=?', (request_id,)).fetchone()
            if row is None:
                raise ValueError('Unknown request')
            if row[2]:
                if row[1] != actual_micro:
                    raise ValueError('Conflicting settlement')
                return
            if actual_micro > row[0]:
                db.execute('INSERT INTO budget_hold VALUES (?)', ('usage exceeds reserved bound',))
                overrun = True
            db.execute('UPDATE budget_requests SET charge_micro=?,settled=1 WHERE request_id=?', (actual_micro, request_id))
        if overrun:
            raise BudgetBlocked('Charge exceeds reserved bound; ledger paused')

    def pause(self):
        with sqlite3.connect(self.path) as db:
            db.execute('INSERT INTO budget_hold VALUES (?)', ('provider usage requires reconciliation',))

    def snapshot(self):
        with sqlite3.connect(self.path) as db:
            charged, reserved = db.execute('SELECT COALESCE(SUM(CASE WHEN settled=1 THEN charge_micro ELSE 0 END),0), COALESCE(SUM(CASE WHEN settled=0 THEN reserve_micro ELSE 0 END),0) FROM budget_requests').fetchone()
        return dict(charged_micro=charged, reserved_micro=reserved, cap_micro=self.cap,
                    checkpoint_micro=self.checkpoint, admission_limit_micro=min(self.cap,self.checkpoint))
