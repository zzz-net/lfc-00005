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
