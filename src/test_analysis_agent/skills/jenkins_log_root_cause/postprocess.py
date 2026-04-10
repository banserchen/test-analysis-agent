"""Post-processing and output validation for jenkins.log_root_cause skill."""

from __future__ import annotations

from typing import Any


def validate_output(data: dict[str, Any]) -> tuple[bool, list[str]]:
    """Validate the structured output from the LLM.

    Returns:
        A tuple of (is_valid, list_of_error_messages).
    """
    errors: list[str] = []

    if not isinstance(data, dict):
        return False, ["Output must be a JSON object."]

    # Validate root_cause_snippets
    snippets = data.get("root_cause_snippets")
    if not isinstance(snippets, list):
        errors.append("'root_cause_snippets' must be an array.")
    else:
        for i, snippet in enumerate(snippets):
            if not isinstance(snippet, dict):
                errors.append(f"Snippet [{i}] must be an object.")
                continue
            if not isinstance(snippet.get("line_start"), int):
                errors.append(f"Snippet [{i}].line_start must be an integer.")
            if not isinstance(snippet.get("line_end"), int):
                errors.append(f"Snippet [{i}].line_end must be an integer.")
            if not isinstance(snippet.get("text"), str) or not snippet["text"].strip():
                errors.append(f"Snippet [{i}].text must be a non-empty string.")

            error_type = snippet.get("error_type", "")
            valid_types = {
                "compilation", "runtime", "dependency", "timeout",
                "permission", "network", "resource", "configuration", "unknown",
            }
            if error_type not in valid_types:
                errors.append(
                    f"Snippet [{i}].error_type '{error_type}' not in {sorted(valid_types)}."
                )

            confidence = snippet.get("confidence")
            if not isinstance(confidence, (int, float)) or not (0.0 <= confidence <= 1.0):
                errors.append(f"Snippet [{i}].confidence must be a number in [0, 1].")

    # Validate primary_error
    primary = data.get("primary_error")
    if not isinstance(primary, str) or not primary.strip():
        errors.append("'primary_error' must be a non-empty string.")

    return len(errors) == 0, errors
