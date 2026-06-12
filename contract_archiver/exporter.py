from __future__ import annotations

import csv
import html
import os
from datetime import datetime
from pathlib import Path
from typing import Iterable, Sequence

from .storage import BatchInfo, IssueRecord, STATE_LABELS, FilterScheme


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
            f'<tr class="project-header"><td colspan="11"><strong>{html.escape(project_name)}</strong>'
            f' <span class="muted">（类型: {html.escape(proj_type)}，共 {len(project_issues)} 个问题）</span></td></tr>'
        )
        for issue in project_issues:
            state_cls = STATE_CLASS.get(issue.state, "")
            sev_cls = SEVERITY_CLASS.get(issue.severity, "")
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
扫描时间: {html.escape(batch.scanned_at)}{scheme_meta_html}
</div>

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
</tr>
</thead>
<tbody>
{''.join(rows_html) if rows_html else '<tr><td colspan="10" class="muted">暂无问题</td></tr>'}
</tbody>
</table>

{audit_html}

<div class="footer">报告生成时间: {html.escape(now)}</div>
</body>
</html>"""

    with open(out, "w", encoding="utf-8") as f:
        f.write(html_content)
    return out
