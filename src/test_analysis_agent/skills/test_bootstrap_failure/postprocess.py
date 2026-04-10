"""Post-processing and output validation for test.bootstrap_failure skill."""

from __future__ import annotations

from typing import Any


def validate_output(data: dict[str, Any]) -> tuple[bool, list[str]]:
    """Validate structured output."""
    errors: list[str] = []

    if not isinstance(data, dict):
        return False, ["Output must be a JSON object."]

    if not isinstance(data.get("is_bootstrap_failure"), bool):
        errors.append("'is_bootstrap_failure' must be a boolean.")

    valid_phases = {"pip_install", "venv_creation", "pytest_collection", "pytest_startup", "unknown"}
    phase = data.get("bootstrap_phase", "")
    if phase not in valid_phases:
        errors.append(f"'bootstrap_phase' '{phase}' not in {sorted(valid_phases)}.")

    if not isinstance(data.get("error_details"), str):
        errors.append("'error_details' must be a string.")

    if not isinstance(data.get("suggestion"), str):
        errors.append("'suggestion' must be a string.")

    return len(errors) == 0, errors
