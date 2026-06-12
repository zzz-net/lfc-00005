from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Iterable

from .exceptions import DirectoryNotFoundError
from .rules import AttachmentRule, ProjectType, ValidationRules


class IssueType(str, Enum):
    MISSING = "missing"
    NAMING_ERROR = "naming_error"
    EMPTY_FILE = "empty_file"
    DUPLICATE_VERSION = "duplicate_version"
    UNDOCUMENTED = "undocumented"
    SIZE_EXCEEDED = "size_exceeded"


class IssueSeverity(str, Enum):
    ERROR = "error"
    WARNING = "warning"


ISSUE_LABELS = {
    IssueType.MISSING: "缺失附件",
    IssueType.NAMING_ERROR: "命名错误",
    IssueType.EMPTY_FILE: "空文件",
    IssueType.DUPLICATE_VERSION: "重复版本",
    IssueType.UNDOCUMENTED: "未纳入规则",
    IssueType.SIZE_EXCEEDED: "文件过大",
}


@dataclass
class ScannedFile:
    rel_path: str
    abs_path: str
    size_bytes: int
    matched_rule: AttachmentRule | None = None
    extracted_version: str | None = None


@dataclass
class ScanIssue:
    issue_type: IssueType
    severity: IssueSeverity
    message: str
    rule_name: str | None = None
    file_path: str | None = None
    file_size_kb: float | None = None
    limit_kb: int | None = None
    fingerprint: str | None = None

    @property
    def issue_label(self) -> str:
        return ISSUE_LABELS.get(self.issue_type, str(self.issue_type))


def compute_fingerprint(project_path: str, *parts: str | None) -> str:
    key_parts = [str(Path(project_path).resolve()).lower()]
    for p in parts:
        key_parts.append(str(p).lower() if p else "")
    raw = "|".join(key_parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


@dataclass
class ProjectScanResult:
    project_path: str
    project_name: str
    project_type_id: str | None
    project_type_name: str | None
    files: list[ScannedFile] = field(default_factory=list)
    issues: list[ScanIssue] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return any(i.severity == IssueSeverity.ERROR for i in self.issues)


@dataclass
class ScanResult:
    scan_path: str
    timestamp: datetime
    projects: list[ProjectScanResult] = field(default_factory=list)
    unscanned_dirs: list[str] = field(default_factory=list)
    unscanned_files: list[str] = field(default_factory=list)


def _should_ignore(name: str, ignore_patterns: Iterable[str]) -> bool:
    if name.startswith("."):
        return True
    for p in ignore_patterns:
        if re.search(p, name, re.IGNORECASE):
            return True
    return False


def _get_max_size(rule: AttachmentRule, global_max: int | None) -> int | None:
    if rule.max_size_kb is not None:
        return rule.max_size_kb
    return global_max


def _scan_project(
    project_dir: Path,
    project_type: ProjectType,
    rules: ValidationRules,
) -> ProjectScanResult:
    project_path = str(project_dir)
    result = ProjectScanResult(
        project_path=project_path,
        project_name=project_dir.name,
        project_type_id=project_type.type_id,
        project_type_name=project_type.display_name,
    )

    seen_files: list[ScannedFile] = []

    try:
        entries = sorted(os.listdir(project_dir))
    except OSError as e:
        result.issues.append(ScanIssue(
            issue_type=IssueType.UNDOCUMENTED,
            severity=IssueSeverity.WARNING,
            message=f"无法读取目录: {e}",
            file_path=str(project_dir),
        ))
        return result

    rule_match_counts: dict[str, list[ScannedFile]] = {r.name: [] for r in project_type.attachments}

    for entry in entries:
        entry_path = project_dir / entry
        full_path = str(entry_path)
        rel_path = entry

        if _should_ignore(entry, rules.ignore_patterns):
            continue

        if entry_path.is_dir():
            result.unscanned_dirs = getattr(result, "unscanned_dirs", [])
            result.unscanned_dirs.append(rel_path)
            result.issues.append(ScanIssue(
                issue_type=IssueType.UNDOCUMENTED,
                severity=IssueSeverity.WARNING,
                message=f"发现未纳入规则的子目录: {entry}",
                file_path=rel_path,
                fingerprint=compute_fingerprint(project_path, IssueType.UNDOCUMENTED.value, None, rel_path),
            ))
            continue

        if not entry_path.is_file():
            continue

        try:
            size = entry_path.stat().st_size
        except OSError:
            size = 0

        scanned = ScannedFile(
            rel_path=rel_path,
            abs_path=full_path,
            size_bytes=size,
        )

        matched = False
        for rule in project_type.attachments:
            if rule.matches_name(entry):
                scanned.matched_rule = rule
                scanned.extracted_version = rule.extract_version(entry)
                rule_match_counts[rule.name].append(scanned)
                matched = True
                break

        if not matched:
            result.issues.append(ScanIssue(
                issue_type=IssueType.UNDOCUMENTED,
                severity=IssueSeverity.WARNING,
                message=f"文件未匹配任何附件规则: {entry}",
                file_path=rel_path,
                fingerprint=compute_fingerprint(project_path, IssueType.UNDOCUMENTED.value, None, rel_path),
            ))
        else:
            max_size_kb = _get_max_size(scanned.matched_rule, rules.global_max_size_kb)
            if size == 0:
                result.issues.append(ScanIssue(
                    issue_type=IssueType.EMPTY_FILE,
                    severity=IssueSeverity.ERROR,
                    message=f"文件为空: {entry}",
                    rule_name=scanned.matched_rule.name,
                    file_path=rel_path,
                    fingerprint=compute_fingerprint(project_path, IssueType.EMPTY_FILE.value, scanned.matched_rule.name, rel_path),
                ))
            elif max_size_kb is not None and size > max_size_kb * 1024:
                result.issues.append(ScanIssue(
                    issue_type=IssueType.SIZE_EXCEEDED,
                    severity=IssueSeverity.WARNING,
                    message=(
                        f"文件大小 {size/1024:.1f}KB 超过限制 {max_size_kb}KB: {entry}"
                    ),
                    rule_name=scanned.matched_rule.name,
                    file_path=rel_path,
                    file_size_kb=round(size / 1024, 2),
                    limit_kb=max_size_kb,
                    fingerprint=compute_fingerprint(project_path, IssueType.SIZE_EXCEEDED.value, scanned.matched_rule.name, rel_path),
                ))

            if scanned.matched_rule.naming_pattern is not None:
                if not scanned.matched_rule.naming_regex.fullmatch(os.path.splitext(entry)[0]):
                    result.issues.append(ScanIssue(
                        issue_type=IssueType.NAMING_ERROR,
                        severity=IssueSeverity.ERROR,
                        message=(
                            f"文件名不符合命名模式 '{scanned.matched_rule.naming_pattern}': {entry}"
                        ),
                        rule_name=scanned.matched_rule.name,
                        file_path=rel_path,
                        fingerprint=compute_fingerprint(project_path, IssueType.NAMING_ERROR.value, scanned.matched_rule.name, rel_path),
                    ))

        seen_files.append(scanned)

    for rule in project_type.attachments:
        matches = rule_match_counts.get(rule.name, [])
        if not matches:
            if rule.required:
                result.issues.append(ScanIssue(
                    issue_type=IssueType.MISSING,
                    severity=IssueSeverity.ERROR,
                    message=f"缺少必需附件: {rule.name}",
                    rule_name=rule.name,
                    fingerprint=compute_fingerprint(project_path, IssueType.MISSING.value, rule.name, None),
                ))
            continue

        if rule.version_priority:
            version_files: dict[str, list[ScannedFile]] = {}
            no_version: list[ScannedFile] = []
            for f in matches:
                v = f.extracted_version
                if v:
                    version_files.setdefault(v, []).append(f)
                else:
                    no_version.append(f)

            for v, files in version_files.items():
                if len(files) > 1:
                    names = ", ".join(f.rel_path for f in files)
                    fp_rel = "|".join(sorted(f.rel_path for f in files))
                    result.issues.append(ScanIssue(
                        issue_type=IssueType.DUPLICATE_VERSION,
                        severity=IssueSeverity.ERROR,
                        message=f"版本 '{v}' 存在重复文件: {names}",
                        rule_name=rule.name,
                        fingerprint=compute_fingerprint(project_path, IssueType.DUPLICATE_VERSION.value, rule.name, fp_rel),
                    ))

            highest = None
            for v in rule.version_priority:
                if v in version_files:
                    highest = v
                    break

            if highest is None and no_version:
                pass
            elif highest:
                for v, files in version_files.items():
                    if v != highest:
                        names = ", ".join(f.rel_path for f in files)
                        fp_rel = "|".join(sorted(f.rel_path for f in files))
                        dup_severity = IssueSeverity.ERROR if rule.required else IssueSeverity.WARNING
                        result.issues.append(ScanIssue(
                            issue_type=IssueType.DUPLICATE_VERSION,
                            severity=dup_severity,
                            message=(
                                f"存在低优先级版本 '{v}'，建议使用最高版本 '{highest}': {names}"
                            ),
                            rule_name=rule.name,
                            fingerprint=compute_fingerprint(project_path, IssueType.DUPLICATE_VERSION.value, rule.name, fp_rel),
                        ))

        elif len(matches) > 1:
            names = ", ".join(f.rel_path for f in matches)
            fp_rel = "|".join(sorted(f.rel_path for f in matches))
            result.issues.append(ScanIssue(
                issue_type=IssueType.DUPLICATE_VERSION,
                severity=IssueSeverity.ERROR,
                message=f"同一附件存在多个文件: {names}",
                rule_name=rule.name,
                fingerprint=compute_fingerprint(project_path, IssueType.DUPLICATE_VERSION.value, rule.name, fp_rel),
            ))

    result.files = seen_files
    return result


def scan_directory(root_path: str | os.PathLike, rules: ValidationRules) -> ScanResult:
    root = Path(root_path).resolve()
    if not root.exists() or not root.is_dir():
        raise DirectoryNotFoundError(str(root))

    result = ScanResult(
        scan_path=str(root),
        timestamp=datetime.now(),
    )

    try:
        entries = sorted(os.listdir(root))
    except OSError as e:
        raise DirectoryNotFoundError(f"{root}: {e}")

    for entry in entries:
        entry_path = root / entry

        if _should_ignore(entry, rules.ignore_patterns):
            continue

        if not entry_path.is_dir():
            if entry_path.is_file():
                result.unscanned_files.append(entry)
            continue

        project_type = rules.match_project_type(entry)
        if project_type is None:
            result.unscanned_dirs.append(entry)
            continue

        project_result = _scan_project(entry_path, project_type, rules)
        result.projects.append(project_result)

    return result
