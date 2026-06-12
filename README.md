# 合同附件归档校验工具 (contract-archiver)

法务资料目录合规检查工具。按规则自动扫描合同附件，识别缺失、命名错误、空文件、重复版本等问题，并支持状态标记、撤销、备注和报告导出。

## 功能特性

- **规则配置化**：支持 YAML/JSON 定义项目类型、必需附件、命名正则、版本优先级、大小限制
- **六类问题识别**：缺失附件、命名错误、空文件、重复版本（含版本优先级）、未纳入规则材料、文件大小超限
- **状态管理**：待补(pending) / 通过(passed) / 忽略(ignored)，保存处理人和备注
- **撤销机制**：每条标记操作可逐条撤销，防止误操作
- **批次管理**：同一目录重复扫描检测，支持强制新建批次（历史批次保留不删除）
- **持久化**：SQLite 存储，重启后复核状态、备注、撤销历史全部保留
- **报告导出**：CSV（UTF-8 BOM，Excel 友好）和 HTML（带汇总卡片、颜色分组）
- **错误反馈**：配置写错、目录不存在、空撤销、重复扫描、无效参数均有明确可验证提示

## 安装

```bash
pip install -r requirements.txt
```

仅依赖 **PyYAML**（标准库即可运行其余功能，若只用 JSON 配置可不用安装）。

## 目录结构约定

工具将扫描根目录下的每个**一级子目录**视为一个项目。项目名中需包含可被规则匹配的关键字（如"销售"、"采购"、"NDA"）。

```
资料根目录/
├── 2024-销售合同-ABC公司/          # ← 自动识别为 SALES 类型
│   ├── 销售合同最终版.pdf
│   ├── 营业执照.pdf
│   ├── 法人身份证正面.jpg
│   ├── 授权委托书.pdf
│   └── ~$临时文件.tmp             # ← 被 ignore_patterns 忽略
├── 2024-采购合同-XYZ供应商/        # ← 自动识别为 PURCHASE 类型
│   ├── 采购合同v3.docx
│   ├── 供应商资质.pdf
│   └── 报价单.xlsx
├── 2024-保密协议-某合作方/         # ← 自动识别为 NDA 类型
│   └── 保密协议v1.pdf
├── 无法识别的杂项目录/              # ← 不匹配任何类型，列在未识别中
│   └── ...
└── 根目录说明.txt                   # ← 根目录的游离文件会被提示
```

## 规则配置说明

支持 YAML 或 JSON，示例见 [examples/rules.yaml](examples/rules.yaml)。

```yaml
global_max_size_kb: 51200          # 全局单文件上限 (KB)

ignore_patterns:                    # 文件名匹配这些正则的文件将被忽略
  - "^~$"
  - "\.tmp$"
  - "Thumbs\.db"

project_types:
  - type_id: SALES                  # 项目类型ID（用于匹配和展示）
    display_name: 销售合同
    directory_pattern: "销售|SALES"  # 目录名匹配此正则即判定为该类型
    attachments:
      - name: 主合同                 # 附件规则名（显示用）
        required: true               # 是否必需（缺失会记为错误）
        naming_pattern: ".*销售合同.*(v\d+|最终版)"  # 文件名正则（不含扩展名）
        version_priority: ["最终版", "v3", "v2", "v1"]  # 版本优先级（前高后低）
        max_size_kb: 20480          # 该附件独立大小上限，可覆盖全局
      - name: 营业执照
        required: true
        naming_pattern: ".*营业执照.*"
      - name: 授权委托书
        required: false             # 非必需，缺失不会报错
        naming_pattern: ".*授权委托.*"
```

字段说明：
- `naming_pattern`：不写则仅按 `name` 子串模糊匹配；写了则用正则**严格校验**
- `version_priority`：不写则同一规则下出现多个文件即报重复；写了则按优先级提示低版本重复
- `max_size_kb`：附件级 > 全局级；不写则不检查大小

## 操作链路

完整流程：**加载规则 → 扫描目录 → 标记处理 → (撤销) → 导出报告**

### 1. 扫描目录

```bash
python -m contract_archiver scan \
  -c examples/rules.yaml \
  -d examples/sample_data
```

输出：
```
[OK] 扫描完成
  批次ID: 20260613010000_ab12cd34
  扫描路径: D:\path\to\sample_data
  项目数: 3
  未识别目录: 无法识别的杂项目录
  根目录游离文件: 1 个
  发现问题: 5 个
```

**重复扫描**同一目录会报错并给出已有批次ID。加 `--force` 可新建批次，旧批次保留不变。

### 2. 查看批次和问题

```bash
# 查看所有批次
python -m contract_archiver list

# 查看某批次所有问题
python -m contract_archiver list -b 20260613010000_ab12cd34

# 过滤：只看未处理的错误级问题
python -m contract_archiver list -b 20260613010000_ab12cd34 \
  --state pending --severity error
```

### 3. 标记问题状态

```bash
# 标记单个问题为"通过"，填写处理人+备注
python -m contract_archiver mark -b <批次ID> \
  --to passed --ids 1 2 \
  --handler "张律师" --note "已核对原件"

# 标记所有待补问题为"忽略"
python -m contract_archiver mark -b <批次ID> \
  --to ignored --all --state pending \
  --handler "李法务"

# 静默模式（不逐条打印）
python -m contract_archiver mark -b <批次ID> \
  --to passed --ids 3 -q
```

状态：`pending`（待补）、`passed`（通过）、`ignored`（忽略）

### 4. 撤销操作

```bash
# 查看可撤销数量
python -m contract_archiver undo -b <批次ID> --count

# 撤销最近一条标记（状态、处理人、备注全部还原）
python -m contract_archiver undo -b <批次ID>
```

没有可撤销操作时会返回明确错误。撤销可连续执行直到全部还原。

### 5. 导出报告

```bash
# 导出 HTML（默认，带汇总卡片和颜色分组）
python -m contract_archiver export -b <批次ID> -o report.html

# 导出 CSV（UTF-8 BOM，Excel 直接打开不乱码）
python -m contract_archiver export -b <批次ID> -o report.csv -f csv
```

## 错误反馈场景

| 场景 | 提示示例 |
|------|---------|
| 配置文件语法错误 | `[配置错误] 配置文件解析失败: while parsing a flow sequence...` |
| 配置缺少必填字段 | `[配置错误] project_types[0].attachments[2]: 缺少必填字段 'name'` |
| 扫描目录不存在 | `[目录不存在] 扫描路径不存在: Z:\nonexistent` |
| 目录已扫描过 | `[重复扫描] 目录已存在扫描记录，批次ID: xxx。使用 --force 可创建新批次（旧批次保留）。` |
| 无可撤销操作 | `[撤销失败] 没有可撤销的操作` |
| 无效状态参数 | argparse 直接提示 `invalid choice`，退出码 2 |

所有错误均通过 stderr 输出并返回非 0 退出码，便于脚本集成和 CI 校验。

## 数据存储

默认在当前目录生成 `contract_archive.db`（SQLite），可通过 `--db` 参数指定路径。包含以下表：

- `batches`：扫描批次元信息
- `issues`：问题明细及状态/处理人/备注/稳定指纹
- `undo_log`：撤销日志（逐条回滚用，执行撤销后删除）
- `audit_log`：**永久审计日志**（所有状态变更和撤销操作，永不删除）

数据库支持自动迁移：旧版本数据库会自动添加 `fingerprint` 列和 `audit_log` 表，无需手动升级。

删除数据库文件即清空所有历史，数据库文件可直接归档保留。

## 完整用法速查

```bash
python -m contract_archiver --help
python -m contract_archiver scan --help
python -m contract_archiver list --help
python -m contract_archiver mark --help
python -m contract_archiver undo --help
python -m contract_archiver export --help
```

## 项目结构

```
lfc-00005/
├── contract_archiver/
│   ├── __init__.py
│   ├── __main__.py          # python -m contract_archiver 入口
│   ├── cli.py               # 命令行解析与子命令调度
│   ├── rules.py             # 规则加载与校验
│   ├── scanner.py           # 目录扫描与问题识别
│   ├── storage.py           # SQLite 持久化与撤销
│   ├── exporter.py          # CSV / HTML 导出
│   └── exceptions.py        # 自定义异常
├── examples/
│   ├── rules.yaml           # YAML 规则示例
│   ├── rules.json           # JSON 规则示例
│   └── sample_data/         # 样例资料目录（含各种问题场景）
├── requirements.txt
└── README.md
```
