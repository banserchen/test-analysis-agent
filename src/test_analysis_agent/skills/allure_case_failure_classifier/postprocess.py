"""allure.case_failure_classifier 技能的后处理和输出校验。"""

from __future__ import annotations

from typing import Any


def validate_output(data: dict[str, Any]) -> tuple[bool, list[str]]:
    """校验结构化输出。"""
    errors: list[str] = []

    if not isinstance(data, dict):
        return False, ["Output must be a JSON object."]

    valid_classes = {"product_bug", "test_bug", "infrastructure", "environment", "flaky", "unknown"}

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
            fc = item.get("failure_class", "")
            if fc not in valid_classes:
                errors.append(f"classifications[{i}].failure_class '{fc}' not in {sorted(valid_classes)}.")
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
        by_class = summary.get("by_class")
        if not isinstance(by_class, dict):
            errors.append("'summary.by_class' must be an object.")

    return len(errors) == 0, errors
