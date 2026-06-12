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
    MigrationPackageEmptyError,
    MigrationPackageMissingFieldError,
    MigrationPackageParseError,
    InvalidMigrationPackageError,
    SchemeImportConflictError,
    WorkPackageError,
    InvalidWorkPackageError,
    WorkPackageEmptyError,
    WorkPackageParseError,
    WorkPackageMissingFieldError,
    WorkPackageBatchExistsError,
    WorkPackageIssueStateConflictError,
    WorkPackageRuleMismatchError,
    WorkPackageSchemeExistsError,
    EmptyWorkPackageUndoError,
)
from . import __version__ as PACKAGE_VERSION
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
    inherited_from_batch_id: str | None = None
    import_source: str | None = None

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


DIFF_TYPE_LABELS = {
    "added": "新增",
    "removed": "消失",
    "inherited": "继承",
}


@dataclass
class DiffIssueRecord:
    diff_type: str
    issue: IssueRecord
    old_issue: IssueRecord | None = None

    @property
    def diff_type_label(self) -> str:
        return DIFF_TYPE_LABELS.get(self.diff_type, self.diff_type)


@dataclass
class BatchDiffResult:
    batch_old: BatchInfo
    batch_new: BatchInfo
    added: list[IssueRecord]
    removed: list[IssueRecord]
    inherited: list[DiffIssueRecord]

    @property
    def added_count(self) -> int:
        return len(self.added)

    @property
    def removed_count(self) -> int:
        return len(self.removed)

    @property
    def inherited_count(self) -> int:
        return len(self.inherited)

    @property
    def total_old(self) -> int:
        return self.removed_count + self.inherited_count

    @property
    def total_new(self) -> int:
        return self.added_count + self.inherited_count


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
class SchemeUndoRecord:
    id: int
    scheme_name: str
    old_batch_id: str | None
    old_state: str | None
    old_severity: str | None
    old_project_type_id: str | None
    old_created_at: str
    old_updated_at: str
    created_at: str


@dataclass
class ImportUndoRecord:
    id: int
    import_source: str
    import_source_file: str | None
    batch_id: str
    imported_issue_ids: list[int]
    imported_scheme_names: list[str]
    created_at: str


@dataclass
class RuleConfigSummary:
    project_type_count: int
    rule_names: list[str]
    global_max_size_kb: int | None
    config_path: str | None
    config_hash: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_type_count": self.project_type_count,
            "rule_names": list(self.rule_names),
            "global_max_size_kb": self.global_max_size_kb,
            "config_path": self.config_path,
            "config_hash": self.config_hash,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RuleConfigSummary":
        return cls(
            project_type_count=d["project_type_count"],
            rule_names=list(d["rule_names"]),
            global_max_size_kb=d.get("global_max_size_kb"),
            config_path=d.get("config_path"),
            config_hash=d["config_hash"],
        )

    def compare(self, other: "RuleConfigSummary") -> list[str]:
        diffs = []
        if self.project_type_count != other.project_type_count:
            diffs.append(f"项目类型数量: {self.project_type_count} vs {other.project_type_count}")
        if set(self.rule_names) != set(other.rule_names):
            missing = set(other.rule_names) - set(self.rule_names)
            extra = set(self.rule_names) - set(other.rule_names)
            parts = []
            if missing:
                parts.append(f"缺失规则: {', '.join(missing)}")
            if extra:
                parts.append(f"多余规则: {', '.join(extra)}")
            diffs.append("规则名集合不同 - " + "; ".join(parts))
        if self.global_max_size_kb != other.global_max_size_kb:
            diffs.append(f"全局大小限制: {self.global_max_size_kb} vs {other.global_max_size_kb}")
        if self.config_hash != other.config_hash:
            diffs.append("配置内容哈希不同")
        return diffs


@dataclass
class UndoHistorySummary:
    total_undo_count: int
    last_undo_time: str | None
    last_undo_action: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_undo_count": self.total_undo_count,
            "last_undo_time": self.last_undo_time,
            "last_undo_action": self.last_undo_action,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "UndoHistorySummary":
        return cls(
            total_undo_count=d["total_undo_count"],
            last_undo_time=d.get("last_undo_time"),
            last_undo_action=d.get("last_undo_action"),
        )


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
    version: int = 1

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

    def to_migration_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "batch_id": self.batch_id,
            "state": self.state,
            "severity": self.severity,
            "project_type_id": self.project_type_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "version": self.version,
        }


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
                    updated_at TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS scheme_audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    action TEXT NOT NULL,
                    scheme_name TEXT NOT NULL,
                    source_file TEXT,
                    result TEXT NOT NULL,
                    detail TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_scheme_audit_name ON scheme_audit_log(scheme_name);

                CREATE TABLE IF NOT EXISTS scheme_undo_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scheme_name TEXT NOT NULL UNIQUE,
                    old_batch_id TEXT,
                    old_state TEXT,
                    old_severity TEXT,
                    old_project_type_id TEXT,
                    old_created_at TEXT NOT NULL,
                    old_updated_at TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            try:
                conn.execute("SELECT fingerprint FROM issues LIMIT 1")
            except sqlite3.OperationalError:
                conn.execute("ALTER TABLE issues ADD COLUMN fingerprint TEXT")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_issues_fingerprint ON issues(fingerprint)")

            try:
                conn.execute("SELECT version FROM filter_schemes LIMIT 1")
            except sqlite3.OperationalError:
                conn.execute("ALTER TABLE filter_schemes ADD COLUMN version INTEGER NOT NULL DEFAULT 1")

            try:
                conn.execute("SELECT inherited_from_batch_id FROM issues LIMIT 1")
            except sqlite3.OperationalError:
                conn.execute("ALTER TABLE issues ADD COLUMN inherited_from_batch_id TEXT")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_issues_inherited_from ON issues(inherited_from_batch_id)")

            try:
                conn.execute("SELECT import_source FROM issues LIMIT 1")
            except sqlite3.OperationalError:
                conn.execute("ALTER TABLE issues ADD COLUMN import_source TEXT")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_issues_import_source ON issues(import_source)")

            try:
                conn.execute("SELECT import_source FROM batches LIMIT 1")
            except sqlite3.OperationalError:
                conn.execute("ALTER TABLE batches ADD COLUMN import_source TEXT")
                conn.execute("ALTER TABLE batches ADD COLUMN rule_summary_json TEXT")
                conn.execute("ALTER TABLE batches ADD COLUMN imported_at TEXT")

            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS import_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    import_source TEXT NOT NULL,
                    import_source_file TEXT,
                    batch_id TEXT NOT NULL REFERENCES batches(batch_id) ON DELETE CASCADE,
                    imported_issue_ids TEXT NOT NULL,
                    imported_scheme_names TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_import_log_batch ON import_log(batch_id);
                CREATE INDEX IF NOT EXISTS idx_import_log_source ON import_log(import_source);

                CREATE TABLE IF NOT EXISTS import_undo_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    import_source TEXT NOT NULL,
                    import_source_file TEXT,
                    batch_id TEXT NOT NULL,
                    old_batch_import_source TEXT,
                    old_batch_imported_at TEXT,
                    old_issue_states TEXT NOT NULL,
                    old_schemes TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_import_undo_batch ON import_undo_log(batch_id);
                """
            )

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
                "SELECT batch_id FROM batches WHERE scan_path = ? ORDER BY scanned_at DESC, batch_id DESC LIMIT 1",
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
            scanned_at = scan_result.timestamp.isoformat(timespec="microseconds")
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
                         fingerprint, state, handler, note, updated_at,
                         inherited_from_batch_id)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                            inherited_from,
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
        sql += " GROUP BY b.batch_id ORDER BY b.scanned_at DESC, b.batch_id DESC"

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

    def get_batch(self, batch_id: str) -> BatchInfo | None:
        sql = """SELECT b.*,
                 SUM(CASE WHEN i.state='pending' THEN 1 ELSE 0 END) as pending_count,
                 SUM(CASE WHEN i.state='passed' THEN 1 ELSE 0 END) as passed_count,
                 SUM(CASE WHEN i.state='ignored' THEN 1 ELSE 0 END) as ignored_count,
                 COUNT(i.id) as issue_count
                 FROM batches b LEFT JOIN issues i ON b.batch_id = i.batch_id
                 WHERE b.batch_id = ?
                 GROUP BY b.batch_id"""
        with self._conn() as conn:
            row = conn.execute(sql, (batch_id,)).fetchone()
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

    def get_previous_batch(self, batch_id: str) -> BatchInfo | None:
        batch = self.get_batch(batch_id)
        if batch is None:
            return None
        batches = self.list_batches(batch.scan_path)
        found = False
        for b in batches:
            if b.batch_id == batch_id:
                found = True
                continue
            if found:
                return b
        return None

    def compare_batches(self, batch_old_id: str, batch_new_id: str) -> BatchDiffResult:
        from .exceptions import BatchNotFoundError

        batch_old = self.get_batch(batch_old_id)
        if batch_old is None:
            raise BatchNotFoundError(batch_old_id)
        batch_new = self.get_batch(batch_new_id)
        if batch_new is None:
            raise BatchNotFoundError(batch_new_id)

        old_issues = self.get_issues(batch_old_id)
        new_issues = self.get_issues(batch_new_id)

        old_by_fp: dict[str, IssueRecord] = {}
        for i in old_issues:
            if i.fingerprint:
                old_by_fp[i.fingerprint] = i

        new_by_fp: dict[str, IssueRecord] = {}
        for i in new_issues:
            if i.fingerprint:
                new_by_fp[i.fingerprint] = i

        added: list[IssueRecord] = []
        removed: list[IssueRecord] = []
        inherited: list[DiffIssueRecord] = []

        for ni in new_issues:
            if ni.fingerprint and ni.fingerprint in old_by_fp:
                inherited.append(DiffIssueRecord(
                    diff_type="inherited",
                    issue=ni,
                    old_issue=old_by_fp[ni.fingerprint],
                ))
            else:
                added.append(ni)

        for oi in old_issues:
            if not oi.fingerprint or oi.fingerprint not in new_by_fp:
                removed.append(oi)

        return BatchDiffResult(
            batch_old=batch_old,
            batch_new=batch_new,
            added=added,
            removed=removed,
            inherited=inherited,
        )

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
                self._row_to_issue(r)
                for r in rows
            ]

    def _row_to_issue(self, r: sqlite3.Row) -> IssueRecord:
        keys = set(r.keys())
        return IssueRecord(
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
            fingerprint=r["fingerprint"] if "fingerprint" in keys else None,
            state=r["state"],
            handler=r["handler"],
            note=r["note"],
            updated_at=r["updated_at"],
            inherited_from_batch_id=r["inherited_from_batch_id"] if "inherited_from_batch_id" in keys else None,
            import_source=r["import_source"] if "import_source" in keys else None,
        )

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

            return self._row_to_issue(updated)

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
        source_file: str | None = None,
        preserve_created_at: str | None = None,
        preserve_updated_at: str | None = None,
    ) -> tuple[FilterScheme, str]:
        name = name.strip()
        if not name:
            raise ValueError("方案名称不能为空")

        has_any = any(v is not None for v in (batch_id, state, severity, project_type_id))
        if not has_any:
            raise EmptySchemeError(name)

        if state is not None:
            state = _validate_state(state)

        now = datetime.now().isoformat(timespec="seconds")
        use_created_at = preserve_created_at or now
        use_updated_at = preserve_updated_at or now
        result_action = ""

        with self._tx() as conn:
            existing = conn.execute(
                "SELECT * FROM filter_schemes WHERE name = ?",
                (name,),
            ).fetchone()

            if existing and not overwrite:
                raise SchemeExistsError(name)

            if existing:
                conn.execute(
                    """INSERT OR REPLACE INTO scheme_undo_log
                    (scheme_name, old_batch_id, old_state, old_severity, old_project_type_id,
                     old_created_at, old_updated_at, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        name,
                        existing["batch_id"],
                        existing["state"],
                        existing["severity"],
                        existing["project_type_id"],
                        existing["created_at"],
                        existing["updated_at"],
                        now,
                    ),
                )
                new_version = (existing["version"] or 1) + 1
                final_created_at = existing["created_at"] if preserve_created_at is None else preserve_created_at
                conn.execute(
                    """UPDATE filter_schemes
                    SET batch_id=?, state=?, severity=?, project_type_id=?, updated_at=?, version=?, created_at=?
                    WHERE name=?""",
                    (batch_id, state, severity, project_type_id, use_updated_at, new_version, final_created_at, name),
                )
                result_action = "overwrite"
            else:
                new_version = 1
                try:
                    conn.execute(
                        """INSERT INTO filter_schemes
                        (name, batch_id, state, severity, project_type_id, created_at, updated_at, version)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (name, batch_id, state, severity, project_type_id, use_created_at, use_updated_at, new_version),
                    )
                except sqlite3.IntegrityError as e:
                    if "UNIQUE" in str(e):
                        raise SchemeExistsError(name) from e
                    raise
                result_action = "create"

            audit_detail = []
            if batch_id:
                audit_detail.append(f"batch={batch_id}")
            if state:
                audit_detail.append(f"state={state}")
            if severity:
                audit_detail.append(f"severity={severity}")
            if project_type_id:
                audit_detail.append(f"project_type={project_type_id}")
            conn.execute(
                """INSERT INTO scheme_audit_log
                (action, scheme_name, source_file, result, detail, created_at)
                VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    "import" if source_file else "save",
                    name,
                    source_file,
                    result_action,
                    ";".join(audit_detail) if audit_detail else None,
                    now,
                ),
            )

            row = conn.execute(
                "SELECT * FROM filter_schemes WHERE name = ?",
                (name,),
            ).fetchone()
            scheme = FilterScheme(
                id=row["id"],
                name=row["name"],
                batch_id=row["batch_id"],
                state=row["state"],
                severity=row["severity"],
                project_type_id=row["project_type_id"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                version=row["version"] or 1,
            )
            return scheme, result_action

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
                    version=r["version"] or 1,
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
                version=row["version"] or 1,
            )

    def delete_scheme(self, name: str) -> None:
        with self._tx() as conn:
            existing = conn.execute(
                "SELECT id FROM filter_schemes WHERE name = ?",
                (name,),
            ).fetchone()
            if existing is None:
                raise SchemeNotFoundError(name)
            now = datetime.now().isoformat(timespec="seconds")
            conn.execute(
                """INSERT INTO scheme_audit_log
                (action, scheme_name, source_file, result, detail, created_at)
                VALUES (?, ?, ?, ?, ?, ?)""",
                ("delete", name, None, "delete", None, now),
            )
            conn.execute("DELETE FROM scheme_undo_log WHERE scheme_name = ?", (name,))
            conn.execute("DELETE FROM filter_schemes WHERE name = ?", (name,))

    @staticmethod
    def _make_migration_package(schemes: list[FilterScheme]) -> dict[str, Any]:
        return {
            "package_version": 1,
            "tool_version": PACKAGE_VERSION,
            "exported_at": datetime.now().isoformat(timespec="seconds"),
            "scheme_count": len(schemes),
            "schemes": [s.to_migration_dict() for s in schemes],
        }

    def export_schemes(self, names: list[str] | None = None) -> dict[str, Any]:
        schemes = self.list_schemes()
        if names:
            name_set = set(names)
            missing = name_set - {s.name for s in schemes}
            if missing:
                raise SchemeNotFoundError(next(iter(missing)))
            schemes = [s for s in schemes if s.name in name_set]
        if not schemes:
            raise SchemeNotFoundError("(无方案可导出)")
        return self._make_migration_package(schemes)

    def export_schemes_to_file(self, output_path: str | os.PathLike, names: list[str] | None = None) -> Path:
        pkg = self.export_schemes(names)
        out = Path(output_path)
        out.write_text(json.dumps(pkg, ensure_ascii=False, indent=2), encoding="utf-8")
        return out

    @staticmethod
    def _load_migration_package(file_path: str | os.PathLike) -> dict[str, Any]:
        fp = Path(file_path)
        abs_path = str(fp.resolve())
        raw = fp.read_text(encoding="utf-8")
        if not raw.strip():
            raise MigrationPackageEmptyError(abs_path)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise MigrationPackageParseError(abs_path, f"JSON 格式错误: {e.msg} (行{e.lineno}, 列{e.colno})") from e
        if not isinstance(data, dict):
            raise InvalidMigrationPackageError(f"文件 {abs_path} 顶层结构应为 JSON 对象，实际是 {type(data).__name__}")
        if "schemes" not in data:
            raise MigrationPackageMissingFieldError(abs_path, "schemes")
        if not isinstance(data["schemes"], list):
            raise InvalidMigrationPackageError(f"文件 {abs_path} 的 'schemes' 字段应为数组")
        if not data["schemes"]:
            raise MigrationPackageEmptyError(abs_path)
        required_fields = {"name", "created_at", "updated_at", "version"}
        optional_any = {"batch_id", "state", "severity", "project_type_id"}
        for idx, s in enumerate(data["schemes"]):
            if not isinstance(s, dict):
                raise InvalidMigrationPackageError(
                    f"文件 {abs_path} 第{idx + 1}个方案不是 JSON 对象"
                )
            for fld in required_fields:
                if fld not in s:
                    raise MigrationPackageMissingFieldError(abs_path, fld, idx)
            if not isinstance(s["name"], str) or not s["name"].strip():
                raise InvalidMigrationPackageError(
                    f"文件 {abs_path} 第{idx + 1}个方案 'name' 字段为空或不是字符串"
                )
            has_any = any(s.get(f) is not None for f in optional_any)
            if not has_any:
                raise InvalidMigrationPackageError(
                    f"文件 {abs_path} 第{idx + 1}个方案 '{s['name']}' 未指定任何筛选条件"
                )
        return data

    def _log_scheme_audit(
        self,
        conn: sqlite3.Connection,
        action: str,
        scheme_name: str,
        result: str,
        source_file: str | None = None,
        detail: str | None = None,
    ) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        conn.execute(
            """INSERT INTO scheme_audit_log
            (action, scheme_name, source_file, result, detail, created_at)
            VALUES (?, ?, ?, ?, ?, ?)""",
            (action, scheme_name, source_file, result, detail, now),
        )

    def import_schemes(
        self,
        file_path: str | os.PathLike,
        overwrite: bool = False,
        preserve_timestamps: bool = True,
    ) -> dict[str, Any]:
        fp = Path(file_path)
        abs_path = str(fp.resolve())
        pkg = self._load_migration_package(abs_path)

        created: list[str] = []
        overwritten: list[str] = []
        skipped: list[str] = []
        errors: list[dict[str, str]] = []

        with self._tx() as conn:
            for idx, s in enumerate(pkg["schemes"]):
                name = s["name"].strip()
                try:
                    existing = conn.execute(
                        "SELECT * FROM filter_schemes WHERE name = ?", (name,)
                    ).fetchone()

                    if existing and not overwrite:
                        skipped.append(name)
                        self._log_scheme_audit(
                            conn, "import", name, "skip", source_file=abs_path,
                            detail="方案已存在，未使用 --overwrite，已跳过"
                        )
                        continue

                    state_val = s.get("state")
                    if state_val is not None:
                        try:
                            state_val = _validate_state(state_val)
                        except InvalidStateError:
                            errors.append({
                                "name": name,
                                "reason": f"无效的 state 值: {state_val}",
                            })
                            self._log_scheme_audit(
                                conn, "import", name, "error", source_file=abs_path,
                                detail=f"无效 state 值: {state_val}"
                            )
                            continue

                    severity_val = s.get("severity")
                    if severity_val is not None and severity_val not in {"error", "warning"}:
                        errors.append({
                            "name": name,
                            "reason": f"无效的 severity 值: {severity_val}（应为 error/warning）",
                        })
                        self._log_scheme_audit(
                            conn, "import", name, "error", source_file=abs_path,
                            detail=f"无效 severity 值: {severity_val}"
                        )
                        continue

                    batch_val = s.get("batch_id")
                    pt_val = s.get("project_type_id")
                    version_val = int(s.get("version") or 1)

                    if existing:
                        conn.execute(
                            """INSERT OR REPLACE INTO scheme_undo_log
                            (scheme_name, old_batch_id, old_state, old_severity, old_project_type_id,
                             old_created_at, old_updated_at, created_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                            (
                                name, existing["batch_id"], existing["state"],
                                existing["severity"], existing["project_type_id"],
                                existing["created_at"], existing["updated_at"],
                                datetime.now().isoformat(timespec="seconds"),
                            ),
                        )
                        new_ver = max((existing["version"] or 1) + 1, version_val)
                        final_created_at = (
                            s["created_at"] if preserve_timestamps else existing["created_at"]
                        )
                        final_updated_at = s["updated_at"] if preserve_timestamps else datetime.now().isoformat(timespec="seconds")
                        conn.execute(
                            """UPDATE filter_schemes
                            SET batch_id=?, state=?, severity=?, project_type_id=?,
                                created_at=?, updated_at=?, version=?
                            WHERE name=?""",
                            (
                                batch_val, state_val, severity_val, pt_val,
                                final_created_at, final_updated_at, new_ver, name,
                            ),
                        )
                        overwritten.append(name)
                        result = "overwrite"
                    else:
                        final_created_at = s["created_at"] if preserve_timestamps else datetime.now().isoformat(timespec="seconds")
                        final_updated_at = s["updated_at"] if preserve_timestamps else datetime.now().isoformat(timespec="seconds")
                        try:
                            conn.execute(
                                """INSERT INTO filter_schemes
                                (name, batch_id, state, severity, project_type_id, created_at, updated_at, version)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                                (
                                    name, batch_val, state_val, severity_val, pt_val,
                                    final_created_at, final_updated_at, version_val,
                                ),
                            )
                        except sqlite3.IntegrityError as e:
                            if "UNIQUE" in str(e):
                                skipped.append(name)
                                self._log_scheme_audit(
                                    conn, "import", name, "skip", source_file=abs_path,
                                    detail="并发插入导致 UNIQUE 冲突，已跳过"
                                )
                                continue
                            raise
                        created.append(name)
                        result = "create"

                    detail_parts = []
                    if batch_val:
                        detail_parts.append(f"batch={batch_val}")
                    if state_val:
                        detail_parts.append(f"state={state_val}")
                    if severity_val:
                        detail_parts.append(f"severity={severity_val}")
                    if pt_val:
                        detail_parts.append(f"project_type={pt_val}")
                    self._log_scheme_audit(
                        conn, "import", name, result, source_file=abs_path,
                        detail=";".join(detail_parts) if detail_parts else None,
                    )
                except sqlite3.OperationalError as e:
                    if _is_permission_error(e):
                        raise
                    sanitized = "数据操作失败"
                    if not any(er["name"] == name for er in errors):
                        errors.append({"name": name, "reason": sanitized})
                except ContractArchiverError:
                    raise
                except Exception as e:
                    sanitized = type(e).__name__
                    err_msg = str(e)
                    if "sqlite3" not in err_msg.lower() and "operational" not in err_msg.lower():
                        sanitized = err_msg if len(err_msg) < 80 else err_msg[:77] + "..."
                    if not any(er["name"] == name for er in errors):
                        errors.append({"name": name, "reason": sanitized})
                    try:
                        self._log_scheme_audit(
                            conn, "import", name, "error", source_file=abs_path,
                            detail=sanitized[:500],
                        )
                    except Exception:
                        pass

        return {
            "source_file": abs_path,
            "package_version": pkg.get("package_version"),
            "tool_version": pkg.get("tool_version"),
            "exported_at": pkg.get("exported_at"),
            "total": len(pkg["schemes"]),
            "created": created,
            "overwritten": overwritten,
            "skipped": skipped,
            "errors": errors,
        }

    def get_scheme_undo_count(self) -> int:
        with self._conn() as conn:
            row = conn.execute("SELECT COUNT(*) as cnt FROM scheme_undo_log").fetchone()
            return row["cnt"] or 0

    def undo_last_scheme_change(self, scheme_name: str | None = None) -> SchemeUndoRecord:
        with self._tx() as conn:
            if scheme_name:
                row = conn.execute(
                    "SELECT * FROM scheme_undo_log WHERE scheme_name = ?",
                    (scheme_name,),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM scheme_undo_log ORDER BY id DESC LIMIT 1"
                ).fetchone()
            if row is None:
                raise EmptyUndoError()

            undo = SchemeUndoRecord(
                id=row["id"],
                scheme_name=row["scheme_name"],
                old_batch_id=row["old_batch_id"],
                old_state=row["old_state"],
                old_severity=row["old_severity"],
                old_project_type_id=row["old_project_type_id"],
                old_created_at=row["old_created_at"],
                old_updated_at=row["old_updated_at"],
                created_at=row["created_at"],
            )

            existing = conn.execute(
                "SELECT id FROM filter_schemes WHERE name = ?", (undo.scheme_name,)
            ).fetchone()

            if existing:
                conn.execute(
                    """UPDATE filter_schemes
                    SET batch_id=?, state=?, severity=?, project_type_id=?,
                        created_at=?, updated_at=?, version = CASE WHEN version > 1 THEN version - 1 ELSE 1 END
                    WHERE name=?""",
                    (
                        undo.old_batch_id, undo.old_state, undo.old_severity,
                        undo.old_project_type_id, undo.old_created_at,
                        undo.old_updated_at, undo.scheme_name,
                    ),
                )
                result_action = "restore"
            else:
                conn.execute(
                    """INSERT INTO filter_schemes
                    (name, batch_id, state, severity, project_type_id, created_at, updated_at, version)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 1)""",
                    (
                        undo.scheme_name, undo.old_batch_id, undo.old_state,
                        undo.old_severity, undo.old_project_type_id,
                        undo.old_created_at, undo.old_updated_at,
                    ),
                )
                result_action = "recreate"

            now = datetime.now().isoformat(timespec="seconds")
            conn.execute(
                """INSERT INTO scheme_audit_log
                (action, scheme_name, source_file, result, detail, created_at)
                VALUES (?, ?, ?, ?, ?, ?)""",
                ("undo_scheme", undo.scheme_name, None, result_action, None, now),
            )
            conn.execute("DELETE FROM scheme_undo_log WHERE id = ?", (undo.id,))
            return undo

    def get_scheme_audit_log(self, scheme_name: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM scheme_audit_log"
        params: list[Any] = []
        if scheme_name:
            sql += " WHERE scheme_name = ?"
            params.append(scheme_name)
        sql += " ORDER BY created_at ASC, id ASC"
        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]

    def _compute_rule_summary(self, config_path: str | None) -> RuleConfigSummary | None:
        if not config_path:
            return None
        try:
            from .rules import load_rules
            rules = load_rules(config_path)
            rule_names = []
            for pt in rules.project_types:
                for att in pt.attachments:
                    rule_names.append(att.name)
            import hashlib
            config_content = Path(config_path).read_text(encoding="utf-8")
            config_hash = hashlib.sha256(config_content.encode("utf-8")).hexdigest()[:16]
            return RuleConfigSummary(
                project_type_count=len(rules.project_types),
                rule_names=rule_names,
                global_max_size_kb=rules.global_max_size_kb,
                config_path=config_path,
                config_hash=config_hash,
            )
        except Exception:
            return None

    def _get_undo_history_summary(self, batch_id: str) -> UndoHistorySummary:
        with self._conn() as conn:
            audit_rows = conn.execute(
                "SELECT created_at, action FROM audit_log WHERE batch_id = ? ORDER BY created_at DESC, id DESC LIMIT 1",
                (batch_id,),
            ).fetchall()
            last_time = None
            last_action = None
            if audit_rows:
                last_time = audit_rows[0]["created_at"]
                last_action = audit_rows[0]["action"]

            undo_count = conn.execute(
                "SELECT COUNT(*) as cnt FROM undo_log WHERE batch_id = ?",
                (batch_id,),
            ).fetchone()["cnt"]

            return UndoHistorySummary(
                total_undo_count=undo_count,
                last_undo_time=last_time,
                last_undo_action=last_action,
            )

    def _get_schemes_for_batch(self, batch_id: str) -> list[FilterScheme]:
        schemes = self.list_schemes()
        return [s for s in schemes if s.batch_id == batch_id]

    def _make_work_package(
        self,
        batch: BatchInfo,
        issues: list[IssueRecord],
        rule_summary: RuleConfigSummary | None,
        undo_summary: UndoHistorySummary,
        schemes: list[FilterScheme],
    ) -> dict[str, Any]:
        issues_data = []
        for i in issues:
            issues_data.append({
                "id": i.id,
                "project_path": i.project_path,
                "project_name": i.project_name,
                "project_type_id": i.project_type_id,
                "issue_type": i.issue_type,
                "severity": i.severity,
                "rule_name": i.rule_name,
                "file_path": i.file_path,
                "message": i.message,
                "fingerprint": i.fingerprint,
                "state": i.state,
                "handler": i.handler,
                "note": i.note,
                "updated_at": i.updated_at,
                "inherited_from_batch_id": i.inherited_from_batch_id,
            })

        return {
            "package_version": 1,
            "tool_version": PACKAGE_VERSION,
            "exported_at": datetime.now().isoformat(timespec="seconds"),
            "package_type": "work_package",
            "batch": {
                "batch_id": batch.batch_id,
                "scan_path": batch.scan_path,
                "scanned_at": batch.scanned_at,
                "config_path": batch.config_path,
                "issue_count": batch.issue_count,
                "pending_count": batch.pending_count,
                "passed_count": batch.passed_count,
                "ignored_count": batch.ignored_count,
            },
            "issues": issues_data,
            "rule_summary": rule_summary.to_dict() if rule_summary else None,
            "undo_history": undo_summary.to_dict(),
            "schemes": [s.to_migration_dict() for s in schemes],
        }

    def export_work_package(
        self,
        batch_id: str,
    ) -> dict[str, Any]:
        batch = self.get_batch(batch_id)
        if batch is None:
            raise BatchNotFoundError(batch_id)

        issues = self.get_issues(batch_id)
        rule_summary = self._compute_rule_summary(batch.config_path)
        undo_summary = self._get_undo_history_summary(batch_id)
        schemes = self._get_schemes_for_batch(batch_id)

        return self._make_work_package(batch, issues, rule_summary, undo_summary, schemes)

    def export_work_package_to_file(
        self,
        batch_id: str,
        output_path: str | os.PathLike,
    ) -> Path:
        pkg = self.export_work_package(batch_id)
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(pkg, ensure_ascii=False, indent=2), encoding="utf-8")
        return out

    @staticmethod
    def _load_work_package(file_path: str | os.PathLike) -> dict[str, Any]:
        fp = Path(file_path)
        abs_path = str(fp.resolve())
        raw = fp.read_text(encoding="utf-8")
        if not raw.strip():
            raise WorkPackageEmptyError(abs_path)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise WorkPackageParseError(abs_path, f"JSON 格式错误: {e.msg} (行{e.lineno}, 列{e.colno})") from e
        if not isinstance(data, dict):
            raise InvalidWorkPackageError(f"文件 {abs_path} 顶层结构应为 JSON 对象，实际是 {type(data).__name__}")
        if data.get("package_type") != "work_package":
            raise InvalidWorkPackageError(f"文件 {abs_path} 不是有效的工作包（缺少 package_type=work_package）")

        required_top = {"batch", "issues", "package_version"}
        for fld in required_top:
            if fld not in data:
                raise WorkPackageMissingFieldError(abs_path, fld)

        batch_required = {"batch_id", "scan_path", "scanned_at"}
        for fld in batch_required:
            if fld not in data["batch"]:
                raise WorkPackageMissingFieldError(abs_path, fld, "batch")

        if not isinstance(data["issues"], list):
            raise InvalidWorkPackageError(f"文件 {abs_path} 的 'issues' 字段应为数组")
        if not data["issues"]:
            raise WorkPackageEmptyError(abs_path)

        issue_required = {"project_path", "project_name", "issue_type", "severity", "message"}
        for idx, issue in enumerate(data["issues"]):
            if not isinstance(issue, dict):
                raise InvalidWorkPackageError(f"文件 {abs_path} 第{idx + 1}个问题不是 JSON 对象")
            for fld in issue_required:
                if fld not in issue:
                    raise WorkPackageMissingFieldError(abs_path, fld, f"issues[{idx}]")

        if "schemes" in data and not isinstance(data["schemes"], list):
            raise InvalidWorkPackageError(f"文件 {abs_path} 的 'schemes' 字段应为数组")

        return data

    def import_work_package(
        self,
        file_path: str | os.PathLike,
        overwrite_batch: bool = False,
        overwrite_state: bool = False,
        overwrite_scheme: bool = False,
        ignore_rule_mismatch: bool = False,
        import_source_label: str | None = None,
    ) -> dict[str, Any]:
        fp = Path(file_path)
        abs_path = str(fp.resolve())
        source_label = import_source_label or f"import:{fp.name}"

        pkg = self._load_work_package(abs_path)
        batch_data = pkg["batch"]
        batch_id = batch_data["batch_id"]
        issues_data = pkg["issues"]
        rule_summary_pkg = None
        if pkg.get("rule_summary"):
            rule_summary_pkg = RuleConfigSummary.from_dict(pkg["rule_summary"])
        schemes_data = pkg.get("schemes", [])

        result = {
            "source_file": abs_path,
            "package_version": pkg.get("package_version"),
            "tool_version": pkg.get("tool_version"),
            "exported_at": pkg.get("exported_at"),
            "batch_id": batch_id,
            "total_issues": len(issues_data),
            "total_schemes": len(schemes_data),
            "imported_issues": [],
            "skipped_issues": [],
            "conflict_issues": [],
            "imported_schemes": [],
            "skipped_schemes": [],
            "warnings": [],
        }

        with self._tx() as conn:
            existing_batch = conn.execute(
                "SELECT * FROM batches WHERE batch_id = ?", (batch_id,)
            ).fetchone()

            if existing_batch and not overwrite_batch:
                raise WorkPackageBatchExistsError(batch_id)

            if rule_summary_pkg and existing_batch and "rule_summary_json" in existing_batch and existing_batch["rule_summary_json"]:
                try:
                    existing_rule_summary = RuleConfigSummary.from_dict(
                        json.loads(existing_batch["rule_summary_json"])
                    )
                    diffs = rule_summary_pkg.compare(existing_rule_summary)
                    if diffs and not ignore_rule_mismatch:
                        raise WorkPackageRuleMismatchError(batch_id, "; ".join(diffs))
                    if diffs:
                        result["warnings"].append(f"规则摘要不一致: {'; '.join(diffs)}")
                except (json.JSONDecodeError, KeyError):
                    pass

            local_rule_summary = None
            if batch_data.get("config_path") and Path(batch_data["config_path"]).exists():
                local_rule_summary = self._compute_rule_summary(batch_data["config_path"])
                if rule_summary_pkg and local_rule_summary:
                    diffs = rule_summary_pkg.compare(local_rule_summary)
                    if diffs and not ignore_rule_mismatch:
                        raise WorkPackageRuleMismatchError(batch_id, "; ".join(diffs))
                    if diffs:
                        result["warnings"].append(f"规则摘要与本地配置不一致: {'; '.join(diffs)}")

            old_batch_import_source = None
            old_batch_imported_at = None
            old_issue_states = {}

            if existing_batch and overwrite_batch:
                old_batch_import_source = existing_batch["import_source"] if "import_source" in existing_batch else None
                old_batch_imported_at = existing_batch["imported_at"] if "imported_at" in existing_batch else None
                old_issues = conn.execute(
                    "SELECT * FROM issues WHERE batch_id = ?",
                    (batch_id,),
                ).fetchall()
                for oi in old_issues:
                    old_issue_states[oi["id"]] = {
                        "project_path": oi["project_path"],
                        "project_name": oi["project_name"],
                        "project_type_id": oi["project_type_id"],
                        "issue_type": oi["issue_type"],
                        "severity": oi["severity"],
                        "rule_name": oi["rule_name"],
                        "file_path": oi["file_path"],
                        "message": oi["message"],
                        "fingerprint": oi["fingerprint"],
                        "state": oi["state"],
                        "handler": oi["handler"],
                        "note": oi["note"],
                        "updated_at": oi["updated_at"],
                        "inherited_from_batch_id": oi["inherited_from_batch_id"],
                        "import_source": oi["import_source"] if "import_source" in oi else None,
                    }
                conn.execute("DELETE FROM issues WHERE batch_id = ?", (batch_id,))
                conn.execute("DELETE FROM batches WHERE batch_id = ?", (batch_id,))

            now = datetime.now().isoformat(timespec="seconds")
            rule_summary_json = json.dumps(rule_summary_pkg.to_dict()) if rule_summary_pkg else None

            conn.execute(
                """INSERT INTO batches
                (batch_id, scan_path, scanned_at, config_path, import_source, rule_summary_json, imported_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    batch_id,
                    batch_data["scan_path"],
                    batch_data["scanned_at"],
                    batch_data.get("config_path"),
                    source_label,
                    rule_summary_json,
                    now,
                ),
            )

            new_issue_ids = []
            for issue_data in issues_data:
                fingerprint = issue_data.get("fingerprint")
                existing_by_fp = None
                if fingerprint:
                    existing_by_fp = conn.execute(
                        "SELECT * FROM issues WHERE batch_id = ? AND fingerprint = ?",
                        (batch_id, fingerprint),
                    ).fetchone()

                if existing_by_fp and not overwrite_state:
                    if existing_by_fp["state"] != issue_data.get("state", "pending"):
                        result["conflict_issues"].append({
                            "id": existing_by_fp["id"],
                            "project_name": issue_data.get("project_name"),
                            "rule_name": issue_data.get("rule_name"),
                            "existing_state": existing_by_fp["state"],
                            "import_state": issue_data.get("state", "pending"),
                        })
                    result["skipped_issues"].append({
                        "fingerprint": fingerprint,
                        "project_name": issue_data.get("project_name"),
                        "rule_name": issue_data.get("rule_name"),
                    })
                    continue

                state_val = issue_data.get("state", "pending")
                if state_val not in VALID_STATES:
                    state_val = "pending"

                conn.execute(
                    """INSERT INTO issues
                    (batch_id, project_path, project_name, project_type_id,
                     issue_type, severity, rule_name, file_path, message,
                     fingerprint, state, handler, note, updated_at,
                     inherited_from_batch_id, import_source)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        batch_id,
                        issue_data["project_path"],
                        issue_data["project_name"],
                        issue_data.get("project_type_id"),
                        issue_data["issue_type"],
                        issue_data["severity"],
                        issue_data.get("rule_name"),
                        issue_data.get("file_path"),
                        issue_data["message"],
                        fingerprint,
                        state_val,
                        issue_data.get("handler"),
                        issue_data.get("note"),
                        issue_data.get("updated_at") or now,
                        issue_data.get("inherited_from_batch_id"),
                        source_label,
                    ),
                )
                new_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                new_issue_ids.append(new_id)
                result["imported_issues"].append({
                    "new_id": new_id,
                    "project_name": issue_data.get("project_name"),
                    "rule_name": issue_data.get("rule_name"),
                    "state": state_val,
                })

            old_schemes_json = None
            imported_scheme_names = []
            if schemes_data:
                old_schemes_data = []
                for s in schemes_data:
                    name = s.get("name", "").strip()
                    if not name:
                        continue
                    existing_scheme = conn.execute(
                        "SELECT * FROM filter_schemes WHERE name = ?", (name,)
                    ).fetchone()
                    if existing_scheme and not overwrite_scheme:
                        result["skipped_schemes"].append(name)
                        continue
                    if existing_scheme:
                        old_schemes_data.append(dict(existing_scheme))
                        conn.execute("DELETE FROM filter_schemes WHERE name = ?", (name,))

                    try:
                        state_val = s.get("state")
                        if state_val is not None:
                            state_val = _validate_state(state_val)
                        severity_val = s.get("severity")
                        if severity_val is not None and severity_val not in {"error", "warning"}:
                            severity_val = None

                        now_s = datetime.now().isoformat(timespec="seconds")
                        created_at = s.get("created_at") or now_s
                        updated_at = s.get("updated_at") or now_s
                        version_val = int(s.get("version") or 1)

                        conn.execute(
                            """INSERT INTO filter_schemes
                            (name, batch_id, state, severity, project_type_id, created_at, updated_at, version)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                            (
                                name,
                                batch_id,
                                state_val,
                                severity_val,
                                s.get("project_type_id"),
                                created_at,
                                updated_at,
                                version_val,
                            ),
                        )
                        imported_scheme_names.append(name)
                        result["imported_schemes"].append(name)
                    except Exception as e:
                        result["warnings"].append(f"方案 '{name}' 导入失败: {e}")

                if old_schemes_data:
                    old_schemes_json = json.dumps(old_schemes_data)

            old_issue_states_json = json.dumps(old_issue_states) if old_issue_states else "{}"
            conn.execute(
                """INSERT INTO import_undo_log
                (import_source, import_source_file, batch_id,
                 old_batch_import_source, old_batch_imported_at,
                 old_issue_states, old_schemes, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    source_label,
                    abs_path,
                    batch_id,
                    old_batch_import_source,
                    old_batch_imported_at,
                    old_issue_states_json,
                    old_schemes_json,
                    now,
                ),
            )

            conn.execute(
                """INSERT INTO import_log
                (import_source, import_source_file, batch_id,
                 imported_issue_ids, imported_scheme_names, created_at)
                VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    source_label,
                    abs_path,
                    batch_id,
                    json.dumps(new_issue_ids),
                    json.dumps(imported_scheme_names) if imported_scheme_names else None,
                    now,
                ),
            )

        return result

    def get_import_undo_count(self, batch_id: str | None = None) -> int:
        with self._conn() as conn:
            if batch_id:
                row = conn.execute(
                    "SELECT COUNT(*) as cnt FROM import_undo_log WHERE batch_id = ?",
                    (batch_id,),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT COUNT(*) as cnt FROM import_undo_log"
                ).fetchone()
            return row["cnt"] or 0

    def undo_last_import(self, batch_id: str | None = None) -> ImportUndoRecord:
        with self._tx() as conn:
            if batch_id:
                row = conn.execute(
                    "SELECT * FROM import_undo_log WHERE batch_id = ? ORDER BY id DESC LIMIT 1",
                    (batch_id,),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM import_undo_log ORDER BY id DESC LIMIT 1"
                ).fetchone()
            if row is None:
                raise EmptyWorkPackageUndoError()

            undo_batch_id = row["batch_id"]
            import_source = row["import_source"]
            import_source_file = row["import_source_file"]

            try:
                old_issue_states = json.loads(row["old_issue_states"] or "{}")
            except json.JSONDecodeError:
                old_issue_states = {}

            current_issues = conn.execute(
                "SELECT id FROM issues WHERE batch_id = ?", (undo_batch_id,)
            ).fetchall()
            imported_issue_ids = [r["id"] for r in current_issues]

            audit_issue_id = current_issues[0]["id"] if current_issues else 0

            try:
                imported_scheme_names = []
                import_log_row = conn.execute(
                    "SELECT imported_scheme_names FROM import_log WHERE batch_id = ? ORDER BY id DESC LIMIT 1",
                    (undo_batch_id,),
                ).fetchone()
                if import_log_row and import_log_row["imported_scheme_names"]:
                    imported_scheme_names = json.loads(import_log_row["imported_scheme_names"])
            except (json.JSONDecodeError, KeyError):
                imported_scheme_names = []

            now = datetime.now().isoformat(timespec="seconds")
            conn.execute(
                """INSERT INTO audit_log
                (batch_id, issue_id, action, old_state, old_handler, old_note,
                 new_state, new_handler, new_note, created_at)
                VALUES (?, ?, 'undo_import', ?, ?, ?, ?, ?, ?, ?)""",
                (
                    undo_batch_id,
                    audit_issue_id,
                    "", "", "", "", "", "",
                    now,
                ),
            )

            conn.execute("DELETE FROM filter_schemes WHERE batch_id = ?", (undo_batch_id,))
            
            if old_issue_states:
                conn.execute("DELETE FROM issues WHERE batch_id = ?", (undo_batch_id,))
                for old_id, old_data in old_issue_states.items():
                    conn.execute(
                        """INSERT INTO issues
                        (id, batch_id, project_path, project_name, project_type_id,
                         issue_type, severity, rule_name, file_path, message,
                         fingerprint, state, handler, note, updated_at,
                         inherited_from_batch_id, import_source)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            int(old_id),
                            undo_batch_id,
                            old_data["project_path"],
                            old_data["project_name"],
                            old_data["project_type_id"],
                            old_data["issue_type"],
                            old_data["severity"],
                            old_data["rule_name"],
                            old_data["file_path"],
                            old_data["message"],
                            old_data["fingerprint"],
                            old_data["state"],
                            old_data["handler"],
                            old_data["note"],
                            old_data["updated_at"],
                            old_data["inherited_from_batch_id"],
                            old_data.get("import_source"),
                        ),
                    )

                conn.execute(
                    "UPDATE batches SET import_source = ?, imported_at = ? WHERE batch_id = ?",
                    (row["old_batch_import_source"], row["old_batch_imported_at"], undo_batch_id),
                )
            else:
                conn.execute("DELETE FROM issues WHERE batch_id = ?", (undo_batch_id,))
                conn.execute("DELETE FROM batches WHERE batch_id = ?", (undo_batch_id,))

            if row["old_schemes"]:
                try:
                    old_schemes = json.loads(row["old_schemes"])
                    for s in old_schemes:
                        conn.execute(
                            """INSERT OR REPLACE INTO filter_schemes
                            (id, name, batch_id, state, severity, project_type_id, created_at, updated_at, version)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            (
                                s["id"], s["name"], s["batch_id"], s["state"],
                                s["severity"], s["project_type_id"],
                                s["created_at"], s["updated_at"], s["version"],
                            ),
                        )
                except (json.JSONDecodeError, KeyError):
                    pass

            conn.execute("DELETE FROM import_undo_log WHERE id = ?", (row["id"],))

            return ImportUndoRecord(
                id=row["id"],
                import_source=import_source,
                import_source_file=import_source_file,
                batch_id=undo_batch_id,
                imported_issue_ids=imported_issue_ids,
                imported_scheme_names=imported_scheme_names,
                created_at=row["created_at"],
            )

    def get_import_log(self, batch_id: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM import_log"
        params: list[Any] = []
        if batch_id:
            sql += " WHERE batch_id = ?"
            params.append(batch_id)
        sql += " ORDER BY created_at DESC, id DESC"
        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
            result = []
            for r in rows:
                d = dict(r)
                try:
                    d["imported_issue_ids"] = json.loads(d["imported_issue_ids"] or "[]")
                except (json.JSONDecodeError, TypeError):
                    d["imported_issue_ids"] = []
                try:
                    if d.get("imported_scheme_names"):
                        d["imported_scheme_names"] = json.loads(d["imported_scheme_names"])
                    else:
                        d["imported_scheme_names"] = []
                except (json.JSONDecodeError, TypeError):
                    d["imported_scheme_names"] = []
                result.append(d)
            return result
