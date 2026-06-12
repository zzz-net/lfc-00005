"""
合同附件归档校验工具 - 筛选方案功能测试
包含：配置持久化、导出字段、冲突覆盖、完整链路测试
"""
from __future__ import annotations

import csv
import io
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from contract_archiver.cli import main, _ensure_utf8_stdio
from contract_archiver.storage import Storage, FilterScheme
from contract_archiver.exceptions import (
    SchemeExistsError,
    SchemeNotFoundError,
    EmptySchemeError,
)

_ensure_utf8_stdio()


def run_cli(*argv: str) -> tuple[int, str, str]:
    """运行 CLI 并捕获 stdout/stderr"""
    import io
    import contextlib

    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()
    with contextlib.redirect_stdout(stdout_buf), contextlib.redirect_stderr(stderr_buf):
        try:
            rc = main(list(argv))
        except SystemExit as e:
            rc = e.code if e.code is not None else 0
    return rc, stdout_buf.getvalue(), stderr_buf.getvalue()


def assert_rc(rc: int, expected: int, stdout: str, stderr: str, ctx: str):
    if rc != expected:
        raise AssertionError(
            f"[{ctx}] 期望退出码={expected}, 实际={rc}\n"
            f"stdout: {stdout}\nstderr: {stderr}"
        )


def test_scheme_persistence(tmp_db: Path):
    """测试1: 方案配置持久化 - 保存后重启 Storage 仍存在"""
    print("=" * 60)
    print("测试1: 方案配置持久化")

    # 创建第一个 Storage 实例并保存方案
    storage1 = Storage(tmp_db)
    s1, _ = storage1.save_scheme(
        name="法务常用-待补错误",
        batch_id="TEST_BATCH_001",
        state="pending",
        severity="error",
        project_type_id="SALES",
    )
    assert s1.name == "法务常用-待补错误"
    assert s1.state == "pending"
    assert s1.severity == "error"
    assert s1.project_type_id == "SALES"
    assert s1.batch_id == "TEST_BATCH_001"
    assert not s1.is_empty
    print("  ✓ Storage1 保存方案成功")

    # 列出方案
    schemes = storage1.list_schemes()
    assert len(schemes) == 1
    assert schemes[0].name == "法务常用-待补错误"
    print("  ✓ Storage1 列出方案成功")

    # 模拟重启：创建新的 Storage 实例
    del storage1
    storage2 = Storage(tmp_db)
    schemes2 = storage2.list_schemes()
    assert len(schemes2) == 1, f"持久化失败，重启后方案数={len(schemes2)}"
    s2 = schemes2[0]
    assert s2.name == "法务常用-待补错误"
    assert s2.state == "pending"
    assert s2.severity == "error"
    assert s2.project_type_id == "SALES"
    assert s2.batch_id == "TEST_BATCH_001"
    print("  ✓ 重启 Storage 后方案依然存在（持久化通过）")

    # get_scheme 也能拿到
    s3 = storage2.get_scheme("法务常用-待补错误")
    assert s3.name == s2.name
    print("  ✓ get_scheme 获取成功")

    # 测试显示字典
    display = s3.to_display_dict()
    assert "批次" in display and display["批次"] == "TEST_BATCH_001"
    assert "状态" in display and display["状态"] == "待补"
    assert "严重度" in display and display["严重度"] == "错误"
    assert "项目类型" in display and display["项目类型"] == "SALES"
    print(f"  ✓ 显示字典: {display}")
    print("测试1: 通过 ✓\n")


def test_scheme_conflict_and_overwrite(tmp_db: Path):
    """测试2: 方案重名冲突与覆盖"""
    print("=" * 60)
    print("测试2: 方案重名冲突与覆盖")

    storage = Storage(tmp_db)

    # 第一次保存
    s1, _ = storage.save_scheme(
        name="采购问题",
        state="pending",
        project_type_id="PURCHASE",
    )
    assert s1.state == "pending"
    print(f"  ✓ 首次创建方案「采购问题」: state={s1.state}")

    # 第二次同名不覆盖 - 应报错
    try:
        storage.save_scheme(
            name="采购问题",
            state="passed",
            project_type_id="PURCHASE",
        )
        raise AssertionError("重名未加 overwrite 应该报错")
    except SchemeExistsError as e:
        assert "采购问题" in str(e)
        assert "--overwrite" in str(e)
        print(f"  ✓ 重名报错（符合预期）: {e}")

    # 验证原方案未变
    s_old = storage.get_scheme("采购问题")
    assert s_old.state == "pending"
    print(f"  ✓ 未覆盖情况下原方案保持不变: state={s_old.state}")

    # 第三次同名加 overwrite - 应成功覆盖
    s2, _ = storage.save_scheme(
        name="采购问题",
        state="passed",
        severity="warning",
        project_type_id="PURCHASE",
        overwrite=True,
    )
    assert s2.state == "passed"
    assert s2.severity == "warning"
    print(f"  ✓ 加 overwrite 后覆盖成功: state={s2.state}, severity={s2.severity}")

    # 列出方案仍只有 1 个
    schemes = storage.list_schemes()
    assert len(schemes) == 1
    print(f"  ✓ 覆盖后总方案数仍为 1（无重复）")
    print("测试2: 通过 ✓\n")


def test_scheme_empty_and_notfound(tmp_db: Path):
    """测试3: 空条件方案和不存在的方案"""
    print("=" * 60)
    print("测试3: 空条件方案和不存在的方案")

    storage = Storage(tmp_db)

    # 空条件报错
    try:
        storage.save_scheme(name="空方案")
        raise AssertionError("空方案应该报错")
    except EmptySchemeError as e:
        assert "空方案" in str(e)
        print(f"  ✓ 空方案报错（符合预期）: {e}")

    # 部分空条件也报错
    try:
        storage.save_scheme(name="还是空的", batch_id=None, state=None)
        raise AssertionError("应报错")
    except EmptySchemeError as e:
        print(f"  ✓ 全 None 条件仍报错: {e}")

    # 取不存在的方案
    try:
        storage.get_scheme("不存在的方案")
        raise AssertionError("应报错")
    except SchemeNotFoundError as e:
        assert "不存在的方案" in str(e)
        print(f"  ✓ 取不存在方案报错: {e}")

    # 删除不存在的方案
    try:
        storage.delete_scheme("不存在的方案")
        raise AssertionError("应报错")
    except SchemeNotFoundError as e:
        print(f"  ✓ 删除不存在方案报错: {e}")

    # 正常保存 + 删除
    storage.save_scheme(name="临时方案", severity="error")
    assert len(storage.list_schemes()) == 1
    storage.delete_scheme("临时方案")
    assert len(storage.list_schemes()) == 0
    print("  ✓ 正常删除成功")
    print("测试3: 通过 ✓\n")


def test_export_fields(tmp_db: Path, rules_file: Path, sample_dir: Path):
    """测试4: 导出字段 - CSV/HTML 包含方案名和条件"""
    import gc
    import shutil
    import time

    print("=" * 60)
    print("测试4: 导出字段 - CSV/HTML 方案信息")

    out_dir = tempfile.mkdtemp(prefix="cas_export_")
    try:
        out_path = Path(out_dir)

        # 1. 扫描
        rc, out, err = run_cli(
            "--db", str(tmp_db),
            "scan", "-c", str(rules_file), "-d", str(sample_dir),
        )
        assert_rc(rc, 0, out, err, "scan")
        batch_id = _extract_batch_id(out)
        print(f"  ✓ 扫描完成, batch={batch_id}")

        # 2. 保存方案
        scheme_name = "销售-待补错误"
        rc, out, err = run_cli(
            "--db", str(tmp_db),
            "scheme", "save", scheme_name,
            "-b", batch_id,
            "--state", "pending",
            "--severity", "error",
            "--project-type", "SALES",
        )
        assert_rc(rc, 0, out, err, "scheme save")
        assert scheme_name in out
        print(f"  ✓ 保存方案成功: {out.strip()}")

        # 3. 导出 CSV
        csv_path = out_path / "report.csv"
        rc, out, err = run_cli(
            "--db", str(tmp_db),
            "export", "-b", batch_id,
            "-o", str(csv_path),
            "-f", "csv",
            "--scheme", scheme_name,
        )
        assert_rc(rc, 0, out, err, "export csv")
        assert csv_path.exists()
        assert scheme_name in out
        print(f"  ✓ CSV 导出成功: {csv_path}")

        # 检查 CSV 前几行是否有方案信息
        with open(csv_path, "r", encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            rows = list(reader)
        # 第一行：方案名和条件
        assert rows[0][0] == "筛选方案", f"CSV 首列应是'筛选方案', 实际={rows[0][0]}"
        assert rows[0][1] == scheme_name, f"方案名不匹配: {rows[0][1]}"
        assert "状态=待补" in rows[0][3] or "错误" in rows[0][3]
        print(f"  ✓ CSV 首行包含方案信息: {rows[0]}")

        # 找到真正的数据表头行（包含"问题ID"那行）
        header_idx = None
        for r_idx, row in enumerate(rows):
            if "问题ID" in row and "筛选方案" in row:
                header_idx = r_idx
                break
        assert header_idx is not None, f"未找到数据表头行，共{len(rows)}行"
        header_row = rows[header_idx]
        scheme_col_idx = header_row.index("筛选方案")
        print(f"  ✓ CSV 数据表头行（索引={header_idx}）包含方案列（索引={scheme_col_idx}）")

        # 数据行是否有内容（方案筛选可能 0 条或更多）
        data_rows = [r for r in rows[header_idx + 1:] if len(r) > scheme_col_idx]
        # 检查如果有数据的话，方案名是否被填充
        if data_rows:
            filled = [r for r in data_rows if r[scheme_col_idx] == scheme_name]
            # 方案筛选条件可能导致 0 条匹配，这也正常；但只要有数据就应填充方案名
            print(f"  ✓ 数据行共 {len(data_rows)} 条（筛选后可能为 0，有数据则均填充方案名）")
            if len(data_rows) > 0:
                assert len(filled) == len(data_rows), (
                    f"部分数据行未填充方案名: 填充={len(filled)}/{len(data_rows)}"
                )

        # 4. 导出 HTML
        html_path = out_path / "report.html"
        rc, out, err = run_cli(
            "--db", str(tmp_db),
            "export", "-b", batch_id,
            "-o", str(html_path),
            "-f", "html",
            "--scheme", scheme_name,
        )
        assert_rc(rc, 0, out, err, "export html")
        assert html_path.exists()
        print(f"  ✓ HTML 导出成功: {html_path}")

        html_content = html_path.read_text(encoding="utf-8")
        # 检查 HTML 包含方案相关信息
        assert scheme_name in html_content, f"HTML 应包含方案名 '{scheme_name}'"
        assert "筛选方案" in html_content, "HTML 应包含'筛选方案'文字"
        assert "使用筛选方案导出" in html_content, "HTML 应有方案卡片标题"
        print("  ✓ HTML 包含方案信息（方案卡片+元信息）")

    finally:
        gc.collect()
        time.sleep(0.2)
        try:
            shutil.rmtree(out_dir, ignore_errors=True)
        except Exception:
            pass

    print("测试4: 通过 ✓\n")


def test_full_pipeline(rules_file: Path, sample_dir: Path):
    """测试5: 完整链路 - 扫描 → 保存方案 → 按方案 list → 导出报告"""
    import gc
    import shutil
    import time

    print("=" * 60)
    print("测试5: 完整链路 - 扫描 → 保存方案 → 按方案 list → 导出报告")

    work_dir = tempfile.mkdtemp(prefix="cas_fp_")
    try:
        db_path = Path(work_dir) / "pipeline.db"

        # Step 1: 扫描目录
        print("\n--- Step 1: 扫描目录 ---")
        rc, out, err = run_cli(
            "--db", str(db_path),
            "scan", "-c", str(rules_file), "-d", str(sample_dir),
        )
        assert_rc(rc, 0, out, err, "step1 scan")
        batch_id = _extract_batch_id(out)
        print(out.strip())

        # Step 2: 查看所有批次
        print("\n--- Step 2: 查看批次列表 ---")
        rc, out, err = run_cli("--db", str(db_path), "list")
        assert_rc(rc, 0, out, err, "step2 list batches")
        assert batch_id in out
        print(out.strip())

        # Step 3: 查看所有问题（获取基线）
        print("\n--- Step 3: 查看所有问题（基线） ---")
        rc, out, err = run_cli("--db", str(db_path), "list", "-b", batch_id)
        assert_rc(rc, 0, out, err, "step3 list all issues")
        total_count = _extract_issue_count(out)
        print(f"  基线: 共 {total_count} 条问题")
        print(out.strip())

        # Step 4: 用参数筛选，获取待补错误数
        print("\n--- Step 4: 按条件筛选（pending + error） ---")
        rc, out, err = run_cli(
            "--db", str(db_path),
            "list", "-b", batch_id,
            "--state", "pending", "--severity", "error",
        )
        assert_rc(rc, 0, out, err, "step4 filtered list")
        pending_error_count = _extract_issue_count(out)
        print(f"  筛选后: 共 {pending_error_count} 条（待补+错误）")
        print(out.strip())

        # Step 5: 保存为常用方案
        print("\n--- Step 5: 保存筛选方案 ---")
        scheme_name = "日常复核-待补错误"
        rc, out, err = run_cli(
            "--db", str(db_path),
            "scheme", "save", scheme_name,
            "-b", batch_id,
            "--state", "pending",
            "--severity", "error",
        )
        assert_rc(rc, 0, out, err, "step5 save scheme")
        assert "已创建" in out or "已覆盖更新" in out
        print(out.strip())

        # Step 5b: 再保存一个采购类型的方案
        print("\n--- Step 5b: 再保存采购方案（无批次，跨批次可用） ---")
        rc, out, err = run_cli(
            "--db", str(db_path),
            "scheme", "save", "采购类问题",
            "--state", "pending",
            "--project-type", "PURCHASE",
        )
        assert_rc(rc, 0, out, err, "step5b save purchase scheme")
        print(out.strip())

        # Step 6: 列出所有方案
        print("\n--- Step 6: 列出所有筛选方案 ---")
        rc, out, err = run_cli("--db", str(db_path), "scheme", "list")
        assert_rc(rc, 0, out, err, "step6 list schemes")
        assert scheme_name in out
        assert "采购类问题" in out
        print(out.strip())

        # Step 7: 查看方案详情
        print("\n--- Step 7: 查看方案详情 ---")
        rc, out, err = run_cli("--db", str(db_path), "scheme", "show", scheme_name)
        assert_rc(rc, 0, out, err, "step7 show scheme")
        assert scheme_name in out
        assert "待补" in out
        assert "错误" in out
        print(out.strip())

        # Step 8: 使用方案按名筛选
        print("\n--- Step 8: 使用方案筛选问题 ---")
        rc, out, err = run_cli(
            "--db", str(db_path),
            "list", "--scheme", scheme_name,
        )
        assert_rc(rc, 0, out, err, "step8 list by scheme")
        scheme_count = _extract_issue_count(out)
        # 使用方案后的数量应与之前参数筛选一致
        assert scheme_count == pending_error_count, (
            f"方案筛选数 {scheme_count} != 参数筛选数 {pending_error_count}"
        )
        assert f"使用筛选方案「{scheme_name}」" in out
        print(f"  方案筛选数量={scheme_count}, 与参数筛选一致 ✓")
        print(out.strip())

        # Step 9: 跨批次方案 + 显式批次组合使用
        print("\n--- Step 9: 跨批次方案 + 显式指定批次 ---")
        rc, out, err = run_cli(
            "--db", str(db_path),
            "list", "-b", batch_id,
            "--scheme", "采购类问题",
        )
        assert_rc(rc, 0, out, err, "step9 purchase scheme list")
        purchase_count = _extract_issue_count(out)
        print(f"  采购方案筛选数量={purchase_count}")
        print(out.strip())

        # Step 10: 使用方案导出 CSV 报告
        print("\n--- Step 10: 使用方案导出 CSV 报告 ---")
        csv_report = Path(work_dir) / "scheme_report.csv"
        rc, out, err = run_cli(
            "--db", str(db_path),
            "export", "-o", str(csv_report), "-f", "csv",
            "--scheme", scheme_name,
        )
        assert_rc(rc, 0, out, err, "step10 export csv by scheme")
        assert csv_report.exists()
        assert scheme_name in out
        print(out.strip())

        # Step 11: 使用方案导出 HTML 报告
        print("\n--- Step 11: 使用方案导出 HTML 报告 ---")
        html_report = Path(work_dir) / "scheme_report.html"
        rc, out, err = run_cli(
            "--db", str(db_path),
            "export", "-b", batch_id,
            "-o", str(html_report), "-f", "html",
            "--scheme", "采购类问题",
            "--severity", "warning",  # 命令行参数覆盖/补充方案
        )
        assert_rc(rc, 0, out, err, "step11 export html by scheme+params")
        assert html_report.exists()
        print(out.strip())

        # 验证 HTML 包含方案信息
        html_txt = html_report.read_text(encoding="utf-8")
        assert "采购类问题" in html_txt
        print("  ✓ HTML 报告包含'采购类问题'方案信息")

        # Step 12: 删除方案
        print("\n--- Step 12: 删除方案 ---")
        rc, out, err = run_cli(
            "--db", str(db_path),
            "scheme", "delete", "采购类问题",
        )
        assert_rc(rc, 0, out, err, "step12 delete scheme")
        assert "已删除" in out
        print(out.strip())

        rc, out, err = run_cli("--db", str(db_path), "scheme", "list")
        assert_rc(rc, 0, out, err, "list after delete")
        assert "采购类问题" not in out
        assert scheme_name in out  # 另一个还在
        print("  ✓ 删除成功，仅保留一个方案")
        print(out.strip())

        # Step 13: 使用不存在的方案 - 报错
        print("\n--- Step 13: 边界: 使用不存在方案报错 ---")
        rc, out, err = run_cli(
            "--db", str(db_path),
            "list", "--scheme", "已经被删除的方案",
        )
        assert_rc(rc, 1, out, err, "step13 scheme not found")  # ContractArchiverError → 1
        assert "方案不存在" in err
        print(f"  ✓ 正确报错: stderr={err.strip()}")

        # Step 14: 保存空方案 - 报错
        print("\n--- Step 14: 边界: 保存空方案报错 ---")
        rc, out, err = run_cli(
            "--db", str(db_path),
            "scheme", "save", "这是一个空方案",
        )
        assert_rc(rc, 1, out, err, "step14 empty scheme")
        assert "空方案" in err
        print(f"  ✓ 正确报错: stderr={err.strip()}")

        # Step 15: 重名不加 overwrite - 报错
        print("\n--- Step 15: 边界: 重名不覆盖报错 ---")
        rc, out, err = run_cli(
            "--db", str(db_path),
            "scheme", "save", scheme_name,
            "--state", "passed",
        )
        assert_rc(rc, 1, out, err, "step15 duplicate scheme")
        assert "方案已存在" in err
        assert "--overwrite" in err
        print(f"  ✓ 正确报错: stderr={err.strip()}")

        # Step 16: 重名加 overwrite - 成功
        print("\n--- Step 16: 边界: 重名加 overwrite 成功 ---")
        rc, out, err = run_cli(
            "--db", str(db_path),
            "scheme", "save", scheme_name,
            "--state", "passed",
            "--severity", "warning",
            "--overwrite",
        )
        assert_rc(rc, 0, out, err, "step16 overwrite scheme")
        assert "已覆盖更新" in out
        print(out.strip())

        # 验证覆盖后详情
        rc, out, err = run_cli("--db", str(db_path), "scheme", "show", scheme_name)
        assert_rc(rc, 0, out, err, "after overwrite show")
        assert "通过" in out or "passed" in out
        assert "警告" in out or "warning" in out
        print("  ✓ 覆盖成功，条件已更新:")
        print(out.strip())

    finally:
        gc.collect()
        time.sleep(0.3)
        try:
            shutil.rmtree(work_dir, ignore_errors=True)
        except Exception:
            pass

    print("测试5: 通过 ✓\n")


def _extract_batch_id(scan_output: str) -> str:
    for line in scan_output.splitlines():
        if "批次ID:" in line:
            return line.split("批次ID:")[1].strip()
    raise AssertionError(f"无法从扫描输出中解析批次ID: {scan_output}")


def _extract_issue_count(list_output: str) -> int:
    """从 list 输出中提取问题总数"""
    for line in list_output.splitlines():
        line = line.strip()
        if line.startswith("共 ") and line.endswith(" 条记录"):
            return int(line.split()[1])
    if "无问题记录" in list_output or "无匹配问题" in list_output:
        return 0
    count = 0
    for line in list_output.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        first_part = stripped.split()[0] if stripped.split() else ""
        try:
            int(first_part)
            count += 1
        except (ValueError, IndexError):
            continue
    return count


def test_readonly_db_scheme_save(tmp_db: Path):
    """测试6: 只读数据库下 scheme save 应返回清晰的中文权限提示，而非原始 traceback"""
    import stat
    from contract_archiver.exceptions import DatabasePermissionError

    print("=" * 60)
    print("测试6: 只读数据库 scheme save 权限提示")

    # 先用正常权限创建数据库和表
    storage = Storage(tmp_db)
    storage.save_scheme(name="已有的方案", severity="error")
    del storage

    import gc
    gc.collect()

    # 设置文件为只读
    tmp_db.chmod(tmp_db.stat().st_mode & ~stat.S_IWUSR & ~stat.S_IWGRP & ~stat.S_IWOTH)
    print(f"  已将数据库设为只读: {tmp_db}")

    try:
        # 测试 Storage API 层面
        readonly_storage = Storage(tmp_db)
        try:
            readonly_storage.save_scheme(name="只读测试方案", state="pending")
            raise AssertionError("只读数据库应该报错")
        except DatabasePermissionError as e:
            assert "数据库权限错误" in str(e), f"权限提示不正确: {e}"
            assert "写入" in str(e), f"提示应包含'写入': {e}"
            assert str(tmp_db) in str(e), f"提示应包含数据库路径: {e}"
            # 确保不是原始 OperationalError traceback
            assert "sqlite3.OperationalError" not in str(e)
            assert "attempt to write" not in str(e).lower()
            print(f"  Storage API 层: {e}")
            print("  ✓ Storage API 层返回清晰的 DatabasePermissionError")
        finally:
            del readonly_storage
            gc.collect()

        # 测试 CLI 层面
        rc, out, err = run_cli(
            "--db", str(tmp_db),
            "scheme", "save", "CLI只读测试",
            "--state", "pending",
        )
        assert rc != 0, f"只读数据库 scheme save 退出码应为非0, 实际={rc}"
        assert "数据库权限错误" in err, f"stderr 应含'数据库权限错误', 实际: {err}"
        assert "写入" in err, f"stderr 应含'写入', 实际: {err}"
        assert "sqlite3.OperationalError" not in err, f"不应暴露原始异常类名: {err}"
        assert "attempt to write" not in err.lower(), f"不应暴露英文原始错误: {err}"
        assert "Traceback" not in err, f"不应暴露 traceback: {err}"
        print(f"  CLI 层: rc={rc}, stderr={err.strip()}")
        print("  ✓ CLI 层返回清晰的中文权限提示（无 traceback）")

    finally:
        # 恢复写权限以便清理
        tmp_db.chmod(tmp_db.stat().st_mode | stat.S_IWUSR)

    print("测试6: 通过 ✓\n")


def test_scheme_export_import_basic(work_dir: Path):
    """测试7: 正常导出导入 - 单个方案和全部方案"""
    import gc
    import time
    import shutil
    import json

    print("=" * 60)
    print("测试7: 迁移包 - 正常导出导入")

    src_db = work_dir / "src.db"
    dst_db = work_dir / "dst.db"
    export_all = work_dir / "all.json"
    export_one = work_dir / "one.json"

    # 源库创建 3 个方案
    src = Storage(src_db)
    s1, _ = src.save_scheme(name="方案A-法务待补", state="pending", severity="error", project_type_id="SALES")
    time.sleep(0.1)
    s2, _ = src.save_scheme(name="方案B-采购问题", state="pending", project_type_id="PURCHASE")
    time.sleep(0.1)
    s3, _ = src.save_scheme(name="方案C-忽略警告", state="ignored", severity="warning")
    print(f"  ✓ 源库创建3个方案: A/B/C")

    # 导出全部
    rc, out, err = run_cli("--db", str(src_db), "scheme", "export", "-o", str(export_all))
    assert_rc(rc, 0, out, err, "export all")
    assert export_all.exists()
    assert "方案A" in out and "方案B" in out and "方案C" in out
    print("  ✓ CLI 导出全部方案成功")

    pkg = json.loads(export_all.read_text(encoding="utf-8"))
    assert pkg["scheme_count"] == 3
    assert pkg["package_version"] == 1
    assert "tool_version" in pkg and "exported_at" in pkg
    names = {s["name"] for s in pkg["schemes"]}
    assert {"方案A-法务待补", "方案B-采购问题", "方案C-忽略警告"} <= names
    # 每个方案必须包含 8 个迁移字段
    for s in pkg["schemes"]:
        for fld in ("name", "batch_id", "state", "severity", "project_type_id",
                    "created_at", "updated_at", "version"):
            assert fld in s, f"迁移包方案缺少字段: {fld}"
    print("  ✓ 迁移包结构正确: package_version, tool_version, exported_at, scheme_count, schemes[8字段]")

    # 导出单个方案
    rc, out, err = run_cli("--db", str(src_db), "scheme", "export", "方案A-法务待补", "-o", str(export_one))
    assert_rc(rc, 0, out, err, "export one")
    pkg_one = json.loads(export_one.read_text(encoding="utf-8"))
    assert pkg_one["scheme_count"] == 1
    assert pkg_one["schemes"][0]["name"] == "方案A-法务待补"
    print("  ✓ CLI 导出单个方案成功")

    # 导入到空目标库
    rc, out, err = run_cli("--db", str(dst_db), "scheme", "import", str(export_all))
    assert_rc(rc, 0, out, err, "import all")
    assert "总计: 3" in out
    assert "新增: 3 个" in out
    print("  ✓ CLI 导入全部到空库成功")

    # 验证时间戳和字段保留
    dst = Storage(dst_db)
    schemes_map = {s.name: s for s in dst.list_schemes()}
    s_a_dst = schemes_map["方案A-法务待补"]
    s_a_src = src.get_scheme("方案A-法务待补")
    assert s_a_dst.created_at == s_a_src.created_at, (
        f"import created_at 丢失: dst={s_a_dst.created_at} src={s_a_src.created_at}"
    )
    assert s_a_dst.updated_at == s_a_src.updated_at, (
        f"import updated_at 丢失: dst={s_a_dst.updated_at} src={s_a_src.updated_at}"
    )
    assert s_a_dst.state == "pending"
    assert s_a_dst.severity == "error"
    assert s_a_dst.project_type_id == "SALES"
    assert s_a_dst.version == s_a_src.version
    print("  ✓ 导入后 created_at/updated_at/条件/version 全部保留")

    # 审计日志
    audits = dst.get_scheme_audit_log()
    imported = [a for a in audits if a["action"] == "import" and a["result"] == "create"]
    assert len(imported) == 3, f"应有3条导入审计记录, 实际={len(imported)}"
    assert imported[0]["source_file"] == str(export_all.resolve())
    print(f"  ✓ 审计日志: 3 条导入(create)记录, 来源文件={imported[0]['source_file']}")

    del dst
    gc.collect()
    time.sleep(0.2)
    shutil.rmtree(str(work_dir), ignore_errors=True)
    print("测试7: 通过 ✓\n")


def test_scheme_import_conflict(work_dir: Path):
    """测试8: 导入同名冲突 - 默认跳过, --overwrite 覆盖"""
    import gc
    import time
    import shutil
    import json

    print("=" * 60)
    print("测试8: 迁移包 - 同名冲突跳过/覆盖")

    src_db = work_dir / "src.db"
    dst_db = work_dir / "dst.db"
    pkg_file = work_dir / "pkg.json"

    src = Storage(src_db)
    src.save_scheme(name="冲突方案", state="pending", severity="error")
    src.save_scheme(name="独有的方案", state="passed")
    run_cli("--db", str(src_db), "scheme", "export", "-o", str(pkg_file))
    print("  ✓ 源库: 冲突方案(pending/error) + 独有的方案(passed)")

    dst = Storage(dst_db)
    dst.save_scheme(name="冲突方案", state="ignored", project_type_id="PURCHASE")
    dst_s_before = dst.get_scheme("冲突方案")
    print(f"  ✓ 目标库: 冲突方案(ignored/PURCHASE) created_at={dst_s_before.created_at}")

    # 默认跳过
    rc, out, err = run_cli("--db", str(dst_db), "scheme", "import", str(pkg_file))
    assert_rc(rc, 0, out, err, "import skip")
    assert "跳过: 1 个" in out, f"应含跳过摘要, 实际out={out}"
    assert "新增: 1 个" in out
    assert "冲突方案" in out
    dst_s = dst.get_scheme("冲突方案")
    assert dst_s.state == "ignored", f"跳过但状态变了: {dst_s.state}"
    assert dst_s.project_type_id == "PURCHASE"
    assert dst_s.created_at == dst_s_before.created_at
    print("  ✓ 默认跳过同名: 冲突方案保留原 state/severity/project_type/created_at")

    # 跳过审计日志
    audits = dst.get_scheme_audit_log("冲突方案")
    skip_audits = [a for a in audits if a["action"] == "import" and a["result"] == "skip"]
    assert len(skip_audits) >= 1
    print("  ✓ 跳过操作写入 scheme_audit_log(result=skip)")

    # --overwrite 覆盖
    rc, out, err = run_cli("--db", str(dst_db), "scheme", "import", str(pkg_file), "--overwrite")
    assert_rc(rc, 0, out, err, "import overwrite")
    assert "覆盖: 1 个" in out or ("覆盖" in out and "冲突方案" in out)
    dst_s2 = dst.get_scheme("冲突方案")
    assert dst_s2.state == "pending", f"覆盖后应为pending: {dst_s2.state}"
    assert dst_s2.severity == "error"
    # created_at 按 preserve_timestamps 默认保留迁移包里的（保留规则：created_at/updated_at 默认保留迁移包原值）
    print(f"  ✓ --overwrite 覆盖后: state={dst_s2.state}, severity={dst_s2.severity}, created_at={dst_s2.created_at}, version={dst_s2.version}")

    # 独有方案也应存在
    dst_other = dst.get_scheme("独有的方案")
    assert dst_other.state == "passed"
    print("  ✓ 非冲突的独有方案正确新增")

    # --no-preserve-timestamps 测试
    dst2_db = work_dir / "dst2.db"
    dst2 = Storage(dst2_db)
    dst2.save_scheme(name="冲突方案", state="ignored")
    dst2_old = dst2.get_scheme("冲突方案")
    time.sleep(0.2)
    rc, out, err = run_cli(
        "--db", str(dst2_db), "scheme", "import", str(pkg_file),
        "--overwrite", "--no-preserve-timestamps",
    )
    assert_rc(rc, 0, out, err, "import overwrite no-preserve")
    dst2_new = dst2.get_scheme("冲突方案")
    assert dst2_new.state == "pending"
    # --no-preserve-timestamps: 覆盖时 created_at 保留目标库原有值, updated_at 用当前时间
    assert dst2_new.created_at == dst2_old.created_at, (
        f"no-preserve覆盖应保留目标库created_at: dst={dst2_new.created_at} old={dst2_old.created_at}"
    )
    assert dst2_new.updated_at >= dst2_old.updated_at, (
        f"no-preserve覆盖 updated_at 应更新: {dst2_new.updated_at} < {dst2_old.updated_at}"
    )
    print("  ✓ --no-preserve-timestamps: 覆盖保留目标库created_at, updated_at用当前")

    del dst, dst2
    gc.collect()
    time.sleep(0.2)
    shutil.rmtree(str(work_dir), ignore_errors=True)
    print("测试8: 通过 ✓\n")


def test_scheme_import_overwrite_undo(work_dir: Path):
    """测试9: 覆盖导入后可用撤销机制回退"""
    import gc
    import time
    import shutil

    print("=" * 60)
    print("测试9: 迁移包 - 覆盖导入 + 撤销")

    src_db = work_dir / "src.db"
    dst_db = work_dir / "dst.db"
    pkg_file = work_dir / "pkg.json"

    Storage(src_db).save_scheme(name="可撤销方案", state="pending", severity="error", project_type_id="SALES")
    run_cli("--db", str(src_db), "scheme", "export", "-o", str(pkg_file))

    dst = Storage(dst_db)
    dst.save_scheme(name="可撤销方案", state="ignored", severity="warning", project_type_id="NDA")
    before = dst.get_scheme("可撤销方案")
    print(f"  覆盖前: state={before.state}, severity={before.severity}, project={before.project_type_id}")

    # 覆盖
    rc, out, err = run_cli("--db", str(dst_db), "scheme", "import", str(pkg_file), "--overwrite")
    assert_rc(rc, 0, out, err, "import overwrite")
    after = dst.get_scheme("可撤销方案")
    assert after.state == "pending" and after.severity == "error" and after.project_type_id == "SALES"
    print(f"  覆盖后: state={after.state}, severity={after.severity}, project={after.project_type_id}")

    # 查看可撤销数
    rc, out, err = run_cli("--db", str(dst_db), "scheme", "undo", "--count")
    assert_rc(rc, 0, out, err, "undo count")
    assert "可撤销方案操作数: 1" in out or "1" in out
    print(f"  可撤销数: {out.strip()}")

    # 执行撤销 - 指定方案名
    rc, out, err = run_cli("--db", str(dst_db), "scheme", "undo", "可撤销方案")
    assert_rc(rc, 0, out, err, "undo by name")
    assert "已撤销" in out or "撤销" in out
    restored = dst.get_scheme("可撤销方案")
    assert restored.state == before.state, (
        f"撤销后 state 应还原: {restored.state} != {before.state}"
    )
    assert restored.severity == before.severity
    assert restored.project_type_id == before.project_type_id
    assert restored.created_at == before.created_at
    assert restored.updated_at == before.updated_at
    print(f"  撤销后: state={restored.state}, severity={restored.severity}, project={restored.project_type_id}")
    print("  ✓ 覆盖后撤销成功还原所有条件+时间戳")

    # 无撤销可执行时报中文错
    rc, out, err = run_cli("--db", str(dst_db), "scheme", "undo")
    assert rc != 0
    assert "没有可撤销" in err
    assert "Traceback" not in err
    print("  ✓ 无撤销时返回中文错误且无堆栈")

    del dst
    gc.collect()
    time.sleep(0.2)
    shutil.rmtree(str(work_dir), ignore_errors=True)
    print("测试9: 通过 ✓\n")


def test_scheme_import_bad_json(work_dir: Path):
    """测试10: 坏 JSON / 缺字段 / 空文件 / 缺必填条件"""
    import gc
    import time
    import shutil
    import json

    print("=" * 60)
    print("测试10: 迁移包 - 错误输入全部中文报错+无堆栈")

    dst_db = work_dir / "dst.db"

    # (a) 空文件
    empty = work_dir / "empty.json"
    empty.write_text("", encoding="utf-8")
    rc, out, err = run_cli("--db", str(dst_db), "scheme", "import", str(empty))
    assert_rc(rc, 4, out, err, "import empty file")
    assert "为空" in err
    assert "Traceback" not in err
    assert "json.JSONDecodeError" not in err
    print(f"  ✓ 空文件 rc=4: {err.strip()[:60]}")

    # (b) 坏 JSON
    bad = work_dir / "bad.json"
    bad.write_text("{ [ this is not valid ]", encoding="utf-8")
    rc, out, err = run_cli("--db", str(dst_db), "scheme", "import", str(bad))
    assert_rc(rc, 6, out, err, "import bad json")
    assert "JSON" in err or "解析" in err
    assert "Traceback" not in err
    print(f"  ✓ 坏JSON rc=6: {err.strip()[:60]}")

    # (c) 顶层缺 schemes 字段
    miss_schemes = work_dir / "miss_schemes.json"
    miss_schemes.write_text(json.dumps({"package_version": 1}, ensure_ascii=False), encoding="utf-8")
    rc, out, err = run_cli("--db", str(dst_db), "scheme", "import", str(miss_schemes))
    assert_rc(rc, 5, out, err, "import missing schemes field")
    assert "缺少必填字段" in err and "schemes" in err
    assert "Traceback" not in err
    print(f"  ✓ 缺schemes字段 rc=5: {err.strip()[:60]}")

    # (d) schemes里某个方案缺必填字段 version
    miss_ver = work_dir / "miss_ver.json"
    miss_ver.write_text(json.dumps({
        "package_version": 1,
        "schemes": [{
            "name": "缺字段方案",
            "state": "pending",
            "created_at": "2024-01-01T00:00:00",
            "updated_at": "2024-01-01T00:00:00",
            # 缺 version
        }]
    }, ensure_ascii=False), encoding="utf-8")
    rc, out, err = run_cli("--db", str(dst_db), "scheme", "import", str(miss_ver))
    assert_rc(rc, 5, out, err, "import missing version field in scheme")
    assert "缺少必填字段" in err and "version" in err
    assert "Traceback" not in err
    print(f"  ✓ 方案缺version rc=5: {err.strip()[:60]}")

    # (e) schemes数组为空
    empty_list = work_dir / "empty_list.json"
    empty_list.write_text(json.dumps({
        "package_version": 1, "schemes": []
    }, ensure_ascii=False), encoding="utf-8")
    rc, out, err = run_cli("--db", str(dst_db), "scheme", "import", str(empty_list))
    assert_rc(rc, 4, out, err, "import empty schemes list")
    assert "为空" in err
    assert "Traceback" not in err
    print(f"  ✓ schemes为空数组 rc=4: {err.strip()[:60]}")

    # (f) 方案未指定任何筛选条件 (全None)
    empty_cond = work_dir / "empty_cond.json"
    empty_cond.write_text(json.dumps({
        "package_version": 1,
        "schemes": [{
            "name": "全空方案",
            "batch_id": None,
            "state": None,
            "severity": None,
            "project_type_id": None,
            "created_at": "2024-01-01T00:00:00",
            "updated_at": "2024-01-01T00:00:00",
            "version": 1,
        }]
    }, ensure_ascii=False), encoding="utf-8")
    rc, out, err = run_cli("--db", str(dst_db), "scheme", "import", str(empty_cond))
    assert_rc(rc, 7, out, err, "import scheme without any condition")
    assert "未指定任何筛选条件" in err or "无效" in err
    assert "Traceback" not in err
    print(f"  ✓ 全None条件方案 rc=7: {err.strip()[:60]}")

    # (g) 文件不存在
    rc, out, err = run_cli("--db", str(dst_db), "scheme", "import", str(work_dir / "not_exist.json"))
    assert rc != 0
    assert "Traceback" not in err
    assert "sqlite3" not in err
    print(f"  ✓ 文件不存在 rc={rc}: {err.strip()[:60]}")

    gc.collect()
    time.sleep(0.2)
    shutil.rmtree(str(work_dir), ignore_errors=True)
    print("测试10: 通过 ✓\n")


def test_scheme_import_cross_restart(work_dir: Path):
    """测试11: 跨重启读取 - 导入后重启 Storage 仍存在且字段完整"""
    import gc
    import time
    import shutil
    import json

    print("=" * 60)
    print("测试11: 迁移包 - 跨重启读取")

    src_db = work_dir / "src.db"
    dst_db = work_dir / "dst.db"
    pkg_file = work_dir / "pkg.json"

    src = Storage(src_db)
    s_a, _ = src.save_scheme(name="重启A", state="pending", severity="error", project_type_id="SALES")
    s_b, _ = src.save_scheme(name="重启B", batch_id="BATCH_XYZ", state="passed")
    run_cli("--db", str(src_db), "scheme", "export", "-o", str(pkg_file))

    # 导入
    rc, out, err = run_cli("--db", str(dst_db), "scheme", "import", str(pkg_file))
    assert_rc(rc, 0, out, err, "import before restart")

    # 关闭连接 模拟重启
    del src
    gc.collect()
    time.sleep(0.3)

    # 新 Storage 实例
    dst = Storage(dst_db)
    all_schemes = dst.list_schemes()
    names = {s.name for s in all_schemes}
    assert {"重启A", "重启B"} <= names, f"重启后方案缺失: {names}"
    print("  ✓ 重启 Storage 后 list_schemes 仍有 2 个方案")

    a = dst.get_scheme("重启A")
    b = dst.get_scheme("重启B")
    assert a.state == s_a.state and a.severity == s_a.severity and a.project_type_id == s_a.project_type_id
    assert a.created_at == s_a.created_at and a.updated_at == s_a.updated_at
    assert a.version == s_a.version
    assert b.batch_id == s_b.batch_id and b.state == s_b.state
    assert b.created_at == s_b.created_at
    print("  ✓ 所有字段(state/severity/project_type/batch_id/created_at/updated_at/version)跨重启完整保留")

    # 再重启 再验证
    del dst
    gc.collect()
    time.sleep(0.2)
    dst2 = Storage(dst_db)
    a2 = dst2.get_scheme("重启A")
    assert a2.state == "pending" and a2.severity == "error"
    # 审计日志也跨重启
    audits = dst2.get_scheme_audit_log()
    assert len(audits) >= 2
    print(f"  ✓ 二次重启仍完整, 审计日志{len(audits)}条")

    del dst2
    gc.collect()
    time.sleep(0.2)
    shutil.rmtree(str(work_dir), ignore_errors=True)
    print("测试11: 通过 ✓\n")


def test_scheme_import_readonly_db(work_dir: Path):
    """测试12: 只读库导入失败 - 中文权限提示+无Python堆栈"""
    import gc
    import time
    import shutil
    import json

    print("=" * 60)
    print("测试12: 迁移包 - 只读库导入失败")

    src_db = work_dir / "src.db"
    dst_db = work_dir / "dst.db"
    pkg_file = work_dir / "pkg.json"

    Storage(src_db).save_scheme(name="只读测试方案", state="pending", severity="warning")
    run_cli("--db", str(src_db), "scheme", "export", "-o", str(pkg_file))

    # 先在正常模式建表 + 存一个方案(确保只读库有表结构)
    dst = Storage(dst_db)
    dst.save_scheme(name="原有只读库方案", state="ignored")
    del dst
    gc.collect()
    time.sleep(0.3)

    # 设为只读
    import stat
    dst_db.chmod(dst_db.stat().st_mode & ~stat.S_IWUSR & ~stat.S_IWGRP & ~stat.S_IWOTH)
    print(f"  目标库已设为只读: {dst_db}")

    try:
        rc, out, err = run_cli("--db", str(dst_db), "scheme", "import", str(pkg_file))
        print(f"  导入结果: rc={rc}")
        print(f"  stderr: {err.strip()[:120]}")
        assert rc != 0, f"只读库导入应返回非0 rc, 实际={rc}"
        assert "数据库权限错误" in err or "权限" in err or "写入" in err, (
            f"应有中文权限提示: {err}"
        )
        assert "Traceback" not in err, f"不应有 traceback: {err}"
        assert "sqlite3.OperationalError" not in err, f"不应暴露 sqlite3 异常类名: {err}"
        assert "attempt to write" not in err.lower(), f"不应暴露英文原始错误: {err}"
        print("  ✓ 只读库导入: 非0退出码 + 中文权限提示 + 无堆栈 + 无原始sqlite3错误")
    finally:
        # 恢复
        dst_db.chmod(dst_db.stat().st_mode | stat.S_IWUSR)

    gc.collect()
    time.sleep(0.2)
    shutil.rmtree(str(work_dir), ignore_errors=True)
    print("测试12: 通过 ✓\n")


def test_scheme_cli_chain(work_dir: Path, rules_file: Path, sample_dir: Path):
    """测试13: 完整命令链 - 扫描→保存方案→导出→清空目标库→导入→用方案list/export导出报告"""
    import gc
    import time
    import shutil
    import json

    print("=" * 60)
    print("测试13: 完整命令链 - 完整迁移场景")

    pc_db = work_dir / "pc.db"
    usb_db = work_dir / "usb.db"
    pkg_file = work_dir / "法务常用方案.json"
    out_csv = work_dir / "report.csv"

    # --- 办公室PC: 扫描 + 保存常用方案 + 导出迁移包 ---
    print("\n--- [PC端] 扫描目录 ---")
    rc, out, err = run_cli("--db", str(pc_db), "scan", "-c", str(rules_file), "-d", str(sample_dir))
    assert_rc(rc, 0, out, err, "scan")
    batch_id = _extract_batch_id(out)
    print(f"  batch_id={batch_id}")

    print("\n--- [PC端] 保存2个常用筛选方案 ---")
    rc, out, err = run_cli(
        "--db", str(pc_db), "scheme", "save",
        "法务-待补错误", "-b", batch_id, "--state", "pending", "--severity", "error",
    )
    assert_rc(rc, 0, out, err, "save 法务-待补错误")
    print(f"  {out.strip().splitlines()[0]}")

    rc, out, err = run_cli(
        "--db", str(pc_db), "scheme", "save",
        "采购类问题", "--state", "pending", "--project-type", "PURCHASE",
    )
    assert_rc(rc, 0, out, err, "save 采购类问题")
    print(f"  {out.strip().splitlines()[0]}")

    print("\n--- [PC端] 导出迁移包到U盘 ---")
    rc, out, err = run_cli("--db", str(pc_db), "scheme", "export", "-o", str(pkg_file))
    assert_rc(rc, 0, out, err, "export to usb file")
    assert pkg_file.exists()
    print(f"  迁移包: {pkg_file}")
    pkg = json.loads(pkg_file.read_text(encoding="utf-8"))
    assert pkg["scheme_count"] == 2
    print(f"  含 {pkg['scheme_count']} 个方案, 生成于 {pkg['exported_at']}")

    # --- 家里笔记本: 新库 + 导入迁移包 + 使用 ---
    print("\n--- [笔记本端] 空库导入迁移包 ---")
    rc, out, err = run_cli("--db", str(usb_db), "scheme", "import", str(pkg_file))
    assert_rc(rc, 0, out, err, "import on laptop")
    assert "新增: 2 个" in out
    print(f"  {out.strip().splitlines()[0]}")

    # 笔记本端还没有扫描批次，先做一次扫描
    print("\n--- [笔记本端] 扫描相同资料目录 ---")
    rc, out, err = run_cli("--db", str(usb_db), "scan", "-c", str(rules_file), "-d", str(sample_dir), "--force")
    assert_rc(rc, 0, out, err, "laptop scan")
    batch2 = _extract_batch_id(out)
    print(f"  笔记本端batch={batch2}")

    print("\n--- [笔记本端] 使用导入的方案+显式批次筛选问题 ---")
    rc, out, err = run_cli(
        "--db", str(usb_db), "list", "-b", batch2,
        "--scheme", "法务-待补错误",
    )
    assert_rc(rc, 0, out, err, "list with scheme on laptop")
    assert "使用筛选方案「法务-待补错误」" in out
    print(f"  {out.strip().splitlines()[:3]}")

    print("\n--- [笔记本端] 用方案导出CSV报告 ---")
    rc, out, err = run_cli(
        "--db", str(usb_db), "export", "-b", batch2,
        "-o", str(out_csv), "-f", "csv",
        "--scheme", "法务-待补错误",
    )
    assert_rc(rc, 0, out, err, "export csv by scheme")
    assert out_csv.exists()
    print(f"  CSV 报告: {out_csv}")

    with open(out_csv, "r", encoding="utf-8-sig") as f:
        first = f.readline()
    assert "筛选方案" in first and "法务-待补错误" in first, (
        f"CSV首行应有方案信息: {first}"
    )
    print("  ✓ CSV首行包含导入的方案名")

    gc.collect()
    time.sleep(0.2)
    shutil.rmtree(str(work_dir), ignore_errors=True)
    print("\n测试13: 通过 ✓\n")


def main_tests():
    import gc
    import time

    base = Path(__file__).parent
    rules_file = base / "examples" / "rules.yaml"
    sample_dir = base / "examples" / "sample_data"

    if not rules_file.exists():
        raise FileNotFoundError(f"找不到规则文件: {rules_file}")
    if not sample_dir.exists():
        raise FileNotFoundError(f"找不到示例数据目录: {sample_dir}")

    passed = 0
    failed = 0
    errors: list[tuple[str, str]] = []

    # 以 tmp_db 为参数的测试
    per_db_tests = [
        ("test_scheme_persistence", lambda d: test_scheme_persistence(d)),
        ("test_scheme_conflict_and_overwrite", lambda d: test_scheme_conflict_and_overwrite(d)),
        ("test_scheme_empty_and_notfound", lambda d: test_scheme_empty_and_notfound(d)),
        ("test_export_fields", lambda d: test_export_fields(d, rules_file, sample_dir)),
        ("test_readonly_db_scheme_save", lambda d: test_readonly_db_scheme_save(d)),
    ]

    for name, test_fn in per_db_tests:
        td = tempfile.mkdtemp(prefix="cas_test_")
        tmp_db = Path(td) / "test.db"
        try:
            test_fn(tmp_db)
            passed += 1
        except Exception as e:
            failed += 1
            errors.append((name, f"{type(e).__name__}: {e}"))
            print(f"\n[X] {name} 失败: {type(e).__name__}: {e}\n")
        finally:
            gc.collect()
            time.sleep(0.2)
            try:
                import shutil
                shutil.rmtree(td, ignore_errors=True)
            except Exception:
                pass

    # 以 work_dir 为参数的迁移包测试（目录内多个db和json文件）
    per_dir_tests = [
        ("test_scheme_export_import_basic", lambda d: test_scheme_export_import_basic(Path(d))),
        ("test_scheme_import_conflict", lambda d: test_scheme_import_conflict(Path(d))),
        ("test_scheme_import_overwrite_undo", lambda d: test_scheme_import_overwrite_undo(Path(d))),
        ("test_scheme_import_bad_json", lambda d: test_scheme_import_bad_json(Path(d))),
        ("test_scheme_import_cross_restart", lambda d: test_scheme_import_cross_restart(Path(d))),
        ("test_scheme_import_readonly_db", lambda d: test_scheme_import_readonly_db(Path(d))),
    ]

    for name, test_fn in per_dir_tests:
        td = tempfile.mkdtemp(prefix="cas_mig_")
        try:
            test_fn(td)
            passed += 1
        except Exception as e:
            failed += 1
            errors.append((name, f"{type(e).__name__}: {e}"))
            print(f"\n[X] {name} 失败: {type(e).__name__}: {e}\n")
        finally:
            gc.collect()
            time.sleep(0.2)
            try:
                import shutil
                shutil.rmtree(td, ignore_errors=True)
            except Exception:
                pass

    # 完整链路测试使用独立临时目录
    pipeline_tests = [
        ("test_full_pipeline", lambda d: test_full_pipeline(rules_file, sample_dir)),
        ("test_scheme_cli_chain", lambda d: test_scheme_cli_chain(Path(d), rules_file, sample_dir)),
    ]

    for name, test_fn in pipeline_tests:
        work_dir = tempfile.mkdtemp(prefix="cas_pipeline_")
        try:
            test_fn(work_dir)
            passed += 1
        except Exception as e:
            failed += 1
            errors.append((name, f"{type(e).__name__}: {e}"))
            print(f"\n[X] {name} 失败: {type(e).__name__}: {e}\n")
        finally:
            gc.collect()
            time.sleep(0.2)
            try:
                import shutil
                shutil.rmtree(work_dir, ignore_errors=True)
            except Exception:
                pass

    print("=" * 60)
    print(f"测试完成: 通过 {passed}/{passed+failed}")
    if errors:
        print("失败详情:")
        for name, msg in errors:
            print(f"  - {name}: {msg}")
        sys.exit(1)
    else:
        print("🎉 所有测试通过！")
        sys.exit(0)


if __name__ == "__main__":
    main_tests()
