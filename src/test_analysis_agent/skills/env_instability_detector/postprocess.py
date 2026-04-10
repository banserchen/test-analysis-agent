"""Post-processing and output validation for env.instability_detector skill."""

from __future__ import annotations

from typing import Any


def validate_output(data: dict[str, Any]) -> tuple[bool, list[str]]:
    """Validate structured output."""
    errors: list[str] = []

    if not isinstance(data, dict):
        return False, ["Output must be a JSON object."]

    valid_categories = {
        "network", "dependency_source", "permission", "dns",
        "disk", "memory", "clock_skew", "certificate", "unknown",
    }
    valid_severities = {"high", "medium", "low"}

    instabilities = data.get("instabilities")
    if not isinstance(instabilities, list):
        errors.append("'instabilities' must be an array.")
    else:
        for i, item in enumerate(instabilities):
            if not isinstance(item, dict):
                errors.append(f"instabilities[{i}] must be an object.")
                continue
            cat = item.get("category", "")
            if cat not in valid_categories:
                errors.append(f"instabilities[{i}].category '{cat}' not in {sorted(valid_categories)}.")
            if not isinstance(item.get("evidence"), str):
                errors.append(f"instabilities[{i}].evidence must be a string.")
            if not isinstance(item.get("occurrence_count"), int):
                errors.append(f"instabilities[{i}].occurrence_count must be an integer.")
            sev = item.get("severity", "")
            if sev not in valid_severities:
                errors.append(f"instabilities[{i}].severity '{sev}' not in {sorted(valid_severities)}.")

    if not isinstance(data.get("is_unstable"), bool):
        errors.append("'is_unstable' must be a boolean.")

    if not isinstance(data.get("summary"), str):
        errors.append("'summary' must be a string.")

    return len(errors) == 0, errors
