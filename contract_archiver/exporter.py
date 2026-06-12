from __future__ import annotations

import csv
import html
import os
from datetime import datetime
from pathlib import Path
from typing import Iterable, Sequence

from .storage import BatchInfo, IssueRecord, STATE_LABELS, FilterScheme
from .storage import BatchDiffResult, DiffIssueRecord, DIFF_TYPE_LABELS


STATE_CLASS = {
    "pending": "state-pending",
    "passed": "state-passed",
    "ignored": "state-ignored",
}

SEVERITY_CLASS = {
    "error": "sev-error",
    "warning": "sev-warning",
}

ACTION_LABELS = {
    "update": "状态变更",
    "undo": "撤销操作",
}


def export_csv(
    batch: BatchInfo,
    issues: Iterable[IssueRecord],
    output_path: str | os.PathLike,
    audit_log: Sequence[dict] | None = None,
    filter_scheme: FilterScheme | None = None,
) -> Path:
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    scheme_name = filter_scheme.name if filter_scheme else ""
    scheme_conditions = ""
    if filter_scheme:
        d = filter_scheme.to_display_dict()
        scheme_conditions = " + ".join(f"{k}={v}" for k, v in d.items())

    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        if filter_scheme:
            writer.writerow([
                "筛选方案", scheme_name,
                "筛选条件", scheme_conditions,
            ])
            writer.writerow([])
        writer.writerow([
            "批次ID", "扫描路径", "扫描时间",
            "项目名称", "项目类型",
            "问题ID", "问题类型", "严重程度",
            "规则名", "文件路径",
            "问题描述", "状态", "处理人", "备注", "更新时间",
            "筛选方案", "方案条件",
            "导入来源",
        ])
        for issue in issues:
            writer.writerow([
                batch.batch_id,
                batch.scan_path,
                batch.scanned_at,
                issue.project_name,
                issue.project_type_id or "",
                issue.id,
                issue.issue_label,
                issue.severity_label,
                issue.rule_name or "",
                issue.file_path or "",
                issue.message,
                issue.state_label,
                issue.handler or "",
                issue.note or "",
                issue.updated_at or "",
                scheme_name,
                scheme_conditions,
                getattr(issue, "import_source", None) or "",
            ])

    if audit_log:
        audit_out = out.with_name(out.stem + "_audit" + out.suffix)
        with open(audit_out, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "批次ID", "时间", "操作类型",
                "问题ID", "项目名称", "规则名", "文件路径",
                "变更前状态", "变更后状态",
                "变更前处理人", "变更后处理人",
                "变更前备注", "变更后备注",
            ])
            for entry in audit_log:
                writer.writerow([
                    batch.batch_id,
                    entry.get("timestamp", ""),
                    ACTION_LABELS.get(entry.get("action", ""), entry.get("action", "")),
                    entry.get("issue_id", ""),
                    entry.get("project_name", ""),
                    entry.get("rule_name", ""),
                    entry.get("file_path", ""),
                    STATE_LABELS.get(entry.get("old_state", ""), entry.get("old_state", "")),
                    STATE_LABELS.get(entry.get("new_state", ""), entry.get("new_state", "")),
                    entry.get("old_handler") or "",
                    entry.get("new_handler") or "",
                    entry.get("old_note") or "",
                    entry.get("new_note") or "",
                ])

    return out


def export_html(
    batch: BatchInfo,
    issues: list[IssueRecord],
    output_path: str | os.PathLike,
    audit_log: Sequence[dict] | None = None,
    filter_scheme: FilterScheme | None = None,
) -> Path:
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    grouped: dict[str, list[IssueRecord]] = {}
    for issue in issues:
        grouped.setdefault(issue.project_name, []).append(issue)

    total = len(issues)
    pending = sum(1 for i in issues if i.state == "pending")
    passed = sum(1 for i in issues if i.state == "passed")
    ignored = sum(1 for i in issues if i.state == "ignored")
    errors = sum(1 for i in issues if i.severity == "error")
    warnings = sum(1 for i in issues if i.severity == "warning")

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    import_source_html = ""
    import_meta_html = ""
    first_issue = issues[0] if issues else None
    if first_issue and getattr(first_issue, "import_source", None):
        import_src = first_issue.import_source
        import_meta_html = f" &nbsp;|&nbsp; 导入来源: <code style='background:#f3e5f5;color:#7b1fa2;'>{html.escape(import_src)}</code>"
        import_source_html = f"""
<div class="import-info" style="margin-bottom:24px;padding:16px;background:#f3e5f5;border:1px solid #ce93d8;border-radius:6px;">
  <div style="font-size:13px;color:#6a1b9a;font-weight:bold;margin-bottom:8px;">📦 工作包导入数据</div>
  <div><strong>导入来源:</strong> <code style='background:#e1bee7;color:#4a148c;'>{html.escape(import_src)}</code></div>
</div>
"""

    scheme_html = ""
    scheme_meta_html = ""
    if filter_scheme:
        d = filter_scheme.to_display_dict()
        cond_parts = [f"<strong>{html.escape(k)}</strong>: {html.escape(v)}" for k, v in d.items()]
        cond_html = " &nbsp;|&nbsp; ".join(cond_parts) if cond_parts else "(无具体条件)"
        scheme_html = f"""
<div class="scheme-info" style="margin-bottom:24px;padding:16px;background:#fff8e1;border:1px solid #ffe082;border-radius:6px;">
  <div style="font-size:13px;color:#e65100;font-weight:bold;margin-bottom:8px;">📌 使用筛选方案导出</div>
  <div><strong>方案名:</strong> <span style="font-size:18px;color:#e65100;">「{html.escape(filter_scheme.name)}」</span></div>
  <div style="margin-top:6px;">{cond_html}</div>
  <div style="margin-top:6px;font-size:12px;color:#888;">方案创建时间: {html.escape(filter_scheme.created_at)} &nbsp;|&nbsp; 最近更新: {html.escape(filter_scheme.updated_at)}</div>
</div>
"""
        scheme_meta_html = f" &nbsp;|&nbsp; 筛选方案: <code style='background:#fff8e1;color:#e65100;'>{html.escape(filter_scheme.name)}</code>"

    rows_html = []
    for project_name in sorted(grouped.keys()):
        project_issues = grouped[project_name]
        proj_type = project_issues[0].project_type_id or ""
        rows_html.append(
            f'<tr class="project-header"><td colspan="12"><strong>{html.escape(project_name)}</strong>'
            f' <span class="muted">（类型: {html.escape(proj_type)}，共 {len(project_issues)} 个问题）</span></td></tr>'
        )
        for issue in project_issues:
            state_cls = STATE_CLASS.get(issue.state, "")
            sev_cls = SEVERITY_CLASS.get(issue.severity, "")
            import_src = getattr(issue, "import_source", None) or ""
            import_src_html = f'<code style="background:#f3e5f5;color:#7b1fa2;">{html.escape(import_src)}</code>' if import_src else ""
            rows_html.append(
                f"<tr>"
                f'<td>{issue.id}</td>'
                f'<td><span class="{sev_cls}">{html.escape(issue.issue_label)}</span></td>'
                f'<td><span class="{sev_cls}">{html.escape(issue.severity_label)}</span></td>'
                f"<td>{html.escape(issue.rule_name or '')}</td>"
                f"<td>{html.escape(issue.file_path or '')}</td>"
                f'<td class="message">{html.escape(issue.message)}</td>'
                f'<td><span class="state-tag {state_cls}">{html.escape(issue.state_label)}</span></td>'
                f"<td>{html.escape(issue.handler or '')}</td>"
                f"<td>{html.escape(issue.note or '')}</td>"
                f"<td>{html.escape(issue.updated_at or '')}</td>"
                f"<td>{import_src_html}</td>"
                f"</tr>"
            )

    audit_html = ""
    if audit_log:
        audit_rows = []
        for entry in audit_log:
            old_state = STATE_LABELS.get(entry.get("old_state", ""), entry.get("old_state", ""))
            new_state = STATE_LABELS.get(entry.get("new_state", ""), entry.get("new_state", ""))
            action = ACTION_LABELS.get(entry.get("action", ""), entry.get("action", ""))
            audit_rows.append(
                f"<tr>"
                f"<td>{html.escape(entry.get('timestamp', ''))}</td>"
                f"<td>{html.escape(action)}</td>"
                f"<td>#{html.escape(str(entry.get('issue_id', '')))}</td>"
                f"<td>{html.escape(entry.get('project_name') or '')}</td>"
                f"<td>{html.escape(entry.get('rule_name') or '')}</td>"
                f"<td>{html.escape(entry.get('file_path') or '')}</td>"
                f'<td><span class="state-tag {STATE_CLASS.get(entry.get("old_state", ""), "")}">{html.escape(old_state)}</span></td>'
                f'<td><span class="state-tag {STATE_CLASS.get(entry.get("new_state", ""), "")}">{html.escape(new_state)}</span></td>'
                f"<td>{html.escape(entry.get('old_handler') or '')}</td>"
                f"<td>{html.escape(entry.get('new_handler') or '')}</td>"
                f"<td>{html.escape(entry.get('old_note') or '')}</td>"
                f"<td>{html.escape(entry.get('new_note') or '')}</td>"
                f"</tr>"
            )
        audit_html = f"""
<h2>操作历史（共 {len(audit_log)} 条）</h2>
<table>
<thead>
<tr>
  <th>时间</th>
  <th>操作</th>
  <th>问题ID</th>
  <th>项目</th>
  <th>规则名</th>
  <th>文件</th>
  <th>变更前状态</th>
  <th>变更后状态</th>
  <th>变更前处理人</th>
  <th>变更后处理人</th>
  <th>变更前备注</th>
  <th>变更后备注</th>
</tr>
</thead>
<tbody>
{''.join(audit_rows)}
</tbody>
</table>
"""

    html_content = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>合同附件归档校验报告 - {html.escape(batch.batch_id)}</title>
<style>
body {{ font-family: "Microsoft YaHei", "PingFang SC", sans-serif; margin: 24px; color: #333; }}
h1 {{ margin-bottom: 8px; }}
h2 {{ margin-top: 32px; border-bottom: 2px solid #333; padding-bottom: 6px; }}
.meta {{ color: #666; margin-bottom: 24px; font-size: 14px; }}
.summary {{ display: flex; gap: 16px; flex-wrap: wrap; margin-bottom: 24px; }}
.card {{ padding: 12px 20px; border-radius: 6px; background: #f5f5f5; min-width: 120px; }}
.card strong {{ display: block; font-size: 24px; }}
.card span {{ font-size: 13px; color: #666; }}
.card.errors {{ background: #ffeaea; }}
.card.warnings {{ background: #fff8e1; }}
.card.pending {{ background: #e3f2fd; }}
.card.passed {{ background: #e8f5e9; }}
.card.ignored {{ background: #f3e5f5; }}
table {{ border-collapse: collapse; width: 100%; font-size: 14px; }}
th, td {{ padding: 8px 10px; text-align: left; border: 1px solid #ddd; vertical-align: top; }}
th {{ background: #f0f0f0; position: sticky; top: 0; }}
tr.project-header td {{ background: #e8eaf6; font-weight: bold; }}
tr:hover td {{ background: #fafafa; }}
td.message {{ max-width: 400px; }}
.state-tag {{ display: inline-block; padding: 2px 8px; border-radius: 3px; font-size: 12px; font-weight: bold; }}
.state-pending {{ background: #ffeb3b; color: #5d4037; }}
.state-passed {{ background: #4caf50; color: #fff; }}
.state-ignored {{ background: #9e9e9e; color: #fff; }}
.sev-error {{ color: #c62828; font-weight: bold; }}
.sev-warning {{ color: #ef6c00; font-weight: bold; }}
.muted {{ color: #999; font-weight: normal; }}
.footer {{ margin-top: 32px; color: #999; font-size: 12px; }}
</style>
</head>
<body>
<h1>合同附件归档校验报告</h1>
<div class="meta">
批次 ID: <strong>{html.escape(batch.batch_id)}</strong> &nbsp;|&nbsp;
扫描路径: <code>{html.escape(batch.scan_path)}</code> &nbsp;|&nbsp;
扫描时间: {html.escape(batch.scanned_at)}{scheme_meta_html}{import_meta_html}
</div>

{import_source_html}
{scheme_html}

<h2>问题汇总</h2>
<div class="summary">
  <div class="card"><strong>{total}</strong><span>问题总数</span></div>
  <div class="card errors"><strong>{errors}</strong><span>错误</span></div>
  <div class="card warnings"><strong>{warnings}</strong><span>警告</span></div>
  <div class="card pending"><strong>{pending}</strong><span>待补</span></div>
  <div class="card passed"><strong>{passed}</strong><span>通过</span></div>
  <div class="card ignored"><strong>{ignored}</strong><span>忽略</span></div>
</div>

<h2>问题明细</h2>
<table>
<thead>
<tr>
  <th>ID</th>
  <th>问题类型</th>
  <th>严重度</th>
  <th>规则名</th>
  <th>文件路径</th>
  <th>问题描述</th>
  <th>状态</th>
  <th>处理人</th>
  <th>备注</th>
  <th>更新时间</th>
  <th>导入来源</th>
</tr>
</thead>
<tbody>
{''.join(rows_html) if rows_html else '<tr><td colspan="12" class="muted">暂无问题</td></tr>'}
</tbody>
</table>

{audit_html}

<div class="footer">报告生成时间: {html.escape(now)}</div>
</body>
</html>"""

    with open(out, "w", encoding="utf-8") as f:
        f.write(html_content)
    return out


DIFF_CLASS = {
    "added": "diff-added",
    "removed": "diff-removed",
    "inherited": "diff-inherited",
}


def export_diff_csv(
    diff: BatchDiffResult,
    output_path: str | os.PathLike,
) -> Path:
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "差异类型",
            "旧批次ID", "旧批次扫描时间", "旧批次扫描路径",
            "新批次ID", "新批次扫描时间", "新批次扫描路径",
            "项目名称", "项目类型",
            "问题类型", "严重程度",
            "规则名", "文件路径",
            "问题描述",
            "当前状态", "处理人", "备注", "更新时间",
            "指纹",
            "继承来源批次",
            "导入来源",
        ])

        for issue in diff.added:
            writer.writerow([
                DIFF_TYPE_LABELS.get("added", "added"),
                diff.batch_old.batch_id, diff.batch_old.scanned_at, diff.batch_old.scan_path,
                diff.batch_new.batch_id, diff.batch_new.scanned_at, diff.batch_new.scan_path,
                issue.project_name, issue.project_type_id or "",
                issue.issue_label, issue.severity_label,
                issue.rule_name or "", issue.file_path or "",
                issue.message,
                issue.state_label, issue.handler or "", issue.note or "", issue.updated_at or "",
                issue.fingerprint or "",
                issue.inherited_from_batch_id or "",
                getattr(issue, "import_source", None) or "",
            ])

        for issue in diff.removed:
            writer.writerow([
                DIFF_TYPE_LABELS.get("removed", "removed"),
                diff.batch_old.batch_id, diff.batch_old.scanned_at, diff.batch_old.scan_path,
                diff.batch_new.batch_id, diff.batch_new.scanned_at, diff.batch_new.scan_path,
                issue.project_name, issue.project_type_id or "",
                issue.issue_label, issue.severity_label,
                issue.rule_name or "", issue.file_path or "",
                issue.message,
                issue.state_label, issue.handler or "", issue.note or "", issue.updated_at or "",
                issue.fingerprint or "",
                "",
                getattr(issue, "import_source", None) or "",
            ])

        for di in diff.inherited:
            issue = di.issue
            writer.writerow([
                DIFF_TYPE_LABELS.get("inherited", "inherited"),
                diff.batch_old.batch_id, diff.batch_old.scanned_at, diff.batch_old.scan_path,
                diff.batch_new.batch_id, diff.batch_new.scanned_at, diff.batch_new.scan_path,
                issue.project_name, issue.project_type_id or "",
                issue.issue_label, issue.severity_label,
                issue.rule_name or "", issue.file_path or "",
                issue.message,
                issue.state_label, issue.handler or "", issue.note or "", issue.updated_at or "",
                issue.fingerprint or "",
                issue.inherited_from_batch_id or "",
                getattr(issue, "import_source", None) or "",
            ])

    return out


def export_diff_html(
    diff: BatchDiffResult,
    output_path: str | os.PathLike,
) -> Path:
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _issue_rows(issues: list[IssueRecord], diff_type: str) -> list[str]:
        rows = []
        for issue in issues:
            state_cls = STATE_CLASS.get(issue.state, "")
            sev_cls = SEVERITY_CLASS.get(issue.severity, "")
            diff_cls = DIFF_CLASS.get(diff_type, "")
            rows.append(
                f'<tr class="{diff_cls}">'
                f'<td><span class="diff-tag {diff_cls}">{DIFF_TYPE_LABELS.get(diff_type, diff_type)}</span></td>'
                f'<td>{html.escape(issue.project_name)}</td>'
                f'<td><span class="{sev_cls}">{html.escape(issue.issue_label)}</span></td>'
                f'<td><span class="{sev_cls}">{html.escape(issue.severity_label)}</span></td>'
                f"<td>{html.escape(issue.rule_name or '')}</td>"
                f"<td>{html.escape(issue.file_path or '')}</td>"
                f'<td class="message">{html.escape(issue.message)}</td>'
                f'<td><span class="state-tag {state_cls}">{html.escape(issue.state_label)}</span></td>'
                f"<td>{html.escape(issue.handler or '')}</td>"
                f"<td>{html.escape(issue.note or '')}</td>"
                f"<td>{html.escape(issue.updated_at or '')}</td>"
                f"<td>{html.escape(issue.inherited_from_batch_id or '')}</td>"
                f"</tr>"
            )
        return rows

    def _inherited_rows(diff_issues: list[DiffIssueRecord]) -> list[str]:
        rows = []
        for di in diff_issues:
            issue = di.issue
            state_cls = STATE_CLASS.get(issue.state, "")
            sev_cls = SEVERITY_CLASS.get(issue.severity, "")
            diff_cls = DIFF_CLASS.get("inherited", "")
            rows.append(
                f'<tr class="{diff_cls}">'
                f'<td><span class="diff-tag {diff_cls}">{DIFF_TYPE_LABELS.get("inherited", "inherited")}</span></td>'
                f'<td>{html.escape(issue.project_name)}</td>'
                f'<td><span class="{sev_cls}">{html.escape(issue.issue_label)}</span></td>'
                f'<td><span class="{sev_cls}">{html.escape(issue.severity_label)}</span></td>'
                f"<td>{html.escape(issue.rule_name or '')}</td>"
                f"<td>{html.escape(issue.file_path or '')}</td>"
                f'<td class="message">{html.escape(issue.message)}</td>'
                f'<td><span class="state-tag {state_cls}">{html.escape(issue.state_label)}</span></td>'
                f"<td>{html.escape(issue.handler or '')}</td>"
                f"<td>{html.escape(issue.note or '')}</td>"
                f"<td>{html.escape(issue.updated_at or '')}</td>"
                f"<td>{html.escape(issue.inherited_from_batch_id or '')}</td>"
                f"</tr>"
            )
        return rows

    added_rows = _issue_rows(diff.added, "added")
    removed_rows = _issue_rows(diff.removed, "removed")
    inherited_rows = _inherited_rows(diff.inherited)

    all_rows = added_rows + removed_rows + inherited_rows

    path_same = diff.batch_old.scan_path == diff.batch_new.scan_path
    path_info = (
        f"扫描路径: <code>{html.escape(diff.batch_new.scan_path)}</code>"
        if path_same
        else f"""
旧路径: <code>{html.escape(diff.batch_old.scan_path)}</code><br>
新路径: <code>{html.escape(diff.batch_new.scan_path)}</code>
"""
    )

    html_content = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>批次对比报告 - {html.escape(diff.batch_new.batch_id)}</title>
<style>
body {{ font-family: "Microsoft YaHei", "PingFang SC", sans-serif; margin: 24px; color: #333; }}
h1 {{ margin-bottom: 8px; }}
h2 {{ margin-top: 32px; border-bottom: 2px solid #333; padding-bottom: 6px; }}
.meta {{ color: #666; margin-bottom: 24px; font-size: 14px; line-height: 1.8; }}
.summary {{ display: flex; gap: 16px; flex-wrap: wrap; margin-bottom: 24px; }}
.card {{ padding: 12px 20px; border-radius: 6px; background: #f5f5f5; min-width: 120px; }}
.card strong {{ display: block; font-size: 24px; }}
.card span {{ font-size: 13px; color: #666; }}
.card.added {{ background: #e8f5e9; }}
.card.removed {{ background: #ffebee; }}
.card.inherited {{ background: #e3f2fd; }}
.card.old-total {{ background: #f3e5f5; }}
.card.new-total {{ background: #fff8e1; }}
table {{ border-collapse: collapse; width: 100%; font-size: 14px; }}
th, td {{ padding: 8px 10px; text-align: left; border: 1px solid #ddd; vertical-align: top; }}
th {{ background: #f0f0f0; position: sticky; top: 0; }}
tr:hover td {{ background: #fafafa; }}
tr.diff-added td {{ background: #f1f8e9; }}
tr.diff-removed td {{ background: #ffebee; }}
tr.diff-inherited td {{ background: #e8f0fe; }}
tr.diff-added:hover td {{ background: #dcedc8; }}
tr.diff-removed:hover td {{ background: #ffcdd2; }}
tr.diff-inherited:hover td {{ background: #d2e3fc; }}
td.message {{ max-width: 400px; }}
.state-tag {{ display: inline-block; padding: 2px 8px; border-radius: 3px; font-size: 12px; font-weight: bold; }}
.state-pending {{ background: #ffeb3b; color: #5d4037; }}
.state-passed {{ background: #4caf50; color: #fff; }}
.state-ignored {{ background: #9e9e9e; color: #fff; }}
.diff-tag {{ display: inline-block; padding: 2px 10px; border-radius: 12px; font-size: 12px; font-weight: bold; }}
.diff-tag.diff-added {{ background: #4caf50; color: #fff; }}
.diff-tag.diff-removed {{ background: #f44336; color: #fff; }}
.diff-tag.diff-inherited {{ background: #2196f3; color: #fff; }}
.sev-error {{ color: #c62828; font-weight: bold; }}
.sev-warning {{ color: #ef6c00; font-weight: bold; }}
.muted {{ color: #999; font-weight: normal; }}
.footer {{ margin-top: 32px; color: #999; font-size: 12px; }}
.batch-info {{ background: #f5f5f5; padding: 12px 16px; border-radius: 6px; margin-bottom: 16px; }}
.batch-info .label {{ color: #666; font-size: 12px; }}
.batch-info .value {{ font-weight: bold; font-size: 14px; }}
.batch-compare {{ display: flex; gap: 16px; margin-bottom: 20px; flex-wrap: wrap; }}
.batch-box {{ flex: 1; min-width: 280px; padding: 12px 16px; border-radius: 6px; border: 1px solid #ddd; }}
.batch-box.old {{ background: #fafafa; }}
.batch-box.new {{ background: #f0f8ff; border-color: #90caf9; }}
.batch-box h3 {{ margin: 0 0 8px 0; font-size: 14px; color: #666; }}
.batch-box .batch-id {{ font-size: 18px; font-weight: bold; margin-bottom: 6px; }}
.batch-box .meta-row {{ font-size: 12px; color: #666; margin: 2px 0; }}
</style>
</head>
<body>
<h1>批次对比报告</h1>

<div class="batch-compare">
  <div class="batch-box old">
    <h3>旧批次</h3>
    <div class="batch-id">{html.escape(diff.batch_old.batch_id)}</div>
    <div class="meta-row">扫描时间: {html.escape(diff.batch_old.scanned_at)}</div>
    <div class="meta-row">问题数: {diff.total_old}</div>
  </div>
  <div style="display:flex;align-items:center;font-size:24px;color:#999;">→</div>
  <div class="batch-box new">
    <h3>新批次</h3>
    <div class="batch-id">{html.escape(diff.batch_new.batch_id)}</div>
    <div class="meta-row">扫描时间: {html.escape(diff.batch_new.scanned_at)}</div>
    <div class="meta-row">问题数: {diff.total_new}</div>
  </div>
</div>

<div class="meta">
{path_info}
</div>

<h2>对比汇总</h2>
<div class="summary">
  <div class="card added"><strong>{diff.added_count}</strong><span>新增问题</span></div>
  <div class="card removed"><strong>{diff.removed_count}</strong><span>消失问题</span></div>
  <div class="card inherited"><strong>{diff.inherited_count}</strong><span>继承问题</span></div>
  <div class="card old-total"><strong>{diff.total_old}</strong><span>旧批次总计</span></div>
  <div class="card new-total"><strong>{diff.total_new}</strong><span>新批次总计</span></div>
</div>

<h2>差异明细</h2>
<table>
<thead>
<tr>
  <th>差异类型</th>
  <th>项目名称</th>
  <th>问题类型</th>
  <th>严重度</th>
  <th>规则名</th>
  <th>文件路径</th>
  <th>问题描述</th>
  <th>状态</th>
  <th>处理人</th>
  <th>备注</th>
  <th>更新时间</th>
  <th>继承来源批次</th>
</tr>
</thead>
<tbody>
{''.join(all_rows) if all_rows else '<tr><td colspan="12" class="muted">无差异</td></tr>'}
</tbody>
</table>

<div class="footer">报告生成时间: {html.escape(now)}</div>
</body>
</html>"""

    with open(out, "w", encoding="utf-8") as f:
        f.write(html_content)
    return out
