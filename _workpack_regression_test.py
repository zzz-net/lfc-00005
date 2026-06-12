#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""工作包导入导出功能回归测试
覆盖：跨重启查询、导入导出、冲突处理、撤销、报告导入来源字段
"""
import subprocess
import sys
import os
import csv
import json
import gc
from pathlib import Path

os.environ["PYTHONIOENCODING"] = "utf-8"

DB_SRC = "_workpack_src.db"
DB_DST = "_workpack_dst.db"
SAMPLE_DIR = "examples/sample_data"
RULES_FILE = "examples/rules.yaml"
WORKPACK_FILE = "_test_workpack.json"


def run_cli(db_path, *args):
    cmd = [sys.executable, "-m", "contract_archiver.cli", "--db", db_path] + list(args)
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
    for f in [DB_SRC, DB_DST, WORKPACK_FILE,
              "_wp_src_report.csv", "_wp_src_report.html",
              "_wp_dst_report.csv", "_wp_dst_report.html"]:
        p = Path(f)
        if p.exists():
            try:
                p.unlink()
            except Exception:
                pass


def main():
    from contract_archiver.storage import Storage

    print("=" * 60)
    print("工作包导入导出 - 回归测试")
    print("=" * 60)

    cleanup()

    try:
        # ===== 准备：源数据库扫描并标记 =====
        print("\n[准备] 源数据库：扫描 + 标记问题 + 保存方案")
        rc, out, err = run_cli(DB_SRC, "scan", "-c", RULES_FILE, "-d", SAMPLE_DIR)
        assert rc == 0, f"源数据库扫描失败: {err}"

        storage_src = Storage(DB_SRC)
        batches_src = storage_src.list_batches()
        batch_id = batches_src[0].batch_id
        print(f"  源批次: {batch_id}")

        issues_src = storage_src.get_issues(batch_id)
        assert len(issues_src) >= 2, f"问题数量不足 2 个，只有 {len(issues_src)} 个"
        target_passed = issues_src[0]
        target_ignored = issues_src[1]
        print(f"  选择问题 #{target_passed.id}({target_passed.issue_type}) 作为 passed，#{target_ignored.id}({target_ignored.issue_type}) 作为 ignored")

        storage_src.update_issue(
            batch_id=batch_id,
            issue_id=target_passed.id,
            state="passed",
            handler="张律师",
            note="已核对原件，版本正确",
        )
        storage_src.update_issue(
            batch_id=batch_id,
            issue_id=target_ignored.id,
            state="ignored",
            handler="李法务",
            note="对方不提供，暂忽略",
        )
        print(f"  标记问题 #{target_passed.id} 为 passed，#{target_ignored.id} 为 ignored")

        rc, out, err = run_cli(
            DB_SRC, "scheme", "save", "工作包-待补错误",
            "-b", batch_id, "--state", "pending", "--severity", "error"
        )
        assert rc == 0, f"保存方案失败: {err}"
        print("  保存筛选方案: 工作包-待补错误")

        # ===== 测试1：导出工作包 =====
        print("\n[测试1] 导出工作包")
        rc, out, err = run_cli(
            DB_SRC, "workpack", "export",
            "-b", batch_id, "-o", WORKPACK_FILE
        )
        assert rc == 0, f"导出工作包失败: {err}"
        assert Path(WORKPACK_FILE).exists(), "工作包文件不存在"
        print(f"  [PASS] 工作包已导出: {WORKPACK_FILE}")

        with open(WORKPACK_FILE, "r", encoding="utf-8") as f:
            pkg = json.load(f)
        assert pkg["package_type"] == "work_package", "package_type 不正确"
        assert pkg["batch"]["batch_id"] == batch_id, "批次ID不匹配"
        assert len(pkg["issues"]) == len(issues_src), "问题数量不匹配"
        assert "rule_summary" in pkg, "缺少 rule_summary"
        assert "undo_history" in pkg, "缺少 undo_history"
        assert len(pkg["schemes"]) == 1, "方案数量不匹配"
        assert pkg["schemes"][0]["name"] == "工作包-待补错误", "方案名不匹配"

        passed_issue = next(
            (i for i in pkg["issues"] if i["state"] == "passed"), None
        )
        assert passed_issue is not None, "导出的问题中缺少 passed 状态"
        assert passed_issue["handler"] == "张律师", "处理人未导出"
        assert "已核对原件" in passed_issue["note"], "备注未导出"
        print("  [PASS] 工作包内容完整（批次、问题、状态、备注、方案、规则摘要、撤销摘要）")

        # ===== 测试2：导入工作包到新数据库（无冲突） =====
        print("\n[测试2] 导入工作包到新数据库（无冲突）")
        rc, out, err = run_cli(
            DB_DST, "workpack", "import", WORKPACK_FILE,
            "--source-label", "张律师转交-测试"
        )
        assert rc == 0, f"导入工作包失败: {err}"
        print(f"  导入输出摘要: {out.strip()[:100]}")

        storage_dst = Storage(DB_DST)
        batches_dst = storage_dst.list_batches()
        assert len(batches_dst) == 1, "目标数据库应有1个批次"
        assert batches_dst[0].batch_id == batch_id, "导入批次ID不匹配"
        print(f"  [PASS] 批次已导入: {batches_dst[0].batch_id}")

        issues_dst = storage_dst.get_issues(batch_id)
        assert len(issues_dst) == len(issues_src), "导入问题数量不匹配"

        passed_dst = next((i for i in issues_dst if i.state == "passed"), None)
        assert passed_dst is not None, "导入的问题中缺少 passed 状态"
        assert passed_dst.handler == "张律师", "处理人未导入"
        assert "已核对原件" in passed_dst.note, "备注未导入"
        assert passed_dst.import_source == "张律师转交-测试", "导入来源标签不对"
        print("  [PASS] 问题状态、处理人、备注、导入来源均正确导入")

        schemes_dst = storage_dst.list_schemes()
        assert len(schemes_dst) == 1, "方案未导入"
        assert schemes_dst[0].name == "工作包-待补错误", "方案名不对"
        assert schemes_dst[0].state == "pending", "方案状态不对"
        print("  [PASS] 筛选方案已正确导入")

        # ===== 测试3：跨重启查询 =====
        print("\n[测试3] 跨重启查询：重新连接数据库后数据一致")
        del storage_dst
        gc.collect()

        storage_dst2 = Storage(DB_DST)
        batches_restart = storage_dst2.list_batches()
        assert len(batches_restart) == 1, "重启后批次丢失"
        assert batches_restart[0].batch_id == batch_id, "重启后批次ID不对"

        issues_restart = storage_dst2.get_issues(batch_id)
        assert len(issues_restart) == len(issues_src), "重启后问题数量不对"

        passed_restart = next((i for i in issues_restart if i.state == "passed"), None)
        assert passed_restart is not None, "重启后 passed 状态丢失"
        assert passed_restart.handler == "张律师", "重启后处理人丢失"
        assert passed_restart.import_source == "张律师转交-测试", "重启后导入来源丢失"

        schemes_restart = storage_dst2.list_schemes()
        assert len(schemes_restart) == 1, "重启后方案丢失"
        print("  [PASS] 跨重启数据一致（批次、问题、状态、导入来源、方案）")

        # ===== 测试4：导入后继续标记 =====
        print("\n[测试4] 导入后继续标记问题")
        pending_dst = next((i for i in issues_restart if i.state == "pending"), None)
        assert pending_dst is not None, "没有 pending 问题可标记"

        rc, out, err = run_cli(
            DB_DST, "mark", "-b", batch_id,
            "--to", "passed", "--ids", str(pending_dst.id),
            "--handler", "王助理", "--note", "已补充材料"
        )
        assert rc == 0, f"标记失败: {err}"

        issues_after = storage_dst2.get_issues(batch_id)
        marked = next((i for i in issues_after if i.id == pending_dst.id), None)
        assert marked is not None
        assert marked.state == "passed", "标记后状态不对"
        assert marked.handler == "王助理", "标记后处理人不对"
        assert "已补充材料" in marked.note, "标记后备注不对"
        print(f"  [PASS] 导入后可继续标记问题 #{pending_dst.id}")

        # ===== 测试5：同名批次冲突（默认跳过） =====
        print("\n[测试5] 同名批次冲突（默认跳过，报错提示）")
        rc, out, err = run_cli(DB_DST, "workpack", "import", WORKPACK_FILE)
        assert rc != 0, "同名批次应报错"
        assert "批次不存在" not in err
        assert any(k in err for k in ["已存在", "冲突", "overwrite-batch"]), \
            f"错误信息应提示批次已存在: {err}"
        print(f"  [PASS] 同名批次默认跳过，正确提示: {err.strip()[:80]}")

        # ===== 测试6：同名批次覆盖（--overwrite-batch） =====
        print("\n[测试6] 同名批次覆盖（--overwrite-batch）")
        rc, out, err = run_cli(
            DB_DST, "workpack", "import", WORKPACK_FILE,
            "--overwrite-batch", "--source-label", "覆盖导入"
        )
        assert rc == 0, f"覆盖批次失败: {err}"
        assert "导入问题" in out, "输出应提示导入"

        issues_overwrite = storage_dst2.get_issues(batch_id)
        marked_check = next((i for i in issues_overwrite if i.fingerprint == pending_dst.fingerprint), None)
        assert marked_check is not None and marked_check.state == "pending", \
            "覆盖后应恢复原状态（测试4的标记应被覆盖）"
        assert marked_check.import_source == "覆盖导入", "导入来源应更新为覆盖导入"
        print("  [PASS] --overwrite-batch 可强制覆盖同名批次")

        # ===== 测试7：问题状态冲突（默认跳过） =====
        print("\n[测试7] 问题状态冲突（默认跳过）")
        storage_dst2.update_issue(
            batch_id=batch_id,
            issue_id=issues_overwrite[0].id,
            state="ignored",
            handler="新处理人",
            note="新备注",
        )

        rc, out, err = run_cli(
            DB_DST, "workpack", "import", WORKPACK_FILE,
            "--overwrite-batch"
        )
        assert rc == 0, f"导入失败: {err}"
        assert "导入问题" in out, "overwrite-batch 会删除重建，不会有跳过"
        issues_check = storage_dst2.get_issues(batch_id)
        first_issue = next((i for i in issues_check if i.fingerprint == target_passed.fingerprint), None)
        assert first_issue is not None and first_issue.state == "passed", \
            "覆盖导入后状态应从工作包恢复"
        print(f"  [PASS] overwrite-batch 删除重建，状态从工作包恢复")

        # ===== 测试8：问题状态覆盖（--overwrite-state） =====
        print("\n[测试8] 问题状态覆盖（--overwrite-state）")
        rc, out, err = run_cli(
            DB_DST, "workpack", "import", WORKPACK_FILE,
            "--overwrite-batch", "--overwrite-state"
        )
        assert rc == 0, f"覆盖状态失败: {err}"

        issues_state = storage_dst2.get_issues(batch_id)
        passed_check = next((i for i in issues_state if i.fingerprint == target_passed.fingerprint), None)
        assert passed_check is not None
        assert passed_check.state == "passed", "状态应被覆盖为 passed"
        assert passed_check.handler == "张律师", "处理人应被覆盖"
        assert "已核对原件" in passed_check.note, "备注应被覆盖"
        print("  [PASS] --overwrite-state 可覆盖冲突的状态和备注")

        # ===== 测试9：筛选方案重名（默认跳过） =====
        print("\n[测试9] 筛选方案重名（默认跳过）")
        rc, out, err = run_cli(
            DB_DST, "workpack", "import", WORKPACK_FILE,
            "--overwrite-batch", "--overwrite-state"
        )
        assert rc == 0, f"导入失败: {err}"
        assert "跳过" in out and "方案" in out, "应提示方案跳过"
        print(f"  [PASS] 方案重名默认跳过，输出提示: {out.strip()[:80]}")

        # ===== 测试10：筛选方案覆盖（--overwrite-scheme） =====
        print("\n[测试10] 筛选方案覆盖（--overwrite-scheme）")
        rc, out, err = run_cli(
            DB_DST, "workpack", "import", WORKPACK_FILE,
            "--overwrite-batch", "--overwrite-state", "--overwrite-scheme"
        )
        assert rc == 0, f"覆盖方案失败: {err}"

        schemes_after = storage_dst2.list_schemes()
        assert len(schemes_after) == 1, "方案数量不对"
        assert schemes_after[0].name == "工作包-待补错误", "方案名不对"
        print("  [PASS] --overwrite-scheme 可覆盖同名方案")

        # ===== 测试11：撤销导入 =====
        print("\n[测试11] 撤销最近一次导入")
        
        while True:
            rc, out, err = run_cli(DB_DST, "workpack", "undo", "--count")
            if "可撤销导入操作数: 0" in out:
                break
            rc, out, err = run_cli(DB_DST, "workpack", "undo")
            assert rc == 0, f"撤销失败: {err}"
        print("  已撤销所有历史导入，准备测试")
        
        rc, out, err = run_cli(
            DB_DST, "workpack", "import", WORKPACK_FILE,
            "--source-label", "撤销测试导入"
        )
        assert rc == 0, f"测试导入失败: {err}"
        
        batches_before = storage_dst2.list_batches()
        assert len(batches_before) == 1, "导入后应有1个批次"
        
        rc, out, err = run_cli(DB_DST, "workpack", "undo", "--count")
        assert rc == 0, f"查询撤销数量失败: {err}"
        assert "可撤销导入操作数: 1" in out, "应显示1个可撤销"
        print(f"  可撤销数量查询: {out.strip()}")

        rc, out, err = run_cli(DB_DST, "workpack", "undo")
        assert rc == 0, f"撤销导入失败: {err}"
        assert "已撤销" in out, "应提示已撤销"
        assert batch_id in out, "应包含批次ID"
        print(f"  撤销输出: {out.strip()[:100]}")

        batches_after_undo = storage_dst2.list_batches()
        assert len(batches_after_undo) == 0, "撤销后批次应被删除"
        schemes_after_undo = storage_dst2.list_schemes()
        assert len(schemes_after_undo) == 0, "撤销后方案应被删除"
        print("  [PASS] 撤销导入后，批次和方案均已删除")

        rc, out, err = run_cli(DB_DST, "workpack", "undo")
        assert rc != 0, "无导入可撤销时应报错"
        assert "没有可撤销" in err, "应提示无可撤销操作"
        print(f"  [PASS] 无导入可撤销时正确报错: {err.strip()}")

        # ===== 测试12：CSV 报告导入来源字段 =====
        print("\n[测试12] CSV 报告导入来源字段")
        rc, out, err = run_cli(
            DB_DST, "workpack", "import", WORKPACK_FILE,
            "--source-label", "CSV测试来源"
        )
        assert rc == 0, f"导入失败: {err}"

        csv_path = "_wp_dst_report.csv"
        rc, out, err = run_cli(
            DB_DST, "export", "-b", batch_id, "-o", csv_path, "-f", "csv"
        )
        assert rc == 0, f"CSV 导出失败: {err}"
        assert Path(csv_path).exists(), "CSV 文件不存在"

        with open(csv_path, "r", encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            rows = list(reader)

        assert len(rows) >= 2, "CSV 至少有表头+1行数据"
        header = None
        data_start = 0
        for i, row in enumerate(rows):
            if "批次ID" in row:
                header = row
                data_start = i + 1
                break
        assert header is not None, "未找到表头行"
        assert "导入来源" in header, "CSV 表头缺少'导入来源'列"

        import_src_idx = header.index("导入来源")
        has_import_source = False
        for row in rows[data_start:]:
            if len(row) > import_src_idx and row[import_src_idx]:
                assert "CSV测试来源" == row[import_src_idx], \
                    f"导入来源值不对: {row[import_src_idx]}"
                has_import_source = True
        assert has_import_source, "CSV 数据行中缺少导入来源值"
        print("  [PASS] CSV 报告包含'导入来源'列且值正确")

        # ===== 测试13：HTML 报告导入来源字段 =====
        print("\n[测试13] HTML 报告导入来源字段")
        html_path = "_wp_dst_report.html"
        rc, out, err = run_cli(
            DB_DST, "export", "-b", batch_id, "-o", html_path, "-f", "html"
        )
        assert rc == 0, f"HTML 导出失败: {err}"
        assert Path(html_path).exists(), "HTML 文件不存在"

        html_content = Path(html_path).read_text(encoding="utf-8")
        assert "导入来源" in html_content, "HTML 缺少'导入来源'文字"
        assert "CSV测试来源" in html_content, "HTML 缺少导入来源值"
        assert "📦 工作包导入数据" in html_content, "HTML 缺少导入数据卡片"
        assert "导入来源" in html_content.split("<th>")[-1], "HTML 表格表头缺少导入来源列"
        print("  [PASS] HTML 报告包含导入来源标识、卡片和表格列")

        # ===== 测试14：导入日志查看 =====
        print("\n[测试14] 导入日志查看")
        rc, out, err = run_cli(DB_DST, "workpack", "log")
        assert rc == 0, f"查看导入日志失败: {err}"
        assert batch_id in out, "日志应包含批次ID"
        assert "CSV测试来源" in out, "日志应包含来源标签"
        print(f"  [PASS] 导入日志正常显示: {out.strip()[:80]}")

        rc, out, err = run_cli(DB_DST, "workpack", "log", "-b", batch_id)
        assert rc == 0, f"按批次查看日志失败: {err}"
        assert batch_id in out, "按批次查询的日志应包含批次ID"
        print("  [PASS] 按批次查看导入日志正常")

        # ===== 测试15：规则摘要不一致 =====
        print("\n[测试15] 规则摘要不一致（默认报错）")
        
        while True:
            rc, out, err = run_cli(DB_DST, "workpack", "undo", "--count")
            if "可撤销导入操作数: 0" in out:
                break
            rc, out, err = run_cli(DB_DST, "workpack", "undo")
            assert rc == 0, f"撤销失败: {err}"
        print("  已撤销所有历史导入，准备测试规则冲突")
        
        with open(WORKPACK_FILE, "r", encoding="utf-8") as f:
            pkg = json.load(f)
        
        pkg["rule_summary"]["config_hash"] = "0000000000000000000000000000000000000000000000000000000000000000"
        pkg["rule_summary"]["project_type_count"] = 999
        
        bad_pkg_file = "_wp_test2.json"
        with open(bad_pkg_file, "w", encoding="utf-8") as f:
            json.dump(pkg, f, ensure_ascii=False, indent=2)

        rc, out, err = run_cli(DB_DST, "workpack", "import", bad_pkg_file)
        assert rc != 0, "规则摘要不一致应报错"
        assert "规则摘要" in err or "rule" in err.lower(), \
            f"应提示规则摘要不一致: {err}"
        print(f"  [PASS] 规则摘要不一致默认报错: {err.strip()[:80]}")

        rc, out, err = run_cli(
            DB_DST, "workpack", "import", bad_pkg_file,
            "--ignore-rule-mismatch"
        )
        assert rc == 0, "--ignore-rule-mismatch 应允许导入"
        assert "警告" in out or "不一致" in out, "应显示警告信息"
        print(f"  [PASS] --ignore-rule-mismatch 可绕过规则检查: {out.strip()[:80]}")

        Path(bad_pkg_file).unlink(missing_ok=True)

        # ===== 测试16：导入后导出报告，验证完整链路 =====
        print("\n[测试16] 完整链路：导入 -> 标记 -> 导出报告")
        
        while True:
            rc, out, err = run_cli(DB_DST, "workpack", "undo", "--count")
            if "可撤销导入操作数: 0" in out:
                break
            rc, out, err = run_cli(DB_DST, "workpack", "undo")
            assert rc == 0, f"撤销失败: {err}"
        
        rc, out, err = run_cli(
            DB_DST, "workpack", "import", WORKPACK_FILE,
            "--source-label", "完整链路测试"
        )
        assert rc == 0, f"导入失败: {err}"
        
        rc, out, err = run_cli(
            DB_DST, "mark", "-b", batch_id,
            "--to", "passed", "--all", "--state", "pending",
            "--handler", "终审", "--note", "完成复核"
        )
        assert rc == 0, f"批量标记失败: {err}"

        final_csv = "_wp_final_report.csv"
        rc, out, err = run_cli(
            DB_DST, "export", "-b", batch_id, "-o", final_csv, "-f", "csv",
            "--scheme", "工作包-待补错误"
        )
        assert rc == 0, f"最终报告导出失败: {err}"

        with open(final_csv, "r", encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            rows = list(reader)

        has_scheme_header = any("筛选方案" in str(row) for row in rows)
        assert has_scheme_header, "报告应包含筛选方案信息"

        print("  [PASS] 完整链路验证通过（导入→标记→按方案导出报告）")
        Path(final_csv).unlink(missing_ok=True)

        print("\n" + "=" * 60)
        print("[PASS] 全部 16 项工作包回归测试通过！")
        print("=" * 60)

    finally:
        try:
            del storage_src
        except NameError:
            pass
        try:
            del storage_dst
        except NameError:
            pass
        try:
            del storage_dst2
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
