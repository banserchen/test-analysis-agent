"""report.summary_generator 技能的后处理和输出校验。"""

from __future__ import annotations

from typing import Any


def validate_output(data: dict[str, Any]) -> tuple[bool, list[str]]:
    """校验结构化输出。"""
    errors: list[str] = []

    if not isinstance(data, dict):
        return False, ["Output must be a JSON object."]

    # Validate management_summary
    mgmt = data.get("management_summary")
    if not isinstance(mgmt, dict):
        errors.append("'management_summary' must be an object.")
    else:
        if not isinstance(mgmt.get("title"), str):
            errors.append("'management_summary.title' must be a string.")
        if not isinstance(mgmt.get("one_liner"), str):
            errors.append("'management_summary.one_liner' must be a string.")
        risk = mgmt.get("risk_level", "")
        if risk not in ("critical", "high", "medium", "low"):
            errors.append(f"'management_summary.risk_level' '{risk}' must be critical/high/medium/low.")
        if not isinstance(mgmt.get("action_items"), list):
            errors.append("'management_summary.action_items' must be an array.")
        if not isinstance(mgmt.get("body"), str):
            errors.append("'management_summary.body' must be a string.")
        metrics = mgmt.get("key_metrics")
        if not isinstance(metrics, dict):
            errors.append("'management_summary.key_metrics' must be an object.")

    # Validate developer_report
    dev = data.get("developer_report")
    if not isinstance(dev, dict):
        errors.append("'developer_report' must be an object.")
    else:
        if not isinstance(dev.get("title"), str):
            errors.append("'developer_report.title' must be a string.")
        breakdown = dev.get("failure_breakdown")
        if not isinstance(breakdown, list):
            errors.append("'developer_report.failure_breakdown' must be an array.")
        if not isinstance(dev.get("detailed_findings"), str):
            errors.append("'developer_report.detailed_findings' must be a string.")
        if not isinstance(dev.get("next_steps"), list):
            errors.append("'developer_report.next_steps' must be an array.")

    return len(errors) == 0, errors
