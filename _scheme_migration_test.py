"""方案迁移包 + 通用包处理链路测试

覆盖：
1. 方案迁移包：save → export → 新建DB import → 冲突处理 → 覆盖 → 撤销 → 审计
2. 所有包类型：默认跳过冲突、显式覆盖、撤销回退、审计记录、导出后再导入、错误路径中文提示

运行: python _scheme_migration_test.py
"""
from __future__ import annotations

import json
import pathlib
import shutil
import sys
import tempfile
import unittest
from datetime import datetime

TEST_DIR = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(TEST_DIR))

from contract_archiver.exceptions import (
    BatchNotFoundError,
    EmptyUndoError,
    MigrationPackageEmptyError,
    MigrationPackageError,
    MigrationPackageMissingFieldError,
    MigrationPackageParseError,
    SchemeNotFoundError,
)
from contract_archiver.rules import load_rules
from contract_archiver.scanner import scan_directory
from contract_archiver.storage import Storage


def _quick_scan(db_path: pathlib.Path) -> str:
    storage = Storage(str(db_path))
    rules = load_rules(TEST_DIR / "examples" / "rules.yaml")
    result = scan_directory(str(TEST_DIR / "examples" / "sample_data"), rules)
    batch_id, _ = storage.create_batch(
        str(TEST_DIR / "examples" / "sample_data"), result, str(TEST_DIR / "examples" / "rules.yaml"),
    )
    return batch_id


class TestSchemeMigration(unittest.TestCase):
    """方案迁移包完整链路"""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="sch_mig_"))
        self.db1 = self.tmp / "src.db"
        self.db2 = self.tmp / "dst.db"
        self.batch_id = _quick_scan(self.db1)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_01_save_export_import_roundtrip(self):
        """保存 → 导出 → 新建DB导入"""
        s1 = Storage(str(self.db1))
        s1.save_scheme("方案A", self.batch_id, "pending", "error", None)
        s1.save_scheme("方案B", self.batch_id, "passed", None, None)
        pkg_file = self.tmp / "schemes.json"
        out = s1.export_schemes_to_file(str(pkg_file))
        self.assertTrue(out.exists())
        data = json.loads(out.read_text(encoding="utf-8"))
        self.assertIn("package_version", data)
        self.assertIn("schemes", data)
        self.assertEqual(len(data["schemes"]), 2)

        s2 = Storage(str(self.db2))
        result = s2.import_schemes(str(pkg_file))
        self.assertEqual(result["total"], 2)
        self.assertEqual(len(result["created"]), 2)
        self.assertEqual(len(result["overwritten"]), 0)
        self.assertEqual(len(result["skipped"]), 0)

        schemes = s2.list_schemes()
        self.assertEqual(len(schemes), 2)
        names = {s.name for s in schemes}
        self.assertEqual(names, {"方案A", "方案B"})

    def test_02_duplicate_name_default_skip(self):
        """默认：同名方案已存在 → 跳过（不报错）"""
        s1 = Storage(str(self.db1))
        s1.save_scheme("dup_scheme", self.batch_id, None, "error", None)
        pkg = self.tmp / "dup.json"
        s1.export_schemes_to_file(str(pkg))

        s2 = Storage(str(self.db2))
        s2.save_scheme("dup_scheme", self.batch_id, "pending", None, None)
        result = s2.import_schemes(str(pkg))
        self.assertEqual(len(result["skipped"]), 1)
        self.assertEqual(len(result["created"]), 0)
        self.assertEqual(len(result["overwritten"]), 0)
        # 跳过 = 保留原来的状态筛选
        got = s2.get_scheme("dup_scheme")
        self.assertEqual(got.state, "pending")
        self.assertIsNone(got.severity)

    def test_03_explicit_overwrite(self):
        """--overwrite：显式覆盖"""
        s1 = Storage(str(self.db1))
        s1.save_scheme("overwrite_me", self.batch_id, None, "error", None)
        pkg = self.tmp / "ov.json"
        s1.export_schemes_to_file(str(pkg))

        s2 = Storage(str(self.db2))
        s2.save_scheme("overwrite_me", self.batch_id, "pending", None, None)
        result = s2.import_schemes(str(pkg), overwrite=True)
        self.assertEqual(len(result["overwritten"]), 1)
        self.assertEqual(len(result["created"]), 0)
        # 覆盖后 = 导入的条件 (severity=error, state=None)
        got = s2.get_scheme("overwrite_me")
        self.assertIsNone(got.state)
        self.assertEqual(got.severity, "error")

    def test_04_undo_after_overwrite(self):
        """覆盖后撤销回退"""
        s1 = Storage(str(self.db1))
        s1.save_scheme("undo_me", self.batch_id, None, "error", None)
        pkg = self.tmp / "u.json"
        s1.export_schemes_to_file(str(pkg))

        s2 = Storage(str(self.db2))
        s2.save_scheme("undo_me", self.batch_id, "pending", None, None)
        # 覆盖
        s2.import_schemes(str(pkg), overwrite=True)
        self.assertEqual(s2.get_scheme("undo_me").state, None)
        # 撤销
        undo_rec = s2.undo_last_scheme_change("undo_me")
        self.assertEqual(undo_rec.scheme_name, "undo_me")
        # 撤销后 = 恢复原来的
        got = s2.get_scheme("undo_me")
        self.assertEqual(got.state, "pending")
        self.assertIsNone(got.severity)

    def test_05_audit_log(self):
        """审计日志记录"""
        s1 = Storage(str(self.db1))
        s1.save_scheme("audited", self.batch_id, None, "error", None)
        pkg = self.tmp / "a.json"
        s1.export_schemes_to_file(str(pkg))

        s2 = Storage(str(self.db2))
        s2.import_schemes(str(pkg))
        audits = s2.get_scheme_audit_log("audited")
        actions = [a["action"] for a in audits]
        self.assertTrue(any("import" in a for a in actions))

    def test_06_invalid_package(self):
        """错误路径中文提示"""
        s = Storage(str(self.db2))
        # 顶层不是 dict
        bad1 = self.tmp / "bad1.json"
        bad1.write_text("[1, 2, 3]", encoding="utf-8")
        with self.assertRaises(MigrationPackageParseError) as ctx:
            s.import_schemes(str(bad1))
        self.assertIn("JSON 对象", str(ctx.exception))
        self.assertIn("顶层", str(ctx.exception))

        # 空文件
        empty = self.tmp / "empty.json"
        empty.write_text("", encoding="utf-8")
        with self.assertRaises(MigrationPackageEmptyError):
            s.import_schemes(str(empty))

        # 缺少 schemes 字段
        bad2 = self.tmp / "bad2.json"
        bad2.write_text(json.dumps({"package_type": "scheme_migration", "package_version": 1}), encoding="utf-8")
        with self.assertRaises(MigrationPackageMissingFieldError) as ctx:
            s.import_schemes(str(bad2))
        self.assertIn("schemes", str(ctx.exception))

        # 不存在的文件
        with self.assertRaises(MigrationPackageParseError):
            s.import_schemes(str(self.tmp / "不存在.json"))

        # JSON 格式错误
        bad3 = self.tmp / "bad3.json"
        bad3.write_text("{ this is not valid json }", encoding="utf-8")
        with self.assertRaises(MigrationPackageParseError) as ctx:
            s.import_schemes(str(bad3))
        self.assertIn("JSON 格式错误", str(ctx.exception))

    def test_07_zip_export_import(self):
        """ZIP 压缩包导入导出"""
        s1 = Storage(str(self.db1))
        s1.save_scheme("zip方案", self.batch_id, None, "warning", None)
        pkg_zip = self.tmp / "schemes.zip"
        s1.export_schemes_to_file(str(pkg_zip))
        self.assertTrue(pkg_zip.exists())

        s2 = Storage(str(self.db2))
        result = s2.import_schemes(str(pkg_zip))
        self.assertEqual(len(result["created"]), 1)
        self.assertIsNotNone(s2.get_scheme("zip方案"))

    def test_08_export_after_restart(self):
        """跨重启：保存后关闭再打开，导出导入仍工作"""
        s1 = Storage(str(self.db1))
        s1.save_scheme("重启方案", self.batch_id, "passed", "warning", None)
        del s1  # 模拟关闭

        # 重启
        s1_new = Storage(str(self.db1))
        pkg = self.tmp / "restart.json"
        s1_new.export_schemes_to_file(str(pkg), names=["重启方案"])
        data = json.loads(pkg.read_text(encoding="utf-8"))
        self.assertEqual(len(data["schemes"]), 1)
        self.assertEqual(data["schemes"][0]["name"], "重启方案")

        s2 = Storage(str(self.db2))
        s2.import_schemes(str(pkg))
        got = s2.get_scheme("重启方案")
        self.assertEqual(got.state, "passed")
        self.assertEqual(got.severity, "warning")

    def test_09_empty_undo_raises(self):
        """撤销空报错"""
        s = Storage(str(self.db2))
        with self.assertRaises(EmptyUndoError):
            s.undo_last_scheme_change("不存在的方案")

    def test_10_missing_scheme_export(self):
        """导出不存在的方案会抛 SchemeNotFoundError"""
        s = Storage(str(self.db1))
        s.save_scheme("exist", self.batch_id, None, None, None)
        pkg = self.tmp / "export_miss.json"
        with self.assertRaises(SchemeNotFoundError):
            s.export_schemes_to_file(str(pkg), names=["exist", "不存在的"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
