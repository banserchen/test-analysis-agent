"""技能实现：自动生成缺陷单草稿。"""

from __future__ import annotations

from typing import Any

from test_analysis_agent.skills.base import BaseSkill


class BugDraftGeneratorSkill(BaseSkill):
    """根据分析结果自动生成缺陷单草稿。"""

    name = "bug_draft_generator"
    description = "根据分析结果自动生成缺陷单草稿"
    version = "1.0.0"

    def can_handle(self, context: dict[str, Any]) -> bool:
        issues = context.get("issues", [])
        return isinstance(issues, list) and len(issues) > 0

    def execute(self, context: dict[str, Any]) -> dict[str, Any]:
        issues = context.get("issues", [])
        job_name = context.get("job_name", "unknown")
        build_number = context.get("build_number", 0)

        drafts: list[dict[str, Any]] = []

        for issue in issues:
            severity = _get(issue, "severity", "medium")
            # Normalize severity if it's an enum
            if hasattr(severity, "value"):
                severity = severity.value
            should_file = _get(issue, "should_file_bug", False)

            # 仅对值得提单的问题生成草稿
            if not should_file and severity not in ("critical", "high"):
                continue

            title = _get(issue, "title", "Unknown Issue")
            category = _get(issue, "category", "unknown")
            if hasattr(category, "value"):
                category = category.value
            description_text = _get(issue, "description", "")
            root_cause = _get(issue, "root_cause", "")
            suggestion = _get(issue, "suggestion", "")
            affected_tests = _get(issue, "affected_tests", [])
            log_evidence = _get(issue, "log_evidence", "")

            # 构建结构化的缺陷描述
            desc_parts = [
                f"## 概要\n\n{description_text}",
                f"\n## 根因分析\n\n{root_cause}" if root_cause else "",
                f"\n## 日志证据\n\n```\n{log_evidence}\n```" if log_evidence else "",
                "\n## 受影响的测试\n\n"
                + "\n".join(f"- `{t}`" for t in affected_tests)
                if affected_tests else "",
                f"\n## 修复建议\n\n{suggestion}" if suggestion else "",
                f"\n---\n*由 {job_name} #{build_number} 自动生成*",
            ]
            full_description = "\n".join(p for p in desc_parts if p)

            # 从分类推断组件
            component = _category_to_component(category)

            # 生成标签
            labels = [f"severity:{severity}", f"category:{category}", "auto-generated"]
            if affected_tests:
                labels.append("test-failure")

            drafts.append({
                "title": f"[{severity.upper()}] {title}",
                "severity": severity,
                "component": component,
                "description": full_description,
                "steps_to_reproduce": f"1. 运行 Jenkins 任务 `{job_name}` 构建 #{build_number}\n"
                f"2. 观察 {category} 阶段/区域的失败",
                "expected_behavior": "流水线应当成功完成",
                "actual_behavior": description_text,
                "environment": f"CI 流水线：{job_name} #{build_number}",
                "labels": labels,
            })

        return {
            "bug_drafts": drafts,
            "count": len(drafts),
        }


def _category_to_component(category: str) -> str:
    """将失败分类映射到可能的组件名称。"""
    mapping = {
        "environment_error": "基础设施",
        "dependency_error": "依赖管理",
        "deployment_error": "部署",
        "configuration_error": "配置",
        "network_error": "网络/基础设施",
        "permission_error": "安全/权限",
        "test_startup_failure": "测试基础设施",
        "test_case_failure": "测试脚本",
        "test_infrastructure_error": "测试基础设施",
        "timeout_error": "性能",
        "resource_error": "基础设施",
        "function_bug": "产品功能",
        # backward compat
        "code_bug": "产品功能",
    }
    return mapping.get(category, "通用")


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)
