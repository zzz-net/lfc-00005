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
