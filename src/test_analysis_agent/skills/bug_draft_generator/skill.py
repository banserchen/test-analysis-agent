"""Skill implementation: Automatic bug draft generation."""

from __future__ import annotations

from typing import Any

from test_analysis_agent.skills.base import BaseSkill


class BugDraftGeneratorSkill(BaseSkill):
    """Automatically generate bug report drafts from analysis results."""

    name = "bug_draft_generator"
    description = "Automatically generate bug report drafts from analysis results"
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

            # Only generate drafts for issues worth filing
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

            # Build structured bug description
            desc_parts = [
                f"## Summary\n\n{description_text}",
                f"\n## Root Cause Analysis\n\n{root_cause}" if root_cause else "",
                f"\n## Log Evidence\n\n```\n{log_evidence}\n```" if log_evidence else "",
                "\n## Affected Tests\n\n"
                + "\n".join(f"- `{t}`" for t in affected_tests)
                if affected_tests else "",
                f"\n## Suggestion\n\n{suggestion}" if suggestion else "",
                f"\n---\n*Auto-generated from {job_name} #{build_number}*",
            ]
            full_description = "\n".join(p for p in desc_parts if p)

            # Infer component from category
            component = _category_to_component(category)

            # Generate labels
            labels = [f"severity:{severity}", f"category:{category}", "auto-generated"]
            if affected_tests:
                labels.append("test-failure")

            drafts.append({
                "title": f"[{severity.upper()}] {title}",
                "severity": severity,
                "component": component,
                "description": full_description,
                "steps_to_reproduce": f"1. Run Jenkins job `{job_name}` build #{build_number}\n"
                f"2. Observe failure in the {category} stage/area",
                "expected_behavior": "The pipeline should complete successfully",
                "actual_behavior": description_text,
                "environment": f"CI Pipeline: {job_name} #{build_number}",
                "labels": labels,
            })

        return {
            "bug_drafts": drafts,
            "count": len(drafts),
        }


def _category_to_component(category: str) -> str:
    """Map a failure category to a likely component name."""
    mapping = {
        "environment_error": "Infrastructure",
        "dependency_error": "Dependencies",
        "deployment_error": "Deployment",
        "configuration_error": "Configuration",
        "network_error": "Network/Infrastructure",
        "permission_error": "Security/Permissions",
        "test_startup_failure": "Test Infrastructure",
        "test_case_failure": "Product",
        "test_infrastructure_error": "Test Infrastructure",
        "timeout_error": "Performance",
        "resource_error": "Infrastructure",
        "code_bug": "Product",
    }
    return mapping.get(category, "General")


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)
