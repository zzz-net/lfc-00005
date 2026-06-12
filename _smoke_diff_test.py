#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""批次对比功能冒烟测试"""
import sys
import io
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
os.environ["PYTHONIOENCODING"] = "utf-8"

from contract_archiver.storage import Storage
from contract_archiver.cli import main
from contract_archiver.exceptions import (
    BatchNotFoundError,
    NoPreviousBatchError,
    BatchPathMismatchWarning,
)


def run_cli(*argv):
    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()
    old_stdout = sys.stdout
    old_stderr = sys.stderr
    try:
        sys.stdout = stdout_buf
        sys.stderr = stderr_buf
        try:
            rc = main(list(argv))
        except SystemExit as e:
            rc = e.code if e.code is not None else 0
    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr
    return rc, stdout_buf.getvalue(), stderr_buf.getvalue()


def main_test():
    db_path = "_smoke_diff_test.db"
    if Path(db_path).exists():
        Path(db_path).unlink()

    print("=" * 60)
    print("批次对比功能冒烟测试")
    print("=" * 60)

    # 1. 扫描两次
    print("\n[测试1] 扫描两次，创建两个批次")
    rc, out, err = run_cli("--db", db_path, "scan", "-c", "examples/rules.yaml", "-d", "examples/sample_data")
    assert rc == 0, f"第一次扫描失败: {err}"
    print("  第一次扫描成功")

    storage = Storage(db_path)
    batches1 = storage.list_batches()
    batch1_id = batches1[0].batch_id
    print(f"  批次1: {batch1_id}")

    # 标记一个问题，验证状态继承
    issues = storage.get_issues(batch1_id)
    dup_issue = [i for i in issues if i.issue_type == "duplicate_version"][0]
    storage.update_issue(
        batch_id=batch1_id,
        issue_id=dup_issue.id,
        state="passed",
        handler="张律师",
        note="已确认保留低版本",
    )
    print(f"  标记问题 #{dup_issue.id} 为 passed")

    rc, out, err = run_cli("--db", db_path, "scan", "-c", "examples/rules.yaml", "-d", "examples/sample_data", "--force")
    assert rc == 0, f"第二次扫描失败: {err}"
    print("  第二次扫描成功")

    batches2 = storage.list_batches()
    batch2_id = batches2[0].batch_id
    print(f"  批次2: {batch2_id}")

    # 2. 测试 --directory --latest
    print("\n[测试2] diff --directory --latest")
    rc, out, err = run_cli("--db", db_path, "diff", "-d", "examples/sample_data", "--latest")
    assert rc == 0, f"diff 失败: {err}"
    assert "新增" in out or "新增" in err or "继承" in out or "继承" in err
    print("  [PASS] 最新两批对比成功")

    # 3. 测试 -b 自动找上一批
    print("\n[测试3] diff -b 自动找上一批")
    rc, out, err = run_cli("--db", db_path, "diff", "-b", batch2_id)
    assert rc == 0, f"diff -b 失败: {err}"
    assert "继承" in out or "继承" in err
    print("  [PASS] 指定新批次自动找上一批成功")

    # 4. 验证继承数量
    print("\n[测试4] 验证继承状态和指纹继承")
    diff = storage.compare_batches(batch1_id, batch2_id)
    print(f"  新增: {diff.added_count}")
    print(f"  消失: {diff.removed_count}")
    print(f"  继承: {diff.inherited_count}")
    assert diff.added_count == 0, f"不应有新增问题，实际 {diff.added_count}"
    assert diff.removed_count == 0, f"不应有消失问题，实际 {diff.removed_count}"
    assert diff.inherited_count >= 1, f"应至少有1个继承问题，实际 {diff.inherited_count}"

    # 验证继承的问题状态
    inherited_passed = [d for d in diff.inherited if d.issue.state == "passed"]
    assert len(inherited_passed) >= 1, "应至少有1个继承的 passed 状态问题"
    inv = inherited_passed[0]
    assert inv.issue.handler == "张律师", f"处理人未继承: {inv.issue.handler}"
    assert "已确认保留低版本" in (inv.issue.note or ""), f"备注未继承: {inv.issue.note}"
    assert inv.issue.inherited_from_batch_id == batch1_id, f"继承来源批次不对: {inv.issue.inherited_from_batch_id}"
    print("  [PASS] 状态、处理人、备注、继承来源批次全部正确继承")

    # 5. 测试 --batch1 --batch2
    print("\n[测试5] diff --batch1 --batch2 指定两个批次")
    rc, out, err = run_cli("--db", db_path, "diff", "--batch1", batch1_id, "--batch2", batch2_id)
    assert rc == 0, f"diff --batch1 --batch2 失败: {err}"
    print("  [PASS] 指定两个批次对比成功")

    # 6. 测试批次不存在
    print("\n[测试6] 批次不存在错误")
    rc, out, err = run_cli("--db", db_path, "diff", "-b", "nonexistent_batch_12345")
    assert rc != 0, f"不存在的批次应报错，rc={rc}"
    assert "批次不存在" in err, f"错误信息应包含'批次不存在': {err}"
    print(f"  [PASS] 批次不存在正确报错: {err.strip()[:50]}")

    # 7. 测试无历史批次
    print("\n[测试7] 第一批无上一批错误")
    rc, out, err = run_cli("--db", db_path, "diff", "-b", batch1_id)
    assert rc != 0, f"第一批应无历史，rc={rc}"
    assert "无上一批次" in err or "没有更早" in err, f"错误信息不对: {err}"
    print(f"  [PASS] 无上一批次正确报错: {err.strip()[:60]}")

    # 8. 测试 CSV 导出内容
    print("\n[测试8] CSV 导出内容验证")
    csv_path = "_smoke_diff.csv"
    rc, out, err = run_cli("--db", db_path, "diff", "-b", batch2_id, "-o", csv_path, "-f", "csv")
    assert rc == 0, f"CSV 导出失败: {err}"
    assert Path(csv_path).exists(), "CSV 文件不存在"

    import csv
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        rows = list(reader)

    assert len(rows) >= 2, "CSV 至少有表头+1行数据"
    header = rows[0]
    assert "差异类型" in header, f"表头应含'差异类型': {header}"
    assert "旧批次ID" in header, f"表头应含'旧批次ID': {header}"
    assert "新批次ID" in header, f"表头应含'新批次ID': {header}"
    assert "指纹" in header, f"表头应含'指纹': {header}"
    assert "继承来源批次" in header, f"表头应含'继承来源批次': {header}"
    print("  [PASS] CSV 表头包含所有必要字段")

    # 找一个继承的行
    inherited_rows = [r for r in rows[1:] if r[0] == "继承"]
    assert len(inherited_rows) >= 1, "应至少有1行继承记录"
    inv_row = inherited_rows[0]
    assert inv_row[-1] == batch1_id, f"继承来源批次不对: {inv_row[-1]}"
    print(f"  [PASS] CSV 中继承来源批次正确: {inv_row[-1]}")

    # 9. 测试 HTML 导出
    print("\n[测试9] HTML 导出内容验证")
    html_path = "_smoke_diff.html"
    rc, out, err = run_cli("--db", db_path, "diff", "-b", batch2_id, "-o", html_path, "-f", "html")
    assert rc == 0, f"HTML 导出失败: {err}"
    assert Path(html_path).exists(), "HTML 文件不存在"

    html_content = Path(html_path).read_text(encoding="utf-8")
    assert "批次对比报告" in html_content, "HTML 应含'批次对比报告'"
    assert "新增问题" in html_content, "HTML 应含'新增问题'"
    assert "消失问题" in html_content, "HTML 应含'消失问题'"
    assert "继承问题" in html_content, "HTML 应含'继承问题'"
    assert batch1_id in html_content, "HTML 应含旧批次ID"
    assert batch2_id in html_content, "HTML 应含新批次ID"
    print("  [PASS] HTML 包含所有必要元素")

    # 10. 跨重启验证（重新创建 Storage）
    print("\n[测试10] 跨重启验证继承信息")
    del storage
    storage2 = Storage(db_path)
    diff2 = storage2.compare_batches(batch1_id, batch2_id)
    inherited2 = [d for d in diff2.inherited if d.issue.state == "passed"]
    assert len(inherited2) >= 1, "重启后继承的 passed 问题不见了"
    assert inherited2[0].issue.handler == "张律师", "重启后处理人丢失"
    assert inherited2[0].issue.inherited_from_batch_id == batch1_id, "重启后继承来源丢失"
    print("  [PASS] 重启后继承信息完整保留")

    # 11. 同路径多批次 - 扫描第三批
    print("\n[测试11] 同路径多批次 - 扫描第三批并验证找上一批")
    rc, out, err = run_cli("--db", db_path, "scan", "-c", "examples/rules.yaml", "-d", "examples/sample_data", "--force")
    assert rc == 0, f"第三次扫描失败: {err}"
    batches3 = storage2.list_batches()
    batch3_id = batches3[0].batch_id
    print(f"  批次3: {batch3_id}")

    # 验证第三批的继承来源应该是批次2
    issues3 = storage2.get_issues(batch3_id)
    inv3 = [i for i in issues3 if i.inherited_from_batch_id]
    assert len(inv3) >= 1, "第三批应有继承的问题"
    assert inv3[0].inherited_from_batch_id == batch2_id, f"第三批继承来源应是批次2: {inv3[0].inherited_from_batch_id}"
    print("  [PASS] 第三批正确从批次2继承")

    # 验证 get_previous_batch
    prev = storage2.get_previous_batch(batch3_id)
    assert prev is not None, "批次3应能找上一批"
    assert prev.batch_id == batch2_id, f"找上一批结果不对: {prev.batch_id}"
    print(f"  [PASS] get_previous_batch 正确返回上一批: {prev.batch_id}")

    # 清理
    import gc
    del storage
    del storage2
    gc.collect()
    for f in [csv_path, html_path, db_path]:
        p = Path(f)
        if p.exists():
            try:
                p.unlink()
            except Exception:
                pass

    print("\n" + "=" * 60)
    print("[PASS] 所有冒烟测试通过！")
    print("=" * 60)


if __name__ == "__main__":
    try:
        main_test()
    except Exception as e:
        print(f"\n[FAIL] 测试失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
