"""Post-processing and output validation for code.stacktrace_mapper skill."""

from __future__ import annotations

from typing import Any


def validate_output(data: dict[str, Any]) -> tuple[bool, list[str]]:
    """Validate structured output."""
    errors: list[str] = []

    if not isinstance(data, dict):
        return False, ["Output must be a JSON object."]

    frames = data.get("frames")
    if not isinstance(frames, list):
        errors.append("'frames' must be an array.")
    else:
        for i, frame in enumerate(frames):
            if not isinstance(frame, dict):
                errors.append(f"frames[{i}] must be an object.")
                continue
            if not isinstance(frame.get("file"), str):
                errors.append(f"frames[{i}].file must be a string.")
            if not isinstance(frame.get("line"), int):
                errors.append(f"frames[{i}].line must be an integer.")
            if not isinstance(frame.get("function"), str):
                errors.append(f"frames[{i}].function must be a string.")
            if not isinstance(frame.get("is_user_code"), bool):
                errors.append(f"frames[{i}].is_user_code must be a boolean.")

    if not isinstance(data.get("language"), str):
        errors.append("'language' must be a string.")

    deepest = data.get("deepest_user_frame")
    if deepest is not None:
        if not isinstance(deepest, dict):
            errors.append("'deepest_user_frame' must be an object or null.")
        else:
            if not isinstance(deepest.get("file"), str):
                errors.append("'deepest_user_frame.file' must be a string.")
            if not isinstance(deepest.get("line"), int):
                errors.append("'deepest_user_frame.line' must be an integer.")

    return len(errors) == 0, errors
