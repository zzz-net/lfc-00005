from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Optional

from .exceptions import (
    DuplicateBatchError,
    EmptyUndoError,
    InvalidStateError,
    SchemeNotFoundError,
    SchemeExistsError,
    EmptySchemeError,
    DatabasePermissionError,
)
from .scanner import IssueSeverity, IssueType, ScanIssue, ScanResult, ProjectScanResult


VALID_STATES = {"pending", "passed", "ignored"}
STATE_LABELS = {"pending": "待补", "passed": "通过", "ignored": "忽略"}


def _is_permission_error(exc: sqlite3.OperationalError) -> bool:
    msg = str(exc).lower()
    return "readonly" in msg or "read-only" in msg or "permission" in msg


def _validate_state(state: str) -> str:
    s = state.lower()
    if s not in VALID_STATES:
        raise InvalidStateError(state)
    return s


@dataclass
class IssueRecord:
    id: int
    batch_id: str
    project_path: str
    project_name: str
    project_type_id: str | None
    issue_type: str
    severity: str
    rule_name: str | None
    file_path: str | None
    message: str
    fingerprint: str | None = None
    state: str = "pending"
    handler: str | None = None
    note: str | None = None
    updated_at: str | None = None

    @property
    def state_label(self) -> str:
        return STATE_LABELS.get(self.state, self.state)

    @property
    def issue_label(self) -> str:
        from .scanner import ISSUE_LABELS
        return ISSUE_LABELS.get(IssueType(self.issue_type), self.issue_type)

    @property
    def severity_label(self) -> str:
        return "错误" if self.severity == IssueSeverity.ERROR else "警告"


@dataclass
class BatchInfo:
    batch_id: str
    scan_path: str
    scanned_at: str
    config_path: str | None
    issue_count: int = 0
    pending_count: int = 0
    passed_count: int = 0
    ignored_count: int = 0


@dataclass
class UndoRecord:
    id: int
    batch_id: str
    issue_id: int
    old_state: str
    old_handler: str | None
    old_note: str | None
    new_state: str
    new_handler: str | None
    new_note: str | None
    created_at: str


@dataclass
class FilterScheme:
    id: int
    name: str
    batch_id: str | None
    state: str | None
    severity: str | None
    project_type_id: str | None
    created_at: str
    updated_at: str

    @property
    def is_empty(self) -> bool:
        return all(v is None for v in (self.batch_id, self.state, self.severity, self.project_type_id))

    def to_display_dict(self) -> dict[str, str]:
        labels: dict[str, str] = {}
        if self.batch_id:
            labels["批次"] = self.batch_id
        if self.state:
            labels["状态"] = STATE_LABELS.get(self.state, self.state)
        if self.severity:
            labels["严重度"] = "错误" if self.severity == "error" else "警告"
        if self.project_type_id:
            labels["项目类型"] = self.project_type_id
        return labels


class Storage:
    def __init__(self, db_path: str | os.PathLike = None):
        if db_path is None:
            db_path = Path.cwd() / "contract_archive.db"
        self.db_path = Path(db_path)
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        try:
            conn = sqlite3.connect(str(self.db_path))
        except sqlite3.OperationalError as e:
            if _is_permission_error(e):
                raise DatabasePermissionError(str(self.db_path), "连接") from e
            raise
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        conn = self._conn()
        try:
            yield conn
            try:
                conn.commit()
            except sqlite3.OperationalError as e:
                if _is_permission_error(e):
                    raise DatabasePermissionError(str(self.db_path), "写入") from e
                raise
        except DatabasePermissionError:
            raise
        except sqlite3.OperationalError as e:
            conn.rollback()
            if _is_permission_error(e):
                raise DatabasePermissionError(str(self.db_path), "写入") from e
            raise
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._tx() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS batches (
                    batch_id TEXT PRIMARY KEY,
                    scan_path TEXT NOT NULL,
                    scanned_at TEXT NOT NULL,
                    config_path TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_batches_path ON batches(scan_path);

                CREATE TABLE IF NOT EXISTS issues (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    batch_id TEXT NOT NULL REFERENCES batches(batch_id) ON DELETE CASCADE,
                    project_path TEXT NOT NULL,
                    project_name TEXT NOT NULL,
                    project_type_id TEXT,
                    issue_type TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    rule_name TEXT,
                    file_path TEXT,
                    message TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'pending',
                    handler TEXT,
                    note TEXT,
                    updated_at TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_issues_batch ON issues(batch_id);

                CREATE TABLE IF NOT EXISTS undo_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    batch_id TEXT NOT NULL REFERENCES batches(batch_id) ON DELETE CASCADE,
                    issue_id INTEGER NOT NULL REFERENCES issues(id) ON DELETE CASCADE,
                    old_state TEXT NOT NULL,
                    old_handler TEXT,
                    old_note TEXT,
                    new_state TEXT NOT NULL,
                    new_handler TEXT,
                    new_note TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_undo_batch ON undo_log(batch_id);

                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    batch_id TEXT NOT NULL REFERENCES batches(batch_id) ON DELETE CASCADE,
                    issue_id INTEGER NOT NULL REFERENCES issues(id) ON DELETE CASCADE,
                    action TEXT NOT NULL,
                    old_state TEXT NOT NULL,
                    old_handler TEXT,
                    old_note TEXT,
                    new_state TEXT NOT NULL,
                    new_handler TEXT,
                    new_note TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_audit_batch ON audit_log(batch_id);

                CREATE TABLE IF NOT EXISTS filter_schemes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    batch_id TEXT,
                    state TEXT,
                    severity TEXT,
                    project_type_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            try:
                conn.execute("SELECT fingerprint FROM issues LIMIT 1")
            except sqlite3.OperationalError:
                conn.execute("ALTER TABLE issues ADD COLUMN fingerprint TEXT")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_issues_fingerprint ON issues(fingerprint)")

    def find_latest_batch(self, scan_path: str) -> Optional[BatchInfo]:
        resolved = str(Path(scan_path).resolve())
        with self._conn() as conn:
            row = conn.execute(
                "SELECT b.*, "
                "SUM(CASE WHEN i.state='pending' THEN 1 ELSE 0 END) as pending_count, "
                "SUM(CASE WHEN i.state='passed' THEN 1 ELSE 0 END) as passed_count, "
                "SUM(CASE WHEN i.state='ignored' THEN 1 ELSE 0 END) as ignored_count, "
                "COUNT(i.id) as issue_count "
                "FROM batches b LEFT JOIN issues i ON b.batch_id = i.batch_id "
                "WHERE b.scan_path = ? "
                "GROUP BY b.batch_id "
                "ORDER BY b.scanned_at DESC LIMIT 1",
                (resolved,),
            ).fetchone()
            if row is None:
                return None
            return BatchInfo(
                batch_id=row["batch_id"],
                scan_path=row["scan_path"],
                scanned_at=row["scanned_at"],
                config_path=row["config_path"],
                issue_count=row["issue_count"] or 0,
                pending_count=row["pending_count"] or 0,
                passed_count=row["passed_count"] or 0,
                ignored_count=row["ignored_count"] or 0,
            )

    def create_batch(
        self,
        scan_path: str,
        scan_result: ScanResult,
        config_path: str | None = None,
        force: bool = False,
    ) -> tuple[str, int]:
        resolved = str(Path(scan_path).resolve())
        with self._tx() as conn:
            existing = conn.execute(
                "SELECT batch_id FROM batches WHERE scan_path = ? ORDER BY scanned_at DESC LIMIT 1",
                (resolved,),
            ).fetchone()
            if existing and not force:
                raise DuplicateBatchError(resolved, existing["batch_id"])

            fingerprint_map: dict[str, dict[str, Any]] = {}
            inherited_count = 0
            if existing:
                old_issues = conn.execute(
                    "SELECT fingerprint, state, handler, note, updated_at "
                    "FROM issues WHERE batch_id = ? AND fingerprint IS NOT NULL",
                    (existing["batch_id"],),
                ).fetchall()
                for row in old_issues:
                    if row["fingerprint"]:
                        fingerprint_map[row["fingerprint"]] = {
                            "state": row["state"],
                            "handler": row["handler"],
                            "note": row["note"],
                            "updated_at": row["updated_at"],
                        }

            batch_id = datetime.now().strftime("%Y%m%d%H%M%S") + "_" + uuid.uuid4().hex[:8]
            scanned_at = scan_result.timestamp.isoformat(timespec="seconds")
            config_resolved = str(Path(config_path).resolve()) if config_path else None

            conn.execute(
                "INSERT INTO batches (batch_id, scan_path, scanned_at, config_path) VALUES (?, ?, ?, ?)",
                (batch_id, resolved, scanned_at, config_resolved),
            )

            issue_count = 0
            for proj in scan_result.projects:
                for issue in proj.issues:
                    state = "pending"
                    handler = None
                    note = None
                    updated_at = scanned_at
                    inherited_from = None

                    if issue.fingerprint and issue.fingerprint in fingerprint_map:
                        inherit = fingerprint_map[issue.fingerprint]
                        state = inherit["state"]
                        handler = inherit["handler"]
                        note = inherit["note"]
                        if inherit["updated_at"]:
                            updated_at = inherit["updated_at"]
                        inherited_from = existing["batch_id"]
                        inherited_count += 1

                    conn.execute(
                        """INSERT INTO issues
                        (batch_id, project_path, project_name, project_type_id,
                         issue_type, severity, rule_name, file_path, message,
                         fingerprint, state, handler, note, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            batch_id,
                            proj.project_path,
                            proj.project_name,
                            proj.project_type_id,
                            issue.issue_type.value,
                            issue.severity.value,
                            issue.rule_name,
                            issue.file_path,
                            issue.message,
                            issue.fingerprint,
                            state,
                            handler,
                            note,
                            updated_at,
                        ),
                    )
                    issue_count += 1

            if existing and inherited_count:
                print(f"  [提示] 从旧批次 {existing['batch_id']} 继承了 {inherited_count} 个问题的状态")

            return batch_id, issue_count

    def list_batches(self, scan_path: str | None = None) -> list[BatchInfo]:
        sql = """SELECT b.*,
                 SUM(CASE WHEN i.state='pending' THEN 1 ELSE 0 END) as pending_count,
                 SUM(CASE WHEN i.state='passed' THEN 1 ELSE 0 END) as passed_count,
                 SUM(CASE WHEN i.state='ignored' THEN 1 ELSE 0 END) as ignored_count,
                 COUNT(i.id) as issue_count
                 FROM batches b LEFT JOIN issues i ON b.batch_id = i.batch_id"""
        params: list[Any] = []
        if scan_path:
            sql += " WHERE b.scan_path = ?"
            params.append(str(Path(scan_path).resolve()))
        sql += " GROUP BY b.batch_id ORDER BY b.scanned_at DESC"

        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [
                BatchInfo(
                    batch_id=r["batch_id"],
                    scan_path=r["scan_path"],
                    scanned_at=r["scanned_at"],
                    config_path=r["config_path"],
                    issue_count=r["issue_count"] or 0,
                    pending_count=r["pending_count"] or 0,
                    passed_count=r["passed_count"] or 0,
                    ignored_count=r["ignored_count"] or 0,
                )
                for r in rows
            ]

    def get_issues(
        self,
        batch_id: str,
        state: str | None = None,
        severity: str | None = None,
        project_type_id: str | None = None,
    ) -> list[IssueRecord]:
        sql = "SELECT * FROM issues WHERE batch_id = ?"
        params: list[Any] = [batch_id]
        if state:
            sql += " AND state = ?"
            params.append(_validate_state(state))
        if severity:
            sql += " AND severity = ?"
            params.append(severity)
        if project_type_id:
            sql += " AND project_type_id = ?"
            params.append(project_type_id)
        sql += " ORDER BY project_name, id"

        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [
                IssueRecord(
                    id=r["id"],
                    batch_id=r["batch_id"],
                    project_path=r["project_path"],
                    project_name=r["project_name"],
                    project_type_id=r["project_type_id"],
                    issue_type=r["issue_type"],
                    severity=r["severity"],
                    rule_name=r["rule_name"],
                    file_path=r["file_path"],
                    message=r["message"],
                    fingerprint=r["fingerprint"] if "fingerprint" in r.keys() else None,
                    state=r["state"],
                    handler=r["handler"],
                    note=r["note"],
                    updated_at=r["updated_at"],
                )
                for r in rows
            ]

    def update_issue(
        self,
        batch_id: str,
        issue_id: int,
        state: str,
        handler: str | None = None,
        note: str | None = None,
    ) -> IssueRecord:
        state = _validate_state(state)
        now = datetime.now().isoformat(timespec="seconds")

        with self._tx() as conn:
            row = conn.execute(
                "SELECT * FROM issues WHERE id = ? AND batch_id = ?",
                (issue_id, batch_id),
            ).fetchone()
            if row is None:
                raise ValueError(f"问题记录不存在: id={issue_id}, batch={batch_id}")

            old_state = row["state"]
            old_handler = row["handler"]
            old_note = row["note"]

            new_handler = handler if handler is not None else old_handler
            new_note = note if note is not None else old_note

            conn.execute(
                "UPDATE issues SET state=?, handler=?, note=?, updated_at=? WHERE id=?",
                (state, new_handler, new_note, now, issue_id),
            )

            conn.execute(
                """INSERT INTO undo_log
                (batch_id, issue_id, old_state, old_handler, old_note,
                 new_state, new_handler, new_note, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (batch_id, issue_id, old_state, old_handler, old_note,
                 state, new_handler, new_note, now),
            )

            conn.execute(
                """INSERT INTO audit_log
                (batch_id, issue_id, action, old_state, old_handler, old_note,
                 new_state, new_handler, new_note, created_at)
                VALUES (?, ?, 'update', ?, ?, ?, ?, ?, ?, ?)""",
                (batch_id, issue_id, old_state, old_handler, old_note,
                 state, new_handler, new_note, now),
            )

            updated = conn.execute(
                "SELECT * FROM issues WHERE id = ?", (issue_id,)
            ).fetchone()

            return IssueRecord(
                id=updated["id"],
                batch_id=updated["batch_id"],
                project_path=updated["project_path"],
                project_name=updated["project_name"],
                project_type_id=updated["project_type_id"],
                issue_type=updated["issue_type"],
                severity=updated["severity"],
                rule_name=updated["rule_name"],
                file_path=updated["file_path"],
                message=updated["message"],
                fingerprint=updated["fingerprint"] if "fingerprint" in updated.keys() else None,
                state=updated["state"],
                handler=updated["handler"],
                note=updated["note"],
                updated_at=updated["updated_at"],
            )

    def undo_last(self, batch_id: str) -> UndoRecord:
        with self._tx() as conn:
            row = conn.execute(
                "SELECT * FROM undo_log WHERE batch_id = ? ORDER BY id DESC LIMIT 1",
                (batch_id,),
            ).fetchone()
            if row is None:
                raise EmptyUndoError()

            undo = UndoRecord(
                id=row["id"],
                batch_id=row["batch_id"],
                issue_id=row["issue_id"],
                old_state=row["old_state"],
                old_handler=row["old_handler"],
                old_note=row["old_note"],
                new_state=row["new_state"],
                new_handler=row["new_handler"],
                new_note=row["new_note"],
                created_at=row["created_at"],
            )

            conn.execute(
                "UPDATE issues SET state=?, handler=?, note=?, updated_at=? WHERE id=?",
                (undo.old_state, undo.old_handler, undo.old_note, undo.created_at, undo.issue_id),
            )

            now = datetime.now().isoformat(timespec="seconds")
            conn.execute(
                """INSERT INTO audit_log
                (batch_id, issue_id, action, old_state, old_handler, old_note,
                 new_state, new_handler, new_note, created_at)
                VALUES (?, ?, 'undo', ?, ?, ?, ?, ?, ?, ?)""",
                (batch_id, undo.issue_id,
                 undo.new_state, undo.new_handler, undo.new_note,
                 undo.old_state, undo.old_handler, undo.old_note,
                 now),
            )

            conn.execute("DELETE FROM undo_log WHERE id = ?", (undo.id,))

            return undo

    def get_undo_count(self, batch_id: str) -> int:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM undo_log WHERE batch_id = ?",
                (batch_id,),
            ).fetchone()
            return row["cnt"]

    def get_audit_log(self, batch_id: str) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT
                    a.action,
                    a.issue_id,
                    i.rule_name,
                    i.file_path,
                    i.project_name,
                    a.old_state,
                    a.new_state,
                    a.old_handler,
                    a.new_handler,
                    a.old_note,
                    a.new_note,
                    a.created_at as timestamp
                FROM audit_log a
                JOIN issues i ON a.issue_id = i.id
                WHERE a.batch_id = ?
                ORDER BY a.created_at ASC, a.id ASC
                """,
                (batch_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def save_scheme(
        self,
        name: str,
        batch_id: str | None = None,
        state: str | None = None,
        severity: str | None = None,
        project_type_id: str | None = None,
        overwrite: bool = False,
    ) -> FilterScheme:
        name = name.strip()
        if not name:
            raise ValueError("方案名称不能为空")

        has_any = any(v is not None for v in (batch_id, state, severity, project_type_id))
        if not has_any:
            raise EmptySchemeError(name)

        if state is not None:
            state = _validate_state(state)

        now = datetime.now().isoformat(timespec="seconds")

        with self._tx() as conn:
            existing = conn.execute(
                "SELECT * FROM filter_schemes WHERE name = ?",
                (name,),
            ).fetchone()

            if existing and not overwrite:
                raise SchemeExistsError(name)

            if existing:
                conn.execute(
                    """UPDATE filter_schemes
                    SET batch_id=?, state=?, severity=?, project_type_id=?, updated_at=?
                    WHERE name=?""",
                    (batch_id, state, severity, project_type_id, now, name),
                )
            else:
                try:
                    conn.execute(
                        """INSERT INTO filter_schemes
                        (name, batch_id, state, severity, project_type_id, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (name, batch_id, state, severity, project_type_id, now, now),
                    )
                except sqlite3.IntegrityError as e:
                    if "UNIQUE" in str(e):
                        raise SchemeExistsError(name) from e
                    raise

            row = conn.execute(
                "SELECT * FROM filter_schemes WHERE name = ?",
                (name,),
            ).fetchone()
            return FilterScheme(
                id=row["id"],
                name=row["name"],
                batch_id=row["batch_id"],
                state=row["state"],
                severity=row["severity"],
                project_type_id=row["project_type_id"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )

    def list_schemes(self) -> list[FilterScheme]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM filter_schemes ORDER BY updated_at DESC, name ASC"
            ).fetchall()
            return [
                FilterScheme(
                    id=r["id"],
                    name=r["name"],
                    batch_id=r["batch_id"],
                    state=r["state"],
                    severity=r["severity"],
                    project_type_id=r["project_type_id"],
                    created_at=r["created_at"],
                    updated_at=r["updated_at"],
                )
                for r in rows
            ]

    def get_scheme(self, name: str) -> FilterScheme:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM filter_schemes WHERE name = ?",
                (name,),
            ).fetchone()
            if row is None:
                raise SchemeNotFoundError(name)
            return FilterScheme(
                id=row["id"],
                name=row["name"],
                batch_id=row["batch_id"],
                state=row["state"],
                severity=row["severity"],
                project_type_id=row["project_type_id"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )

    def delete_scheme(self, name: str) -> None:
        with self._tx() as conn:
            existing = conn.execute(
                "SELECT id FROM filter_schemes WHERE name = ?",
                (name,),
            ).fetchone()
            if existing is None:
                raise SchemeNotFoundError(name)
            conn.execute("DELETE FROM filter_schemes WHERE name = ?", (name,))
