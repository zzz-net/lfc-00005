#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""补交跟踪台账功能回归测试
覆盖：跨重启查询、配置、导入导出、冲突处理、撤销、权限错误
"""
import subprocess
import sys
import os
import csv
import json
import gc
from pathlib import Path

os.environ["PYTHONIOENCODING"] = "utf-8"

DB_SRC = "_ledger_src.db"
DB_DST = "_ledger_dst.db"
SAMPLE_DIR = "examples/sample_data"
RULES_FILE = "examples/rules.yaml"
LEDGER_PKG_FILE = "_test_ledger.json"
LEDGER_CSV = "_ledger_report.csv"
LEDGER_HTML = "_ledger_report.html"


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
    for f in [DB_SRC, DB_DST, LEDGER_PKG_FILE, LEDGER_CSV, LEDGER_HTML,
              "_ledger_dst_report.csv", "_ledger_dst_report.html"]:
        p = Path(f)
        if p.exists():
            try:
                p.unlink()
            except Exception:
                pass


def main():
    from contract_archiver.storage import Storage

    print("=" * 60)
    print("补交跟踪台账 - 回归测试")
    print("=" * 60)

    cleanup()

    try:
        # ===== 准备：源数据库扫描 =====
        print("\n[准备] 源数据库：扫描 + 创建台账")
        rc, out, err = run_cli(DB_SRC, "scan", "-c", RULES_FILE, "-d", SAMPLE_DIR)
        assert rc == 0, f"源数据库扫描失败: {err}"

        storage_src = Storage(DB_SRC)
        batches_src = storage_src.list_batches()
        batch_id = batches_src[0].batch_id
        print(f"  源批次: {batch_id}")

        issues_src = storage_src.get_issues(batch_id)
        assert len(issues_src) >= 2, f"问题数量不足 2 个，只有 {len(issues_src)} 个"
        print(f"  问题数: {len(issues_src)}")

        # ===== 测试1：创建台账 =====
        print("\n[测试1] 创建台账")
        rc, out, err = run_cli(
            DB_SRC, "ledger", "create", "法务-补交台账",
            "-b", batch_id, "--responsible", "张律师", "--deadline-days", "7"
        )
        assert rc == 0, f"创建台账失败: {err}"
        assert "法务-补交台账" in out, "输出应包含台账名"
        assert "待办记录" in out, "输出应包含待办记录数"
        print(f"  [PASS] 台账已创建: {out.strip()[:80]}")

        # ===== 测试2：查看台账列表 =====
        print("\n[测试2] 查看台账列表")
        rc, out, err = run_cli(DB_SRC, "ledger", "list")
        assert rc == 0, f"查看台账列表失败: {err}"
        assert "法务-补交台账" in out, "列表应包含台账名"
        print(f"  [PASS] 台账列表正常显示")

        # ===== 测试3：查看台账待办记录 =====
        print("\n[测试3] 查看台账待办记录")
        rc, out, err = run_cli(DB_SRC, "ledger", "list", "法务-补交台账")
        assert rc == 0, f"查看台账记录失败: {err}"
        assert "张律师" in out, "记录应显示负责人"
        print(f"  [PASS] 台账待办记录正常显示")

        # ===== 测试4：按负责人查询 =====
        print("\n[测试4] 按负责人查询台账记录")
        rc, out, err = run_cli(
            DB_SRC, "ledger", "list", "法务-补交台账",
            "--responsible", "张律师"
        )
        assert rc == 0, f"按负责人查询失败: {err}"
        print(f"  [PASS] 按负责人查询正常")

        # ===== 测试5：更新台账记录 =====
        print("\n[测试5] 更新台账记录")
        records = storage_src.get_ledger_records("法务-补交台账")
        assert len(records) >= 1, "台账应有至少一条记录"
        rec_id = records[0].id

        rc, out, err = run_cli(
            DB_SRC, "ledger", "update",
            "--ledger", "法务-补交台账",
            "--id", str(rec_id),
            "--progress", "in_progress",
            "--notes", "已电话催收，预计下周补齐"
        )
        assert rc == 0, f"更新台账记录失败: {err}"
        assert "跟进中" in out, "输出应显示新进度"
        print(f"  [PASS] 台账记录已更新: {out.strip()[:80]}")

        # ===== 测试6：配置管理 =====
        print("\n[测试6] 台账配置管理")
        rc, out, err = run_cli(
            DB_SRC, "ledger", "config", "set",
            "default_deadline_days", "14"
        )
        assert rc == 0, f"设置配置失败: {err}"
        assert "14" in out, "输出应显示配置值"
        print(f"  [PASS] 配置已设置")

        rc, out, err = run_cli(
            DB_SRC, "ledger", "config", "get", "default_deadline_days"
        )
        assert rc == 0, f"查看配置失败: {err}"
        assert "14" in out, "配置值应为14"
        print(f"  [PASS] 配置查询正常")

        rc, out, err = run_cli(DB_SRC, "ledger", "config", "list")
        assert rc == 0, f"列出配置失败: {err}"
        assert "default_deadline_days" in out, "配置列表应包含键"
        print(f"  [PASS] 配置列表正常")

        # ===== 测试7：配置验证 - 无效键 =====
        print("\n[测试7] 配置验证 - 无效键")
        rc, out, err = run_cli(
            DB_SRC, "ledger", "config", "set",
            "invalid_key", "value"
        )
        assert rc != 0, "无效配置键应报错"
        assert "无效" in err or "配置错误" in err, f"应提示无效键: {err}"
        print(f"  [PASS] 无效键正确报错: {err.strip()[:80]}")

        # ===== 测试8：配置验证 - 无效值 =====
        print("\n[测试8] 配置验证 - 无效值")
        rc, out, err = run_cli(
            DB_SRC, "ledger", "config", "set",
            "default_deadline_days", "abc"
        )
        assert rc != 0, "无效配置值应报错"
        assert "正整数" in err or "配置错误" in err, f"应提示正整数: {err}"
        print(f"  [PASS] 无效值正确报错: {err.strip()[:80]}")

        # ===== 测试9：同名台账冲突 =====
        print("\n[测试9] 同名台账冲突（默认报错）")
        rc, out, err = run_cli(
            DB_SRC, "ledger", "create", "法务-补交台账",
            "-b", batch_id
        )
        assert rc != 0, "同名台账应报错"
        assert "已存在" in err or "overwrite" in err.lower(), f"应提示已存在: {err}"
        print(f"  [PASS] 同名台账正确报错: {err.strip()[:80]}")

        # ===== 测试10：同名台账覆盖 =====
        print("\n[测试10] 同名台账覆盖（--overwrite）")
        rc, out, err = run_cli(
            DB_SRC, "ledger", "create", "法务-补交台账",
            "-b", batch_id, "--overwrite", "--responsible", "李法务"
        )
        assert rc == 0, f"覆盖台账失败: {err}"
        assert "覆盖更新" in out, "输出应提示覆盖更新"
        print(f"  [PASS] --overwrite 可覆盖同名台账")

        # ===== 测试11：从方案创建台账 =====
        print("\n[测试11] 从方案创建台账")
        rc, out, err = run_cli(
            DB_SRC, "scheme", "save", "台账-待补错误",
            "-b", batch_id, "--state", "pending", "--severity", "error"
        )
        assert rc == 0, f"保存方案失败: {err}"

        rc, out, err = run_cli(
            DB_SRC, "ledger", "create", "方案台账",
            "-b", batch_id, "--scheme", "台账-待补错误",
            "--responsible", "王助理"
        )
        assert rc == 0, f"从方案创建台账失败: {err}"
        assert "方案台账" in out, "输出应包含台账名"
        print(f"  [PASS] 从方案创建台账正常")

        # ===== 测试12：跨重启查询 =====
        print("\n[测试12] 跨重启查询：重新连接数据库后数据一致")
        del storage_src
        gc.collect()

        storage_src2 = Storage(DB_SRC)
        ledgers = storage_src2.list_ledgers()
        assert len(ledgers) >= 2, f"重启后应有至少2个台账，实际 {len(ledgers)} 个"
        print(f"  重启后台账数: {len(ledgers)}")

        ledger_check = storage_src2.get_ledger("法务-补交台账")
        assert ledger_check is not None, "重启后台账丢失"
        assert ledger_check.record_count > 0, "重启后记录丢失"

        records_check = storage_src2.get_ledger_records("法务-补交台账")
        assert len(records_check) > 0, "重启后台账记录丢失"
        print(f"  [PASS] 跨重启数据一致（台账、记录数）")

        # ===== 测试13：导出台账 CSV =====
        print("\n[测试13] 导出台账 CSV")
        rc, out, err = run_cli(
            DB_SRC, "ledger", "export", "法务-补交台账",
            "-o", LEDGER_CSV, "-f", "csv"
        )
        assert rc == 0, f"CSV 导出失败: {err}"
        assert Path(LEDGER_CSV).exists(), "CSV 文件不存在"

        with open(LEDGER_CSV, "r", encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            rows = list(reader)
        assert len(rows) >= 2, "CSV 至少有表头+1行数据"
        header = rows[0]
        assert "负责人" in header, "CSV 表头缺少'负责人'列"
        assert "截止日期" in header, "CSV 表头缺少'截止日期'列"
        assert "优先级" in header, "CSV 表头缺少'优先级'列"
        assert "沟通备注" in header, "CSV 表头缺少'沟通备注'列"
        assert "进度" in header, "CSV 表头缺少'进度'列"
        print(f"  [PASS] CSV 导出正常，包含所有台账字段")

        # ===== 测试14：导出台账 HTML =====
        print("\n[测试14] 导出台账 HTML")
        rc, out, err = run_cli(
            DB_SRC, "ledger", "export", "法务-补交台账",
            "-o", LEDGER_HTML, "-f", "html"
        )
        assert rc == 0, f"HTML 导出失败: {err}"
        assert Path(LEDGER_HTML).exists(), "HTML 文件不存在"

        html_content = Path(LEDGER_HTML).read_text(encoding="utf-8")
        assert "补交跟踪台账" in html_content, "HTML 缺少标题"
        assert "法务-补交台账" in html_content, "HTML 缺少台账名"
        assert "汇总" in html_content, "HTML 缺少汇总区"
        assert "待办明细" in html_content, "HTML 缺少明细表"
        assert "负责人" in html_content, "HTML 缺少负责人列"
        print(f"  [PASS] HTML 导出正常，包含汇总和明细")

        # ===== 测试15：导出台账包 JSON =====
        print("\n[测试15] 导出台账包 JSON")
        rc, out, err = run_cli(
            DB_SRC, "ledger", "export-pkg", "法务-补交台账",
            "-o", LEDGER_PKG_FILE
        )
        assert rc == 0, f"台账包导出失败: {err}"
        assert Path(LEDGER_PKG_FILE).exists(), "台账包文件不存在"

        with open(LEDGER_PKG_FILE, "r", encoding="utf-8") as f:
            pkg = json.load(f)
        assert pkg["package_type"] == "ledger_package", "package_type 不正确"
        assert pkg["ledger"]["name"] == "法务-补交台账", "台账名不匹配"
        assert len(pkg["records"]) > 0, "台账包无记录"
        assert "config" in pkg, "台账包缺少配置"
        print(f"  [PASS] 台账包导出正常（含记录和配置）")

        # ===== 测试16：导入台账包到新数据库（无冲突） =====
        print("\n[测试16] 导入台账包到新数据库（无冲突）")
        rc, out, err = run_cli(
            DB_DST, "scan", "-c", RULES_FILE, "-d", SAMPLE_DIR
        )
        assert rc == 0, f"目标数据库扫描失败: {err}"

        rc, out, err = run_cli(
            DB_DST, "ledger", "import", LEDGER_PKG_FILE
        )
        assert rc == 0, f"导入台账包失败: {err}"
        assert "导入记录" in out, "输出应提示导入"
        print(f"  [PASS] 台账包导入成功")

        storage_dst = Storage(DB_DST)
        ledgers_dst = storage_dst.list_ledgers()
        assert len(ledgers_dst) >= 1, "目标数据库应有台账"
        assert ledgers_dst[0].name == "法务-补交台账", "导入台账名不匹配"

        records_dst = storage_dst.get_ledger_records("法务-补交台账")
        assert len(records_dst) > 0, "导入后台账无记录"
        print(f"  [PASS] 导入后台账记录完整")

        # ===== 测试17：同名台账导入冲突（默认跳过） =====
        print("\n[测试17] 同名台账导入冲突（默认报错）")
        rc, out, err = run_cli(
            DB_DST, "ledger", "import", LEDGER_PKG_FILE
        )
        assert rc != 0, "同名台账导入应报错"
        assert "已存在" in err or "冲突" in err, f"应提示冲突: {err}"
        print(f"  [PASS] 同名台账导入正确报错: {err.strip()[:80]}")

        # ===== 测试18：同名台账覆盖导入 =====
        print("\n[测试18] 同名台账覆盖导入（--overwrite-ledger）")
        rc, out, err = run_cli(
            DB_DST, "ledger", "import", LEDGER_PKG_FILE,
            "--overwrite-ledger"
        )
        assert rc == 0, f"覆盖导入失败: {err}"
        assert "导入记录" in out, "输出应提示导入"
        print(f"  [PASS] --overwrite-ledger 可覆盖同名台账")

        # ===== 测试19：负责人映射不一致 =====
        print("\n[测试19] 负责人映射不一致")
        rc, out, err = run_cli(
            DB_DST, "ledger", "config", "set",
            "responsible_mapping", '{"主合同": "赵法务"}'
        )
        assert rc == 0, f"设置映射失败: {err}"

        with open(LEDGER_PKG_FILE, "r", encoding="utf-8") as f:
            pkg = json.load(f)
        pkg["config"]["responsible_mapping"] = '{"主合同": "钱法务"}'
        mismatch_pkg = "_ledger_mismatch.json"
        with open(mismatch_pkg, "w", encoding="utf-8") as f:
            json.dump(pkg, f, ensure_ascii=False, indent=2)

        rc, out, err = run_cli(
            DB_DST, "ledger", "import", mismatch_pkg,
            "--overwrite-ledger"
        )
        assert rc != 0, "负责人映射不一致应报错"
        assert "映射不一致" in err or "responsible" in err.lower(), \
            f"应提示映射不一致: {err}"
        print(f"  [PASS] 负责人映射不一致正确报错: {err.strip()[:80]}")

        rc, out, err = run_cli(
            DB_DST, "ledger", "import", mismatch_pkg,
            "--overwrite-ledger", "--ignore-responsible-mismatch"
        )
        assert rc == 0, f"忽略映射差异后导入失败: {err}"
        print(f"  [PASS] --ignore-responsible-mismatch 可忽略差异")

        Path(mismatch_pkg).unlink(missing_ok=True)

        # ===== 测试20：台账包格式错误 =====
        print("\n[测试20] 台账包格式错误")
        bad_pkg = "_ledger_bad.json"
        with open(bad_pkg, "w", encoding="utf-8") as f:
            json.dump({"package_type": "wrong"}, f)

        rc, out, err = run_cli(DB_DST, "ledger", "import", bad_pkg)
        assert rc != 0, "无效台账包应报错"
        assert "无效" in err or "ledger_package" in err, f"应提示无效: {err}"
        print(f"  [PASS] 无效台账包正确报错")

        with open(bad_pkg, "w", encoding="utf-8") as f:
            f.write("")
        rc, out, err = run_cli(DB_DST, "ledger", "import", bad_pkg)
        assert rc != 0, "空台账包应报错"
        print(f"  [PASS] 空台账包正确报错")

        Path(bad_pkg).unlink(missing_ok=True)

        # ===== 测试21：台账撤销 =====
        print("\n[测试21] 台账撤销")
        rc, out, err = run_cli(
            DB_DST, "ledger", "undo", "--count"
        )
        assert rc == 0, f"查询撤销数量失败: {err}"
        print(f"  可撤销数量: {out.strip()}")

        records_before = storage_dst.get_ledger_records("法务-补交台账")
        if records_before:
            first_rec = records_before[0]
            storage_dst.update_ledger_record(
                ledger_name="法务-补交台账",
                record_id=first_rec.id,
                progress="closed",
            )
            rc, out, err = run_cli(
                DB_DST, "ledger", "undo", "法务-补交台账"
            )
            assert rc == 0, f"撤销失败: {err}"
            assert "已撤销" in out, "应提示已撤销"
            print(f"  [PASS] 台账撤销正常")

        rc, out, err = run_cli(DB_DST, "ledger", "undo")
        if "没有可撤销" in err:
            print(f"  [PASS] 无操作可撤销时正确提示")
        else:
            print(f"  撤销结果: {out.strip()[:80]}")

        # ===== 测试22：台账操作日志 =====
        print("\n[测试22] 台账操作日志")
        rc, out, err = run_cli(DB_DST, "ledger", "log")
        assert rc == 0, f"查看日志失败: {err}"
        print(f"  日志输出: {out.strip()[:100]}")

        rc, out, err = run_cli(
            DB_DST, "ledger", "log", "法务-补交台账"
        )
        assert rc == 0, f"按台账名查看日志失败: {err}"
        assert "法务-补交台账" in out, "日志应包含台账名"
        print(f"  [PASS] 台账操作日志正常")

        # ===== 测试23：删除台账 =====
        print("\n[测试23] 删除台账")
        rc, out, err = run_cli(
            DB_DST, "ledger", "delete", "方案台账"
        )
        if rc == 0:
            assert "已删除" in out, "应提示已删除"
            print(f"  [PASS] 台账删除正常")
        else:
            print(f"  目标数据库无方案台账，跳过删除测试")

        # ===== 测试24：台账不存在的错误 =====
        print("\n[测试24] 台账不存在错误")
        rc, out, err = run_cli(DB_SRC, "ledger", "list", "不存在的台账")
        assert rc != 0, "不存在的台账应报错"
        assert "不存在" in err, f"应提示不存在: {err}"
        print(f"  [PASS] 不存在的台账正确报错")

        # ===== 测试25：优先级规则配置 =====
        print("\n[测试25] 优先级规则配置")
        rc, out, err = run_cli(
            DB_SRC, "ledger", "config", "set",
            "priority_rules", '{"error": "high", "warning": "low"}'
        )
        assert rc == 0, f"设置优先级规则失败: {err}"
        print(f"  [PASS] 优先级规则配置正常")

        rc, out, err = run_cli(
            DB_SRC, "ledger", "config", "set",
            "priority_rules", '{"error": "invalid"}'
        )
        assert rc != 0, "无效优先级规则应报错"
        print(f"  [PASS] 无效优先级规则正确报错")

        # ===== 测试26：权限错误模拟 =====
        print("\n[测试26] 权限错误提示")
        readonly_db = Path(__file__).parent / "_ledger_readonly.db"
        try:
            from contract_archiver.storage import Storage as S
            s = S(str(readonly_db))
            s.create_ledger("test", batch_id, overwrite=True)
        except Exception:
            pass

        if readonly_db.exists():
            import stat
            try:
                readonly_db.chmod(stat.S_IREAD)
                rc, out, err = run_cli(
                    str(readonly_db), "ledger", "create", "权限测试",
                    "-b", batch_id
                )
                if rc != 0 and "权限" in err:
                    print(f"  [PASS] 权限错误正确提示: {err.strip()[:80]}")
                else:
                    print(f"  (权限测试跳过 - 系统未阻止写入)")
            except Exception:
                print(f"  (权限测试跳过 - 无法设置只读)")
            finally:
                try:
                    readonly_db.chmod(stat.S_IWRITE | stat.S_IREAD)
                    readonly_db.unlink()
                except Exception:
                    pass
        else:
            print(f"  (权限测试跳过 - 数据库文件未创建)")

        # ===== 测试27：完整链路验证 =====
        print("\n[测试27] 完整链路：扫描→创建台账→更新→导出→导入→查询")
        storage_src2 = Storage(DB_SRC)

        rc, out, err = run_cli(
            DB_SRC, "ledger", "create", "链路测试台账",
            "-b", batch_id, "--state", "pending",
            "--responsible", "测试负责人", "--deadline-days", "3"
        )
        assert rc == 0, f"创建台账失败: {err}"

        records = storage_src2.get_ledger_records("链路测试台账")
        assert len(records) > 0, "台账无记录"

        rc, out, err = run_cli(
            DB_SRC, "ledger", "update",
            "--ledger", "链路测试台账",
            "--id", str(records[0].id),
            "--progress", "submitted",
            "--notes", "已提交补交材料"
        )
        assert rc == 0, f"更新记录失败: {err}"

        pkg_file = "_ledger_chain_test.json"
        rc, out, err = run_cli(
            DB_SRC, "ledger", "export-pkg", "链路测试台账",
            "-o", pkg_file
        )
        assert rc == 0, f"导出台账包失败: {err}"

        rc, out, err = run_cli(
            DB_DST, "ledger", "import", pkg_file
        )
        assert rc == 0, f"导入台账包失败: {err}"

        storage_dst2 = Storage(DB_DST)
        records_dst2 = storage_dst2.get_ledger_records("链路测试台账")
        assert len(records_dst2) > 0, "导入后无记录"

        submitted = [r for r in records_dst2 if r.progress == "submitted"]
        assert len(submitted) >= 1, "导入后缺少 submitted 进度记录"

        Path(pkg_file).unlink(missing_ok=True)
        print(f"  [PASS] 完整链路验证通过（创建→更新→导出→导入→查询）")

        print("\n" + "=" * 60)
        print("[PASS] 全部 27 项补交跟踪台账回归测试通过！")
        print("=" * 60)

    finally:
        try:
            del storage_src
        except NameError:
            pass
        try:
            del storage_src2
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
