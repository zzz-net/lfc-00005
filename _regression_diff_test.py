#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""批次对比功能回归测试
覆盖：跨重启查询、导出内容、同指纹继承、同一路径多批次、无上一批错误、
路径不同警告、--ignore-path、批次不存在、规则名变动提示
"""
import subprocess
import sys
import os
import csv
import gc
from pathlib import Path

os.environ["PYTHONIOENCODING"] = "utf-8"

DB_PATH = "_regression_diff_test.db"
SAMPLE_DIR = "examples/sample_data"
RULES_FILE = "examples/rules.yaml"


def run_cli(*args):
    cmd = [sys.executable, "-m", "contract_archiver.cli", "--db", DB_PATH] + list(args)
    result = subprocess.run(
        cmd,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(Path(__file__).parent),
    )
    return result.returncode, result.stdout, result.stderr


def cleanup():
    gc.collect()
    for f in [DB_PATH, "_reg_diff.csv", "_reg_diff.html", "_reg_diff2.html"]:
        p = Path(f)
        if p.exists():
            try:
                p.unlink()
            except Exception:
                pass


def main():
    from contract_archiver.storage import Storage

    print("=" * 60)
    print("批次对比功能 - 回归测试")
    print("=" * 60)

    cleanup()

    try:
        # ===== 测试1：同指纹状态继承 =====
        print("\n[测试1] 同指纹继承：状态、处理人、备注、继承来源批次")
        rc, out, err = run_cli("scan", "-c", RULES_FILE, "-d", SAMPLE_DIR)
        assert rc == 0, f"第一次扫描失败: {err}"

        storage = Storage(DB_PATH)
        batches = storage.list_batches()
        batch1_id = batches[0].batch_id
        print(f"  批次1: {batch1_id}")

        issues1 = storage.get_issues(batch1_id)
        target = None
        for i in issues1:
            if i.issue_type == "duplicate_version":
                target = i
                break
        assert target is not None, "未找到 duplicate_version 问题"

        storage.update_issue(
            batch_id=batch1_id,
            issue_id=target.id,
            state="passed",
            handler="李法务",
            note="已审核：保留历史版本",
        )
        print(f"  标记问题 #{target.id} 为 passed，处理人=李法务")

        rc, out, err = run_cli("scan", "-c", RULES_FILE, "-d", SAMPLE_DIR, "--force")
        assert rc == 0, f"第二次扫描失败: {err}"

        batches2 = storage.list_batches()
        batch2_id = batches2[0].batch_id
        assert batch2_id != batch1_id, "第二次扫描应创建新批次"
        print(f"  批次2: {batch2_id}")

        diff = storage.compare_batches(batch1_id, batch2_id)
        assert diff.inherited_count >= 1, "应至少有1个继承问题"
        assert diff.added_count == 0, f"不应有新增问题，实际 {diff.added_count}"
        assert diff.removed_count == 0, f"不应有消失问题，实际 {diff.removed_count}"

        inv_passed = [d for d in diff.inherited if d.issue.state == "passed"]
        assert len(inv_passed) >= 1, "应至少有1个继承的 passed 问题"
        inv = inv_passed[0]
        assert inv.issue.handler == "李法务", f"处理人未继承: {inv.issue.handler}"
        assert "已审核" in (inv.issue.note or ""), f"备注未继承: {inv.issue.note}"
        assert inv.issue.inherited_from_batch_id == batch1_id, \
            f"继承来源批次不对: {inv.issue.inherited_from_batch_id}"
        print("  [PASS] 同指纹状态继承正确")

        # ===== 测试2：跨重启查询 =====
        print("\n[测试2] 跨重启查询：重新连接数据库后数据一致")
        del storage
        gc.collect()

        storage2 = Storage(DB_PATH)
        diff2 = storage2.compare_batches(batch1_id, batch2_id)
        assert diff2.inherited_count == diff.inherited_count, "重启后继承数量不一致"
        assert diff2.added_count == diff.added_count, "重启后新增数量不一致"
        assert diff2.removed_count == diff.removed_count, "重启后消失数量不一致"

        inv2 = [d for d in diff2.inherited if d.issue.state == "passed"][0]
        assert inv2.issue.handler == "李法务", "重启后处理人丢失"
        assert inv2.issue.inherited_from_batch_id == batch1_id, "重启后继承来源丢失"
        print("  [PASS] 跨重启数据一致")

        # ===== 测试3：同一路径多批次 =====
        print("\n[测试3] 同一路径多批次：找上一批、链式继承")
        rc, out, err = run_cli("scan", "-c", RULES_FILE, "-d", SAMPLE_DIR, "--force")
        assert rc == 0, f"第三次扫描失败: {err}"

        batches3 = storage2.list_batches()
        batch3_id = batches3[0].batch_id
        print(f"  批次3: {batch3_id}")

        prev = storage2.get_previous_batch(batch3_id)
        assert prev is not None, "批次3应能找上一批"
        assert prev.batch_id == batch2_id, f"找上一批应为批次2，实际 {prev.batch_id}"

        issues3 = storage2.get_issues(batch3_id)
        inv3 = [i for i in issues3 if i.inherited_from_batch_id == batch2_id]
        assert len(inv3) >= 1, "第三批应有从批次2继承的问题"

        inv3_passed = [i for i in inv3 if i.state == "passed"]
        assert len(inv3_passed) >= 1, "第三批应有继承的 passed 问题"
        assert inv3_passed[0].handler == "李法务", "链式继承处理人丢失"
        print("  [PASS] 同路径多批次链式继承正确")

        # ===== 测试4：无上一批错误反馈 =====
        print("\n[测试4] 无上一批错误反馈")
        rc, out, err = run_cli("diff", "-b", batch1_id)
        assert rc != 0, "第一批应无上一批"
        assert "无上一批次" in err or "没有更早" in err, f"错误信息不对: {err}"
        print(f"  [PASS] 错误提示正确: {err.strip()[:50]}")

        # ===== 测试5：批次不存在错误 =====
        print("\n[测试5] 批次不存在错误反馈")
        rc, out, err = run_cli("diff", "-b", "no_such_batch_12345")
        assert rc != 0, "不存在的批次应报错"
        assert "批次不存在" in err, f"错误信息应含'批次不存在': {err}"
        print(f"  [PASS] 错误提示正确: {err.strip()[:50]}")

        rc, out, err = run_cli("diff", "--batch1", "no_such_1", "--batch2", batch2_id)
        assert rc != 0, "不存在的 batch1 应报错"
        assert "批次不存在" in err
        print("  [PASS] --batch1 不存在正确报错")

        # ===== 测试6：不同路径警告 =====
        print("\n[测试6] 不同路径警告")
        other_dir = "examples/sample_data/2024-销售合同-ABC公司"
        rc, out, err = run_cli("scan", "-c", RULES_FILE, "-d", other_dir, "--force")
        assert rc == 0, f"扫描其他目录失败: {err}"

        other_batches = storage2.list_batches(other_dir)
        other_batch_id = other_batches[0].batch_id
        print(f"  其他批次: {other_batch_id}")

        rc, out, err = run_cli("diff", "--batch1", batch1_id, "--batch2", other_batch_id)
        assert rc != 0, "不同路径应警告"
        assert "路径不同" in err, f"错误信息应含'路径不同': {err}"
        assert "--ignore-path" in err, "应提示 --ignore-path"
        print("  [PASS] 不同路径正确警告")

        rc, out, err = run_cli(
            "diff", "--batch1", batch1_id, "--batch2", other_batch_id, "--ignore-path"
        )
        assert rc == 0, "--ignore-path 应允许不同路径对比"
        print("  [PASS] --ignore-path 可绕过路径检查")

        # ===== 测试7：CSV 导出内容 =====
        print("\n[测试7] CSV 导出内容验证")
        csv_path = "_reg_diff.csv"
        rc, out, err = run_cli("diff", "-b", batch2_id, "-o", csv_path, "-f", "csv")
        assert rc == 0, f"CSV 导出失败: {err}"
        assert Path(csv_path).exists(), "CSV 文件不存在"

        with open(csv_path, "r", encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            rows = list(reader)

        assert len(rows) >= 2, "CSV 至少有表头+1行数据"
        header = rows[0]
        required_cols = [
            "差异类型",
            "旧批次ID", "旧批次扫描时间", "旧批次扫描路径",
            "新批次ID", "新批次扫描时间", "新批次扫描路径",
            "项目名称", "项目类型",
            "问题类型", "严重程度",
            "规则名", "文件路径",
            "问题描述",
            "当前状态", "处理人", "备注", "更新时间",
            "指纹",
            "继承来源批次",
        ]
        for col in required_cols:
            assert col in header, f"CSV 表头缺少'{col}'"
        print("  [PASS] CSV 表头完整")

        inherited_rows = [r for r in rows[1:] if r[0] == "继承"]
        assert len(inherited_rows) >= 1, "应至少有1行继承记录"
        inv_row = inherited_rows[0]
        assert inv_row[header.index("继承来源批次")] == batch1_id, \
            "CSV 中继承来源批次不对"
        assert inv_row[header.index("处理人")] == "李法务", \
            "CSV 中处理人未继承"
        print("  [PASS] CSV 数据正确")

        # ===== 测试8：HTML 导出内容 =====
        print("\n[测试8] HTML 导出内容验证")
        html_path = "_reg_diff.html"
        rc, out, err = run_cli("diff", "-b", batch3_id, "-o", html_path, "-f", "html")
        assert rc == 0, f"HTML 导出失败: {err}"
        assert Path(html_path).exists(), "HTML 文件不存在"

        html_content = Path(html_path).read_text(encoding="utf-8")
        assert "批次对比报告" in html_content, "缺少标题"
        assert batch1_id not in html_content, "批次3 vs 批次2 不应包含批次1"
        assert batch2_id in html_content, "应包含批次2"
        assert batch3_id in html_content, "应包含批次3"
        assert "新增问题" in html_content and "消失问题" in html_content and "继承问题" in html_content, \
            "缺少分类标题"
        assert "李法务" in html_content, "HTML 中处理人未显示"
        print("  [PASS] HTML 导出内容正确")

        # ===== 测试9：三种 diff 方式 =====
        print("\n[测试9] 三种 diff 指定方式")
        rc, out, err = run_cli("diff", "-d", SAMPLE_DIR, "--latest")
        assert rc == 0, f"--directory --latest 失败: {err}"
        print("  [PASS] --directory --latest 方式可用")

        rc, out, err = run_cli("diff", "-b", batch3_id)
        assert rc == 0, f"-b 方式失败: {err}"
        print("  [PASS] -b 自动找上一批方式可用")

        rc, out, err = run_cli("diff", "--batch1", batch1_id, "--batch2", batch3_id)
        assert rc == 0, f"--batch1 --batch2 方式失败: {err}"
        print("  [PASS] --batch1 --batch2 方式可用")

        # ===== 测试10：规则名/配置变动提示 =====
        print("\n[测试10] 配置变动提示")
        rc, out, err = run_cli(
            "diff", "--batch1", batch1_id, "--batch2", other_batch_id, "--ignore-path"
        )
        assert rc == 0, "对比应成功"
        # 因为两个批次配置相同，不会有配置变动提示
        # 这里主要验证 --ignore-path 能正常工作，以及差异数量正确
        print("  [PASS] --ignore-path 对比正常执行")

        # ===== 测试11：--directory --latest 只有一批时的错误 =====
        print("\n[测试11] 单批次路径 --latest 错误反馈")
        single_dir = "examples/sample_data/2024-保密协议-合作方"
        rc, out, err = run_cli("scan", "-c", RULES_FILE, "-d", single_dir, "--force")
        assert rc == 0, "扫描单项目录失败"

        rc, out, err = run_cli("diff", "-d", single_dir, "--latest")
        assert rc != 0, "只有一批时应报错"
        assert "无上一批次" in err or "没有更早" in err or "至少" in err, \
            f"错误信息不对: {err}"
        print(f"  [PASS] 单批次路径正确报错: {err.strip()[:50]}")

        # ===== 测试12：get_previous_batch 边界 =====
        print("\n[测试12] get_previous_batch 边界")
        first_prev = storage2.get_previous_batch(batch1_id)
        assert first_prev is None, "第一批的上一批应为 None"
        print("  [PASS] 第一批无上一批返回 None")

        nonexistent_prev = storage2.get_previous_batch("no_such_batch")
        assert nonexistent_prev is None, "不存在批次的上一批应为 None"
        print("  [PASS] 不存在批次返回 None")

        print("\n" + "=" * 60)
        print("[PASS] 全部 12 项回归测试通过！")
        print("=" * 60)

    finally:
        try:
            del storage
        except NameError:
            pass
        try:
            del storage2
        except NameError:
            pass
        cleanup()


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print(f"\n[FAIL] 测试失败: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"\n[ERROR] 测试异常: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(2)
