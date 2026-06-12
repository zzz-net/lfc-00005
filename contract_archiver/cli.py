from __future__ import annotations

import argparse
import io
import os
import sys
from pathlib import Path

from .exceptions import (
    ContractArchiverError,
    DatabasePermissionError,
    MigrationPackageEmptyError,
    MigrationPackageMissingFieldError,
    MigrationPackageParseError,
    InvalidMigrationPackageError,
    SchemeImportConflictError,
    BatchNotFoundError,
    NoPreviousBatchError,
    BatchPathMismatchWarning,
)
from .exporter import export_csv, export_html, export_diff_csv, export_diff_html
from .rules import load_rules
from .scanner import scan_directory
from .storage import STATE_LABELS, Storage, FilterScheme, DIFF_TYPE_LABELS


def _ensure_utf8_stdio() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None:
            continue
        try:
            if getattr(stream, "encoding", "").lower().replace("-", "") == "utf8":
                continue
            buf = getattr(stream, "buffer", None)
            if buf is None:
                continue
            new_stream = io.TextIOWrapper(
                buf,
                encoding="utf-8",
                errors="replace",
                line_buffering=bool(getattr(stream, "line_buffering", False)),
                write_through=True,
            )
            setattr(sys, stream_name, new_stream)
        except Exception:
            pass



def _print_batches(storage: Storage, scan_path: str | None = None) -> None:
    batches = storage.list_batches(scan_path)
    if not batches:
        print("  (无扫描记录)")
        return
    print(f"{'批次ID':<26} {'扫描时间':<20} {'待补':>4} {'通过':>4} {'忽略':>4}  扫描路径")
    for b in batches:
        print(
            f"{b.batch_id:<26} {b.scanned_at:<20} "
            f"{b.pending_count:>4} {b.passed_count:>4} {b.ignored_count:>4}  {b.scan_path}"
        )


def cmd_scan(args: argparse.Namespace, storage: Storage) -> int:
    rules = load_rules(args.config)
    result = scan_directory(args.directory, rules)

    batch_id, issue_count = storage.create_batch(
        scan_path=args.directory,
        scan_result=result,
        config_path=args.config,
        force=args.force,
    )

    print(f"[OK] 扫描完成")
    print(f"  批次ID: {batch_id}")
    print(f"  扫描路径: {result.scan_path}")
    print(f"  项目数: {len(result.projects)}")
    if result.unscanned_dirs:
        print(f"  未识别目录: {', '.join(result.unscanned_dirs)}")
    if result.unscanned_files:
        print(f"  根目录游离文件: {len(result.unscanned_files)} 个")
    print(f"  发现问题: {issue_count} 个")
    return 0


def cmd_list(args: argparse.Namespace, storage: Storage) -> int:
    if args.batch or args.scheme:
        scheme: FilterScheme | None = None
        scheme_batch_id: str | None = None
        state_filter: str | None = args.state
        severity_filter: str | None = args.severity
        project_type_filter: str | None = args.project_type

        if args.scheme:
            scheme = storage.get_scheme(args.scheme)
            state_filter = scheme.state if scheme.state is not None else state_filter
            severity_filter = scheme.severity if scheme.severity is not None else severity_filter
            project_type_filter = scheme.project_type_id if scheme.project_type_id is not None else project_type_filter
            if scheme.batch_id:
                scheme_batch_id = scheme.batch_id

        use_batch = args.batch or scheme_batch_id
        if use_batch is None:
            print("[错误] 请通过 -b/--batch 指定批次，或使用包含批次条件的方案")
            return 2

        issues = storage.get_issues(
            use_batch,
            state=state_filter,
            severity=severity_filter,
            project_type_id=project_type_filter,
        )

        if not issues:
            if scheme:
                cond_str = " + ".join(f"{k}={v}" for k, v in scheme.to_display_dict().items())
                print(f"  (无匹配问题，方案「{scheme.name}」条件: {cond_str})")
            else:
                filter_hints = []
                if state_filter:
                    filter_hints.append(f"状态={STATE_LABELS.get(state_filter, state_filter)}")
                if severity_filter:
                    filter_hints.append(f"严重度={'错误' if severity_filter == 'error' else '警告'}")
                if project_type_filter:
                    filter_hints.append(f"项目类型={project_type_filter}")
                hint = f"（当前筛选: {' + '.join(filter_hints)}）" if filter_hints else ""
                if args.format == "json":
                    print("[]")
                else:
                    print(f"  (无问题记录{hint})")
            return 0

        if scheme:
            cond_str = " + ".join(f"{k}={v}" for k, v in scheme.to_display_dict().items())
            print(f"[使用筛选方案「{scheme.name}」: {cond_str}]")

        if args.format == "json":
            import json
            data = []
            for i in issues:
                data.append({
                    "id": i.id,
                    "batch_id": i.batch_id,
                    "project_path": i.project_path,
                    "project_name": i.project_name,
                    "project_type_id": i.project_type_id,
                    "issue_type": i.issue_type,
                    "issue_label": i.issue_label,
                    "severity": i.severity,
                    "severity_label": i.severity_label,
                    "rule_name": i.rule_name,
                    "file_path": i.file_path,
                    "message": i.message,
                    "state": i.state,
                    "state_label": i.state_label,
                    "handler": i.handler,
                    "note": i.note,
                    "updated_at": i.updated_at,
                    "fingerprint": i.fingerprint,
                })
            print(json.dumps(data, ensure_ascii=False, indent=2))
        else:
            print(f"{'ID':>5} {'类型':<8} {'严重度':<4} {'状态':<4} {'项目':<20} {'规则/文件':<30} 描述")
            for i in issues:
                target = i.rule_name or i.file_path or "-"
                desc = i.message[:50] + ("..." if len(i.message) > 50 else "")
                print(
                    f"{i.id:>5} {i.issue_label:<8} {i.severity_label:<4} "
                    f"{i.state_label:<4} {i.project_name[:20]:<20} {target[:30]:<30} {desc}"
                )
            print(f"\n共 {len(issues)} 条记录")
    else:
        _print_batches(storage, args.directory)
    return 0


def cmd_mark(args: argparse.Namespace, storage: Storage) -> int:
    issues = storage.get_issues(args.batch)
    if not issues:
        print(f"[错误] 批次 {args.batch} 不存在或无问题记录")
        return 2

    target_ids: set[int] = set()
    if args.all:
        target_ids = {i.id for i in issues}
        if args.state:
            target_ids = {i.id for i in issues if i.state == args.state}
    elif args.ids:
        valid_ids = {i.id for i in issues}
        for tid in args.ids:
            if tid not in valid_ids:
                print(f"[警告] 问题ID {tid} 不在批次中，已跳过")
            else:
                target_ids.add(tid)
    else:
        print("[错误] 请指定 --ids 或 --all")
        return 2

    if not target_ids:
        print("[错误] 没有可标记的问题")
        return 2

    updated = 0
    for tid in sorted(target_ids):
        try:
            rec = storage.update_issue(
                batch_id=args.batch,
                issue_id=tid,
                state=args.to,
                handler=args.handler,
                note=args.note,
            )
            updated += 1
            if not args.quiet:
                print(
                    f"  #{rec.id} [{rec.issue_label}] {rec.project_name} -> "
                    f"{STATE_LABELS[rec.state]}"
                    + (f" (处理人: {rec.handler})" if rec.handler else "")
                    + (f" [备注: {rec.note}]" if rec.note else "")
                )
        except Exception as e:
            print(f"  #{tid} 更新失败: {e}")

    print(f"[OK] 已标记 {updated} 条记录")
    return 0


def cmd_undo(args: argparse.Namespace, storage: Storage) -> int:
    undo_count = storage.get_undo_count(args.batch)
    if args.count:
        print(f"可撤销操作数: {undo_count}")
        return 0

    undo = storage.undo_last(args.batch)
    print(
        f"[OK] 已撤销 #{undo.issue_id}: "
        f"{STATE_LABELS[undo.new_state]} -> {STATE_LABELS[undo.old_state]}"
    )
    if undo.old_handler != undo.new_handler:
        print(f"    处理人: {undo.new_handler or '(空)'} -> {undo.old_handler or '(空)'}")
    if undo.old_note != undo.new_note:
        print(f"    备注: {undo.new_note or '(空)'} -> {undo.old_note or '(空)'}")
    remaining = storage.get_undo_count(args.batch)
    if remaining:
        print(f"  剩余可撤销: {remaining} 条")
    return 0


def cmd_export(args: argparse.Namespace, storage: Storage) -> int:
    batch_list = storage.list_batches()

    scheme: FilterScheme | None = None
    scheme_batch_id: str | None = None
    state_filter: str | None = args.state
    severity_filter: str | None = args.severity
    project_type_filter: str | None = args.project_type

    if args.scheme:
        scheme = storage.get_scheme(args.scheme)
        state_filter = scheme.state if scheme.state is not None else state_filter
        severity_filter = scheme.severity if scheme.severity is not None else severity_filter
        project_type_filter = scheme.project_type_id if scheme.project_type_id is not None else project_type_filter
        if scheme.batch_id:
            scheme_batch_id = scheme.batch_id

    use_batch = args.batch or scheme_batch_id
    if use_batch is None:
        print("[错误] 请通过 -b/--batch 指定批次，或使用包含批次条件的方案")
        return 2

    batch = next((b for b in batch_list if b.batch_id == use_batch), None)
    if batch is None:
        print(f"[错误] 批次 {use_batch} 不存在")
        return 2

    issues = storage.get_issues(
        use_batch,
        state=state_filter,
        severity=severity_filter,
        project_type_id=project_type_filter,
    )
    audit_log = storage.get_audit_log(use_batch)
    out_path = Path(args.output)

    if args.format == "csv":
        if not out_path.suffix:
            out_path = out_path.with_suffix(".csv")
        path = export_csv(batch, issues, out_path, audit_log=audit_log, filter_scheme=scheme)
        if audit_log:
            audit_path = out_path.with_name(out_path.stem + "_audit" + out_path.suffix)
            print(f"      审计轨迹: {audit_path}")
    else:
        if not out_path.suffix:
            out_path = out_path.with_suffix(".html")
        path = export_html(batch, issues, out_path, audit_log=audit_log, filter_scheme=scheme)

    print(f"[OK] 报告已导出: {path}")
    if scheme:
        cond_str = " + ".join(f"{k}={v}" for k, v in scheme.to_display_dict().items())
        print(f"      使用方案「{scheme.name}」: {cond_str}")
    return 0


def _resolve_diff_batches(args: argparse.Namespace, storage: Storage) -> tuple[str, str]:
    batch_old = None
    batch_new = None

    if getattr(args, "batch1", None) and getattr(args, "batch2", None):
        batch_old = args.batch1
        batch_new = args.batch2
    elif getattr(args, "batch", None):
        b_new = storage.get_batch(args.batch)
        if b_new is None:
            raise BatchNotFoundError(args.batch)
        batch_new = args.batch
        prev = storage.get_previous_batch(args.batch)
        if prev is None:
            raise NoPreviousBatchError(args.batch, b_new.scan_path)
        batch_old = prev.batch_id
    elif getattr(args, "directory", None) and getattr(args, "latest", False):
        batches = storage.list_batches(args.directory)
        if not batches:
            print(f"[错误] 路径 {args.directory} 没有任何扫描批次", file=sys.stderr)
            sys.exit(2)
        if len(batches) < 2:
            first = batches[0]
            raise NoPreviousBatchError(first.batch_id, first.scan_path)
        batch_new = batches[0].batch_id
        batch_old = batches[1].batch_id
    else:
        print("[错误] 请指定对比方式：\n"
              "  1. --batch1 + --batch2：指定两个批次\n"
              "  2. --batch：指定新批次，自动找上一批\n"
              "  3. --directory --latest：指定路径，对比最新两批", file=sys.stderr)
        sys.exit(2)

    b_old_info = storage.get_batch(batch_old)
    if b_old_info is None:
        raise BatchNotFoundError(batch_old)
    b_new_info = storage.get_batch(batch_new)
    if b_new_info is None:
        raise BatchNotFoundError(batch_new)

    if b_old_info.scan_path != b_new_info.scan_path and not getattr(args, "ignore_path", False):
        raise BatchPathMismatchWarning(
            batch_old, b_old_info.scan_path,
            batch_new, b_new_info.scan_path,
        )

    return batch_old, batch_new


def _print_diff_table(diff) -> None:
    print(f"\n{'差异类型':<6} {'类型':<8} {'严重度':<4} {'状态':<4} {'项目':<20} {'规则/文件':<30} 描述")
    print("-" * 100)

    for issue in diff.added:
        target = issue.rule_name or issue.file_path or "-"
        desc = issue.message[:45] + ("..." if len(issue.message) > 45 else "")
        print(
            f"{'新增':<6} {issue.issue_label:<8} {issue.severity_label:<4} "
            f"{issue.state_label:<4} {issue.project_name[:20]:<20} {target[:30]:<30} {desc}"
        )

    for issue in diff.removed:
        target = issue.rule_name or issue.file_path or "-"
        desc = issue.message[:45] + ("..." if len(issue.message) > 45 else "")
        print(
            f"{'消失':<6} {issue.issue_label:<8} {issue.severity_label:<4} "
            f"{issue.state_label:<4} {issue.project_name[:20]:<20} {target[:30]:<30} {desc}"
        )

    for di in diff.inherited:
        issue = di.issue
        target = issue.rule_name or issue.file_path or "-"
        desc = issue.message[:45] + ("..." if len(issue.message) > 45 else "")
        print(
            f"{'继承':<6} {issue.issue_label:<8} {issue.severity_label:<4} "
            f"{issue.state_label:<4} {issue.project_name[:20]:<20} {target[:30]:<30} {desc}"
        )

    print("-" * 100)
    print(f"  新增: {diff.added_count}  |  消失: {diff.removed_count}  |  继承: {diff.inherited_count}")
    print(f"  旧批次总计: {diff.total_old}  |  新批次总计: {diff.total_new}")


def cmd_diff(args: argparse.Namespace, storage: Storage) -> int:
    batch_old_id, batch_new_id = _resolve_diff_batches(args, storage)

    b_old = storage.get_batch(batch_old_id)
    b_new = storage.get_batch(batch_new_id)

    print(f"[批次对比]")
    print(f"  旧批次: {batch_old_id}  ({b_old.scanned_at})")
    print(f"  新批次: {batch_new_id}  ({b_new.scanned_at})")
    if b_old.scan_path == b_new.scan_path:
        print(f"  扫描路径: {b_new.scan_path}")
    else:
        print(f"  旧路径: {b_old.scan_path}")
        print(f"  新路径: {b_new.scan_path}")

    config_changed = False
    if b_old.config_path != b_new.config_path:
        config_changed = True
        print(f"  [提示] 配置文件不同：")
        print(f"    旧配置: {b_old.config_path}")
        print(f"    新配置: {b_new.config_path}")

    diff = storage.compare_batches(batch_old_id, batch_new_id)

    if diff.added_count > 0 and diff.removed_count > 0:
        print(f"  [提示] 同时存在新增({diff.added_count})和消失({diff.removed_count})的问题")
        print(f"    可能原因：规则配置变动（规则名/检测逻辑变更）、或数据真实变化")
        if config_changed:
            print(f"    建议：如仅为规则名调整，可忽略新增/消失，关注继承的问题状态")

    out_path = getattr(args, "output", None)
    fmt = getattr(args, "format", "table")

    if out_path:
        out = Path(out_path)
        if fmt == "csv":
            if not out.suffix:
                out = out.with_suffix(".csv")
            path = export_diff_csv(diff, out)
        else:
            if not out.suffix:
                out = out.with_suffix(".html")
            path = export_diff_html(diff, out)
        print(f"\n[OK] 对比报告已导出: {path}")

    if not out_path or fmt == "table" or True:
        _print_diff_table(diff)

    return 0


def _format_scheme_conditions(scheme: FilterScheme) -> str:
    d = scheme.to_display_dict()
    if not d:
        return "(空方案)"
    return "  ".join(f"{k}={v}" for k, v in d.items())


def cmd_scheme_list(args: argparse.Namespace, storage: Storage) -> int:
    schemes = storage.list_schemes()
    if not schemes:
        print("  (无筛选方案)")
        return 0
    print(f"{'方案名':<20} {'更新时间':<20}  筛选条件")
    for s in schemes:
        print(f"{s.name:<20} {s.updated_at:<20}  {_format_scheme_conditions(s)}")
    return 0


def cmd_scheme_show(args: argparse.Namespace, storage: Storage) -> int:
    scheme = storage.get_scheme(args.name)
    print(f"方案名: {scheme.name}")
    print(f"创建时间: {scheme.created_at}")
    print(f"更新时间: {scheme.updated_at}")
    print("筛选条件:")
    d = scheme.to_display_dict()
    if not d:
        print("  (无)")
    for k, v in d.items():
        print(f"  {k}: {v}")
    return 0


def cmd_scheme_save(args: argparse.Namespace, storage: Storage) -> int:
    scheme, action = storage.save_scheme(
        name=args.name,
        batch_id=args.batch,
        state=args.state,
        severity=args.severity,
        project_type_id=args.project_type,
        overwrite=args.overwrite,
    )
    action_label = "覆盖更新" if action == "overwrite" else "创建"
    print(f"[OK] 已{action_label}方案「{scheme.name}」")
    print(f"  版本: v{scheme.version}")
    print(f"  筛选条件: {_format_scheme_conditions(scheme)}")
    return 0


def cmd_scheme_delete(args: argparse.Namespace, storage: Storage) -> int:
    storage.delete_scheme(args.name)
    print(f"[OK] 已删除筛选方案「{args.name}」")
    return 0


def cmd_scheme_export(args: argparse.Namespace, storage: Storage) -> int:
    names = None
    if getattr(args, "name", None):
        names = list(args.name)
    out_path = Path(args.output)
    if not out_path.suffix:
        out_path = out_path.with_suffix(".json")
    path = storage.export_schemes_to_file(out_path, names)
    pkg = storage.export_schemes(names)
    print(f"[OK] 已导出 {pkg['scheme_count']} 个方案到: {path}")
    for s in pkg["schemes"]:
        print(f"  - {s['name']} (v{s['version']})")
    print(f"  导出时间: {pkg['exported_at']}")
    print(f"  迁移包格式版本: v{pkg['package_version']}")
    return 0


def _format_import_result(result: dict) -> str:
    lines = [f"[OK] 迁移包 {result['source_file']} 处理完成"]
    lines.append(f"  总计: {result['total']} 个方案")
    if result["created"]:
        lines.append(f"  新增: {len(result['created'])} 个 - {', '.join(result['created'])}")
    if result["overwritten"]:
        lines.append(f"  覆盖: {len(result['overwritten'])} 个 - {', '.join(result['overwritten'])}")
    if result["skipped"]:
        lines.append(f"  跳过: {len(result['skipped'])} 个 - {', '.join(result['skipped'])}（同名冲突，未使用 --overwrite）")
    if result["errors"]:
        lines.append(f"  失败: {len(result['errors'])} 个")
        for er in result["errors"]:
            lines.append(f"    - {er['name']}: {er['reason']}")
    return "\n".join(lines)


def cmd_scheme_import(args: argparse.Namespace, storage: Storage) -> int:
    from contract_archiver.exceptions import (
        MigrationPackageError,
    )
    try:
        result = storage.import_schemes(
            file_path=args.input,
            overwrite=args.overwrite,
            preserve_timestamps=not args.no_preserve_timestamps,
        )
    except MigrationPackageError:
        raise
    except ContractArchiverError:
        raise
    except Exception as e:
        print(f"[导入失败] {e}", file=sys.stderr)
        return 3

    print(_format_import_result(result))

    if result["errors"] and not result["created"] and not result["overwritten"]:
        return 3
    if result["skipped"] and not args.overwrite and not result["created"] and not result["overwritten"]:
        print("\n[提示] 上述方案因同名已存在被跳过，如需覆盖请加 --overwrite", file=sys.stderr)
    return 0


def cmd_scheme_undo(args: argparse.Namespace, storage: Storage) -> int:
    from contract_archiver.exceptions import EmptyUndoError
    if getattr(args, "count", False):
        cnt = storage.get_scheme_undo_count()
        print(f"可撤销方案操作数: {cnt}")
        return 0
    try:
        undo = storage.undo_last_scheme_change(getattr(args, "name", None))
    except EmptyUndoError:
        print("[撤销失败] 没有可撤销的方案操作", file=sys.stderr)
        return 1
    print(f"[OK] 已撤销方案「{undo.scheme_name}」")
    old_parts = []
    if undo.old_batch_id:
        old_parts.append(f"batch={undo.old_batch_id}")
    if undo.old_state:
        old_parts.append(f"state={undo.old_state}")
    if undo.old_severity:
        old_parts.append(f"severity={undo.old_severity}")
    if undo.old_project_type_id:
        old_parts.append(f"project_type={undo.old_project_type_id}")
    print(f"  已还原为: {' + '.join(old_parts) if old_parts else '(无筛选条件)'}")
    print(f"  原创建时间: {undo.old_created_at}")
    print(f"  原更新时间: {undo.old_updated_at}")
    remaining = storage.get_scheme_undo_count()
    if remaining:
        print(f"  剩余可撤销: {remaining} 条")
    return 0


def cmd_scheme_audit(args: argparse.Namespace, storage: Storage) -> int:
    logs = storage.get_scheme_audit_log(getattr(args, "name", None))
    if not logs:
        print("  (无方案操作审计记录)")
        return 0
    print(f"{'时间':<20} {'操作':<12} {'方案名':<20} {'结果':<10}  来源/详情")
    for lg in logs:
        src = lg.get("source_file") or ""
        detail = lg.get("detail") or ""
        extra = src or detail
        print(
            f"{lg['created_at']:<20} {lg['action']:<12} "
            f"{lg['scheme_name']:<20} {lg['result']:<10}  {extra}"
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="contract-archiver",
        description="合同附件归档校验工具 - 法务资料合规检查",
    )
    parser.add_argument(
        "--db", default="contract_archive.db",
        help="SQLite 数据库文件路径 (默认: contract_archive.db)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_scan = sub.add_parser("scan", help="扫描目录并生成批次")
    p_scan.add_argument("-c", "--config", required=True, help="规则配置文件 (.yaml/.yml/.json)")
    p_scan.add_argument("-d", "--directory", required=True, help="要扫描的资料根目录")
    p_scan.add_argument("--force", action="store_true", help="强制新建批次（保留历史）")
    p_scan.set_defaults(func=cmd_scan)

    p_list = sub.add_parser("list", help="查看批次或问题列表")
    p_list.add_argument("-b", "--batch", help="批次ID，查看该批次问题")
    p_list.add_argument("-d", "--directory", help="按扫描路径过滤批次")
    p_list.add_argument("--state", choices=["pending", "passed", "ignored"], help="按状态过滤问题")
    p_list.add_argument("--severity", choices=["error", "warning"], help="按严重度过滤问题")
    p_list.add_argument("--project-type", dest="project_type", help="按项目类型ID过滤问题")
    p_list.add_argument("--scheme", help="使用已保存的筛选方案过滤问题")
    p_list.add_argument("--format", choices=["table", "json"], default="table", help="输出格式 (默认: table)")
    p_list.set_defaults(func=cmd_list)

    p_mark = sub.add_parser("mark", help="标记问题状态")
    p_mark.add_argument("-b", "--batch", required=True, help="批次ID")
    p_mark.add_argument("--to", required=True, choices=list(STATE_LABELS.keys()), help="目标状态")
    p_mark.add_argument("--ids", nargs="*", type=int, help="问题ID列表")
    p_mark.add_argument("--all", action="store_true", help="标记批次内所有问题")
    p_mark.add_argument("--state", choices=list(STATE_LABELS.keys()), help="仅标记指定原状态的问题（配合--all）")
    p_mark.add_argument("--handler", help="处理人姓名")
    p_mark.add_argument("--note", help="备注")
    p_mark.add_argument("-q", "--quiet", action="store_true", help="静默模式")
    p_mark.set_defaults(func=cmd_mark)

    p_undo = sub.add_parser("undo", help="撤销上一条标记操作")
    p_undo.add_argument("-b", "--batch", required=True, help="批次ID")
    p_undo.add_argument("--count", action="store_true", help="仅显示可撤销数量")
    p_undo.set_defaults(func=cmd_undo)

    p_exp = sub.add_parser("export", help="导出报告")
    p_exp.add_argument("-b", "--batch", help="批次ID（如方案已指定批次可省略）")
    p_exp.add_argument("-o", "--output", required=True, help="输出文件路径")
    p_exp.add_argument(
        "-f", "--format", choices=["csv", "html"], default="html",
        help="导出格式 (默认: html)",
    )
    p_exp.add_argument("--state", choices=["pending", "passed", "ignored"], help="按状态过滤问题")
    p_exp.add_argument("--severity", choices=["error", "warning"], help="按严重度过滤问题")
    p_exp.add_argument("--project-type", dest="project_type", help="按项目类型ID过滤问题")
    p_exp.add_argument("--scheme", help="使用已保存的筛选方案过滤问题")
    p_exp.set_defaults(func=cmd_export)

    p_diff = sub.add_parser("diff", help="对比两个批次的问题差异")
    p_diff.add_argument("-b", "--batch", help="指定新批次ID，自动找上一批对比")
    p_diff.add_argument("--batch1", help="指定对比的旧批次ID（配合 --batch2 使用）")
    p_diff.add_argument("--batch2", help="指定对比的新批次ID（配合 --batch1 使用）")
    p_diff.add_argument("-d", "--directory", help="按扫描路径，对比最新两批（需配合 --latest）")
    p_diff.add_argument("--latest", action="store_true", help="对比指定路径的最新两个批次")
    p_diff.add_argument("-o", "--output", help="导出对比报告的文件路径")
    p_diff.add_argument(
        "-f", "--format", choices=["csv", "html", "table"], default="table",
        help="输出/导出格式 (默认: table)",
    )
    p_diff.add_argument("--ignore-path", action="store_true", dest="ignore_path",
                        help="忽略扫描路径不同的警告，强制对比")
    p_diff.set_defaults(func=cmd_diff)

    p_scheme = sub.add_parser("scheme", help="筛选方案管理")
    scheme_sub = p_scheme.add_subparsers(dest="scheme_action", required=True)

    ps_list = scheme_sub.add_parser("list", help="列出所有筛选方案")
    ps_list.set_defaults(func=cmd_scheme_list)

    ps_show = scheme_sub.add_parser("show", help="查看筛选方案详情")
    ps_show.add_argument("name", help="方案名称")
    ps_show.set_defaults(func=cmd_scheme_show)

    ps_save = scheme_sub.add_parser("save", help="保存筛选方案")
    ps_save.add_argument("name", help="方案名称")
    ps_save.add_argument("-b", "--batch", help="批次ID条件")
    ps_save.add_argument("--state", choices=["pending", "passed", "ignored"], help="状态条件")
    ps_save.add_argument("--severity", choices=["error", "warning"], help="严重度条件")
    ps_save.add_argument("--project-type", dest="project_type", help="项目类型ID条件")
    ps_save.add_argument("--overwrite", action="store_true", help="若同名方案已存在则覆盖")
    ps_save.set_defaults(func=cmd_scheme_save)

    ps_del = scheme_sub.add_parser("delete", help="删除筛选方案")
    ps_del.add_argument("name", help="方案名称")
    ps_del.set_defaults(func=cmd_scheme_delete)

    ps_exp = scheme_sub.add_parser("export", help="导出筛选方案为迁移包 JSON")
    ps_exp.add_argument("name", nargs="*", help="要导出的方案名（省略则导出全部）")
    ps_exp.add_argument("-o", "--output", required=True, help="输出 JSON 文件路径")
    ps_exp.set_defaults(func=cmd_scheme_export)

    ps_imp = scheme_sub.add_parser("import", help="从迁移包 JSON 导入筛选方案")
    ps_imp.add_argument("input", help="迁移包 JSON 文件路径")
    ps_imp.add_argument("--overwrite", action="store_true", help="同名方案已存在时覆盖（默认跳过）")
    ps_imp.add_argument(
        "--no-preserve-timestamps", action="store_true",
        help="不保留原 created_at/updated_at（使用当前时间），默认保留",
    )
    ps_imp.set_defaults(func=cmd_scheme_import)

    ps_undo = scheme_sub.add_parser("undo", help="撤销最近一次方案覆盖/删除操作")
    ps_undo.add_argument("name", nargs="?", default=None, help="撤销指定方案名的最近变更（省略则撤销最近）")
    ps_undo.add_argument("--count", action="store_true", help="仅显示可撤销操作数量")
    ps_undo.set_defaults(func=cmd_scheme_undo)

    ps_audit = scheme_sub.add_parser("audit", help="查看方案操作审计日志")
    ps_audit.add_argument("name", nargs="?", default=None, help="仅查看指定方案名的日志（省略则查看全部）")
    ps_audit.set_defaults(func=cmd_scheme_audit)

    return parser


def main(argv: list[str] | None = None) -> int:
    _ensure_utf8_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        storage = Storage(args.db)
        return args.func(args, storage)
    except MigrationPackageEmptyError as e:
        print(str(e), file=sys.stderr)
        return 4
    except MigrationPackageMissingFieldError as e:
        print(str(e), file=sys.stderr)
        return 5
    except MigrationPackageParseError as e:
        print(str(e), file=sys.stderr)
        return 6
    except InvalidMigrationPackageError as e:
        print(str(e), file=sys.stderr)
        return 7
    except SchemeImportConflictError as e:
        print(str(e), file=sys.stderr)
        return 8
    except DatabasePermissionError as e:
        print(str(e), file=sys.stderr)
        return 9
    except ContractArchiverError as e:
        print(str(e), file=sys.stderr)
        return 1
    except FileNotFoundError as e:
        print(f"[文件不存在] {e}", file=sys.stderr)
        return 10
    except IsADirectoryError as e:
        print(f"[路径错误] 期望文件路径，实际是目录: {e}", file=sys.stderr)
        return 10
    except PermissionError as e:
        print(f"[权限错误] 无法访问文件: {e}", file=sys.stderr)
        return 11
    except UnicodeDecodeError as e:
        print(f"[编码错误] 文件不是 UTF-8 编码: {e}", file=sys.stderr)
        return 12
    except KeyboardInterrupt:
        print("\n用户中断", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
