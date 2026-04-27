"""allure.case_failure_classifier 技能的后处理和输出校验。"""

from __future__ import annotations

from typing import Any

# Valid FailureCategory values (kept in sync with schemas.FailureCategory)
_VALID_CATEGORIES = {
    "environment_error", "dependency_error", "deployment_error",
    "configuration_error", "network_error", "permission_error",
    "test_startup_failure", "test_case_failure", "test_infrastructure_error",
    "timeout_error", "resource_error", "function_bug", "unknown",
}


def validate_output(data: dict[str, Any]) -> tuple[bool, list[str]]:
    """校验结构化输出。"""
    errors: list[str] = []

    if not isinstance(data, dict):
        return False, ["Output must be a JSON object."]

    classifications = data.get("classifications")
    if not isinstance(classifications, list):
        errors.append("'classifications' must be an array.")
    else:
        for i, item in enumerate(classifications):
            if not isinstance(item, dict):
                errors.append(f"classifications[{i}] must be an object.")
                continue
            if not isinstance(item.get("test_name"), str) or not item["test_name"]:
                errors.append(f"classifications[{i}].test_name must be a non-empty string.")
            fc = item.get("failure_category", "")
            if fc not in _VALID_CATEGORIES:
                errors.append(f"classifications[{i}].failure_category '{fc}' not in {sorted(_VALID_CATEGORIES)}.")
            if not isinstance(item.get("reason"), str):
                errors.append(f"classifications[{i}].reason must be a string.")
            conf = item.get("confidence")
            if not isinstance(conf, (int, float)) or not (0.0 <= conf <= 1.0):
                errors.append(f"classifications[{i}].confidence must be a number in [0, 1].")

    summary = data.get("summary")
    if not isinstance(summary, dict):
        errors.append("'summary' must be an object.")
    else:
        if not isinstance(summary.get("total"), int):
            errors.append("'summary.total' must be an integer.")
        by_category = summary.get("by_category")
        if not isinstance(by_category, dict):
            errors.append("'summary.by_category' must be an object.")

    return len(errors) == 0, errors
