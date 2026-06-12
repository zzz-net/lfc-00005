class ContractArchiverError(Exception):
    pass


class ConfigError(ContractArchiverError):
    def __init__(self, message, field=None):
        self.field = field
        super().__init__(f"[配置错误] {f'{field}: ' if field else ''}{message}")


class DirectoryNotFoundError(ContractArchiverError):
    def __init__(self, path):
        super().__init__(f"[目录不存在] 扫描路径不存在: {path}")


class EmptyUndoError(ContractArchiverError):
    def __init__(self):
        super().__init__("[撤销失败] 没有可撤销的操作")


class DuplicateBatchError(ContractArchiverError):
    def __init__(self, path, batch_id):
        super().__init__(
            f"[重复扫描] 目录已存在扫描记录，批次ID: {batch_id}。"
            f"使用 --force 可创建新批次（旧批次保留）。"
        )
        self.path = path
        self.batch_id = batch_id


class InvalidStateError(ContractArchiverError):
    def __init__(self, state):
        super().__init__(f"[状态无效] 不支持的标记状态: {state}")


class SchemeNotFoundError(ContractArchiverError):
    def __init__(self, name: str):
        super().__init__(f"[方案不存在] 未找到筛选方案: {name}")


class SchemeExistsError(ContractArchiverError):
    def __init__(self, name: str):
        super().__init__(
            f"[方案已存在] 筛选方案 '{name}' 已存在，使用 --overwrite 可覆盖"
        )


class EmptySchemeError(ContractArchiverError):
    def __init__(self, name: str):
        super().__init__(
            f"[空方案] 筛选方案 '{name}' 未指定任何筛选条件，至少需要一个条件"
        )


class DatabasePermissionError(ContractArchiverError):
    def __init__(self, db_path: str, operation: str):
        super().__init__(
            f"[数据库权限错误] 无法对数据库 {db_path} 执行 {operation} 操作，请检查文件权限"
        )


class MigrationPackageError(ContractArchiverError):
    """迁移包通用错误基类"""
    pass


class InvalidMigrationPackageError(MigrationPackageError):
    def __init__(self, reason: str):
        super().__init__(f"[迁移包无效] {reason}")


class MigrationPackageEmptyError(MigrationPackageError):
    def __init__(self, file_path: str):
        super().__init__(f"[迁移包为空] 迁移包文件 {file_path} 为空，没有可导入的方案")


class MigrationPackageParseError(MigrationPackageError):
    def __init__(self, file_path: str, detail: str):
        super().__init__(f"[迁移包解析失败] 无法解析文件 {file_path}: {detail}")


class MigrationPackageMissingFieldError(MigrationPackageError):
    def __init__(self, file_path: str, field: str, scheme_index: int | None = None):
        pos = f"（第{scheme_index + 1}个方案）" if scheme_index is not None else ""
        super().__init__(
            f"[迁移包缺字段] 文件 {file_path}{pos} 缺少必填字段: {field}"
        )


class SchemeImportConflictError(MigrationPackageError):
    def __init__(self, name: str):
        super().__init__(
            f"[导入冲突] 方案 '{name}' 已存在，使用 --overwrite 可覆盖"
        )


class BatchNotFoundError(ContractArchiverError):
    def __init__(self, batch_id: str):
        super().__init__(f"[批次不存在] 未找到批次: {batch_id}")


class NoPreviousBatchError(ContractArchiverError):
    def __init__(self, batch_id: str, scan_path: str):
        super().__init__(
            f"[无上一批次] 批次 {batch_id} 所在路径 {scan_path} 没有更早的扫描批次，无法对比"
        )


class BatchPathMismatchWarning(ContractArchiverError):
    def __init__(self, batch1_id: str, path1: str, batch2_id: str, path2: str):
        super().__init__(
            f"[路径不同] 两个批次扫描路径不一致：\n"
            f"  {batch1_id}: {path1}\n"
            f"  {batch2_id}: {path2}\n"
            f"路径不同的批次对比可能无意义，如确认后可使用 --ignore-path 强制执行"
        )


class WorkPackageError(ContractArchiverError):
    """工作包通用错误基类"""
    pass


class InvalidWorkPackageError(WorkPackageError):
    def __init__(self, reason: str):
        super().__init__(f"[工作包无效] {reason}")


class WorkPackageEmptyError(WorkPackageError):
    def __init__(self, file_path: str):
        super().__init__(f"[工作包为空] 工作包文件 {file_path} 为空，没有可导入的内容")


class WorkPackageParseError(WorkPackageError):
    def __init__(self, file_path: str, detail: str):
        super().__init__(f"[工作包解析失败] 无法解析文件 {file_path}: {detail}")


class WorkPackageMissingFieldError(WorkPackageError):
    def __init__(self, file_path: str, field: str, section: str | None = None):
        pos = f"（{section}）" if section else ""
        super().__init__(
            f"[工作包缺字段] 文件 {file_path}{pos} 缺少必填字段: {field}"
        )


class WorkPackageBatchExistsError(WorkPackageError):
    def __init__(self, batch_id: str):
        super().__init__(
            f"[导入冲突] 批次 {batch_id} 已存在，使用 --overwrite-batch 可覆盖批次及其问题"
        )


class WorkPackageIssueStateConflictError(WorkPackageError):
    def __init__(self, issue_id: int, project_name: str, rule_name: str | None):
        target = rule_name or "(未知规则)"
        super().__init__(
            f"[状态冲突] 问题 #{issue_id} ({project_name} - {target}) 已有状态，"
            f"使用 --overwrite-state 可覆盖状态和备注"
        )


class WorkPackageRuleMismatchError(WorkPackageError):
    def __init__(self, batch_id: str, detail: str):
        super().__init__(
            f"[规则摘要不一致] 批次 {batch_id} 的规则配置摘要与本地不同：{detail}。"
            f"导入后可能导致后续扫描对比异常，如确认可继续使用 --ignore-rule-mismatch"
        )


class WorkPackageSchemeExistsError(WorkPackageError):
    def __init__(self, scheme_name: str):
        super().__init__(
            f"[方案冲突] 筛选方案 '{scheme_name}' 已存在，使用 --overwrite-scheme 可覆盖"
        )


class EmptyWorkPackageUndoError(ContractArchiverError):
    def __init__(self):
        super().__init__("[撤销失败] 没有可撤销的工作包导入操作")
