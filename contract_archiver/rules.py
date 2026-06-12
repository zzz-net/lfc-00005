from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .exceptions import ConfigError


@dataclass
class AttachmentRule:
    name: str
    required: bool = True
    naming_pattern: str | None = None
    naming_regex: re.Pattern | None = None
    version_priority: list[str] = field(default_factory=list)
    max_size_kb: int | None = None

    def matches_name(self, filename: str) -> bool:
        if self.naming_regex is None:
            return self.name.lower() in filename.lower()
        return bool(self.naming_regex.search(filename))

    def extract_version(self, filename: str) -> str | None:
        if not self.version_priority:
            return None
        base = os.path.splitext(filename)[0]
        for v in self.version_priority:
            if v.lower() in base.lower():
                return v
        return None


@dataclass
class ProjectType:
    type_id: str
    display_name: str
    attachments: list[AttachmentRule] = field(default_factory=list)
    directory_pattern: str | None = None
    directory_regex: re.Pattern | None = None

    def matches_directory(self, dirname: str) -> bool:
        if self.directory_regex is None:
            return self.type_id.lower() in dirname.lower() or self.display_name.lower() in dirname.lower()
        return bool(self.directory_regex.search(dirname))


@dataclass
class ValidationRules:
    project_types: list[ProjectType] = field(default_factory=list)
    global_max_size_kb: int | None = None
    ignore_patterns: list[str] = field(default_factory=list)

    def match_project_type(self, dirname: str) -> ProjectType | None:
        for pt in self.project_types:
            if pt.matches_directory(dirname):
                return pt
        return None


def _validate_required(data: dict, key: str, parent: str = "") -> Any:
    if key not in data or data[key] in (None, ""):
        raise ConfigError(f"缺少必填字段 '{key}'", field=parent)
    return data[key]


def _load_attachment_rule(raw: dict, idx: int) -> AttachmentRule:
    parent = f"project_types[].attachments[{idx}]"
    name = _validate_required(raw, "name", parent)

    naming_pattern = raw.get("naming_pattern")
    naming_regex = None
    if naming_pattern:
        try:
            naming_regex = re.compile(naming_pattern, re.IGNORECASE)
        except re.error as e:
            raise ConfigError(f"naming_pattern 正则无效: {e}", field=f"{parent}.naming_pattern")

    version_priority = raw.get("version_priority", [])
    if not isinstance(version_priority, list):
        raise ConfigError("version_priority 必须是字符串数组", field=f"{parent}.version_priority")

    max_size_kb = raw.get("max_size_kb")
    if max_size_kb is not None and (not isinstance(max_size_kb, int) or max_size_kb <= 0):
        raise ConfigError("max_size_kb 必须是正整数", field=f"{parent}.max_size_kb")

    return AttachmentRule(
        name=name,
        required=bool(raw.get("required", True)),
        naming_pattern=naming_pattern,
        naming_regex=naming_regex,
        version_priority=list(version_priority),
        max_size_kb=max_size_kb,
    )


def _load_project_type(raw: dict, idx: int) -> ProjectType:
    parent = f"project_types[{idx}]"
    type_id = _validate_required(raw, "type_id", parent)
    display_name = raw.get("display_name") or type_id

    directory_pattern = raw.get("directory_pattern")
    directory_regex = None
    if directory_pattern:
        try:
            directory_regex = re.compile(directory_pattern, re.IGNORECASE)
        except re.error as e:
            raise ConfigError(f"directory_pattern 正则无效: {e}", field=f"{parent}.directory_pattern")

    raw_attachments = raw.get("attachments", [])
    if not isinstance(raw_attachments, list) or not raw_attachments:
        raise ConfigError("至少需要定义一个附件规则", field=f"{parent}.attachments")

    attachments = [_load_attachment_rule(a, i) for i, a in enumerate(raw_attachments)]
    return ProjectType(
        type_id=type_id,
        display_name=display_name,
        attachments=attachments,
        directory_pattern=directory_pattern,
        directory_regex=directory_regex,
    )


def load_rules(config_path: str | os.PathLike) -> ValidationRules:
    path = Path(config_path)
    if not path.exists():
        raise ConfigError(f"配置文件不存在: {path}")

    suffix = path.suffix.lower()
    if suffix not in (".yaml", ".yml", ".json"):
        raise ConfigError(f"不支持的配置格式: {suffix}，请使用 .json/.yaml/.yml")

    yaml_lib = None
    if suffix in (".yaml", ".yml"):
        try:
            import yaml as yaml_lib  # type: ignore
        except ImportError:
            raise ConfigError("需要 PyYAML 才能加载 YAML 配置，请执行 pip install PyYAML")

    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    except UnicodeDecodeError:
        raise ConfigError("配置文件编码必须是 UTF-8")

    try:
        if suffix in (".yaml", ".yml"):
            data = yaml_lib.safe_load(content)  # type: ignore
        else:
            data = json.loads(content)
    except Exception as e:
        raise ConfigError(f"配置文件解析失败: {e}")

    if not isinstance(data, dict):
        raise ConfigError("配置根节点必须是对象")

    raw_projects = data.get("project_types")
    if not isinstance(raw_projects, list) or not raw_projects:
        raise ConfigError("至少需要定义一个 project_types")

    project_types = [_load_project_type(p, i) for i, p in enumerate(raw_projects)]

    global_max_size_kb = data.get("global_max_size_kb")
    if global_max_size_kb is not None and (not isinstance(global_max_size_kb, int) or global_max_size_kb <= 0):
        raise ConfigError("global_max_size_kb 必须是正整数", field="global_max_size_kb")

    ignore_patterns = data.get("ignore_patterns", [])
    if not isinstance(ignore_patterns, list):
        raise ConfigError("ignore_patterns 必须是字符串数组", field="ignore_patterns")

    return ValidationRules(
        project_types=project_types,
        global_max_size_kb=global_max_size_kb,
        ignore_patterns=list(ignore_patterns),
    )
