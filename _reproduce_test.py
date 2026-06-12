#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""复现测试：验证三个bug修复"""
import subprocess
import sys
import os
import json
from pathlib import Path

os.environ["PYTHONIOENCODING"] = "utf-8"

def run_cli(args, check=True):
    """运行CLI命令并返回结果"""
    cmd = [sys.executable, "-m", "contract_archiver.cli", "--db", "contract_archive_test.db"] + args
    print(f"\n>>> 执行: {' '.join(cmd)}")
    result = subprocess.run(
        cmd,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        cwd="d:\\workSpace\\AI__SPACE\\lfc-00005",
    )
    print(f"返回码: {result.returncode}")
    if result.stdout:
        print(f"标准输出:\n{result.stdout}")
    if result.stderr:
        print(f"标准错误:\n{result.stderr}")
    if check and result.returncode != 0:
        raise RuntimeError(f"命令失败: {' '.join(cmd)}")
    return result

def main():
    test_dir = "_test_fix"
    config = "_test_fix_config.yaml"

    print("=" * 70)
    print("复现测试开始")
    print("=" * 70)

    # ========== 测试1：双版本冲突严重度 ==========
    print("\n" + "=" * 50)
    print("测试1：必需附件双版本冲突严重度应为ERROR")
    print("=" * 50)

    r = run_cli(["scan", "-c", config, "-d", test_dir])
    batch_id = None
    for line in r.stdout.split("\n"):
        if "批次ID:" in line:
            batch_id = line.split("批次ID:")[1].strip()
            break
    assert batch_id, f"未找到批次ID，输出:\n{r.stdout}"
    print(f"批次ID: {batch_id}")

    # 列出问题，检查采购合同双版本冲突的严重度
    r = run_cli(["list", "-b", batch_id, "--severity", "error", "--format", "json"])
    issues = json.loads(r.stdout)
    print(f"ERROR级问题数: {len(issues)}")

    # 应该有采购合同双版本冲突的ERROR
    # 用英文枚举值匹配，避免中文编码问题
    dup_errors = [i for i in issues
                  if i.get("issue_type") == "duplicate_version"
                  and i.get("severity") == "error"]
    assert len(dup_errors) >= 1, f"采购合同双版本冲突未标记为ERROR，ERROR问题:\n{json.dumps(issues, ensure_ascii=False, indent=2)}"
    print(f"[PASS] 测试1通过：采购合同双版本冲突为ERROR级，共 {len(dup_errors)} 个重复版本ERROR")

    # ========== 测试2：标记后重扫状态继承 ==========
    print("\n" + "=" * 50)
    print("测试2：标记后--force重扫，状态不回到待补")
    print("=" * 50)

    # 找到待补的采购合同重复版本问题ID
    r = run_cli(["list", "-b", batch_id, "--state", "pending", "--format", "json"])
    pending = json.loads(r.stdout)
    print(f"待补问题数: {len(pending)}")

    dup_issue = None
    for i in pending:
        if i.get("issue_type") == "duplicate_version":
            dup_issue = i
            break
    assert dup_issue, f"未找到重复版本问题，待补问题:\n{json.dumps(pending, ensure_ascii=False, indent=2)}"
    issue_id = str(dup_issue["id"])
    print(f"标记问题ID {issue_id} 为通过")

    # 标记为通过
    r = run_cli(["mark", "-b", batch_id, "--to", "passed", "--ids", issue_id, "--handler", "测试用户", "--note", "已确认保留v2"])
    assert "[OK]" in r.stdout, f"标记失败: {r.stdout}"

    # 验证标记成功
    r = run_cli(["list", "-b", batch_id, "--state", "passed", "--format", "json"])
    passed = json.loads(r.stdout)
    assert any(str(i["id"]) == issue_id for i in passed), "标记后状态未变为passed"
    print("标记成功，当前passed状态问题数:", len(passed))

    # 强制重扫
    print("\n执行 --force 重扫...")
    r = run_cli(["scan", "-c", config, "-d", test_dir, "--force"])
    new_batch_id = None
    for line in r.stdout.split("\n"):
        if "批次ID:" in line:
            new_batch_id = line.split("批次ID:")[1].strip()
            break
    assert new_batch_id, f"未找到新批次ID，输出:\n{r.stdout}"
    print(f"新批次ID: {new_batch_id}")

    # 检查新批次中同一问题的状态是否继承
    r = run_cli(["list", "-b", new_batch_id, "--format", "json"])
    new_issues = json.loads(r.stdout)
    print(f"新批次问题总数: {len(new_issues)}")

    # 找同一指纹的问题（重复版本）
    inherited = None
    for i in new_issues:
        if i.get("issue_type") == "duplicate_version":
            inherited = i
            break
    assert inherited, f"新批次未找到重复版本问题"

    # 验证状态继承
    assert inherited["state"] == "passed", f"状态未继承，期望passed，实际{inherited['state']}"
    assert inherited["handler"] == "测试用户", f"处理人未继承，期望'测试用户'，实际{inherited['handler']}"
    assert "已确认保留v2" in (inherited["note"] or ""), f"备注未继承，实际{inherited['note']}"
    print(f"[PASS] 测试2通过：状态继承为 {inherited['state']}，处理人={inherited['handler']}，备注={inherited['note']}")

    # ========== 测试3：撤销后重启，导出仍能看到完整历史 ==========
    print("\n" + "=" * 50)
    print("测试3：撤销后重启，导出仍能看到完整历史")
    print("=" * 50)

    # 注意：标记操作是在旧批次 batch_id 上执行的，所以撤销也应该在旧批次上
    print("在旧批次上执行撤销...")
    r = run_cli(["undo", "-b", batch_id])
    assert "[OK]" in r.stdout, f"撤销失败: {r.stdout}"
    print("撤销成功")

    # 验证撤销后旧批次状态回到pending
    r = run_cli(["list", "-b", batch_id, "--state", "pending", "--format", "json"])
    pending_after = json.loads(r.stdout)
    dup_pending = [i for i in pending_after if i.get("issue_type") == "duplicate_version"]
    assert len(dup_pending) >= 1, "撤销后重复版本问题未回到pending"
    print(f"撤销成功，旧批次回到pending的问题数: {len(dup_pending)}")

    # 导出旧批次CSV，检查审计文件是否包含update和undo记录
    print("\n导出旧批次CSV...")
    csv_out = "_test_report"
    r = run_cli(["export", "-b", batch_id, "-o", csv_out, "-f", "csv"])
    csv_path = Path("_test_report.csv")
    audit_csv_path = Path("_test_report_audit.csv")
    assert csv_path.exists(), f"CSV文件未生成: {csv_path}"
    assert audit_csv_path.exists(), f"审计CSV文件未生成: {audit_csv_path}"

    # 读取审计CSV，检查是否包含update和undo记录
    with open(audit_csv_path, "r", encoding="utf-8-sig") as f:
        audit_content = f.read()
    print(f"审计CSV内容:\n{audit_content}")

    assert "状态变更" in audit_content, "审计日志缺少update记录"
    assert "撤销操作" in audit_content, "审计日志缺少undo记录"
    assert "测试用户" in audit_content, "审计日志缺少处理人"
    assert "已确认保留v2" in audit_content, "审计日志缺少备注"
    print("[PASS] 测试3a通过：CSV导出包含完整审计历史（含撤销）")

    # 导出旧批次HTML
    print("\n导出旧批次HTML...")
    html_out = "_test_report"
    r = run_cli(["export", "-b", batch_id, "-o", html_out, "-f", "html"])
    html_path = Path("_test_report.html")
    assert html_path.exists(), f"HTML文件未生成: {html_path}"

    with open(html_path, "r", encoding="utf-8") as f:
        html_content = f.read()
    assert "操作历史" in html_content, "HTML缺少操作历史部分"
    assert "状态变更" in html_content, "HTML缺少状态变更记录"
    assert "撤销操作" in html_content, "HTML缺少撤销操作记录"
    assert "测试用户" in html_content, "HTML缺少处理人记录"
    print("[PASS] 测试3b通过：HTML导出包含完整审计历史")

    # ========== 测试4：重启后数据一致性 ==========
    print("\n" + "=" * 50)
    print("测试4：重启后（新建Storage实例）数据一致")
    print("=" * 50)

    # 重新查询旧批次和问题（模拟重启）
    r = run_cli(["list", "-b", batch_id, "--format", "json"])
    issues_reload = json.loads(r.stdout)
    dup_reload = [i for i in issues_reload if i.get("issue_type") == "duplicate_version"]
    assert len(dup_reload) >= 1, "重启后找不到问题"
    assert dup_reload[0]["state"] == "pending", f"重启后状态错误: {dup_reload[0]['state']}"
    print(f"重启后旧批次问题状态: {dup_reload[0]['state']} (正确)")

    # 再次导出旧批次，验证历史仍在
    print("\n重启后再次导出CSV验证历史...")
    csv_out2 = "_test_report_restart"
    r = run_cli(["export", "-b", batch_id, "-o", csv_out2, "-f", "csv"])
    audit_csv_path2 = Path("_test_report_restart_audit.csv")
    assert audit_csv_path2.exists(), "重启后导出审计CSV失败"

    with open(audit_csv_path2, "r", encoding="utf-8-sig") as f:
        audit_content2 = f.read()
    assert "状态变更" in audit_content2, "重启后审计日志丢失update"
    assert "撤销操作" in audit_content2, "重启后审计日志丢失undo"
    print("[PASS] 测试4通过：重启后导出仍能看到完整历史")

    # ========== 测试5：新批次继承状态后也能看到历史（新批次自己的操作） ==========
    print("\n" + "=" * 50)
    print("测试5：新批次标记后撤销，也能看到历史")
    print("=" * 50)

    # 在新批次上标记问题
    r = run_cli(["list", "-b", new_batch_id, "--state", "passed", "--format", "json"])
    new_passed = json.loads(r.stdout)
    new_dup = [i for i in new_passed if i.get("issue_type") == "duplicate_version"]
    assert len(new_dup) >= 1, "新批次找不到继承的passed问题"
    new_issue_id = str(new_dup[0]["id"])
    print(f"新批次问题ID {new_issue_id} 当前状态: {new_dup[0]['state']}")

    # 标记为忽略
    r = run_cli(["mark", "-b", new_batch_id, "--to", "ignored", "--ids", new_issue_id, "--handler", "法务B", "--note", "历史遗留问题，忽略"])
    assert "[OK]" in r.stdout, f"新批次标记失败: {r.stdout}"
    print("新批次标记成功")

    # 撤销新批次的标记
    r = run_cli(["undo", "-b", new_batch_id])
    assert "[OK]" in r.stdout, f"新批次撤销失败: {r.stdout}"
    print("新批次撤销成功")

    # 导出新批次，验证能看到自己的操作历史
    print("\n导出新批次CSV验证历史...")
    csv_out3 = "_test_report_new_batch"
    r = run_cli(["export", "-b", new_batch_id, "-o", csv_out3, "-f", "csv"])
    audit_csv_path3 = Path("_test_report_new_batch_audit.csv")
    assert audit_csv_path3.exists(), "新批次导出审计CSV失败"

    with open(audit_csv_path3, "r", encoding="utf-8-sig") as f:
        audit_content3 = f.read()
    print(f"新批次审计CSV内容:\n{audit_content3}")

    assert "状态变更" in audit_content3, "新批次审计日志缺少update记录"
    assert "撤销操作" in audit_content3, "新批次审计日志缺少undo记录"
    assert "法务B" in audit_content3, "新批次审计日志缺少处理人"
    assert "历史遗留问题" in audit_content3, "新批次审计日志缺少备注"
    print("[PASS] 测试5通过：新批次自己的操作历史也能正常记录和导出")

    print("\n" + "=" * 70)
    print("所有测试通过！")
    print("=" * 70)

    # 清理
    for f in ["_test_report.csv", "_test_report_audit.csv", "_test_report.html",
              "_test_report_restart.csv", "_test_report_restart_audit.csv",
              "_test_report_new_batch.csv", "_test_report_new_batch_audit.csv"]:
        p = Path(f)
        if p.exists():
            p.unlink()

if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print(f"\n[FAIL] 测试失败: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n[ERROR] 测试异常: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
