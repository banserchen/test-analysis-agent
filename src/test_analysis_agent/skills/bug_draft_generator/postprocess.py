"""bug.draft_generator 技能的后处理和输出校验。"""

from __future__ import annotations

from typing import Any


def validate_output(data: dict[str, Any]) -> tuple[bool, list[str]]:
    """校验结构化输出。"""
    errors: list[str] = []

    if not isinstance(data, dict):
        return False, ["Output must be a JSON object."]

    valid_severities = {"critical", "high", "medium", "low"}

    drafts = data.get("bug_drafts")
    if not isinstance(drafts, list):
        errors.append("'bug_drafts' must be an array.")
    else:
        for i, draft in enumerate(drafts):
            if not isinstance(draft, dict):
                errors.append(f"bug_drafts[{i}] must be an object.")
                continue
            if not isinstance(draft.get("title"), str) or not draft["title"].strip():
                errors.append(f"bug_drafts[{i}].title must be a non-empty string.")
            sev = draft.get("severity", "")
            if sev not in valid_severities:
                errors.append(f"bug_drafts[{i}].severity '{sev}' not in {sorted(valid_severities)}.")
            if not isinstance(draft.get("description"), str):
                errors.append(f"bug_drafts[{i}].description must be a string.")
            labels = draft.get("labels")
            if labels is not None and not isinstance(labels, list):
                errors.append(f"bug_drafts[{i}].labels must be an array or null.")

    count = data.get("count")
    if not isinstance(count, int):
        errors.append("'count' must be an integer.")

    return len(errors) == 0, errors
