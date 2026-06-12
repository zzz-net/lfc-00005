from __future__ import annotations

import argparse
import io
import os
import sys
from pathlib import Path

from .exceptions import ContractArchiverError
from .exporter import export_csv, export_html
from .rules import load_rules
from .scanner import scan_directory
from .storage import STATE_LABELS, Storage


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
    if args.batch:
        issues = storage.get_issues(args.batch, state=args.state, severity=args.severity)
        if not issues:
            if args.format == "json":
                print("[]")
            else:
                print("  (无问题记录)")
            return 0

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
    batch = next((b for b in batch_list if b.batch_id == args.batch), None)
    if batch is None:
        print(f"[错误] 批次 {args.batch} 不存在")
        return 2

    issues = storage.get_issues(args.batch)
    audit_log = storage.get_audit_log(args.batch)
    out_path = Path(args.output)

    if args.format == "csv":
        if not out_path.suffix:
            out_path = out_path.with_suffix(".csv")
        path = export_csv(batch, issues, out_path, audit_log=audit_log)
        if audit_log:
            audit_path = out_path.with_name(out_path.stem + "_audit" + out_path.suffix)
            print(f"      审计轨迹: {audit_path}")
    else:
        if not out_path.suffix:
            out_path = out_path.with_suffix(".html")
        path = export_html(batch, issues, out_path, audit_log=audit_log)

    print(f"[OK] 报告已导出: {path}")
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
    p_exp.add_argument("-b", "--batch", required=True, help="批次ID")
    p_exp.add_argument("-o", "--output", required=True, help="输出文件路径")
    p_exp.add_argument(
        "-f", "--format", choices=["csv", "html"], default="html",
        help="导出格式 (默认: html)",
    )
    p_exp.set_defaults(func=cmd_export)

    return parser


def main(argv: list[str] | None = None) -> int:
    _ensure_utf8_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        storage = Storage(args.db)
        return args.func(args, storage)
    except ContractArchiverError as e:
        print(str(e), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n用户中断", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
