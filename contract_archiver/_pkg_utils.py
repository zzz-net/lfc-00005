from __future__ import annotations

import inspect
import json
import zipfile
from pathlib import Path
from typing import Any


def _raise(exc_cls, *args, **kwargs):
    """智能异常构造：根据异常类实际签名裁剪参数"""
    # 先尝试直接调用
    try:
        raise exc_cls(*args, **kwargs)
    except TypeError:
        pass
    sig = inspect.signature(exc_cls.__init__)
    params = list(sig.parameters.keys())[1:]  # 去掉 self
    n_expected = len(params)
    # 构造恰好 n_expected 个参数的调用
    actual_args = list(args)
    # 如果参数太多，合并最后几个参数为消息字符串
    if len(actual_args) > n_expected:
        # 对 2 参数异常：第0个是 file_path 类参数，其余合并成 detail 字符串
        merged_msg = " ".join(str(a) for a in actual_args[1:] if a is not None)
        actual_args = [actual_args[0], merged_msg] if n_expected == 2 else [merged_msg]
    # 如果参数不够，补 None
    while len(actual_args) < n_expected:
        actual_args.append(None)
    actual_args = actual_args[:n_expected]
    raise exc_cls(*actual_args, **kwargs)


def load_package_file(
    file_path: str | Path,
    empty_exc,
    parse_exc,
    missing_exc,
    not_found_exc=None,
) -> tuple[str, str, dict[str, Any]]:
    fp = Path(file_path)
    abs_path = str(fp.resolve())

    if not fp.exists():
        if not_found_exc is not None:
            raise not_found_exc(f"包文件不存在: {abs_path}")
        _raise(parse_exc, abs_path, f"文件不存在: {abs_path}")

    raw = ""
    if fp.suffix.lower() == ".zip":
        try:
            with zipfile.ZipFile(str(fp), "r") as zf:
                json_names = [n for n in zf.namelist() if n.lower().endswith(".json")]
                if not json_names:
                    _raise(parse_exc, abs_path, "ZIP 压缩包内未找到 JSON 文件")
                with zf.open(json_names[0]) as jf:
                    raw = jf.read().decode("utf-8")
        except zipfile.BadZipFile as e:
            _raise(parse_exc, abs_path, f"无效的 ZIP 文件: {e}")
        except FileNotFoundError as e:
            if not_found_exc is not None:
                raise not_found_exc(f"包文件不存在: {abs_path}") from e
            _raise(parse_exc, abs_path, f"文件不存在: {abs_path}")
    else:
        try:
            raw = fp.read_text(encoding="utf-8")
        except FileNotFoundError as e:
            if not_found_exc is not None:
                raise not_found_exc(f"包文件不存在: {abs_path}") from e
            _raise(parse_exc, abs_path, f"文件不存在: {abs_path}")

    if not raw.strip():
        _raise(empty_exc, abs_path)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        _raise(parse_exc, abs_path, f"JSON 格式错误: {e.msg} (行{e.lineno}, 列{e.colno})")

    if not isinstance(data, dict):
        _raise(parse_exc, abs_path, f"顶层结构应为 JSON 对象，实际是 {type(data).__name__}")

    return abs_path, raw, data


def validate_package_type(
    data: dict[str, Any],
    abs_path: str,
    expected_type: str,
    parse_exc,
    missing_exc=None,
) -> None:
    if missing_exc is not None:
        if "package_type" not in data:
            _raise(missing_exc, abs_path, "package_type")
    if data.get("package_type") != expected_type:
        actual = data.get("package_type")
        if actual is None:
            _raise(parse_exc, f"文件 {abs_path} 缺少 package_type 字段")
        else:
            _raise(parse_exc, f"文件 {abs_path} 的 package_type 应为 '{expected_type}'，实际为 {actual!r}")


def validate_package_version(
    data: dict[str, Any],
    abs_path: str,
    parse_exc,
    invalid_exc=None,
    missing_exc=None,
) -> int:
    key = "package_version"
    if missing_exc is not None:
        if key not in data:
            _raise(missing_exc, abs_path, key)
    raw_val = data.get(key, 0)
    try:
        version = int(raw_val)
    except (TypeError, ValueError):
        _raise(parse_exc, abs_path, f"{key} 必须是正整数，实际为 {raw_val!r}")
    if version < 1 or version > 100:
        if invalid_exc is not None:
            raise invalid_exc(f"无效的包 {abs_path}: 不支持的版本号 {version}")
        _raise(parse_exc, abs_path, f"不支持的版本号: {version}")
    return version


def validate_required_fields(
    data: dict[str, Any],
    abs_path: str,
    fields: list[str],
    missing_exc,
    section: str | int | None = None,
) -> None:
    for fld in fields:
        if fld not in data:
            _raise(missing_exc, abs_path, fld, section)


def validate_list_field(
    data: dict[str, Any],
    abs_path: str,
    field_name: str,
    parse_exc,
    empty_exc=None,
    required: bool = True,
) -> list:
    if field_name not in data:
        if required:
            _raise(parse_exc, abs_path, f"缺少必填字段 '{field_name}'")
        return []
    val = data[field_name]
    if not isinstance(val, list):
        _raise(parse_exc, abs_path, f"'{field_name}' 字段应为数组")
    if empty_exc is not None and not val:
        _raise(empty_exc, abs_path)
    return val


def validate_list_items(
    items: list,
    abs_path: str,
    required_fields: set[str],
    parse_exc,
    missing_exc,
    item_label: str = "记录",
) -> None:
    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            _raise(parse_exc, abs_path, f"第{idx + 1}条{item_label}不是 JSON 对象")
        for fld in required_fields:
            if fld not in item:
                _raise(missing_exc, abs_path, fld, f"{item_label}[{idx}]")


def write_package_to_file(
    pkg: dict[str, Any],
    output_path: str | Path,
    io_exc,
) -> Path:
    out = Path(output_path)
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        if out.suffix.lower() == ".zip":
            json_name = out.stem + ".json"
            with zipfile.ZipFile(
                str(out), "w",
                compression=zipfile.ZIP_DEFLATED,
                compresslevel=9,
            ) as zf:
                zf.writestr(
                    json_name,
                    json.dumps(pkg, ensure_ascii=False, indent=2),
                )
        else:
            if not out.suffix:
                out = out.with_suffix(".json")
            out.write_text(
                json.dumps(pkg, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
    except (OSError, PermissionError, FileNotFoundError, zipfile.BadZipFile) as e:
        raise io_exc(f"写入包文件失败: {e}") from e
    return out


def log_audit(
    conn,
    table: str,
    name_field: str,
    name_value: str,
    action: str,
    detail: str | None = None,
    source_file: str | None = None,
    extra_fields: dict[str, Any] | None = None,
) -> None:
    from datetime import datetime
    now = datetime.now().isoformat(timespec="seconds")
    cols = [name_field, "action", "detail", "source_file", "created_at"]
    vals = [name_value, action, detail, source_file, now]
    if extra_fields:
        for k, v in extra_fields.items():
            cols.append(k)
            vals.append(v)
    placeholders = ", ".join("?" for _ in vals)
    col_str = ", ".join(cols)
    conn.execute(
        f"INSERT INTO {table} ({col_str}) VALUES ({placeholders})",
        vals,
    )


def save_undo(
    conn,
    table: str,
    name_field: str,
    name_value: str,
    action: str,
    old_data: dict[str, Any],
) -> None:
    from datetime import datetime
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        f"INSERT INTO {table} ({name_field}, action, old_data, created_at) VALUES (?, ?, ?, ?)",
        (name_value, action, json.dumps(old_data, ensure_ascii=False), now),
    )
