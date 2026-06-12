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
    s1 = storage1.save_scheme(
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
    s1 = storage.save_scheme(
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
    s2 = storage.save_scheme(
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
    # 如果列表里只有 "(无匹配问题...)" 之类的
    if "无问题记录" in list_output or "无匹配问题" in list_output:
        return 0
    # 回退：数非表头的表格行数
    count = 0
    for line in list_output.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        # 判断是否以数字开头（ID 列）
        first_part = stripped.split()[0] if stripped.split() else ""
        try:
            int(first_part)
            count += 1
        except (ValueError, IndexError):
            # 表头行 ID 会失败
            continue
    return count


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

    tests = [
        ("test_scheme_persistence", lambda d: test_scheme_persistence(d)),
        ("test_scheme_conflict_and_overwrite", lambda d: test_scheme_conflict_and_overwrite(d)),
        ("test_scheme_empty_and_notfound", lambda d: test_scheme_empty_and_notfound(d)),
        ("test_export_fields", lambda d: test_export_fields(d, rules_file, sample_dir)),
    ]

    for name, test_fn in tests:
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

    # 完整链路测试使用独立临时目录
    work_dir = tempfile.mkdtemp(prefix="cas_pipeline_")
    try:
        test_full_pipeline(rules_file, sample_dir)
        passed += 1
    except Exception as e:
        failed += 1
        errors.append(("test_full_pipeline", f"{type(e).__name__}: {e}"))
        print(f"\n[X] test_full_pipeline 失败: {type(e).__name__}: {e}\n")
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
