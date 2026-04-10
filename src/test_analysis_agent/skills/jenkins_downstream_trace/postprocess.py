"""Post-processing and output validation for jenkins.downstream_trace skill."""

from __future__ import annotations

from typing import Any


def validate_output(data: dict[str, Any]) -> tuple[bool, list[str]]:
    """Validate structured output."""
    errors: list[str] = []

    if not isinstance(data, dict):
        return False, ["Output must be a JSON object."]

    # Validate call_chain
    chain = data.get("call_chain")
    if not isinstance(chain, list):
        errors.append("'call_chain' must be an array.")
    else:
        valid_statuses = {"success", "failure", "unstable", "aborted", "unknown"}
        for i, entry in enumerate(chain):
            if not isinstance(entry, dict):
                errors.append(f"call_chain[{i}] must be an object.")
                continue
            if not isinstance(entry.get("job_name"), str) or not entry["job_name"]:
                errors.append(f"call_chain[{i}].job_name must be a non-empty string.")
            if not isinstance(entry.get("build_number"), int):
                errors.append(f"call_chain[{i}].build_number must be an integer.")
            status = entry.get("status", "")
            if status not in valid_statuses:
                errors.append(f"call_chain[{i}].status '{status}' not in {sorted(valid_statuses)}.")

    # Validate failing_chain
    failing = data.get("failing_chain")
    if not isinstance(failing, list):
        errors.append("'failing_chain' must be an array.")
    elif not all(isinstance(s, str) for s in failing):
        errors.append("All items in 'failing_chain' must be strings.")

    # Validate summary
    summary = data.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        errors.append("'summary' must be a non-empty string.")

    return len(errors) == 0, errors
